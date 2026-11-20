"""字形、排版、G-code 与模拟器的单元测试。"""

from __future__ import annotations

import json
import math
import re
import unittest

from writerobot.glyphs import Glyph, GlyphProvider, GlyphRepository, LatinProvider
from writerobot.gcode import GCodeGenerator, generate_gcode
from writerobot.layout import LayoutOptions, layout_text
from writerobot.machine import Kinematics, MachineConfig, PenConfig
from writerobot.pipeline import WriteRequest, build_job
from writerobot.simulator import SimulationOptions, render_html, render_svg
from writerobot.trajectory import plan_trajectory


class TestLatinProvider(unittest.TestCase):
    def setUp(self):
        self.provider = LatinProvider()

    def test_uppercase_letters_exist(self):
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            glyph = self.provider.get(ch)
            self.assertIsNotNone(glyph, f"缺少字形 {ch}")
            self.assertGreater(glyph.stroke_count, 0, f"字形 {ch} 没有笔画")

    def test_digits_exist(self):
        for ch in "0123456789":
            self.assertIsNotNone(self.provider.get(ch))

    def test_punctuation_exists(self):
        for ch in ".,:;!?-/()[]{}":
            self.assertIsNotNone(self.provider.get(ch), f"缺少标点 {ch}")

    def test_space_has_no_strokes(self):
        glyph = self.provider.get(" ")
        self.assertIsNotNone(glyph)
        self.assertEqual(glyph.stroke_count, 0)

    def test_lowercase_uses_small_caps(self):
        glyph = self.provider.get("a")
        upper = self.provider.get("A")
        self.assertIsNotNone(glyph)
        self.assertLess(glyph.advance, upper.advance)

    def test_unknown_char_returns_none(self):
        self.assertIsNone(self.provider.get("字"))

    def test_glyph_bounds_within_em(self):
        glyph = self.provider.get("A")
        xs = [p[0] for p in glyph.points()]
        ys = [p[1] for p in glyph.points()]
        self.assertGreaterEqual(min(xs), -1e-9)
        self.assertLessEqual(max(xs), glyph.advance + 1e-9)
        self.assertGreaterEqual(min(ys), -1e-9)
        self.assertLessEqual(max(ys), 1.0 + 1e-9)


class FakeProvider(GlyphProvider):
    name = "fake"

    def get(self, char: str) -> Glyph:
        return Glyph(
            char=char,
            strokes=[[(0.0, 0.0), (1.0, 0.0)], [(1.0, 0.0), (1.0, 1.0)]],
            advance=1.0,
            source="fake",
        )


class TestGlyphRouting(unittest.TestCase):
    """字符路由与故障降级的回归测试。"""

    def test_is_hanzi(self):
        from writerobot.glyphs import is_hanzi

        for ch in "你你好世界的一":
            self.assertTrue(is_hanzi(ch), f"{ch} 应识别为汉字")
        for ch in "ABCabc123 ,.":
            self.assertFalse(is_hanzi(ch), f"{ch} 不应识别为汉字")

    def test_ascii_never_hits_network(self):
        """ASCII 字符必须直接走内置字体，不发起网络请求。"""

        class ExplodingProvider(GlyphProvider):
            name = "boom"

            def get(self, char):
                raise AssertionError(f"不该请求 {char!r}")

        repo = GlyphRepository([ExplodingProvider(), LatinProvider()])
        glyph = repo.get("A")
        self.assertIsNotNone(glyph)
        self.assertEqual(glyph.source, "builtin-latin")

    def test_provider_failure_does_not_abort(self):
        """单个数据源故障应降级为警告，而不是让整个任务失败。"""

        class BrokenProvider(GlyphProvider):
            name = "broken"

            def get(self, char):
                raise OSError("网络不可达")

        repo = GlyphRepository([BrokenProvider(), LatinProvider()])
        glyph = repo.get("A")
        self.assertIsNotNone(glyph, "备用来源应当兜住")
        self.assertTrue(repo.warnings)
        self.assertIn("broken", repo.warnings[0])

    def test_hanzi_provider_skips_non_hanzi(self):
        from writerobot.glyphs import HanziProvider

        provider = HanziProvider(offline=True)
        self.assertIsNone(provider.get("A"))
        self.assertIsNone(provider.get("1"))


class TestMachineConfig(unittest.TestCase):
    def test_default_valid(self):
        MachineConfig().validate()

    def test_invalid_kinematics_rejected(self):
        with self.assertRaises(ValueError):
            MachineConfig(kinematics="delta").validate()

    def test_invalid_origin_rejected(self):
        with self.assertRaises(ValueError):
            MachineConfig(origin="middle").validate()

    def test_z_up_must_exceed_z_down(self):
        with self.assertRaises(ValueError):
            MachineConfig(pen=PenConfig(mode="z", z_up=0, z_down=10)).validate()

    def test_servo_values_must_differ(self):
        with self.assertRaises(ValueError):
            MachineConfig(pen=PenConfig(mode="servo", servo_up=500, servo_down=500)).validate()

    def test_origin_flipping(self):
        m = MachineConfig(origin="back-right", width=100, height=50)
        x, y = m.to_machine(0.0, 0.0)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 50.0)

    def test_cartesian_passthrough(self):
        m = MachineConfig(kinematics=Kinematics.CARTESIAN)
        self.assertEqual(m.to_machine(12.0, 34.0), (12.0, 34.0))

    def test_corexy_firmware_mode_is_passthrough(self):
        m = MachineConfig(kinematics=Kinematics.COREXY, corexy_host_solve=False)
        self.assertEqual(m.to_machine(12.0, 34.0), (12.0, 34.0))

    def test_corexy_host_solve(self):
        """CoreXY 几何：LA = X + Y, LB = X - Y。"""
        m = MachineConfig(kinematics=Kinematics.COREXY, corexy_host_solve=True)
        a, b = m.to_machine(10.0, 4.0)
        self.assertAlmostEqual(a, 14.0)
        self.assertAlmostEqual(b, 6.0)

    def test_config_roundtrip(self):
        import tempfile
        from pathlib import Path

        m = MachineConfig(name="t", kinematics="corexy", width=123, height=45)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            m.save(path)
            loaded = MachineConfig.load(path)
        self.assertEqual(loaded.name, "t")
        self.assertEqual(loaded.width, 123)
        self.assertEqual(loaded.kinematics, "corexy")


class TestGCode(unittest.TestCase):
    def setUp(self):
        self.strokes = [
            ("A", 1, [(0.0, 0.0), (10.0, 0.0)]),
            ("A", 2, [(20.0, 5.0), (30.0, 5.0)]),
        ]
        self.trajectory = plan_trajectory(self.strokes)

    def test_header_contains_unit_commands(self):
        gcode = generate_gcode(self.trajectory, MachineConfig())
        self.assertIn("G21", gcode)
        self.assertIn("G90", gcode)
        self.assertIn("M2", gcode)

    def test_servo_pen_commands(self):
        machine = MachineConfig(pen=PenConfig(mode="servo", servo_down=0, servo_up=1000))
        gcode = generate_gcode(self.trajectory, machine)
        self.assertIn("M3 S0", gcode)
        self.assertIn("M3 S1000", gcode)

    def test_z_pen_commands(self):
        machine = MachineConfig(pen=PenConfig(mode="z", z_up=8.0, z_down=0.0))
        gcode = generate_gcode(self.trajectory, machine)
        self.assertIn("G0 Z8", gcode)
        self.assertIn("G0 Z0", gcode)

    def test_coordinates_are_absolute(self):
        gcode = generate_gcode(self.trajectory, MachineConfig())
        for line in gcode.splitlines():
            if line.startswith("G1 X"):
                self.assertNotIn("G91", line)

    def test_line_numbers(self):
        gcode = generate_gcode(self.trajectory, MachineConfig(), line_numbers=True)
        self.assertRegex(gcode, r"^N1 ", )
        for i, line in enumerate(gcode.splitlines(), start=1):
            self.assertTrue(line.startswith(f"N{i} "), f"第 {i} 行缺少行号")

    def test_feed_values_present(self):
        machine = MachineConfig(draw_feed=1234, travel_feed=5678)
        gcode = generate_gcode(self.trajectory, machine)
        self.assertIn("F1234", gcode)
        self.assertIn("F5678", gcode)

    def test_no_negative_zero(self):
        gcode = generate_gcode(self.trajectory, MachineConfig())
        for line in gcode.splitlines():
            self.assertNotIn("-0 ", line)
            self.assertNotRegex(line, r"X-0\b")


class TestSimulator(unittest.TestCase):
    def setUp(self):
        self.trajectory = plan_trajectory(
            [("A", 1, [(0.0, 0.0), (10.0, 0.0)]), ("A", 2, [(20.0, 5.0), (30.0, 5.0)])]
        )
        self.machine = MachineConfig(width=100, height=100)

    def test_svg_is_valid(self):
        svg = render_svg(self.trajectory, self.machine)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)
        self.assertIn("viewBox", svg)

    def test_svg_path_count_matches_actions(self):
        svg = render_svg(self.trajectory, self.machine)
        self.assertEqual(svg.count("<path"), len(self.trajectory.actions))

    def test_html_contains_embedded_data(self):
        html = render_html(self.trajectory, self.machine)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("const DATA = {", html)
        self.assertNotIn("</script>", html.split("const DATA =")[1][:2000])

    def test_html_is_self_contained(self):
        html = render_html(self.trajectory, self.machine)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)

    def test_table_view_rendered_when_data_given(self):
        """给了表格数据时，预览页应直接用 HTML 表格展示内容。"""
        rows = [["我是一名小学生", "我是一名中学生"], ["我是工人", "我是老师"]]
        html = render_html(
            self.trajectory,
            self.machine,
            SimulationOptions(table_data=rows),
        )
        self.assertIn("表格内容", html)
        self.assertIn("书写轨迹", html)
        self.assertIn("renderTable", html)
        # 表格内容以 JSON 形式嵌入
        self.assertIn("我是一名小学生", html)

    def test_no_table_view_without_data(self):
        """没有表格数据时不应启用表格视图（标签页由 JS 隐藏）。"""
        html = render_html(self.trajectory, self.machine)
        # 模板里始终含有标签页按钮，但数据为 null 时 JS 不会启用它
        self.assertIn('"tableData": null', html)
        self.assertIn("view-trace", html)


class TestPipeline(unittest.TestCase):
    def test_build_job_end_to_end(self):
        repo = GlyphRepository([FakeProvider()])
        machine = MachineConfig(width=100, height=100)
        request = WriteRequest(text="AB", machine=machine)
        request.layout = LayoutOptions(font_size=10, paper_width=100, paper_height=100)

        # 用假仓库替换真实仓库（避免网络）
        import writerobot.pipeline as pipeline_mod

        original = pipeline_mod.GlyphRepository.default
        pipeline_mod.GlyphRepository.default = classmethod(lambda cls, **kw: repo)
        try:
            job = pipeline_mod.build_job(request)
        finally:
            pipeline_mod.GlyphRepository.default = original

        self.assertEqual(len(job.layout.glyphs), 2)
        self.assertEqual(job.stroke_count, 4)
        self.assertIn("G21", job.gcode)
        self.assertGreater(job.estimated_seconds, 0)


if __name__ == "__main__":
    unittest.main()
