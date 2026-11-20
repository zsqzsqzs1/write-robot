"""示例：把一段祝福语写到纸上，并生成 G-code。

用法::

    python examples/write_greeting.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许直接以脚本方式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from writerobot import (  # noqa: E402
    LayoutOptions,
    MachineConfig,
    PenConfig,
    WriteRequest,
    build_job,
    render_preview,
)


def main() -> None:
    text = "你好世界"

    machine = MachineConfig(
        name="demo-corexy",
        kinematics="corexy",
        width=200.0,
        height=200.0,
        draw_feed=3000.0,
        travel_feed=9000.0,
        pen=PenConfig(mode="servo", servo_down=0, servo_up=1000),
    )

    request = WriteRequest(
        text=text,
        machine=machine,
        layout=LayoutOptions(
            font_size=60.0,
            paper_width=200.0,
            paper_height=200.0,
            align="center",
            valign="center",
            margin_left=20.0,
            margin_right=20.0,
            margin_top=20.0,
            margin_bottom=20.0,
        ),
        corner_radius=0.6,
    )

    job = build_job(request)

    print(f"文本       : {text}")
    print(f"笔画数     : {job.stroke_count}")
    print(f"抬落笔次数 : {job.trajectory.pen_toggles}")
    print(f"绘制长度   : {job.trajectory.draw_length:.1f} mm")
    print(f"空走长度   : {job.trajectory.travel_length:.1f} mm")
    print(f"预计耗时   : {job.estimated_seconds:.1f} s")

    out_dir = Path(__file__).resolve().parent.parent / "out"
    out_dir.mkdir(exist_ok=True)

    gcode_path = out_dir / "greeting.nc"
    gcode_path.write_text(job.gcode, encoding="utf-8")
    print(f"\nG-code     -> {gcode_path}")

    preview_path = out_dir / "greeting.html"
    preview_path.write_text(render_preview(job), encoding="utf-8")
    print(f"预览       -> {preview_path}")

    print("\n用浏览器打开预览文件即可看到笔尖的书写过程。")


if __name__ == "__main__":
    main()
