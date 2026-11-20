"""命令行入口。

常用示例
--------
预览效果（不需要硬件）::

    writerobot preview "你好世界" -o preview.html

生成 G-code::

    writerobot gcode "你好世界" -o out.nc --machine configs/corexy.json

发送到机器::

    writerobot run "你好世界" --port COM3

列出串口 / 查看机器配置::

    writerobot ports
    writerobot config --machine configs/corexy.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .glyphs import GlyphRepository
from .layout import LayoutOptions
from .machine import MachineConfig, PenConfig
from .pipeline import WriteRequest, build_job, render_preview
from .simulator import SimulationOptions

__all__ = ["main", "build_parser"]


def _add_layout_args(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("排版")
    g.add_argument("--font-size", type=float, default=20.0, help="字号（em 盒高度，mm）")
    g.add_argument("--line-height", type=float, default=1.6, help="行距系数")
    g.add_argument("--letter-spacing", type=float, default=0.10, help="字间距比例")
    g.add_argument("--margin", type=float, default=10.0, help="四周留边（mm）")
    g.add_argument("--align", choices=("left", "center", "right"), default="left", help="水平对齐")
    g.add_argument("--valign", choices=("top", "center", "bottom"), default="top", help="垂直对齐")
    g.add_argument("--wrap", type=float, default=None, help="自动换行宽度（mm）")
    g.add_argument("--no-auto-fit", action="store_true", help="不自动缩小字号以适应区域")
    g.add_argument("--no-handwriting", action="store_true", help="关闭手写风格（字符严格对齐）")
    g.add_argument("--hw-rotation", type=float, default=2.6, help="单字最大随机旋转角度（度）")
    g.add_argument("--hw-offset", type=float, default=0.9, help="单字最大位置抖动（mm）")
    g.add_argument("--hw-wobble", type=float, default=1.1, help="基线起伏幅度（mm）")
    g.add_argument("--hw-spacing-jitter", type=float, default=0.35, help="字距随机浮动比例")
    g.add_argument("--hw-seed", type=int, default=20260101, help="手写风格随机种子（保证可复现）")


def _add_machine_args(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("机器")
    g.add_argument("--machine", type=Path, default=None, help="机器配置 JSON 文件")
    g.add_argument("--kinematics", choices=("cartesian", "corexy"), default=None, help="运动学类型")
    g.add_argument("--width", type=float, default=None, help="书写区域宽度（mm）")
    g.add_argument("--height", type=float, default=None, help="书写区域高度（mm）")
    g.add_argument("--origin", choices=("front-left", "front-right", "back-left", "back-right"), default=None, help="原点位置")
    g.add_argument("--draw-feed", type=float, default=None, help="绘制速度（mm/min）")
    g.add_argument("--travel-feed", type=float, default=None, help="空走速度（mm/min）")
    g.add_argument("--pen-mode", choices=("z", "servo"), default=None, help="抬落笔方式")
    g.add_argument("--z-up", type=float, default=None, help="抬笔 Z 坐标（mm）")
    g.add_argument("--z-down", type=float, default=None, help="落笔 Z 坐标（mm）")
    g.add_argument("--servo-up", type=int, default=None, help="抬笔 S 值")
    g.add_argument("--servo-down", type=int, default=None, help="落笔 S 值")
    g.add_argument("--corexy-host-solve", action="store_true", default=None, help="由上位机解算 CoreXY 运动学")


def _machine_from_args(args: argparse.Namespace) -> MachineConfig:
    if getattr(args, "machine", None):
        config = MachineConfig.load(args.machine)
    else:
        config = MachineConfig()

    overrides = {
        "kinematics": args.kinematics,
        "width": args.width,
        "height": args.height,
        "origin": args.origin,
        "draw_feed": args.draw_feed,
        "travel_feed": args.travel_feed,
    }
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, value)

    pen_overrides = {
        "mode": args.pen_mode,
        "z_up": args.z_up,
        "z_down": args.z_down,
        "servo_up": args.servo_up,
        "servo_down": args.servo_down,
    }
    for key, value in pen_overrides.items():
        if value is not None:
            setattr(config.pen, key, value)

    if getattr(args, "corexy_host_solve", False):
        config.corexy_host_solve = True

    config.validate()
    return config


def _layout_from_args(args: argparse.Namespace, machine: MachineConfig) -> LayoutOptions:
    return LayoutOptions(
        font_size=args.font_size,
        line_height=args.line_height,
        letter_spacing=args.letter_spacing,
        paper_width=machine.width,
        paper_height=machine.height,
        margin_left=args.margin,
        margin_right=args.margin,
        margin_top=args.margin,
        margin_bottom=args.margin,
        align=args.align,
        valign=args.valign,
        wrap_width=args.wrap,
        auto_fit=not args.no_auto_fit,
        handwriting=not getattr(args, "no_handwriting", False),
        hw_rotation=getattr(args, "hw_rotation", 2.6),
        hw_offset=getattr(args, "hw_offset", 0.9),
        hw_baseline_wobble=getattr(args, "hw_wobble", 1.1),
        hw_spacing_jitter=getattr(args, "hw_spacing_jitter", 0.35),
        hw_seed=getattr(args, "hw_seed", 20260101),
    )


def _build_request(args: argparse.Namespace, machine: MachineConfig) -> WriteRequest:
    return WriteRequest(
        text=args.text,
        machine=machine,
        layout=_layout_from_args(args, machine),
        corner_radius=getattr(args, "corner_radius", 0.6),
        sample_step=getattr(args, "sample_step", 0.5),
        smooth=not getattr(args, "no_smooth", False),
        stroke_wobble=getattr(args, "stroke_wobble", 0.0),
        cache_dir=Path(args.cache_dir) if getattr(args, "cache_dir", None) else None,
        offline=getattr(args, "offline", False),
        line_numbers=getattr(args, "line_numbers", False),
    )


def _write_output(path: Path, content: str) -> Path:
    """写文件，自动创建父目录。"""
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.resolve()


def _print_summary(job, machine: MachineConfig, out: Optional[str]) -> None:
    t = job.trajectory
    print(f"字符数     : {len(job.layout.glyphs)}")
    print(f"笔画数     : {t.stroke_count}")
    print(f"抬落笔次数 : {t.pen_toggles}")
    print(f"绘制长度   : {t.draw_length:.1f} mm")
    print(f"空走长度   : {t.travel_length:.1f} mm")
    print(f"实际字号   : {job.layout.font_size:.2f} mm")
    print(f"预计耗时   : {job.estimated_seconds:.1f} s")
    if job.missing:
        print(f"⚠ 缺少字形 : {''.join(sorted(set(job.missing)))}")
    if out:
        print(f"已输出     : {out}")


# ---------------------------------------------------------------- 子命令

def _open_in_browser(path: Path) -> bool:
    """用系统默认程序打开文件（非阻塞）。失败时返回 False。"""
    import os
    import subprocess
    import sys as _sys

    try:
        if _sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def _prompt(message: str, default: str = "") -> str:
    """读取一行输入；非交互环境（无 stdin）时返回默认值。"""
    try:
        text = input(message).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return text or default


def cmd_menu(args: argparse.Namespace) -> int:
    """交互菜单：双击 wr.bat 时进入这里。"""
    print()
    print("  write-robot · 写字机器人上位机")
    print("  " + "-" * 44)
    print("  1) 预览轨迹      生成 HTML 动画，看笔尖怎么走（无需硬件）")
    print("  2) 生成 G-code   输出 .nc 文件，可导入 GRBL 发送器")
    print("  3) 连接机器书写  需要先接好硬件")
    print("  4) 列出串口")
    print("  5) 查看机器配置")
    print("  0) 退出")
    print()

    while True:
        choice = _prompt("  请选择 [1-5，0 退出]: ", "0")
        if choice in ("0", "q", "Q", "exit"):
            break
        if choice == "1":
            _menu_preview(args)
        elif choice == "2":
            _menu_gcode(args)
        elif choice == "3":
            _menu_run(args)
        elif choice == "4":
            cmd_ports(args)
        elif choice == "5":
            cmd_config(args)
        else:
            print("  无效选项，请输入 1-5 或 0")
            continue
        print()

    print("  再见。")
    _pause()
    return 0


def _pause() -> None:
    """双击运行时保持窗口不关闭。"""
    try:
        input("\n  按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt):
        pass


def _menu_preview(args: argparse.Namespace) -> None:
    text = _prompt("  要写的文字: ")
    if not text:
        print("  已取消")
        return
    font_size = _prompt("  字号（mm，默认 40）: ", "40")
    try:
        size = float(font_size)
    except ValueError:
        size = 40.0
    out = _prompt("  输出文件（默认 out\\preview.html）: ", "out\\preview.html")
    ns = argparse.Namespace(**vars(args))
    ns.text = text
    ns.font_size = size
    ns.output = out
    ns.format = "html"
    cmd_preview(ns)


def _menu_gcode(args: argparse.Namespace) -> None:
    text = _prompt("  要写的文字: ")
    if not text:
        print("  已取消")
        return
    out = _prompt("  输出文件（默认 out\\output.nc）: ", "out\\output.nc")
    ns = argparse.Namespace(**vars(args))
    ns.text = text
    ns.output = out
    cmd_gcode(ns)


def _menu_run(args: argparse.Namespace) -> None:
    text = _prompt("  要写的文字: ")
    if not text:
        print("  已取消")
        return
    port = _prompt("  串口（如 COM3）: ")
    if not port:
        print("  未指定串口，已取消")
        return
    ns = argparse.Namespace(**vars(args))
    ns.text = text
    ns.port = port
    cmd_run(ns)


def cmd_table(args: argparse.Namespace) -> int:
    """把表格写出来：表格线 + 单元格文字。"""
    from .pipeline import build_table_job
    from .table import TableSpec

    rows = _read_table_rows(args)
    if rows is None:
        return 2

    machine = _machine_from_args(args)
    repository = GlyphRepository.default(
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        offline=args.offline,
    )

    spec = TableSpec(
        rows=rows,
        padding=args.padding,
        align=args.align,
        valign=args.valign,
        draw_borders=not args.no_borders,
        border_wobble=args.border_wobble,
    )

    job = build_table_job(
        spec,
        repository,
        machine,
        font_size=args.font_size,
        margin=args.margin,
        line_height=args.line_height,
        letter_spacing=args.letter_spacing,
        corner_radius=args.corner_radius,
        sample_step=args.sample_step,
        smooth=not args.no_smooth,
        stroke_wobble=args.stroke_wobble,
        handwriting=not args.no_handwriting,
        hw_rotation=args.hw_rotation,
        hw_offset=args.hw_offset,
        hw_baseline_wobble=args.hw_wobble,
        hw_spacing_jitter=args.hw_spacing_jitter,
        hw_seed=args.hw_seed,
    )

    sim_options = SimulationOptions(
        show_travel=not args.no_travel,
        # 表格默认不显示笔顺编号，否则数字会压在文字上
        show_stroke_numbers=args.stroke_numbers,
        real_time=not args.demo,
        demo_speed=args.speed,
        canvas_width=args.canvas_width,
        show_final=not args.animate,
        # 预览页直接用 HTML 表格展示内容，与书写轨迹对照
        table_data=rows,
        table_title="表格内容",
    )

    if args.format == "svg":
        content = render_preview(job, sim_options, as_html=False)
        suffix = ".svg"
    else:
        content = render_preview(job, sim_options, as_html=True)
        suffix = ".html"

    out = Path(args.output) if args.output else Path("out") / f"table{suffix}"
    out = _write_output(out, content)

    print(f"表格: {len(rows)} 行 x {len(rows[0])} 列")
    if args.format == "svg":
        print(f"已生成: {out.resolve()}")
    else:
        print(f"预览已生成: {out.resolve()}")
        if args.open:
            if _open_in_browser(out):
                print("已在浏览器中打开")
            else:
                print("（未能自动打开浏览器，请手动双击该文件）")

    t = job.trajectory
    print(f"笔画数     : {t.stroke_count}")
    print(f"抬落笔次数 : {t.pen_toggles}")
    print(f"绘制长度   : {t.draw_length:.1f} mm")
    print(f"空走长度   : {t.travel_length:.1f} mm")
    print(f"实际字号   : {job.table.font_size:.2f} mm")
    print(f"预计耗时   : {job.estimated_seconds:.1f} s")
    return 0


def _read_table_rows(args: argparse.Namespace) -> Optional[List[List[str]]]:
    """读取表格数据：--file 指定 JSON，或用位置参数按行给文本。"""
    import json

    if args.file:
        path = Path(args.file)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"✗ 读取表格文件失败: {exc}", file=sys.stderr)
            return None
        if isinstance(data, list) and data and isinstance(data[0], list):
            rows = [[str(c) for c in row] for row in data]
        elif isinstance(data, list):
            rows = [[str(c)] for c in data]
        else:
            print("✗ 表格文件应为二维数组或一维数组", file=sys.stderr)
            return None
    else:
        if not args.text:
            print("✗ 请用 --file 指定表格 JSON，或用位置参数给出每行文本", file=sys.stderr)
            return None
        rows = []
        for line in args.text:
            rows.append([c for c in line.split("|")])
        # 每行用 | 分隔单元格；行数不一致时按最长的补空
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]

    if not rows:
        print("✗ 表格为空", file=sys.stderr)
        return None
    return rows


def cmd_preview(args: argparse.Namespace) -> int:
    machine = _machine_from_args(args)
    request = _build_request(args, machine)
    job = build_job(request)

    sim_options = SimulationOptions(
        show_travel=not args.no_travel,
        show_stroke_numbers=not args.no_stroke_numbers,
        real_time=not args.demo,
        demo_speed=args.speed,
        canvas_width=args.canvas_width,
    )

    if args.format == "svg":
        content = render_preview(job, sim_options, as_html=False)
        suffix = ".svg"
    else:
        content = render_preview(job, sim_options, as_html=True)
        suffix = ".html"

    out = Path(args.output) if args.output else Path("out") / f"preview{suffix}"
    out = _write_output(out, content)
    print(f"预览已生成: {out.resolve()}")

    if args.open:
        if _open_in_browser(out):
            print("已在浏览器中打开")
        else:
            print("（未能自动打开浏览器，请手动双击该文件）")

    _print_summary(job, machine, str(out.resolve()))
    return 0


def cmd_gcode(args: argparse.Namespace) -> int:
    machine = _machine_from_args(args)
    request = _build_request(args, machine)
    job = build_job(request)

    out = Path(args.output) if args.output else Path("out") / "output.nc"
    out = _write_output(out, job.gcode)
    print(f"G-code 已生成: {out.resolve()}")
    _print_summary(job, machine, str(out.resolve()))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .serial_link import GrblError, SerialLink

    machine = _machine_from_args(args)
    request = _build_request(args, machine)
    job = build_job(request)

    if args.dry_run:
        print(job.gcode)
        return 0

    _print_summary(job, machine, None)
    print(f"\n正在连接 {args.port} ...")

    link = SerialLink(port=args.port, baudrate=args.baudrate, timeout=args.timeout)
    try:
        link.open()
    except Exception as exc:
        print(f"✗ 连接失败: {exc}", file=sys.stderr)
        return 2

    try:
        if args.status:
            print("状态:", link.status())

        last = [-1]

        def progress(done: int, total: int) -> None:
            pct = int(done * 100 / max(total, 1))
            if pct != last[0] and pct % 5 == 0:
                last[0] = pct
                print(f"\r  下发进度 {pct}% ({done}/{total})", end="", flush=True)

        link.run_gcode(job.gcode, progress=progress)
        print("\r✓ 下发完成            ")
        return 0
    except GrblError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    finally:
        link.close()


def cmd_ports(args: argparse.Namespace) -> int:
    from .serial_link import list_ports

    try:
        ports = list_ports()
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if not ports:
        print("未发现串口设备")
        return 0
    print("可用串口:")
    for p in ports:
        print(f"  {p}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    machine = _machine_from_args(args)
    print(json.dumps(machine.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    machine = MachineConfig(
        name=args.name,
        kinematics=args.kinematics,
        width=args.width,
        height=args.height,
        pen=PenConfig(mode=args.pen_mode),
    )
    out = _write_output(Path(args.output), json.dumps(machine.to_dict(), ensure_ascii=False, indent=2))
    print(f"配置已写入: {out}")
    return 0


# ---------------------------------------------------------------- 解析器

def _add_preview_args(parser: argparse.ArgumentParser) -> None:
    """preview 专用参数。menu 也要带上，因为菜单里能调用 preview。"""
    parser.add_argument("--format", choices=("html", "svg"), default="html", help="输出格式")
    parser.add_argument("--demo", action="store_true", help="演示模式（加速播放）")
    parser.add_argument("--speed", type=float, default=4.0, help="演示速度倍率")
    parser.add_argument("--no-travel", action="store_true", help="不显示空走轨迹")
    parser.add_argument("--no-stroke-numbers", action="store_true", help="不显示笔顺编号")
    parser.add_argument("--canvas-width", type=int, default=800, help="画布宽度（像素）")
    parser.add_argument("--open", action="store_true", help="生成后用浏览器打开预览")


def _add_job_args(parser: argparse.ArgumentParser) -> None:
    """gcode / run 专用参数。menu 也要带上。"""
    parser.add_argument("--corner-radius", type=float, default=1.1, help="拐角圆滑半径（mm）")
    parser.add_argument("--sample-step", type=float, default=0.5, help="弧长重采样步长（mm）")
    parser.add_argument("--no-smooth", action="store_true", help="不做样条平滑")
    parser.add_argument(
        "--stroke-wobble", type=float, default=0.0, help="笔画运笔起伏幅度（mm），0=绝对直线"
    )
    parser.add_argument("--line-numbers", action="store_true", help="G-code 带行号")
    parser.add_argument("--cache-dir", default=None, help="汉字数据缓存目录")
    parser.add_argument("--offline", action="store_true", help="离线模式，只用本地缓存")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="writerobot",
        description="写字机器人上位机：把文字变成 G-code，或直接驱动机器书写。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"write-robot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # table
    tb = sub.add_parser("table", help="把表格写出来（表格线 + 单元格文字）")
    tb.add_argument("text", nargs="*", help="每行文本，单元格之间用 | 分隔")
    tb.add_argument("--file", default=None, help="表格数据 JSON 文件（二维数组）")
    tb.add_argument("-o", "--output", default=None, help="输出文件")
    tb.add_argument("--format", choices=("html", "svg"), default="html", help="输出格式")
    tb.add_argument("--padding", type=float, default=3.0, help="单元格内边距（mm）")
    tb.add_argument("--no-borders", action="store_true", help="不绘制表格线")
    tb.add_argument("--border-wobble", type=float, default=0.0, help="表格线手绘起伏幅度（mm）")
    tb.add_argument("--animate", action="store_true", help="打开页面从空白开始播放动画（默认直接显示结果）")
    tb.add_argument("--demo", action="store_true", help="演示模式（加速播放）")
    tb.add_argument("--speed", type=float, default=4.0, help="演示速度倍率")
    tb.add_argument("--no-travel", action="store_true", help="不显示空走轨迹")
    tb.add_argument("--stroke-numbers", action="store_true", help="显示笔顺编号（默认不显示）")
    tb.add_argument("--canvas-width", type=int, default=800, help="画布宽度（像素）")
    tb.add_argument("--open", action="store_true", help="生成后用浏览器打开预览")
    tb.add_argument("--corner-radius", type=float, default=1.1, help="拐角圆滑半径（mm）")
    tb.add_argument("--sample-step", type=float, default=0.5, help="弧长重采样步长（mm）")
    tb.add_argument("--no-smooth", action="store_true", help="不做样条平滑")
    tb.add_argument("--stroke-wobble", type=float, default=0.0, help="笔画运笔起伏幅度（mm）")
    tb.add_argument("--cache-dir", default=None, help="汉字数据缓存目录")
    tb.add_argument("--offline", action="store_true", help="离线模式，只用本地缓存")
    _add_layout_args(tb)
    _add_machine_args(tb)
    tb.set_defaults(func=cmd_table)

    # menu
    m = sub.add_parser("menu", help="交互菜单（双击 wr.bat 时进入）")
    _add_layout_args(m)
    _add_machine_args(m)
    _add_preview_args(m)
    _add_job_args(m)
    m.add_argument("--port", default=None, help="串口设备")
    m.add_argument("--baudrate", type=int, default=115200, help="波特率")
    m.add_argument("--timeout", type=float, default=5.0, help="串口读超时（秒）")
    m.add_argument("--status", action="store_true", help="开始前查询机器状态")
    m.add_argument("--dry-run", action="store_true", help="只打印 G-code，不发送")
    m.set_defaults(func=cmd_menu)

    # preview
    p = sub.add_parser("preview", help="生成轨迹预览（HTML 动画或 SVG），无需硬件")
    p.add_argument("text", help="要写的文字")
    p.add_argument("-o", "--output", default=None, help="输出文件")
    p.add_argument("--format", choices=("html", "svg"), default="html", help="输出格式")
    p.add_argument("--demo", action="store_true", help="演示模式（加速播放，非真实速度）")
    p.add_argument("--speed", type=float, default=4.0, help="演示速度倍率")
    p.add_argument("--no-travel", action="store_true", help="不显示空走轨迹")
    p.add_argument("--no-stroke-numbers", action="store_true", help="不显示笔顺编号")
    p.add_argument("--canvas-width", type=int, default=800, help="画布宽度（像素）")
    p.add_argument("--open", action="store_true", help="生成后用浏览器打开预览")
    _add_job_args(p)
    _add_layout_args(p)
    _add_machine_args(p)
    p.set_defaults(func=cmd_preview)

    # gcode
    g = sub.add_parser("gcode", help="生成 G-code 文件")
    g.add_argument("text", help="要写的文字")
    g.add_argument("-o", "--output", default=None, help="输出文件")
    _add_job_args(g)
    _add_layout_args(g)
    _add_machine_args(g)
    g.set_defaults(func=cmd_gcode)

    # run
    r = sub.add_parser("run", help="连接机器并直接书写")
    r.add_argument("text", help="要写的文字")
    r.add_argument("--port", required=True, help="串口设备（如 COM3 / /dev/ttyUSB0）")
    r.add_argument("--baudrate", type=int, default=115200, help="波特率")
    r.add_argument("--timeout", type=float, default=5.0, help="串口读超时（秒）")
    r.add_argument("--status", action="store_true", help="开始前查询机器状态")
    r.add_argument("--dry-run", action="store_true", help="只打印 G-code，不发送")
    _add_job_args(r)
    _add_layout_args(r)
    _add_machine_args(r)
    r.set_defaults(func=cmd_run)

    # ports
    pz = sub.add_parser("ports", help="列出可用串口")
    pz.set_defaults(func=cmd_ports)

    # config
    c = sub.add_parser("config", help="查看机器配置")
    _add_machine_args(c)
    c.set_defaults(func=cmd_config)

    # init-config
    ic = sub.add_parser("init-config", help="生成一份机器配置模板")
    ic.add_argument("-o", "--output", default="machine.json", help="输出文件")
    ic.add_argument("--name", default="my-writerobot", help="机器名称")
    ic.add_argument("--kinematics", choices=("cartesian", "corexy"), default="corexy")
    ic.add_argument("--width", type=float, default=200.0)
    ic.add_argument("--height", type=float, default=200.0)
    ic.add_argument("--pen-mode", choices=("z", "servo"), default="servo")
    ic.set_defaults(func=cmd_init_config)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"✗ 错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
