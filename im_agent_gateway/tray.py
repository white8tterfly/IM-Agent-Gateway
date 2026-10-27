"""系统托盘图标：服务在后台无窗口运行时，从任务栏角落打开控制台或退出服务。"""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable

from .config import PROJECT_ROOT, Config
from .i18n import set_language, t

logger = logging.getLogger(__name__)

ICON_FILE = PROJECT_ROOT / "assets" / "icon.ico"


def start_tray(cfg: Config, on_quit: Callable[[], None]):
    """启动托盘图标线程，返回图标对象；缺少依赖或平台不支持时返回 None。

    on_quit 由菜单「退出服务」触发，调用方负责线程安全地把退出请求投给事件循环。
    """
    try:
        import pystray
        from PIL import Image
    except ImportError:
        logger.info(t("未安装 pystray / Pillow，跳过托盘图标（pip install pystray pillow）",
                      "pystray / Pillow not installed; skipping tray icon (pip install pystray pillow)"))
        return None

    try:
        image = Image.open(ICON_FILE)
    except OSError:
        image = Image.new("RGBA", (64, 64), (32, 116, 232, 255))

    base_url = cfg.webui_urls[0]
    set_language(cfg.language)

    def open_console(icon, item) -> None:
        webbrowser.open(base_url)

    def open_chat(icon, item) -> None:
        webbrowser.open(f"{base_url}/chat.html")

    def quit_service(icon, item) -> None:
        icon.stop()
        on_quit()

    items = []
    if cfg.webui_enabled:
        items.append(pystray.MenuItem(t("打开 Web 控制台", "Open web console"), open_console, default=True))
        items.append(pystray.MenuItem(t("打开聊天台", "Open chat"), open_chat))
        items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem(t("退出服务", "Quit"), quit_service))
    menu = pystray.Menu(*items)
    icon = pystray.Icon("im-agent-gateway", image,
                        t("IM-Agent Gateway（右键退出）", "IM-Agent Gateway (right-click to quit)"), menu)
    # Windows 下托盘消息循环可放在后台线程，不阻塞 asyncio 主循环
    threading.Thread(target=icon.run, name="tray-icon", daemon=True).start()
    logger.info(t("托盘图标已启动：右键可打开控制台或退出服务",
                  "Tray icon started: right-click to open the console or quit the service"))
    return icon
