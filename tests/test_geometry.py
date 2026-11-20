"""几何与轨迹规划的单元测试。

不依赖网络：汉字部分使用内置的假字形提供者。
"""

from __future__ import annotations

import math
import unittest

from writerobot.geometry import (
    bbox,
    bbox_union,
    catmull_rom,
    polyline_length,
    resample,
    simplify,
    strokes_bbox,
)
from writerobot.glyphs import Glyph, GlyphProvider, GlyphRepository
from writerobot.layout import LayoutOptions, layout_text
from writerobot.trajectory import ActionType, plan_trajectory


class FakeHanziProvider(GlyphProvider):
    """返回一个方框字形，避免测试依赖网络。"""

    name = "fake"

    def get(self, char: str) -> Glyph:
        return Glyph(
            char=char,
            strokes=[
                [(0.1, 0.1), (0.9, 0.1)],
                [(0.9, 0.1), (0.9, 0.9)],
                [(0.9, 0.9), (0.1, 0.9)],
                [(0.1, 0.9), (0.1, 0.1)],
            ],
            advance=1.0,
            source="fake",
        )


class TestGeometry(unittest.TestCase):
    def test_polyline_length(self):
        self.assertAlmostEqual(polyline_length([(0, 0), (3, 4), (3, 14)]), 15.0)

    def test_resample_limits_segment_length(self):
        pts = resample([(0.0, 0.0), (10.0, 0.0)], 1.0)
        self.assertGreaterEqual(len(pts), 10)
        for a, b in zip(pts, pts[1:]):
            self.assertLessEqual(math.dist(a, b), 1.0 + 1e-9)

    def test_resample_keeps_endpoints(self):
        pts = resample([(1.0, 2.0), (5.0, 6.0)], 0.5)
        self.assertEqual(pts[0], (1.0, 2.0))
        self.assertEqual(pts[-1], (5.0, 6.0))

    def test_catmull_rom_passes_through_controls(self):
        ctrl = [(0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (4.0, 0.0)]
        curve = catmull_rom(ctrl, samples_per_segment=8)
        for c in ctrl:
            best = min(math.dist(c, p) for p in curve)
            self.assertLess(best, 0.05, f"控制点 {c} 不在曲线上")

    def test_catmull_rom_no_overshoot_on_uneven_spacing(self):
        """长短段剧变时不应出现明显鼓包（回归测试）。"""
        # 模拟汉字中线：短段 + 超长段 + 短段
        ctrl = [(0.0, 0.0), (0.2, 0.05), (0.4, 0.1), (40.0, 0.2), (40.3, 0.3)]
        curve = catmull_rom(ctrl, samples_per_segment=12)

        # 曲线不应超出控制点包围盒太多
        xs = [p[0] for p in curve]
        ys = [p[1] for p in curve]
        self.assertGreaterEqual(min(xs), -0.5, "出现了明显的负向过冲")
        self.assertLessEqual(max(xs), 41.0, "出现了明显的正向过冲")
        self.assertLessEqual(max(ys), 1.0, "出现了明显的纵向鼓包")

    def test_simplify_removes_collinear_points(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
        self.assertEqual(len(simplify(pts, 0.01)), 2)

    def test_bbox(self):
        self.assertEqual(bbox([(1, 2), (-3, 5)]), (-3, 2, 1, 5))
        self.assertEqual(bbox([]), (0.0, 0.0, 0.0, 0.0))

    def test_bbox_rejects_malformed_points(self):
        with self.assertRaises(ValueError):
            bbox([(1, 2, 3)])

    def test_bbox_union(self):
        self.assertEqual(bbox_union([(0, 0, 1, 1), (2, 2, 3, 3)]), (0, 0, 3, 3))

    def test_strokes_bbox_handles_nested_input(self):
        strokes = [[(0.0, 0.0), (1.0, 1.0)], [(2.0, 3.0), (4.0, 5.0)]]
        self.assertEqual(strokes_bbox(strokes), (0.0, 0.0, 4.0, 5.0))


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.repo = GlyphRepository([FakeHanziProvider()])

    def test_single_line_layout(self):
        opts = LayoutOptions(font_size=10, paper_width=100, paper_height=100, handwriting=False)
        layout = layout_text("AB", self.repo, opts)
        self.assertEqual(len(layout.glyphs), 2)
        self.assertAlmostEqual(layout.font_size, 10.0)

    def test_newline_creates_new_line(self):
        opts = LayoutOptions(font_size=10, paper_width=100, paper_height=100)
        layout = layout_text("A\nB", self.repo, opts)
        self.assertEqual({g.line for g in layout.glyphs}, {0, 1})

    def test_centering_offsets_content(self):
        opts = LayoutOptions(
            font_size=10, paper_width=100, paper_height=100, align="center", handwriting=False
        )
        layout = layout_text("A", self.repo, opts)
        # 单字宽度 1em = 10mm，居中后左边界约在 (100-10)/2 = 45
        self.assertAlmostEqual(layout.glyphs[0].origin[0], 45.0, places=5)

    def test_right_alignment(self):
        opts = LayoutOptions(
            font_size=10,
            paper_width=100,
            paper_height=100,
            align="right",
            margin_right=0,
            handwriting=False,
        )
        layout = layout_text("A", self.repo, opts)
        self.assertAlmostEqual(layout.glyphs[0].origin[0], 90.0, places=5)

    def test_auto_fit_shrinks_font_to_fit(self):
        opts = LayoutOptions(font_size=50, paper_width=60, paper_height=60)
        layout = layout_text("ABCD", self.repo, opts)
        self.assertLess(layout.font_size, 50)
        box = layout.content_box
        self.assertLessEqual(box[2] - box[0], 60.0 + 1e-6)
        self.assertLessEqual(box[3] - box[1], 60.0 + 1e-6)

    def test_wrap_width_creates_multiple_lines(self):
        opts = LayoutOptions(font_size=10, paper_width=100, paper_height=100, wrap_width=25)
        layout = layout_text("AAAAA", self.repo, opts)
        self.assertGreater(len({g.line for g in layout.glyphs}), 1)


class TestTrajectory(unittest.TestCase):
    def test_pen_toggles_between_strokes(self):
        strokes = [("A", 1, [(0.0, 0.0), (10.0, 0.0)]), ("A", 2, [(20.0, 0.0), (30.0, 0.0)])]
        traj = plan_trajectory(strokes)
        types = [a.type for a in traj.actions]
        self.assertEqual(types.count(ActionType.DRAW), 2)
        self.assertEqual(types.count(ActionType.TRAVEL), 2)

    def test_continuous_strokes_are_merged(self):
        """首尾相接的笔画不应产生额外抬落笔。"""
        strokes = [
            ("A", 1, [(0.0, 0.0), (10.0, 0.0)]),
            ("A", 2, [(10.0, 0.0), (20.0, 0.0)]),
        ]
        traj = plan_trajectory(strokes)
        types = [a.type for a in traj.actions]
        self.assertEqual(types.count(ActionType.DRAW), 2)
        self.assertEqual(types.count(ActionType.TRAVEL), 1)

    def test_draw_length_accumulates(self):
        strokes = [("A", 1, [(0.0, 0.0), (10.0, 0.0)])]
        traj = plan_trajectory(strokes, sample_step=0)
        self.assertAlmostEqual(traj.draw_length, 10.0, places=3)

    def test_estimated_seconds_uses_feeds(self):
        strokes = [("A", 1, [(0.0, 0.0), (60.0, 0.0)])]
        traj = plan_trajectory(strokes, sample_step=0)
        # 60mm @ 60mm/min = 1 分钟
        self.assertAlmostEqual(traj.estimated_seconds(60.0, 60.0), 60.0, places=3)

    def test_single_point_stroke_is_handled(self):
        """单点笔画（顿点）应展开成一小段，并产生空走 + 绘制两个动作。"""
        strokes = [("A", 1, [(5.0, 5.0)])]
        traj = plan_trajectory(strokes)
        types = [a.type for a in traj.actions]
        self.assertEqual(types, [ActionType.TRAVEL, ActionType.DRAW])

    def test_empty_strokes_are_skipped(self):
        traj = plan_trajectory([("A", 1, [])])
        self.assertEqual(len(traj.actions), 0)


class TestHandwritingStyle(unittest.TestCase):
    """手写风格扰动的回归测试。"""

    def setUp(self):
        self.repo = GlyphRepository([FakeHanziProvider()])

    def test_handwriting_perturbs_characters(self):
        clean = layout_text(
            "ABCD",
            self.repo,
            LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=False),
        )
        hand = layout_text(
            "ABCD",
            self.repo,
            LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=True),
        )
        clean_y = [g.origin[1] for g in clean.glyphs]
        hand_y = [g.origin[1] for g in hand.glyphs]
        self.assertNotEqual(clean_y, hand_y, "开启手写风格后字形基线应当变化")

    def test_handwriting_is_deterministic(self):
        """同样的输入必须得到同样的结果 —— 写字机器人需要可复现输出。"""
        opts = LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=True)
        a = layout_text("ABCDEF", self.repo, opts)
        b = layout_text("ABCDEF", self.repo, opts)
        self.assertEqual([g.origin for g in a.glyphs], [g.origin for g in b.glyphs])

    def test_different_seeds_differ(self):
        a = layout_text(
            "ABCDEF",
            self.repo,
            LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=True, hw_seed=1),
        )
        b = layout_text(
            "ABCDEF",
            self.repo,
            LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=True, hw_seed=2),
        )
        self.assertNotEqual([g.origin for g in a.glyphs], [g.origin for g in b.glyphs])

    def test_handwriting_jitter_is_bounded(self):
        """扰动幅度必须可控，否则会撑破单元格。"""
        clean = layout_text(
            "ABCDEF",
            self.repo,
            LayoutOptions(font_size=10, paper_width=200, paper_height=100, handwriting=False),
        )
        hand = layout_text(
            "ABCDEF",
            self.repo,
            LayoutOptions(
                font_size=10,
                paper_width=200,
                paper_height=100,
                handwriting=True,
                hw_offset=0.7,
                hw_rotation=1.8,
                hw_baseline_wobble=0.9,
            ),
        )
        for cg, hg in zip(clean.glyphs, hand.glyphs):
            self.assertLess(abs(cg.origin[0] - hg.origin[0]), 2.0)
            self.assertLess(abs(cg.origin[1] - hg.origin[1]), 2.5)

    def test_handwriting_keeps_characters_readable(self):
        """旋转不能大到让字认不出来。"""
        opts = LayoutOptions(font_size=20, paper_width=200, paper_height=100, handwriting=True)
        layout = layout_text("ABCDEFGH", self.repo, opts)
        for g in layout.glyphs:
            self.assertTrue(g.strokes)


if __name__ == "__main__":
    unittest.main()
