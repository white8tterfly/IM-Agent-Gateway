# 开机自启配置与静默启动测试：WATCH_AUTOSTART 解析、计划任务解释器选择（无网络依赖）
# 用法：python scripts/test_watcher_autostart.py
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from im_agent_gateway.config import load_config


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def load_with_env(**pairs):
    """注入环境变量后加载配置，结束后还原现场（值 None 表示按未配置处理）。"""
    saved = {}
    for key, val in pairs.items():
        saved[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    try:
        return load_config()
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def main() -> None:
    # ===== 一、WATCH_AUTOSTART 解析 =====
    check(load_with_env(WATCH_AUTOSTART=None).watch_autostart is True,
          "未配置时默认开机自启（兼容老 .env，行为不变）")
    check(load_with_env(WATCH_AUTOSTART="1").watch_autostart is True,
          "1 = 开机自启")
    for off in ("0", "false", "no"):
        check(load_with_env(WATCH_AUTOSTART=off).watch_autostart is False,
              f"{off} = 关闭开机自启")

    # ===== 二、计划任务用无控制台解释器启动 =====
    script = (ROOT / "scripts" / "install_watcher.ps1").read_text(encoding="utf-8")
    check("pythonw.exe" in script, "安装脚本优先选择 pythonw.exe（无控制台，开机不弹窗）")
    check("-Execute $Launch" in script, "计划任务执行文件走 pythonw 选择结果")
    check("WATCH_AUTOSTART" in script, "安装脚本提示开机自启开关")

    # ===== 三、配置登记与展示 =====
    from im_agent_gateway.manage.config_meta import CONFIG_META_BY_KEY
    from im_agent_gateway.manage.service import config_grouped_view
    item = CONFIG_META_BY_KEY.get("WATCH_AUTOSTART")
    check(item is not None and item.type == "bool" and item.field == "watch_autostart",
          "配置元数据已登记为开关项（管理台/CLI 可改）")
    view = config_grouped_view(load_with_env(WATCH_AUTOSTART="0"))
    common = {it["key"]: it for it in view["common"]}
    check(common.get("WATCH_AUTOSTART", {}).get("value") == "0",
          "配置视图展示当前值（关闭状态为 0）")

    print("\n全部通过")


main()
