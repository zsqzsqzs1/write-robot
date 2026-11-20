"""几何与轨迹基础工具。

约定
----
* 所有二维点统一使用 ``(x, y)`` 元组表示。
* 长度单位统一为 **毫米 (mm)**。
* 机器坐标系：原点位于纸张左下角，X 向右、Y 向上（右手系）。
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Polyline = List[Point]

__all__ = [
    "Point",
    "Polyline",
    "lerp",
    "polyline_length",
    "catmull_rom",
    "resample",
    "simplify",
    "transform",
    "bbox",
    "bbox_union",
    "strokes_bbox",
    "rotate",
    "fit_to_box",
]


def lerp(a: Point, b: Point, t: float) -> Point:
    """线性插值。"""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def polyline_length(points: Sequence[Point]) -> float:
    """折线总长度。"""
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def catmull_rom(
    points: Sequence[Point],
    samples_per_segment: int = 12,
    alpha: float = 0.5,
    closed: bool = False,
) -> Polyline:
    """Catmull-Rom 样条插值，把稀疏控制点变成光滑曲线。

    使用 **向心参数化**（centripetal, ``alpha = 0.5``）：节点间距按
    ``|Δp|^alpha`` 分配而非等分。

    这一点对汉字笔画至关重要：hanzi-writer-data 的中线点分布极不均匀，
    同一笔里既有 24px 的短段也有 400px+ 的长段（长横就是两三个点的事）。
    等分参数化会在长短段交界处算出巨大的切向量，导致曲线鼓包、笔画发���。
    向心参数化能有效抑制这种过冲与尖点。

    参考：Yuksel et al., *On the Parameterization of Catmull-Rom Curves* (2011)。

    ``alpha = 0`` 退化为等分（uniform）参数化，``alpha = 1`` 为弦长参数化。
    """
    if len(points) < 2:
        return list(points)
    if samples_per_segment < 1:
        raise ValueError("samples_per_segment 必须 >= 1")

    pts: List[Point] = list(points)
    if closed:
        pts = [pts[-1]] + pts + [pts[0], pts[1]]
    else:
        # 两端各复制一个控制点，使首尾段的导数也有定义
        pts = [pts[0]] + pts + [pts[-1]]

    seg_count = len(pts) - 3
    if seg_count <= 0:
        return list(points)

    # 每段的采样数按弦长比例分配，避免长段被采样得过于稀疏
    lengths = [
        math.dist(pts[i + 1], pts[i + 2]) for i in range(seg_count)
    ]
    mean_len = (sum(lengths) / seg_count) or 1.0

    out: Polyline = []
    for i in range(seg_count):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        steps = max(
            2,
            min(
                64,
                int(round(samples_per_segment * (lengths[i] / mean_len))) + 2,
            ),
        )
        last = i == seg_count - 1
        n = steps if last else steps - 1
        for s in range(n + 1):
            t = s / steps
            out.append(_catmull_rom_point(p0, p1, p2, p3, t, alpha))
    return out


def _catmull_rom_point(
    p0: Point, p1: Point, p2: Point, p3: Point, t: float, alpha: float
) -> Point:
    """向心 Catmull-Rom 在 ``t ∈ [0,1]`` 处的取值（Barry-Goldman 逐层插值）。"""
    # 节点参数：间距取弦长的 alpha 次幂
    d1 = math.dist(p0, p1) ** alpha
    d2 = math.dist(p1, p2) ** alpha
    d3 = math.dist(p2, p3) ** alpha

    t0 = 0.0
    t1 = t0 + d1
    t2 = t1 + d2
    t3 = t2 + d3

    if t2 - t1 < 1e-12:
        return p1

    t = t1 + (t2 - t1) * t

    def lerp_pts(a: Point, b: Point, ta: float, tb: float, tt: float) -> Point:
        if tb - ta < 1e-12:
            return a
        w = (tt - ta) / (tb - ta)
        return (a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w)

    a1 = lerp_pts(p0, p1, t0, t1, t)
    a2 = lerp_pts(p1, p2, t1, t2, t)
    a3 = lerp_pts(p2, p3, t2, t3, t)
    b1 = lerp_pts(a1, a2, t0, t2, t)
    b2 = lerp_pts(a2, a3, t1, t3, t)
    return lerp_pts(b1, b2, t1, t2, t)


def resample(points: Sequence[Point], max_segment_len: float = 0.5) -> Polyline:
    if len(points) < 2:
        return list(points)
    if max_segment_len <= 0:
        return list(points)

    out: Polyline = [tuple(points[0])]  # type: ignore[list-item]
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg_len = math.dist(a, b)
        if seg_len <= 1e-12:
            continue
        n = max(1, int(math.ceil(seg_len / max_segment_len)))
        for k in range(1, n + 1):
            out.append(lerp(a, b, k / n))
    return out


def simplify(points: Sequence[Point], tolerance: float = 0.02) -> Polyline:
    """Douglas-Peucker 折线抽稀，删除对形状无贡献的点。"""
    if len(points) < 3 or tolerance <= 0:
        return list(points)

    def _perp_dist(p: Point, a: Point, b: Point) -> float:
        if a == b:
            return math.dist(p, a)
        dx, dy = b[0] - a[0], b[1] - a[1]
        num = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0])
        return num / math.hypot(dx, dy)

    first, last = 0, len(points) - 1
    max_dist, index = 0.0, first
    for i in range(first + 1, last):
        d = _perp_dist(points[i], points[first], points[last])
        if d > max_dist:
            max_dist, index = d, i
    if max_dist > tolerance:
        left = simplify(points[first : index + 1], tolerance)
        right = simplify(points[index:last + 1], tolerance)
        return left[:-1] + right
    return [points[first], points[last]]


def transform(
    points: Sequence[Point],
    scale: float = 1.0,
    offset: Tuple[float, float] = (0.0, 0.0),
    mirror_x: bool = False,
    mirror_y: bool = False,
) -> Polyline:
    """统一缩放 + 平移 + 镜像。"""
    sx = -1.0 if mirror_x else 1.0
    sy = -1.0 if mirror_y else 1.0
    out: Polyline = []
    for x, y in points:
        out.append((x * scale * sx + offset[0], y * scale * sy + offset[1]))
    return out


def rotate(
    points: Sequence[Point],
    angle_deg: float,
    origin: Point = (0.0, 0.0),
) -> Polyline:
    """绕 ``origin`` 旋转指定角度（逆时针为正）。"""
    if not points:
        return []
    rad = math.radians(angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    ox, oy = origin
    out: Polyline = []
    for x, y in points:
        dx, dy = x - ox, y - oy
        out.append((ox + dx * ca - dy * sa, oy + dx * sa + dy * ca))
    return out


def bbox(points: Iterable[Point]) -> Tuple[float, float, float, float]:
    """返回 ``(min_x, min_y, max_x, max_y)``。空序列返回全零。"""
    xs, ys = [], []
    for p in points:
        try:
            if len(p) != 2:
                raise ValueError
            x, y = p[0], p[1]
        except (TypeError, IndexError, ValueError) as exc:
            raise ValueError(f"非法坐标点: {p!r}（应为 (x, y) 二元组）") from exc
        xs.append(x)
        ys.append(y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_union(boxes: Iterable[Tuple[float, float, float, float]]) -> Tuple[float, float, float, float]:
    """合并多个包围盒。"""
    boxes = list(boxes)
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    min_x = min(b[0] for b in boxes)
    min_y = min(b[1] for b in boxes)
    max_x = max(b[2] for b in boxes)
    max_y = max(b[3] for b in boxes)
    return (min_x, min_y, max_x, max_y)


def strokes_bbox(strokes: Iterable[Sequence[Point]]) -> Tuple[float, float, float, float]:
    """求一组笔画（每笔是点序列）的整体包围盒。"""
    pts: List[Point] = []
    for stroke in strokes:
        pts.extend(stroke)
    return bbox(pts)


def fit_to_box(
    points: Sequence[Point],
    target_w: float,
    target_h: float,
    source_box: Tuple[float, float, float, float] | None = None,
    preserve_aspect: bool = True,
) -> Tuple[float, Tuple[float, float]]:
    """计算把 ``points`` 放进 ``target_w x target_h`` 所需的缩放与偏移。

    返回 ``(scale, offset)``，应用方式为 ``p * scale + offset``。
    """
    if not points:
        return (1.0, (0.0, 0.0))
    min_x, min_y, max_x, max_y = source_box or bbox(points)
    w = max_x - min_x
    h = max_y - min_y
    if w <= 0 or h <= 0:
        return (1.0, (0.0, 0.0))

    if preserve_aspect:
        scale = min(target_w / w, target_h / h)
    else:
        scale = 1.0
    # 居中
    offset = (
        (target_w - w * scale) / 2.0 - min_x * scale,
        (target_h - h * scale) / 2.0 - min_y * scale,
    )
    return (scale, offset)
