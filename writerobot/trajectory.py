"""轨迹规划：把排好版的笔画转换成机器动作序列。

规划阶段做四件事：

1. **笔画排序**：按书写顺序逐笔执行。汉字笔画顺序来自 ``medians`` 数组顺序，
   已经符合笔顺，无需额外处理。
2. **空走优化**：笔画之间是抬笔移动。用「就近合并」策略，把连续的空走
   压缩成一条直线，减少抬落笔次数。
3. **拐角圆滑**：在笔画内部对折角做圆滑过渡，避免笔尖在直角处划出
   顿挫痕迹——这也是绘图机（plotter）能写出漂亮字的关键。
4. **弧长重采样**：按固定步长重采样，让每段 G-code 的长度可控。

动作类型
--------
``DRAW``
    落笔绘制，笔尖接触纸面。
``TRAVEL``
    抬笔空走，不在纸上留下痕迹。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Point, Polyline, catmull_rom, polyline_length, resample, simplify

__all__ = ["Action", "ActionType", "Trajectory", "plan_trajectory"]


class ActionType:
    """动作类型常量。"""

    DRAW = "draw"
    TRAVEL = "travel"


@dataclass
class Action:
    """一个机器动作。"""

    type: str
    points: Polyline = field(default_factory=list)
    #: 该动作对应的字符（便于调试与可视化分组）
    char: str = ""
    #: 该动作是该字符的第几笔（从 1 开始）
    stroke_index: int = 0

    @property
    def length(self) -> float:
        return polyline_length(self.points)


@dataclass
class Trajectory:
    """完整的轨迹规划结果。"""

    actions: List[Action] = field(default_factory=list)
    #: 抬落笔次数
    pen_toggles: int = 0
    #: 绘制总长度 (mm)
    draw_length: float = 0.0
    #: 空走总长度 (mm)
    travel_length: float = 0.0

    @property
    def stroke_count(self) -> int:
        return sum(1 for a in self.actions if a.type == ActionType.DRAW)

    def estimated_seconds(self, draw_feed: float, travel_feed: float) -> float:
        """估算耗时（秒）。"""
        draw_feed = max(draw_feed, 1.0)
        travel_feed = max(travel_feed, 1.0)
        return self.draw_length / draw_feed * 60.0 + self.travel_length / travel_feed * 60.0


def _round_corners(
    points: Sequence[Point],
    radius: float,
    max_segments: int = 6,
) -> Polyline:
    """把折线拐角替换成圆弧过渡。

    对每个拐角，在其前后各取 ``radius`` 长度以内的点，用二次贝塞尔曲线
    近似圆角。这样笔尖转弯时是连续曲线，不会因为直角产生墨点或断线。
    """
    if len(points) < 3 or radius <= 0:
        return list(points)

    pts = list(points)
    out: Polyline = [pts[0]]

    for i in range(1, len(pts) - 1):
        prev_p, cur, next_p = pts[i - 1], pts[i], pts[i + 1]

        v1 = (prev_p[0] - cur[0], prev_p[1] - cur[1])
        v2 = (next_p[0] - cur[0], next_p[1] - cur[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(cur)
            continue

        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        cosang = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        angle = math.acos(cosang)

        # 转角太小时无需圆滑（几乎共线）；接近 180° 时跳过（尖角回折）
        if angle > math.radians(175) or angle < math.radians(8):
            out.append(cur)
            continue

        # 圆角切线长度：受夹角与相邻边长度限制
        tan_len = radius / math.tan(angle / 2.0)
        tan_len = min(tan_len, l1 * 0.45, l2 * 0.45)
        if tan_len < 1e-6:
            out.append(cur)
            continue

        p_start = (cur[0] + u1[0] * tan_len, cur[1] + u1[1] * tan_len)
        p_end = (cur[0] + u2[0] * tan_len, cur[1] + u2[1] * tan_len)

        out.append(p_start)
        # 二次贝塞尔：控制点为拐角本身
        steps = max(2, min(max_segments, int(tan_len / max(radius * 0.5, 1e-3)) + 2))
        for s in range(1, steps + 1):
            t = s / steps
            mt = 1.0 - t
            x = mt * mt * p_start[0] + 2 * mt * t * cur[0] + t * t * p_end[0]
            y = mt * mt * p_start[1] + 2 * mt * t * cur[1] + t * t * p_end[1]
            out.append((x, y))

    out.append(pts[-1])

    # 去掉相邻重复点
    cleaned: Polyline = [out[0]]
    for p in out[1:]:
        if math.dist(p, cleaned[-1]) > 1e-9:
            cleaned.append(p)
    return cleaned


def _wobble_stroke(
    points: Sequence[Point],
    amplitude: float,
    phase: float,
    wavelength: float = 22.0,
) -> Polyline:
    """给笔画加轻微的运笔起伏。

    手写时笔尖很少走绝对直线，总有细微的晃动。加入低幅度的
    垂直偏移后，笔画看起来是「写」出来的，而不是「画」出来的。
    """
    if len(points) < 2 or amplitude <= 0:
        return list(points)

    total = polyline_length(points)
    if total < 1e-6:
        return list(points)

    # 按弧长重新采样，保证起伏沿笔画的分布是均匀的
    sampled = resample(points, max(0.6, wavelength / 6))
    if len(sampled) < 2:
        return list(points)

    out: Polyline = []
    travelled = 0.0
    for i, pt in enumerate(sampled):
        if i > 0:
            travelled += math.dist(sampled[i - 1], pt)
        # 该点的法向量
        if i == 0:
            dx, dy = sampled[1][0] - pt[0], sampled[1][1] - pt[1]
        elif i == len(sampled) - 1:
            dx, dy = pt[0] - sampled[i - 1][0], pt[1] - sampled[i - 1][1]
        else:
            dx = sampled[i + 1][0] - sampled[i - 1][0]
            dy = sampled[i + 1][1] - sampled[i - 1][1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            out.append(pt)
            continue
        nx, ny = -dy / length, dx / length
        offset = amplitude * math.sin(travelled / wavelength * 2 * math.pi + phase)
        out.append((pt[0] + nx * offset, pt[1] + ny * offset))
    return out


def plan_trajectory(
    strokes: Sequence[Tuple[str, int, Polyline]],
    *,
    geometry: Optional[Sequence[Polyline]] = None,
    corner_radius: float = 1.1,
    sample_step: float = 0.5,
    smooth: bool = True,
    simplify_tolerance: float = 0.01,
    min_travel: float = 0.05,
    stroke_wobble: float = 0.0,
    wobble_seed: int = 4242,
) -> Trajectory:
    """规划轨迹。

    参数
    ----
    strokes:
        序列，每项为 ``(字符, 笔画序号, 点序列)``，点序列为纸张坐标 (mm)。
    geometry:
        额外的几何线条（表格框线、图形轮廓等）。它们**不**做平滑、圆角和
        重采样 —— 直线经过 Catmull-Rom 会在拐角处剧烈过冲，把矩形甩成
        圆角团块。这些线条会先于文字绘制，笔尖不用抬起就能画完整个框。
    corner_radius:
        笔画内部拐角的圆滑半径 (mm)。设为 0 则保留尖角。
    sample_step:
        弧长重采样步长 (mm)。
    smooth:
        是否对笔画做样条平滑（把稀疏控制点变成光滑曲线）。
    min_travel:
        小于该长度的空走会被并入绘制动作，避免无意义的抬落笔抖动。
    stroke_wobble:
        笔画运笔起伏幅度 (mm)。0 表示笔画走绝对直线。手写笔记的笔画
        很少是直的，加一点起伏会明显更像手写。
    """
    trajectory = Trajectory()
    current: Optional[Point] = None

    # 几何线条先画（原始直线，不做任何平滑处理）
    for line in geometry or []:
        if len(line) < 2:
            continue
        start = line[0]
        if current is not None and math.dist(current, start) > min_travel:
            travel_start = current
            trajectory.actions.append(
                Action(
                    type=ActionType.TRAVEL,
                    points=[travel_start, start],
                    char="",
                    stroke_index=0,
                )
            )
            trajectory.travel_length += math.dist(travel_start, start)
            trajectory.pen_toggles += 1
        trajectory.actions.append(
            Action(type=ActionType.DRAW, points=list(line), char="", stroke_index=0)
        )
        trajectory.draw_length += polyline_length(line)
        current = line[-1]

    for char, stroke_index, raw_points in strokes:
        if len(raw_points) < 2:
            if len(raw_points) == 1:
                raw_points = [
                    raw_points[0],
                    (raw_points[0][0] + 0.01, raw_points[0][1] + 0.01),
                ]
            else:
                continue

        pts: Polyline = list(raw_points)

        if smooth:
            # 样条平滑：让稀疏的笔画控制点变成连续曲线
            pts = catmull_rom(pts, samples_per_segment=12, alpha=0.4)

        if corner_radius > 0:
            pts = _round_corners(pts, corner_radius)

        if simplify_tolerance > 0:
            pts = simplify(pts, simplify_tolerance)

        if sample_step > 0:
            pts = resample(pts, sample_step)

        if stroke_wobble > 0:
            rng = random.Random(f"{wobble_seed}:{char}:{stroke_index}:{len(trajectory.actions)}")
            pts = _wobble_stroke(pts, stroke_wobble, rng.uniform(0, 2 * math.pi))

        if len(pts) < 2:
            continue

        start = pts[0]
        if current is not None and math.dist(current, start) <= min_travel:
            # 起点与上一笔终点几乎重合：不必抬笔，直接续画（省一次抬落笔）
            pts = [current] + pts[1:]
        else:
            # 空走到本笔起点
            travel_start = current if current is not None else start
            trajectory.actions.append(
                Action(
                    type=ActionType.TRAVEL,
                    points=[travel_start, start] if travel_start != start else [start],
                    char=char,
                    stroke_index=stroke_index,
                )
            )
            trajectory.travel_length += polyline_length([travel_start, start])
            trajectory.pen_toggles += 1

        trajectory.actions.append(
            Action(type=ActionType.DRAW, points=pts, char=char, stroke_index=stroke_index)
        )
        trajectory.draw_length += polyline_length(pts)
        current = pts[-1]

    return trajectory
