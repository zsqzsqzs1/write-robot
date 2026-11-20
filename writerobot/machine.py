"""机器配置：坐标系、运动学、抬落笔方式与进给参数。

支持两类常见写字机器人结构：

``cartesian``
    传统直角坐标（龙门式 / XY 平台），X 轴与 Y 轴各自独立。

``corexy``
    CoreXY 结构（稚晖君 X-Bot、Laser_Writing_Machine、drawbot 等均属此类）。
    CoreXY 有两种实现方式，本项目都支持：

    * **固件解算**（默认，推荐）：GRBL 打开 ``COREXY`` 宏后，上位机照常发送
      笛卡尔坐标 X/Y，由固件换算成 A/B 电机步数。
    * **上位机解算**：固件不认 CoreXY 时（例如某些精简版 GRBL），由本项目
      把笛卡尔坐标换算成两条皮带轴的长度后发出。见 :meth:`MachineConfig.to_machine`。

抬落笔有两种常见做法：

``z``
    用 Z 轴升降（线性舵机、电磁阀、气缸），发 ``G0 Z<x>``。

``servo``
    用 RC 舵机翻转笔架，GRBL 舵机模式下用 ``M3 S<value>`` / ``M5`` 控制。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["MachineConfig", "Kinematics", "PenConfig", "load_machine_config"]


class Kinematics:
    """运动学类型常量。"""

    CARTESIAN = "cartesian"
    COREXY = "corexy"


@dataclass
class PenConfig:
    """抬落笔参数。"""

    #: 抬落笔方式：``z``（Z 轴升降）或 ``servo``（RC 舵机）
    mode: str = "z"

    #: Z 轴模式下，抬笔 / 落笔对应的 Z 坐标（mm）
    z_up: float = 10.0
    z_down: float = 0.0

    #: 舵机模式下，落笔 / 抬笔的 S 值。
    #: grbl-servo 固件把 S 值按比例映射到舵机脉宽（S=0 最小、S=1000 最大，
    #: 具体角度由 ``SPINDLE_PWM_MIN_VALUE`` / ``SPINDLE_PWM_MAX_VALUE`` 决定）。
    #: 两个值哪个是"落笔"取决于舵机安装方向，装反了就互换。
    servo_down: int = 0
    servo_up: int = 1000

    #: 抬落笔动作的 Z 轴进给速度 (mm/min)
    z_feed: float = 1200.0

    def validate(self) -> None:
        if self.mode not in ("z", "servo"):
            raise ValueError(f"未知的抬落笔方式: {self.mode}（可选 z / servo）")
        if self.mode == "z" and self.z_up <= self.z_down:
            raise ValueError("Z 轴模式下 z_up 必须大于 z_down（抬笔应高于落笔位置）")
        if self.mode == "servo":
            if not (0 <= self.servo_up <= 1000) or not (0 <= self.servo_down <= 1000):
                raise ValueError("舵机模式 S 值必须在 0..1000 之间")
            if self.servo_up == self.servo_down:
                raise ValueError("舵机模式下 servo_up 与 servo_down 不能相同")


@dataclass
class MachineConfig:
    """机器的完整配置。"""

    name: str = "default"
    kinematics: str = Kinematics.CARTESIAN

    #: 可书写区域尺寸 (mm)
    width: float = 200.0
    height: float = 200.0

    #: 书写时原点所在角：``front-left`` / ``front-right`` / ``back-left`` / ``back-right``
    origin: str = "front-left"

    #: 绘制速度 (mm/min)
    draw_feed: float = 3000.0
    #: 空走（抬笔移动）速度 (mm/min)
    travel_feed: float = 9000.0

    #: 每 mm 采样点数上限（弧长重采样步长，mm）
    sample_step: float = 0.5

    #: 是否在 G-code 中输出注释与行程统计
    verbose: bool = True

    pen: PenConfig = field(default_factory=PenConfig)

    #: CoreXY 上位机解算时，A/B 皮带轴的传动比（步/mm）。
    #: 留空表示沿用 X/Y 的步距，由固件自行处理。
    corexy_steps_per_mm: Optional[float] = None

    #: CoreXY 是否由上位机解算运动学。
    #:
    #: * ``False``（默认）：固件已启用 GRBL 的 ``COREXY`` 宏，上位机照常发笛卡尔坐标。
    #: * ``True``：固件不认 CoreXY（例如原版 GRBL），由本项目换算成 A/B 皮带轴坐标。
    corexy_host_solve: bool = False

    def validate(self) -> None:
        if self.kinematics not in (Kinematics.CARTESIAN, Kinematics.COREXY):
            raise ValueError(f"未知的运动学类型: {self.kinematics}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("书写区域尺寸必须为正")
        if self.origin not in (
            "front-left",
            "front-right",
            "back-left",
            "back-right",
        ):
            raise ValueError(f"未知的原点位置: {self.origin}")
        if self.draw_feed <= 0 or self.travel_feed <= 0:
            raise ValueError("进给速度必须为正")
        self.pen.validate()

    # ------------------------------------------------------------ 坐标变换

    def to_machine(self, x: float, y: float) -> tuple[float, float]:
        """把纸张坐标（左下原点）转换为机器坐标。

        纸张坐标：原点在纸的左下角，X 向右、Y 向上。
        机器坐标：以机器的 ``origin`` 设定为基准。

        对于 CoreXY 且 ``corexy_host_solve=True`` 的情况，返回 A/B 两条皮带轴的长度。
        """
        w, h = self.width, self.height
        if self.origin in ("front-right", "back-right"):
            x = w - x
        if self.origin in ("back-left", "back-right"):
            y = h - y

        if self.kinematics == Kinematics.COREXY and self.corexy_host_solve:
            # CoreXY 几何：ΔLA = ΔX + ΔY, ΔLB = ΔX - ΔY
            # （与 GRBL planner.c 中 COREXY 分支的公式一致）
            return (x + y, x - y)
        return (x, y)

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MachineConfig":
        pen_data = data.get("pen") or {}
        pen = PenConfig(**pen_data) if pen_data else PenConfig()
        kwargs = {
            k: v
            for k, v in data.items()
            if k in cls.__dataclass_fields__ and k != "pen"
        }
        return cls(pen=pen, **kwargs)

    @classmethod
    def load(cls, path: Path) -> "MachineConfig":
        """从 JSON 文件读取配置。"""
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        with Path(path).open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)


def load_machine_config(path: Optional[Path]) -> MachineConfig:
    """读取机器配置；未指定路径时返回默认配置。"""
    if path is None:
        return MachineConfig()
    return MachineConfig.load(path)
