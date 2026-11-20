"""模拟器：把轨迹渲染成 SVG / 交互式 HTML。

用途
----
* **无硬件验证**：写 G-code 前先在浏览器里看效果，避免"纸上写出来才发现不对"。
* **教学与调试**：动画按真实速度回放，能直观看出哪里抬笔多余、哪里会打结。
* **笔顺检查**：SVG 中每条笔画独立成段，可单独高亮，检查汉字笔顺是否正确。

HTML 版本是自包含的单文件（无外部依赖），可直接用浏览器打开。
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .machine import MachineConfig
from .trajectory import Action, ActionType, Trajectory

__all__ = ["render_svg", "render_html", "SimulationOptions"]


@dataclass
class SimulationOptions:
    """模拟器渲染参数。"""

    #: 画布四周留白 (mm)
    margin: float = 8.0

    #: 是否显示空走轨迹（抬笔移动）
    show_travel: bool = True

    #: 是否标注每个字符的笔顺编号
    show_stroke_numbers: bool = True

    #: 线条宽度 (mm)
    stroke_width: float = 0.8

    #: 模拟笔压的线宽波动比例（±）。0 表示所有笔画等宽。
    #: 真实钢笔每笔轻重不同，等宽线条反而显得机械。
    pressure_variation: float = 0.14

    #: 笔压随机种子（保证可复现）
    pressure_seed: int = 7788

    #: 背景色
    background: str = "#ffffff"

    #: 墨迹颜色
    ink_color: str = "#1a1a1a"

    #: 空走轨迹颜色
    travel_color: str = "#c9d6e8"

    #: 画布像素宽度
    canvas_width: int = 800

    #: 动画是否按真实速度回放（否则按固定速度演示）
    real_time: bool = True

    #: 演示速度倍率（real_time=False 时生效）
    demo_speed: float = 4.0

    #: 打开页面时是否直接显示最终结果（而不是空白画布）。
    #: 播放按钮会从起点重新回放书写过程。
    show_final: bool = True

    #: 表格内容（行 x 列的文本）。提供时预览页会多出一个「表格内容」标签页，
    #: 直接用 HTML 表格展示文本本身，与「书写轨迹」视图对照。
    table_data: Optional[List[List[str]]] = None

    #: 表格标题（可选）
    table_title: str = "表格内容"


def _path_data(points: Sequence[Tuple[float, float]], to_px) -> str:
    """把点序列转成 SVG path 的 ``d`` 属性。"""
    if not points:
        return ""
    parts = [f"M {to_px(points[0])[0]:.2f} {to_px(points[0])[1]:.2f}"]
    for p in points[1:]:
        px, py = to_px(p)
        parts.append(f"L {px:.2f} {py:.2f}")
    return " ".join(parts)


def _build_projection(machine: MachineConfig, opts: SimulationOptions):
    """计算毫米坐标到画布像素的映射（含 Y 轴翻转）。"""
    w = machine.width + opts.margin * 2
    h = machine.height + opts.margin * 2
    scale = opts.canvas_width / w
    canvas_h = h * scale

    def to_px(pt: Tuple[float, float]) -> Tuple[float, float]:
        # 机器坐标左下原点 -> 画布左上原点，Y 轴翻转
        return (pt[0] * scale + opts.margin * scale, canvas_h - (pt[1] * scale + opts.margin * scale))

    return to_px, scale, canvas_h


def render_svg(
    trajectory: Trajectory,
    machine: MachineConfig,
    options: Optional[SimulationOptions] = None,
) -> str:
    """生成静态 SVG。"""
    opts = options or SimulationOptions()
    to_px, scale, canvas_h = _build_projection(machine, opts)

    draw_paths: List[Tuple[str, float]] = []
    travel_paths: List[str] = []
    labels: List[str] = []

    for index, action in enumerate(trajectory.actions):
        pts = [machine.to_machine(*p) for p in action.points]
        d = _path_data(pts, to_px)
        if not d:
            continue
        if action.type == ActionType.TRAVEL:
            travel_paths.append(d)
        else:
            # 模拟笔压：每笔线宽略有不同
            width = opts.stroke_width
            if opts.pressure_variation > 0:
                rng = random.Random(f"{opts.pressure_seed}:{index}")
                width *= 1.0 + rng.uniform(-opts.pressure_variation, opts.pressure_variation)
            draw_paths.append((d, width))
            if opts.show_stroke_numbers and action.char:
                mx, my = to_px(pts[0])
                labels.append(
                    f'<text x="{mx:.2f}" y="{my:.2f}" font-size="9" fill="#e07a2f" '
                    f'text-anchor="middle" font-family="sans-serif">{action.stroke_index}</text>'
                )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {opts.canvas_width} {canvas_h:.2f}" '
        f'width="{opts.canvas_width}" height="{canvas_h:.2f}">',
        f'<rect width="100%" height="100%" fill="{opts.background}"/>',
        f'<rect x="{opts.margin * scale:.2f}" y="{opts.margin * scale:.2f}" '
        f'width="{machine.width * scale:.2f}" height="{machine.height * scale:.2f}" '
        f'fill="none" stroke="#d0d0d0" stroke-width="1" stroke-dasharray="4 3"/>',
    ]

    if opts.show_travel and travel_paths:
        for d in travel_paths:
            parts.append(
                f'<path d="{d}" fill="none" stroke="{opts.travel_color}" '
                f'stroke-width="{max(opts.stroke_width * 0.5, 0.4):.2f}" '
                'stroke-dasharray="3 3" stroke-linecap="round"/>'
            )

    for d, width in draw_paths:
        parts.append(
            f'<path d="{d}" fill="none" stroke="{opts.ink_color}" '
            f'stroke-width="{width:.2f}" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    parts.extend(labels)
    parts.append("</svg>")
    return "\n".join(parts)


def render_html(
    trajectory: Trajectory,
    machine: MachineConfig,
    options: Optional[SimulationOptions] = None,
    title: str = "写字机器人轨迹预览",
) -> str:
    """生成自包含的交互式 HTML 动画。"""
    opts = options or SimulationOptions()
    to_px, scale, canvas_h = _build_projection(machine, opts)

    draw_feed = max(machine.draw_feed, 1.0)
    travel_feed = max(machine.travel_feed, 1.0)

    segments: List[dict] = []
    for index, action in enumerate(trajectory.actions):
        pts = [machine.to_machine(*p) for p in action.points]
        d = _path_data(pts, to_px)
        if not d:
            continue
        length_mm = action.length
        if action.type == ActionType.DRAW:
            duration = length_mm / draw_feed * 60.0
        else:
            duration = length_mm / travel_feed * 60.0

        # 模拟笔压：真实钢笔每一笔的轻重不同，线宽并非完全均匀。
        # 这是纯视觉效果，G-code 里不含宽度信息（画笔粗细由笔尖决定）。
        width = opts.stroke_width
        if opts.pressure_variation > 0 and action.type == ActionType.DRAW:
            rng = random.Random(f"{opts.pressure_seed}:{index}")
            width *= 1.0 + rng.uniform(-opts.pressure_variation, opts.pressure_variation)

        segments.append(
            {
                "type": action.type,
                "d": d,
                "dur": round(duration, 4),
                "char": action.char,
                "stroke": action.stroke_index,
                "w": round(width, 4),
            }
        )

    total_duration = sum(s["dur"] for s in segments)
    play_duration = total_duration if opts.real_time else total_duration / max(opts.demo_speed, 0.1)

    meta = {
        "segments": segments,
        "duration": play_duration,
        "realTime": opts.real_time,
        "showFinal": opts.show_final,
        "canvasWidth": opts.canvas_width,
        "canvasHeight": round(canvas_h, 2),
        "margin": opts.margin * scale,
        "paperWidth": machine.width * scale,
        "paperHeight": machine.height * scale,
        "strokeWidth": opts.stroke_width,
        "inkColor": opts.ink_color,
        "travelColor": opts.travel_color,
        "showTravel": opts.show_travel,
        "tableData": opts.table_data,
        "tableTitle": opts.table_title,
    }

    payload = json.dumps(meta, ensure_ascii=False)

    return _HTML_TEMPLATE.replace("__TITLE__", title).replace(
        "__DATA__", payload.replace("</", "<\\/")
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #f6f7f9;
    --panel: #ffffff;
    --border: #e3e6ec;
    --text: #1f2430;
    --muted: #6b7280;
    --accent: #2563eb;
    --accent-soft: #dbeafe;
    --ink-color: #1a1a1a;
    --travel-color: #c9d6e8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; font-weight: 650; letter-spacing: .2px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px; margin-bottom: 16px;
  }
  .stage {
    background: #fff; border: 1px solid var(--border); border-radius: 10px;
    overflow: hidden; display: flex; justify-content: center;
  }
  .controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  button {
    font: inherit; font-size: 14px; padding: 8px 18px; border-radius: 8px;
    border: 1px solid var(--accent); background: var(--accent); color: #fff;
    cursor: pointer; transition: background .15s, border-color .15s;
  }
  button:hover { background: #1d4ed8; }
  button.ghost { background: #fff; color: var(--accent); }
  button.ghost:hover { background: var(--accent-soft); }
  input[type=range] { accent-color: var(--accent); width: 180px; }
  .stat { font-size: 13px; color: var(--muted); display: flex; gap: 18px; flex-wrap: wrap; }
  .stat b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
  .legend { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-top: 10px; }
  .swatch { display: inline-block; width: 18px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }
  .badge {
    display: inline-block; font-size: 12px; padding: 2px 9px; border-radius: 999px;
    background: var(--accent-soft); color: #1e40af; margin-left: 8px; vertical-align: 2px;
  }
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab {
    font: inherit; font-size: 14px; padding: 8px 16px; border-radius: 8px;
    border: 1px solid var(--border); background: #fff; color: var(--muted);
    cursor: pointer; transition: all .15s;
  }
  .tab:hover { border-color: var(--accent); color: var(--accent); }
  .tab.active {
    background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600;
  }
  .view { display: none; }
  .view.active { display: block; }
  .table-wrap {
    overflow-x: auto; display: flex; justify-content: center; padding: 8px 0;
  }
  table.content {
    border-collapse: collapse; font-size: 17px; line-height: 1.6;
    color: var(--text); min-width: 320px;
  }
  table.content th, table.content td {
    border: 1px solid #c9cfda; padding: 14px 26px; text-align: center;
    vertical-align: middle; white-space: nowrap;
  }
  table.content td { background: #fff; }
  table.content tr:nth-child(even) td { background: #fafbfd; }
  .hint {
    font-size: 12.5px; color: var(--muted); margin-top: 14px; text-align: center;
    line-height: 1.7;
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="sub" id="subtitle"></div>

  <div class="card">
    <div id="tabs" class="tabs" style="display:none">
      <button class="tab" data-view="table">表格内容</button>
      <button class="tab" data-view="trace">书写轨迹</button>
    </div>

    <div id="view-table" class="view">
      <div class="table-wrap"><table class="content" id="contentTable"></table></div>
      <div class="hint">这是内容的表格形式；切到「书写轨迹」可以看机器实际怎么写出来。</div>
    </div>

    <div id="view-trace" class="view">
      <div class="controls" style="margin-bottom:14px">
        <button id="play">播放</button>
        <button id="stop" class="ghost">停止</button>
        <input type="range" id="seek" min="0" max="1000" value="0">
        <span id="clock" style="font-variant-numeric:tabular-nums;font-size:13px;color:var(--muted)">0.0s</span>
      </div>
      <div class="stage"><svg id="svg" xmlns="http://www.w3.org/2000/svg"></svg></div>
      <div class="legend">
        <span><span class="swatch" style="background:var(--ink-color,#1a1a1a)"></span>落笔绘制</span>
        <span><span class="swatch" style="background:var(--travel-color,#c9d6e8)"></span>抬笔空走</span>
        <span><span class="swatch" style="background:#e07a2f;width:9px;height:9px;border-radius:50%"></span>笔尖位置</span>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="stat" id="stat"></div>
  </div>
</div>

<script>
const DATA = __DATA__;

// ---- 表格视图 ----
function renderTable() {
  if (!DATA.tableData || !DATA.tableData.length) return false;
  const t = document.getElementById('contentTable');
  t.innerHTML = '';
  DATA.tableData.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      td.textContent = cell;
      tr.appendChild(td);
    });
    t.appendChild(tr);
  });
  return true;
}

function switchView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  const tab = document.querySelector('.tab[data-view="' + name + '"]');
  if (tab) tab.classList.add('active');
}

const hasTable = renderTable();
if (hasTable) {
  document.getElementById('subtitle').textContent =
    '表格内容直接展示；切到「书写轨迹」可看机器实际怎么写出来';
} else {
  document.getElementById('subtitle').textContent =
    '按真实进给速度回放笔尖轨迹；可拖动进度条逐帧检查';
}
if (hasTable) {
  document.getElementById('tabs').style.display = 'flex';
  document.querySelectorAll('.tab').forEach(b => {
    b.addEventListener('click', () => switchView(b.dataset.view));
  });
  // 默认直接展示表格内容
  switchView('table');
} else {
  switchView('trace');
}

const svg = document.getElementById('svg');
const NS = 'http://www.w3.org/2000/svg';
const W = DATA.canvasWidth, H = DATA.canvasHeight;

svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
svg.setAttribute('width', W);
svg.setAttribute('height', H);

const bg = document.createElementNS(NS, 'rect');
bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%');
bg.setAttribute('fill', '#ffffff');
svg.appendChild(bg);

const paper = document.createElementNS(NS, 'rect');
paper.setAttribute('x', DATA.margin); paper.setAttribute('y', DATA.margin);
paper.setAttribute('width', DATA.paperWidth); paper.setAttribute('height', DATA.paperHeight);
paper.setAttribute('fill', 'none'); paper.setAttribute('stroke', '#d0d0d0');
paper.setAttribute('stroke-width', '1'); paper.setAttribute('stroke-dasharray', '4 3');
svg.appendChild(paper);

const paths = [];
DATA.segments.forEach(seg => {
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', seg.d);
  p.setAttribute('fill', 'none');
  p.setAttribute('stroke-linecap', 'round');
  p.setAttribute('stroke-linejoin', 'round');
  if (seg.type === 'draw') {
    p.setAttribute('stroke', DATA.inkColor);
    p.setAttribute('stroke-width', seg.w || DATA.strokeWidth);
  } else {
    p.setAttribute('stroke', DATA.travelColor);
    p.setAttribute('stroke-width', Math.max(DATA.strokeWidth * 0.5, 0.4));
    p.setAttribute('stroke-dasharray', '3 3');
  }
  svg.appendChild(p);
  const len = p.getTotalLength();
  if (seg.type === 'draw') {
    p.setAttribute('stroke-dasharray', len);
    p.setAttribute('stroke-dashoffset', len);
  }
  paths.push({ el: p, len, seg });
});

const pen = document.createElementNS(NS, 'circle');
pen.setAttribute('r', '4');
pen.setAttribute('fill', '#e07a2f');
pen.setAttribute('stroke', '#fff');
pen.setAttribute('stroke-width', '1.5');
svg.appendChild(pen);

const penTrail = document.createElementNS(NS, 'path');
penTrail.setAttribute('fill', 'none');
penTrail.setAttribute('stroke', '#f5c08a');
penTrail.setAttribute('stroke-width', '2');
penTrail.setAttribute('stroke-linecap', 'round');
svg.appendChild(penTrail);

let total = 0;
const starts = DATA.segments.map(s => { const t = total; total += s.dur; return t; });

function seek(t) {
  let penX = DATA.margin, penY = DATA.margin + DATA.paperHeight;
  let trail = [];
  for (let i = 0; i < paths.length; i++) {
    const { el, len, seg } = paths[i];
    const s0 = starts[i], s1 = s0 + seg.dur;
    const local = Math.max(0, Math.min(1, (t - s0) / (seg.dur || 1)));
    if (seg.type === 'draw') {
      el.setAttribute('stroke-dashoffset', len * (1 - local));
    }
    const pt = el.getPointAtLength(len * local);
    penX = pt.x; penY = pt.y;
    if (t > s0) trail.push([pt.x, pt.y]);
  }
  pen.setAttribute('cx', penX); pen.setAttribute('cy', penY);

  // 只保留最近一小段轨迹，避免大量空走线把整张图糊住；
  // 动画结束后完全隐藏轨迹，露出干净的最终结果。
  const finished = t >= total - 0.001;
  if (finished || trail.length < 2) {
    penTrail.setAttribute('d', '');
  } else {
    const tail = trail.slice(-40);
    penTrail.setAttribute('d',
      'M ' + tail.map(p => p[0].toFixed(2) + ' ' + p[1].toFixed(2)).join(' L '));
  }

  document.getElementById('clock').textContent = t.toFixed(1) + 's';
  document.getElementById('seek').value = String(Math.round(t / (total || 1) * 1000));
  updateStat(t);
}

function updateStat(t) {
  let idx = 0;
  for (let i = 0; i < starts.length; i++) { if (t >= starts[i]) idx = i; }
  const s = DATA.segments[idx];
  document.getElementById('stat').innerHTML = s
    ? `<span>当前字符 <b>${s.char || '-'}</b></span>
       <span>笔画 <b>${s.stroke}</b></span>
       <span>动作 <b>${s.type === 'draw' ? '落笔' : '抬笔'}</b></span>
       <span>已用时 <b>${t.toFixed(1)}s</b></span>
       <span>总时长 <b>${total.toFixed(1)}s</b></span>`
    : `<span>总时长 <b>${total.toFixed(1)}s</b></span>`;
}

let playing = false, rafId = null, lastTs = null, elapsed = 0;

function tick(ts) {
  if (!playing) return;
  if (lastTs === null) lastTs = ts;
  const dt = (ts - lastTs) / 1000;
  lastTs = ts;
  elapsed += dt;
  if (elapsed >= total) { elapsed = total; playing = false; document.getElementById('play').textContent = '播放'; }
  seek(elapsed);
  if (playing) rafId = requestAnimationFrame(tick);
}

document.getElementById('play').addEventListener('click', () => {
  if (elapsed >= total) elapsed = 0;
  playing = !playing;
  document.getElementById('play').textContent = playing ? '暂停' : '播放';
  if (playing) { lastTs = null; rafId = requestAnimationFrame(tick); }
});

document.getElementById('stop').addEventListener('click', () => {
  playing = false; elapsed = 0; lastTs = null;
  document.getElementById('play').textContent = '播放';
  seek(0);
});

document.getElementById('seek').addEventListener('input', e => {
  playing = false;
  document.getElementById('play').textContent = '播放';
  elapsed = (e.target.value / 1000) * total;
  seek(elapsed);
});

seek(0);
if (DATA.showFinal) {
  // 直接显示最终结果；播放按钮会从起点重新回放书写过程
  seek(total);
  document.getElementById('play').textContent = '重新播放';
}
</script>
</body>
</html>
"""
