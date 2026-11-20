"""高层流水线：文本 → 字形 → 排版 → 轨迹 → G-code。

这是上层调用的推荐入口，把各阶段的参数收拢到一个对象里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .gcode import GCodeGenerator
from .glyphs import GlyphRepository
from .layout import LayoutOptions, TextLayout, layout_text
from .machine import MachineConfig
from .simulator import SimulationOptions, render_html, render_svg
from .trajectory import Trajectory, plan_trajectory

__all__ = ["WriteRequest", "WriteJob", "build_job"]


@dataclass
class WriteRequest:
    """一次书写任务的全部参数。"""

    text: str

    #: 机器配置
    machine: MachineConfig = field(default_factory=MachineConfig)

    #: 排版参数
    layout: LayoutOptions = field(default_factory=LayoutOptions)

    #: 笔画内部拐角的圆滑半径 (mm)，0 表示保留尖角
    corner_radius: float = 0.6

    #: 弧长重采样步长 (mm)
    sample_step: float = 0.5

    #: 是否对笔画做样条平滑
    smooth: bool = True

    #: 汉字数据缓存目录
    cache_dir: Optional[Path] = None

    #: 是否离线（只用本地缓存，不联网）
    offline: bool = False

    #: G-code 是否带行号
    line_numbers: bool = False


@dataclass
class WriteJob:
    """一次书写任务的产物。"""

    layout: TextLayout
    trajectory: Trajectory
    gcode: str
    missing: List[str] = field(default_factory=list)
    #: 生成时的机器配置（用于估算耗时）
    machine: Optional[MachineConfig] = None

    @property
    def stroke_count(self) -> int:
        return self.trajectory.stroke_count

    @property
    def estimated_seconds(self) -> float:
        """按当前进给速度估算的书写耗时（秒）。"""
        if self.machine is None:
            return 0.0
        return self.trajectory.estimated_seconds(
            self.machine.draw_feed, self.machine.travel_feed
        )


def build_job(request: WriteRequest) -> WriteJob:
    """执行完整流水线，返回排版、轨迹与 G-code。"""
    repository = GlyphRepository.default(
        cache_dir=request.cache_dir, offline=request.offline
    )

    # 排版参数中的纸张尺寸跟随机器配置，保持一致
    layout_options = request.layout
    layout_options.paper_width = request.machine.width
    layout_options.paper_height = request.machine.height

    layout = layout_text(request.text, repository, layout_options)
    missing = list(getattr(layout, "missing", []) or [])

    strokes: List[Tuple[str, int, list]] = []
    for glyph in layout.glyphs:
        for idx, stroke in enumerate(glyph.strokes, start=1):
            strokes.append((glyph.char, idx, stroke))

    trajectory = plan_trajectory(
        strokes,
        corner_radius=request.corner_radius,
        sample_step=request.sample_step,
        smooth=request.smooth,
    )

    generator = GCodeGenerator(request.machine, line_numbers=request.line_numbers)
    gcode = generator.generate(trajectory)

    return WriteJob(
        layout=layout,
        trajectory=trajectory,
        gcode=gcode,
        missing=missing,
        machine=request.machine,
    )


def render_preview(
    job: WriteJob,
    options: Optional[SimulationOptions] = None,
    as_html: bool = True,
) -> str:
    """渲染预览。``as_html=True`` 返回交互式 HTML，否则返回静态 SVG。"""
    opts = options or SimulationOptions()
    if as_html:
        return render_html(job.trajectory, job.machine, opts)
    return render_svg(job.trajectory, job.machine, opts)
