"""G-code 生成：把轨迹规划结果输出为 GRBL 可执行的 G-code。

G-code 约定
-----------
* ``G21`` —— 单位为毫米
* ``G90`` —— 绝对坐标
* ``G94`` —— 每分钟进给（GRBL 默认）
* ``G0`` —— 快速定位（空走）
* ``G1 X.. Y.. F..`` —— 直线插补（绘制）
* ``G0 Z<x>`` —— Z 轴抬落笔（``pen.mode = "z"`` 时）
* ``M3 S<v>`` / ``M5`` —— 舵机抬落笔（``pen.mode = "servo"`` 时）

注意：GRBL 的 ``G0`` 快速定位速度由 ``$110`` / ``$111`` 决定，
通常远高于 ``G1`` 的 F 值——正好适合空走。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, TextIO

from .machine import MachineConfig
from .trajectory import Action, ActionType, Trajectory

__all__ = ["GCodeGenerator", "generate_gcode"]


def _fmt(value: float, digits: int = 3) -> str:
    """格式化坐标数值，去掉多余的零。"""
    text = f"{value:.{digits}f}"
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        return "0"
    return text


@dataclass
class GCodeGenerator:
    """G-code 生成器。"""

    machine: MachineConfig

    #: 生成的每一行前面加行号（``N1 ...``），便于 GRBL 报错定位
    line_numbers: bool = False

    #: 起始位置（机器坐标 mm）。None 表示不做起始定位
    start_position: Optional[tuple[float, float]] = None

    #: 结尾是否抬笔并回到起点
    return_to_start: bool = True

    #: 是否在结尾输出 ``M2`` 结束程序
    emit_program_end: bool = True

    #: 是否输出注释头
    emit_header: bool = True

    def generate(self, trajectory: Trajectory) -> str:
        """生成完整的 G-code 文本。"""
        lines: List[str] = []
        if self.emit_header:
            lines.extend(self._header(trajectory))
        lines.extend(self._body(trajectory))
        if self.emit_header:
            lines.append(f"; 总绘制长度 {trajectory.draw_length:.1f} mm")
            lines.append(f"; 总空走长度 {trajectory.travel_length:.1f} mm")
            lines.append(f"; 笔画数 {trajectory.stroke_count}")
        if self.emit_program_end:
            lines.append("M2")
        return self._number(lines)

    # ------------------------------------------------------------ 内部方法

    def _header(self, trajectory: Trajectory) -> List[str]:
        m = self.machine
        lines = [
            "; write-robot 生成的写字机器人 G-code",
            f"; 机器: {m.name}  运动学: {m.kinematics}",
            f"; 抬落笔: {m.pen.mode}  绘制 F{m.draw_feed:g}  空走 F{m.travel_feed:g}",
            "G21 ; 毫米",
            "G90 ; 绝对坐标",
            "G94 ; 每分钟进给",
            "G17 ; XY 平面",
        ]
        return lines

    def _pen_up(self) -> List[str]:
        m = self.machine
        if m.pen.mode == "z":
            return [f"G0 Z{_fmt(m.pen.z_up)}"]
        # 舵机模式：S 值越大脉宽越宽。抬笔用 servo_up。
        return [f"M3 S{m.pen.servo_up}"]

    def _pen_down(self) -> List[str]:
        m = self.machine
        if m.pen.mode == "z":
            return [f"G0 Z{_fmt(m.pen.z_down)}"]
        return [f"M3 S{m.pen.servo_down}"]

    def _body(self, trajectory: Trajectory) -> List[str]:
        m = self.machine
        lines: List[str] = []

        # 抬笔到安全高度，再定位到起点
        lines.extend(self._pen_up())

        if self.start_position is not None:
            sx, sy = self.start_position
            lines.append(f"G0 X{_fmt(sx)} Y{_fmt(sy)} F{_fmt(m.travel_feed)}")

        pen_is_down = False

        for action in trajectory.actions:
            if action.type == ActionType.TRAVEL:
                if pen_is_down:
                    lines.extend(self._pen_up())
                    pen_is_down = False
                if not action.points:
                    continue
                x, y = m.to_machine(*action.points[0])
                lines.append(f"G0 X{_fmt(x)} Y{_fmt(y)} F{_fmt(m.travel_feed)}")
                continue

            # DRAW
            if not action.points:
                continue
            if not pen_is_down:
                lines.extend(self._pen_down())
                pen_is_down = True

            for pt in action.points:
                x, y = m.to_machine(*pt)
                lines.append(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(m.draw_feed)}")

        if pen_is_down:
            lines.extend(self._pen_up())

        if self.return_to_start and self.start_position is not None:
            sx, sy = self.start_position
            lines.append(f"G0 X{_fmt(sx)} Y{_fmt(sy)} F{_fmt(m.travel_feed)}")

        return lines

    def _number(self, lines: Sequence[str]) -> str:
        if not self.line_numbers:
            return "\n".join(lines) + "\n"
        out: List[str] = []
        for i, line in enumerate(lines, start=1):
            out.append(f"N{i} {line}")
        return "\n".join(out) + "\n"


def generate_gcode(
    trajectory: Trajectory,
    machine: MachineConfig,
    **kwargs,
) -> str:
    """便捷函数：生成 G-code。"""
    return GCodeGenerator(machine, **kwargs).generate(trajectory)
