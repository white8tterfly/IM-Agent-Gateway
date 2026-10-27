"""带进度窗口的启动器：桌面快捷方式双击后由它负责启动与提示。

服务本身在后台无窗口运行，本窗口只显示启动进度：
- 就绪后自动打开 Web 控制台（开启时），随后窗口自动关闭；
- 启动失败时打印错误摘要并停留，避免「双击后什么都没发生」。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG = ROOT / "logs" / "im_agent_gateway.log"
SERVICE_OUT = ROOT / "logs" / "launch_service.log"
# 日志已双语：两种语言的就绪/重复实例标记都认，避免切到英文后启动器等不到信号
READY_MARKS = ("服务已启动", "Service started")
DUP_MARKS = ("已有服务实例在运行", "Another service instance is already running")
TIMEOUT = 120


def tail(path: Path, limit: int = 1500) -> str:
    """读取文件尾部：启动失败时给用户看最后一段错误信息。"""
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-limit:].strip()


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def pause() -> None:
    """失败时停住窗口，避免一闪而过；无控制台（pythonw）时只等几秒。"""
    from im_agent_gateway.i18n import t

    if sys.stdin is not None:
        try:
            input(t("按回车键关闭窗口…", "Press Enter to close this window…"))
            return
        except EOFError:
            pass
    time.sleep(8)


def read_new(start: int) -> tuple[str, int]:
    """读取日志中新增的内容，返回（新增文本，新的读取位置）。"""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start)
            text = f.read()
            return text, f.tell()
    except OSError:
        return "", start


def main() -> int:
    from im_agent_gateway.config import load_config
    from im_agent_gateway.i18n import t

    cfg = load_config()
    print(t("IM-Agent Gateway 正在启动，请稍候…", "IM-Agent Gateway is starting, please wait…"))
    pos = LOG.stat().st_size if LOG.exists() else 0

    # 用 pythonw 启动服务本身，避免再弹一个控制台；输出重定向到文件便于失败排查
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    SERVICE_OUT.parent.mkdir(parents=True, exist_ok=True)
    out = open(SERVICE_OUT, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [str(exe), str(ROOT / "main.py")],
            cwd=str(ROOT),
            stdout=out,
            stderr=out,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        out.close()  # 子进程已持有自己的句柄

    deadline = time.time() + TIMEOUT
    ready = dup = False
    while time.time() < deadline:
        chunk, pos = read_new(pos)
        if any(m in chunk for m in READY_MARKS):
            ready = True
            break
        if any(m in chunk for m in DUP_MARKS):
            dup = True
            break
        if proc.poll() is not None:
            print("\n" + t("服务启动失败，错误摘要：", "Failed to start the service. Error summary:"))
            print(tail(SERVICE_OUT) or t("（无输出）", "(no output)"))
            pause()
            return 1
        print(".", end="", flush=True)
        time.sleep(0.3)

    if not (ready or dup):
        print("\n" + t(f"启动超时（{TIMEOUT} 秒），请查看 logs/im_agent_gateway.log",
                      f"Startup timed out after {TIMEOUT}s; see logs/im_agent_gateway.log"))
        print(tail(SERVICE_OUT))
        pause()
        return 1

    print("\n" + (t("已有服务实例在运行，直接打开控制台。", "A service instance is already running; opening the console.")
                  if dup else t("服务已就绪。", "Service is ready.")))
    if cfg.webui_enabled and port_open(cfg.webui_port):
        url = cfg.webui_urls[0]
        try:
            webbrowser.open(url)
            print(t(f"已打开 Web 控制台：{url}", f"Opened the web console: {url}"))
        except Exception as exc:
            print(t(f"未能自动打开浏览器（{exc}），请手动访问：{url}",
                    f"Could not open the browser automatically ({exc}); please visit: {url}"))
    elif cfg.webui_enabled:
        print(t("Web 控制台端口尚未就绪，请稍后手动访问。",
                "The web console port is not ready yet; please open it manually in a moment."))
    print(t("窗口将在 2 秒后自动关闭。", "This window will close automatically in 2 seconds."))
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
