"""GRBL 串口通信：把 G-code 发送到机器。

GRBL 协议要点
--------------
* 波特率固定 **115200**（8N1）。
* 上电后固件会输出一行 ``Grbl 1.1f ['$' for help]``，需要等待它出现。
* 每行 G-code 发送后，固件回 ``ok`` 或 ``error:N``。**必须逐行等待应答**，
  否则串口缓冲区会溢出（``overflow`` 错误）。
* 可随时发送 ``?`` 查询实时状态，固件回 ``<Idle|MPos:...|FS:...>``。
* ``$$`` 打印全部参数，``$G`` 打印当前状态，``$H`` 回零。

流控策略
--------
本模块采用「行确认」模式：发送一行 → 等待 ``ok`` → 再发下一行。
这比一次性灌满缓冲区安全得多，代价是每行多一个串口往返（115200 波特率下
约 1ms），对几千行的 G-code 而言总耗时增加有限。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

__all__ = ["GrblError", "SerialLink", "list_ports"]


class GrblError(RuntimeError):
    """GRBL 返回错误。"""

    def __init__(self, message: str, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code


def list_ports() -> List[str]:
    """列出可用串口（需要安装 pyserial）。"""
    try:
        from serial.tools import list_ports as _lp
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
        raise RuntimeError(
            "需要 pyserial 才能使用串口功能，请先执行: pip install pyserial"
        ) from exc
    return [p.device for p in _lp.comports()]


@dataclass
class SerialLink:
    """GRBL 串口连接。"""

    port: str
    baudrate: int = 115200
    timeout: float = 5.0

    #: 每行发送后的最长等待时间 (秒)
    line_timeout: float = 10.0

    #: 发送前是否先执行 ``$H`` 回零
    home_before_run: bool = False

    #: 是否在结束后发送 ``M5`` / 抬笔
    release_pen_on_exit: bool = True

    def __enter__(self) -> "SerialLink":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ 连接管理

    def open(self) -> None:
        """打开串口并等待 GRBL 就绪。"""
        try:
            import serial  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "需要 pyserial 才能使用串口功能，请先执行: pip install pyserial"
            ) from exc

        self._serial = serial.Serial(
            port=self.port, baudrate=self.baudrate, timeout=self.timeout
        )
        self._buffer = ""
        self._wait_for_grbl_banner()

    def close(self) -> None:
        """关闭串口。"""
        ser = getattr(self, "_serial", None)
        if ser is not None and ser.is_open:
            try:
                ser.close()
            except Exception:  # pragma: no cover
                pass
        self._serial = None

    # ------------------------------------------------------------ 底层收发

    def _read_until_quiet(self, quiet: float = 0.15, limit: float = 3.0) -> str:
        """读取直到串口安静 ``quiet`` 秒，或超过 ``limit`` 秒。"""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            chunk = self._serial.read(self._serial.in_waiting or 1)
            if chunk:
                self._buffer += chunk.decode("utf-8", errors="replace")
                end = time.monotonic() + quiet
        return self._buffer

    def _wait_for_grbl_banner(self, timeout: float = 8.0) -> str:
        """等待 GRBL 启动横幅。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._read_until_quiet(quiet=0.2, limit=1.0)
            if "Grbl" in self._buffer or "ALARM" in self._buffer:
                self._buffer = ""
                return self._buffer
            time.sleep(0.05)
        # 没等到横幅也继续（部分固件不带横幅）
        self._buffer = ""
        return ""

    def _read_line(self, timeout: float) -> str:
        """读取一行（以 ``ok`` / ``error`` 结尾的应答）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "\n" in self._buffer or "\r" in self._buffer:
                sep = "\n" if "\n" in self._buffer else "\r"
                line, self._buffer = self._buffer.split(sep, 1)
                line = line.strip()
                if line:
                    return line
            else:
                chunk = self._serial.read(self._serial.in_waiting or 1)
                if chunk:
                    self._buffer += chunk.decode("utf-8", errors="replace")
                else:
                    time.sleep(0.005)
        raise GrblError(f"等待 GRBL 应答超时（{timeout}s）")

    # ------------------------------------------------------------ GRBL 指令

    def send_line(self, line: str, timeout: Optional[float] = None) -> str:
        """发送一行 G-code 并等待应答，返回应答文本。"""
        cmd = line.strip()
        if not cmd:
            return ""
        self._serial.write((cmd + "\n").encode("utf-8"))
        self._serial.flush()

        wait = timeout if timeout is not None else self.line_timeout
        while True:
            reply = self._read_line(wait)
            if reply.lower().startswith("ok"):
                return reply
            if reply.lower().startswith("error"):
                code = None
                if ":" in reply:
                    try:
                        code = int(reply.split(":", 1)[1].strip())
                    except ValueError:
                        code = None
                raise GrblError(f"GRBL 报错: {reply}", code)
            if reply.startswith("ALARM"):
                raise GrblError(f"GRBL 报警: {reply}")
            # 其他输出（如 echo / [MSG:...]）忽略，继续等 ok

    def query(self, command: str, timeout: float = 3.0) -> str:
        """发送查询指令并收集完整应答（如 ``$G``、``$$``）。"""
        self._serial.write((command.strip() + "\n").encode("utf-8"))
        self._serial.flush()

        deadline = time.monotonic() + timeout
        collected: List[str] = []
        while time.monotonic() < deadline:
            reply = self._read_line(max(0.05, deadline - time.monotonic()))
            if reply.lower().startswith("ok"):
                break
            collected.append(reply)
        return "\n".join(collected)

    def status(self) -> str:
        """获取实时状态（``<Idle|MPos:...|FS:...>``）。"""
        self._serial.write(b"?")
        self._serial.flush()
        return self._read_line(2.0)

    def wait_idle(self, timeout: float = 300.0, poll: float = 0.2) -> None:
        """等待机器回到 Idle 状态。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.status()
            if st.startswith("<") and "Idle" in st:
                return
            if "Alarm" in st or "ALARM" in st:
                raise GrblError(f"机器处于报警状态: {st}")
            time.sleep(poll)
        raise GrblError(f"等待机器空闲超时（{timeout}s）")

    # ------------------------------------------------------------ 主流程

    def run_gcode(self, gcode: str, progress=None) -> None:
        """逐行下发 G-code。

        参数
        ----
        progress:
            可选回调 ``progress(done: int, total: int)``，用于显示进度。
        """
        lines = [ln for ln in gcode.splitlines() if ln.strip() and not ln.strip().startswith(";")]
        total = len(lines)

        if self.home_before_run:
            self.send_line("$H", timeout=60.0)

        for i, line in enumerate(lines, start=1):
            self.send_line(line)
            if progress is not None:
                progress(i, total)

        self.wait_idle(timeout=600.0)

        if self.release_pen_on_exit:
            try:
                self.send_line("M5")
            except GrblError:
                pass
