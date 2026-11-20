"""表格绘制模块的单元测试。"""

from __future__ import annotations

import unittest

from writerobot.geometry import bbox, polyline_length
from writerobot.glyphs import Glyph, GlyphProvider, GlyphRepository
from writerobot.table import TableSpec, layout_table


class FakeProvider(GlyphProvider):
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


def _repo():
    return GlyphRepository([FakeProvider()])


class TestTableSpec(unittest.TestCase):
    def test_empty_table_rejected(self):
        with self.assertRaises(ValueError):
            TableSpec(rows=[]).validate()

    def test_ragged_rows_rejected(self):
        with self.assertRaises(ValueError):
            TableSpec(rows=[["a", "b"], ["c"]]).validate()

    def test_col_widths_length_checked(self):
        with self.assertRaises(ValueError):
            TableSpec(rows=[["a", "b"]], col_widths=[10]).validate()


class TestLayoutTable(unittest.TestCase):
    def setUp(self):
        self.repo = _repo()
        self.spec = TableSpec(rows=[["A", "B"], ["C", "D"], ["E", "F"]])

    def test_basic_geometry(self):
        t = layout_table(
            self.spec, self.repo, paper_width=200, paper_height=150, font_size=20, margin=10
        )
        self.assertEqual(len(t.col_widths), 2)
        self.assertEqual(len(t.row_heights), 3)
        # 外框 + 1 条竖线 + 2 条横线
        self.assertEqual(len(t.borders), 4)
        box = t.box
        self.assertAlmostEqual(box[2] - box[0], sum(t.col_widths), places=6)
        self.assertAlmostEqual(box[3] - box[1], sum(t.row_heights), places=6)

    def test_border_is_rectangular(self):
        """外框必须是规整矩形 —— 不能被样条平滑甩成圆角团块。"""
        t = layout_table(
            self.spec, self.repo, paper_width=200, paper_height=150, font_size=20, margin=10
        )
        outer = t.borders[0]
        xs = [p[0] for p in outer]
        ys = [p[1] for p in outer]
        # 只有 4 个角点坐标（首尾重合）
        self.assertEqual(len(set(xs)), 2, "外框出现了多余的 X 坐标，说明被平滑变形")
        self.assertEqual(len(set(ys)), 2, "外框出现了多余的 Y 坐标，说明被平滑变形")

    def test_text_stays_inside_cells(self):
        t = layout_table(
            self.spec, self.repo, paper_width=200, paper_height=150, font_size=20, margin=10
        )
        self.assertTrue(t.text_strokes)
        tbox = t.box
        for char, idx, stroke in t.text_strokes:
            b = bbox(stroke)
            self.assertGreaterEqual(b[0], tbox[0] - 1e-6, f"{char} 超出表格左边界")
            self.assertLessEqual(b[2], tbox[2] + 1e-6, f"{char} 超出表格右边界")
            self.assertGreaterEqual(b[1], tbox[1] - 1e-6, f"{char} 超出表格下边界")
            self.assertLessEqual(b[3], tbox[3] + 1e-6, f"{char} 超出表格上边界")

    def test_font_shrinks_to_fit(self):
        """内容放不下时字号必须自动缩小。"""
        rows = [["长句子测试内容"]] * 12
        t = layout_table(
            TableSpec(rows=rows),
            self.repo,
            paper_width=120,
            paper_height=80,
            font_size=30,
            margin=8,
        )
        self.assertLess(t.font_size, 30)
        self.assertLessEqual(t.box[2] - t.box[0], 120 - 16 + 1e-6)
        self.assertLessEqual(t.box[3] - t.box[1], 80 - 16 + 1e-6)

    def test_multiline_cell(self):
        t = layout_table(
            TableSpec(rows=[["A\nB"]]),
            self.repo,
            paper_width=200,
            paper_height=150,
            font_size=20,
            margin=10,
        )
        # 两个字符各 4 笔
        self.assertEqual(len(t.text_strokes), 8)

    def test_no_borders_option(self):
        t = layout_table(
            TableSpec(rows=[["A"]], draw_borders=False),
            self.repo,
            paper_width=200,
            paper_height=150,
        )
        self.assertEqual(len(t.borders), 0)


class TestTableGeometryNotSmoothed(unittest.TestCase):
    """表格线是几何直线，不能走平滑管线。"""

    def test_geometry_stays_straight(self):
        from writerobot.trajectory import ActionType, plan_trajectory

        line = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)]
        traj = plan_trajectory([], geometry=[line], smooth=True, corner_radius=0.6)
        draws = [a for a in traj.actions if a.type == ActionType.DRAW]
        self.assertEqual(len(draws), 1)
        self.assertEqual(len(draws[0].points), 3, "几何线被重采样或平滑了")
        self.assertAlmostEqual(polyline_length(draws[0].points), 150.0, places=6)

    def test_geometry_drawn_before_text(self):
        from writerobot.trajectory import ActionType, plan_trajectory

        line = [(0.0, 0.0), (10.0, 0.0)]
        text = [("A", 1, [(20.0, 0.0), (30.0, 0.0)])]
        traj = plan_trajectory(text, geometry=[line])
        types = [a.type for a in traj.actions]
        self.assertEqual(types.index(ActionType.DRAW), 0, "表格线应当先于文字绘制")


if __name__ == "__main__":
    unittest.main()
