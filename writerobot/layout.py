"""文本排版：把一行/多行文本排布到书写区域中。

排版模型
--------
* 文字按 **em 盒** 排布：汉字占 1x1 的方格，拉丁字母按各自推进宽度。
* 支持自动换行、自动缩放（字号自适应）、对齐、字间距与行间距。
* 输出的每个字形都被平移到最终机器坐标（mm）。

坐标约定：排版内部以 **纸张左下角为原点**，X 向右、Y 向上（单位 mm）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Point, Polyline, bbox_union, strokes_bbox
from .glyphs import Glyph, GlyphRepository

__all__ = ["TextLayout", "LayoutOptions", "PlacedGlyph"]


@dataclass
class LayoutOptions:
    """排版参数。"""

    #: 字号（em 盒高度，mm）。汉字的实际墨迹高度约为字号的 0.85。
    font_size: float = 20.0

    #: 行距系数（相对于字号）
    line_height: float = 1.6

    #: 字间距（相对字号的额外间隙比例）
    letter_spacing: float = 0.05

    #: 书写区域尺寸 (mm)
    paper_width: float = 200.0
    paper_height: float = 200.0

    #: 四周留边 (mm)
    margin_left: float = 10.0
    margin_right: float = 10.0
    margin_top: float = 10.0
    margin_bottom: float = 10.0

    #: 水平对齐：``left`` / ``center`` / ``right``
    align: str = "left"

    #: 垂直对齐：``top`` / ``center`` / ``bottom``
    valign: str = "top"

    #: 自动换行宽度（mm）；None 表示不自动换行
    wrap_width: Optional[float] = None

    #: 是否在内容超出区域时自动缩小字号
    auto_fit: bool = True

    #: 自动缩放的最小字号 (mm)
    min_font_size: float = 2.0


@dataclass
class PlacedGlyph:
    """排好版的一个字形。"""

    char: str
    strokes: List[Polyline] = field(default_factory=list)
    #: 字形左下角在纸张坐标系中的位置
    origin: Point = (0.0, 0.0)
    advance: float = 0.0
    #: 该字形占用的行索引
    line: int = 0


@dataclass
class TextLayout:
    """排版结果。"""

    glyphs: List[PlacedGlyph] = field(default_factory=list)
    #: 实际使用的字号
    font_size: float = 0.0
    #: 内容整体包围盒（纸张坐标系）
    content_box: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def stroke_count(self) -> int:
        return sum(len(g.strokes) for g in self.glyphs)

    def all_points(self) -> List[Point]:
        pts: List[Point] = []
        for glyph in self.glyphs:
            for stroke in glyph.strokes:
                pts.extend(stroke)
        return pts


def _split_lines(
    text: str,
    wrap_width: Optional[float],
    font_size: float,
    letter_spacing: float,
    advance_of,
) -> List[List[str]]:
    """按显式换行符和可选的自动换行宽度拆分文本。

    ``wrap_width``、``font_size`` 均为毫米；``advance_of`` 返回字符的 em 推进宽度。
    这里统一换算成毫米再比较，避免 em 与 mm 混用。
    """
    lines: List[List[str]] = []
    for raw_line in text.split("\n"):
        if not raw_line:
            lines.append([])
            continue
        if wrap_width is None:
            lines.append(list(raw_line))
            continue

        current: List[str] = []
        current_width = 0.0
        for ch in raw_line:
            adv_mm = advance_of(ch) * font_size + letter_spacing * font_size
            if current and current_width + adv_mm > wrap_width:
                lines.append(current)
                current = []
                current_width = 0.0
            current.append(ch)
            current_width += adv_mm
        if current:
            lines.append(current)
    return lines


def layout_text(
    text: str,
    repository: GlyphRepository,
    options: LayoutOptions,
) -> TextLayout:
    """把文本排布到书写区域。

    未收录的字符会被跳过，并在 ``missing`` 中返回，方便调用方提示。
    """
    advance_cache: dict[str, float] = {}

    def advance_of(ch: str) -> float:
        if ch not in advance_cache:
            glyph = repository.get(ch)
            advance_cache[ch] = glyph.advance if glyph else 0.5
        return advance_cache[ch]

    # 先按当前字号试排，若超界且允许自适应，则按比例缩小后重排
    font_size = options.font_size
    glyphs, missing = _layout_pass(text, repository, options, font_size)

    if options.auto_fit:
        box = strokes_bbox(s for g in glyphs for s in g.strokes)
        avail_w = options.paper_width - options.margin_left - options.margin_right
        avail_h = options.paper_height - options.margin_top - options.margin_bottom
        if box[2] - box[0] > avail_w or box[3] - box[1] > avail_h:
            content_w = max(box[2] - box[0], 1e-6)
            content_h = max(box[3] - box[1], 1e-6)
            factor = min(avail_w / content_w, avail_h / content_h, 1.0)
            new_size = max(options.min_font_size, font_size * factor)
            if new_size < font_size:
                font_size = new_size
                glyphs, missing = _layout_pass(text, repository, options, font_size)

    content_box = strokes_bbox(s for g in glyphs for s in g.strokes)
    layout = TextLayout(glyphs=glyphs, font_size=font_size, content_box=content_box)
    layout.missing = missing  # type: ignore[attr-defined]
    return layout


def _layout_pass(
    text: str,
    repository: GlyphRepository,
    options: LayoutOptions,
    font_size: float,
) -> Tuple[List[PlacedGlyph], List[str]]:
    """按给定字号排版一遍。"""
    lines = _split_lines(
        text,
        options.wrap_width,
        font_size,
        options.letter_spacing,
        lambda ch: advance_of_units(ch, repository),
    )
    missing: List[str] = []

    placed: List[PlacedGlyph] = []
    line_heights = font_size * options.line_height

    # 先算出每行的宽度，用于水平对齐
    line_widths: List[float] = []
    for line in lines:
        width = sum(advance_of_units(ch, repository) for ch in line) * font_size
        width += options.letter_spacing * font_size * max(0, len(line) - 1)
        line_widths.append(width)

    content_width = max(line_widths) if line_widths else 0.0
    content_height = line_heights * max(0, len(lines) - 1) + font_size

    # 区域内的起始位置
    left = options.margin_left
    top = options.paper_height - options.margin_top

    # 垂直对齐：整体内容块在区域中的位置
    if options.valign == "center":
        start_y = options.margin_bottom + (options.paper_height - options.margin_bottom - options.margin_top - content_height) / 2.0
    elif options.valign == "bottom":
        start_y = options.margin_bottom
    else:
        start_y = top - content_height

    for line_index, line in enumerate(lines):
        line_width = line_widths[line_index]
        # 水平对齐
        if options.align == "center":
            x = left + (options.paper_width - options.margin_left - options.margin_right - line_width) / 2.0
        elif options.align == "right":
            x = options.paper_width - options.margin_right - line_width
        else:
            x = left

        # 该行基线位置：行块底部上方 font_size 处
        baseline = start_y - line_index * line_heights

        cursor = x
        for ch in line:
            glyph = repository.get(ch)
            if glyph is None:
                missing.append(ch)
                cursor += advance_of_units(ch, repository) * font_size
                continue

            strokes: List[Polyline] = []
            for stroke in glyph.strokes:
                moved = [(cursor + px * font_size, baseline + py * font_size) for px, py in stroke]
                strokes.append(moved)

            placed.append(
                PlacedGlyph(
                    char=ch,
                    strokes=strokes,
                    origin=(cursor, baseline),
                    advance=glyph.advance * font_size,
                    line=line_index,
                )
            )
            cursor += glyph.advance * font_size + options.letter_spacing * font_size

    return placed, missing


def advance_of_units(ch: str, repository: GlyphRepository) -> float:
    """字符的推进宽度，单位为 em。"""
    glyph = repository.get(ch)
    return glyph.advance if glyph else 0.5
