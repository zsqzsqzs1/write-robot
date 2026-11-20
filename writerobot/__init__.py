"""write-robot —— 写字机器人上位机。

把文字变成会动的笔，再变成机器能执行的 G-code。

快速开始
--------
::

    from writerobot import MachineConfig, WriteRequest, build_job, render_preview

    machine = MachineConfig(kinematics="corexy", width=200, height=200)
    job = build_job(WriteRequest(text="你好世界", machine=machine))

    open("preview.html", "w", encoding="utf-8").write(render_preview(job))
    open("output.nc", "w", encoding="utf-8").write(job.gcode)
"""

from __future__ import annotations

__version__ = "1.0.0"

from .geometry import Point, Polyline
from .glyphs import Glyph, GlyphRepository, HanziProvider, LatinProvider
from .gcode import GCodeGenerator, generate_gcode
from .layout import LayoutOptions, TextLayout, layout_text
from .machine import Kinematics, MachineConfig, PenConfig
from .pipeline import WriteJob, WriteRequest, build_job, render_preview
from .simulator import SimulationOptions, render_html, render_svg
from .trajectory import Action, ActionType, Trajectory, plan_trajectory

__all__ = [
    "__version__",
    "Point",
    "Polyline",
    "Glyph",
    "GlyphRepository",
    "HanziProvider",
    "LatinProvider",
    "GCodeGenerator",
    "generate_gcode",
    "LayoutOptions",
    "TextLayout",
    "layout_text",
    "Kinematics",
    "MachineConfig",
    "PenConfig",
    "WriteJob",
    "WriteRequest",
    "build_job",
    "render_preview",
    "SimulationOptions",
    "render_html",
    "render_svg",
    "Action",
    "ActionType",
    "Trajectory",
    "plan_trajectory",
]
