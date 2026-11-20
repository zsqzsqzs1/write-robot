"""表格绘制：把二维表格画成写字机器人能写的笔画。

表格由两部分组成：

* **表格线** —— 直线段，直接作为笔画（落笔走直线）绘制。
* **单元格文字** —— 复用普通文字排版引擎，只是被限制在单元格的内部区域里。

坐标约定与排版模块一致：纸张左下角为原点，X 向右、Y 向上，单位 mm。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Point, Polyline, strokes_bbox
from .glyphs import GlyphRepository
from .layout import LayoutOptions, layout_text

__all__ = ["TableSpec", "TableLayout", "layout_table"]


@dataclass
class TableSpec:
    """表格定义。"""

    #: 行数据，每行是一个单元格文本列表（单元格内可用 \\n 换行）
    rows: List[List[str]]

    #: 各列宽度 (mm)；None 表示按内容自动计算
    col_widths: Optional[List[float]] = None

    #: 单元格内边距 (mm)
    padding: float = 3.0

    #: 表格线宽 (mm)
    border_width: float = 0.45

    #: 单元格内文字对齐
    align: str = "center"
    valign: str = "center"

    #: 是否绘制表格线
    draw_borders: bool = True

    def validate(self) -> None:
        if not self.rows:
            raise ValueError("表格至少要有一行")
        width = len(self.rows[0])
        if width == 0:
            raise ValueError("表格至少要有一列")
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"第 {i + 1} 行有 {len(row)} 个单元格，应为 {width} 个")
        if self.col_widths is not None and len(self.col_widths) != width:
            raise ValueError(f"col_widths 有 {len(self.col_widths)} 项，应为 {width} 项")


@dataclass
class TableLayout:
    """表格排版结果。"""

    #: 表格线（笔画，纸张坐标 mm）
    borders: List[Polyline] = field(default_factory=list)

    #: 单元格文字笔画：(字符, 笔画序号, 点序列)
    text_strokes: List[Tuple[str, int, Polyline]] = field(default_factory=list)

    #: 表格整体包围盒
    box: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    #: 实际使用的字号
    font_size: float = 0.0

    #: 各列实际宽度 (mm)
    col_widths: List[float] = field(default_factory=list)

    #: 各行实际高度 (mm)
    row_heights: List[float] = field(default_factory=list)

    @property
    def all_strokes(self) -> List[Tuple[str, int, Polyline]]:
        """表格线在前、文字在后 —— 真实写字机器人也是先画框再写字。

        注意：表格线属于几何线条，不应混进文字的平滑管线，
        请配合 :func:`writerobot.trajectory.plan_trajectory` 的
        ``geometry`` 参数使用。
        """
        return list(self.text_strokes)

    @property
    def cell_count(self) -> int:
        return len(self.text_strokes) and sum(1 for _ in self.text_strokes)


def _cell_lines(text: str) -> List[str]:
    """单元格内的一行行文本。"""
    return text.split("\n") if text else [""]


def _column_em_width(rows: Sequence[Sequence[str]], col: int, repository: GlyphRepository) -> float:
    """某列最宽单元格的推进宽度（em）。"""
    widest = 0.0
    for row in rows:
        for line in _cell_lines(row[col]):
            width = 0.0
            for ch in line:
                glyph = repository.get(ch)
                width += glyph.advance if glyph else 0.5
            widest = max(widest, width)
    return widest


def layout_table(
    spec: TableSpec,
    repository: GlyphRepository,
    *,
    paper_width: float,
    paper_height: float,
    font_size: float = 20.0,
    margin: float = 10.0,
    line_height: float = 1.4,
    letter_spacing: float = 0.05,
    min_font_size: float = 3.0,
) -> TableLayout:
    """把表格排到纸张上。

    字号会自适应收缩，直到表格能完整放进书写区域。
    """
    spec.validate()

    avail_w = paper_width - margin * 2
    avail_h = paper_height - margin * 2
    n_cols = len(spec.rows[0])
    n_rows = len(spec.rows)

    size = font_size
    # 内边距随字号等比缩放：否则字号缩到下限后，固定的内边距会成为
    # 表格高度的死底线，导致表格仍然装不进书写区域。
    pad = spec.padding

    for _ in range(24):
        col_widths = _compute_col_widths(spec, repository, size, pad)
        row_heights = _compute_row_heights(spec, repository, size, line_height, pad)

        total_w = sum(col_widths)
        total_h = sum(row_heights)

        if total_w <= avail_w and total_h <= avail_h:
            break
        if total_w <= 0 or total_h <= 0:
            break

        factor = min(avail_w / total_w, avail_h / total_h)
        new_size = max(min_font_size, size * factor)
        if new_size >= size:
            break
        pad = max(0.4, pad * (new_size / size))
        size = new_size

    col_widths = _compute_col_widths(spec, repository, size, pad)
    row_heights = _compute_row_heights(spec, repository, size, line_height, pad)

    total_w = sum(col_widths)
    total_h = sum(row_heights)

    # 整体在书写区域内居中
    x0 = margin + (avail_w - total_w) / 2.0
    y1 = paper_height - margin - (avail_h - total_h) / 2.0
    y0 = y1 - total_h

    layout = TableLayout(
        font_size=size,
        col_widths=col_widths,
        row_heights=row_heights,
        box=(x0, y0, x0 + total_w, y1),
    )

    # ---- 表格线 ----
    if spec.draw_borders:
        layout.borders = _build_borders(
            x0, y0, x0 + total_w, y1, col_widths, row_heights
        )

    # ---- 单元格文字 ----
    col_edges = [x0]
    for w in col_widths:
        col_edges.append(col_edges[-1] + w)
    row_edges = [y1]
    for h in row_heights:
        row_edges.append(row_edges[-1] - h)

    for r, row in enumerate(spec.rows):
        for c, cell_text in enumerate(row):
            cell_x0 = col_edges[c] + pad
            cell_x1 = col_edges[c + 1] - pad
            cell_y1 = row_edges[r] - pad
            cell_y0 = row_edges[r + 1] + pad

            inner_w = cell_x1 - cell_x0
            inner_h = cell_y1 - cell_y0
            if inner_w <= 0 or inner_h <= 0:
                continue

            options = LayoutOptions(
                font_size=size,
                line_height=line_height,
                letter_spacing=letter_spacing,
                paper_width=inner_w,
                paper_height=inner_h,
                margin_left=0.0,
                margin_right=0.0,
                margin_top=0.0,
                margin_bottom=0.0,
                align=spec.align,
                valign=spec.valign,
                auto_fit=True,
                min_font_size=min_font_size,
            )
            cell_layout = layout_text(cell_text, repository, options)

            for glyph in cell_layout.glyphs:
                for idx, stroke in enumerate(glyph.strokes, start=1):
                    moved = [(px + cell_x0, py + cell_y0) for px, py in stroke]
                    layout.text_strokes.append((glyph.char, idx, moved))

    return layout


def _compute_col_widths(
    spec: TableSpec, repository: GlyphRepository, font_size: float, padding: float
) -> List[float]:
    """计算各列宽度 (mm)。"""
    n_cols = len(spec.rows[0])
    if spec.col_widths is not None:
        return list(spec.col_widths)

    widths = []
    for c in range(n_cols):
        em = _column_em_width(spec.rows, c, repository)
        widths.append(em * font_size + padding * 2)
    return widths


def _compute_row_heights(
    spec: TableSpec,
    repository: GlyphRepository,
    font_size: float,
    line_height: float,
    padding: float,
) -> List[float]:
    """计算各行高度 (mm)。"""
    heights = []
    for row in spec.rows:
        max_lines = max(len(_cell_lines(cell)) for cell in row)
        heights.append(max_lines * font_size * line_height + padding * 2)
    return heights


def _build_borders(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    col_widths: Sequence[float],
    row_heights: Sequence[float],
) -> List[Polyline]:
    """生成表格线：外框 + 内部横线 + 内部竖线。"""
    lines: List[Polyline] = []

    # 外框（一条闭合路径，笔尖不用抬笔就能画完一圈）
    lines.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])

    # 内部竖线
    edge = x0
    for w in col_widths[:-1]:
        edge += w
        lines.append([(edge, y0), (edge, y1)])

    # 内部横线
    edge = y1
    for h in row_heights[:-1]:
        edge -= h
        lines.append([(x0, edge), (x1, edge)])

    return lines
