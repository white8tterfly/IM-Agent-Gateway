# 测试管理 CLI：离线 config show / check / set（注入临时配置，不碰真实 .env），
# 在线 status / agents / agent on|off（httpx MockTransport 打假 API），无真实网络
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

import manage
from im_agent_gateway.config import Config


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def base_cfg() -> Config:
    """干净的测试配置：无必填凭证，避免校验分支干扰。"""
    return Config(bot_id="", secret="")


# ---------- 离线：config show（分组视图与 /api/config 同一口径） ----------

def test_show() -> None:
    cfg = base_cfg()
    cfg.sf_api_key = "sk-test"
    cfg.feishu_app_secret = "fs-test"
    cfg.bot_id = "bot-123"
    out = manage.cmd_config_show(cfg)
    check("【常用配置】" in out and "【高级设置】" in out, "show 按常用/高级分组")
    check("接入哪些智能体" in out and "说明：" in out, "show 每项带通俗名称与说明")
    check("API_KEY）= ******" in out and "FEISHU_APP_SECRET）= ******" in out,
          "show 敏感值脱敏")
    check("BOT_ID）= bot-123" in out and "WEBUI_PORT）= 8790" in out,
          "show 常规配置明文")


# ---------- 离线：config check（声明 / 必填 / 前置条件建议） ----------

def test_check() -> None:
    # 场景一：通道必填缺失
    cfg = base_cfg()
    cfg.channels = ["feishu"]
    cfg.feishu_app_id = ""
    out = manage.cmd_config_check(cfg)
    check("FEISHU_APP_ID" in out, "check 提示缺失的必填项")

    # 场景二：webui 接入但白名单未放行浏览器用户
    cfg = base_cfg()
    cfg.channels = ["webui"]
    cfg.allowed_users = {"someone"}
    out = manage.cmd_config_check(cfg)
    check("webui" in out and "允许名单" in out, "check 提示白名单未含 webui 用户")

    # 场景三：健康配置给出正常结论
    cfg = base_cfg()
    cfg.channels = ["wecom"]
    cfg.bot_id = "bot-1"
    cfg.secret = "sec-1"
    cfg.allowed_users = set()
    out = manage.cmd_config_check(cfg)
    check("一切正常" in out, "check 健康配置输出正常结论")


# ---------- 离线：config set（统一命令层写回临时 .env） ----------

def test_set() -> None:
    with tempfile.TemporaryDirectory() as td:
        envf = Path(td) / ".env"
        envf.write_text("X=1\n", encoding="utf-8")
        cfg = base_cfg()
        cfg.env_file = envf
        out = manage.cmd_config_set(cfg, "webui_port", "9000")
        content = envf.read_text(encoding="utf-8")
        check("WEBUI_PORT=9000" in content, "set 写回键值（键名规范化为大写）")
        check("X=1" in content, "set 保留原有内容")
        check("重启" in out, "set 提示重启生效")
        # 统一命令层校验：未登记键拒绝、敏感项留空=不改
        err = manage.cmd_config_set(cfg, "not_a_key", "x")
        check("不支持" in err, "set 未登记键拒绝")
        err = manage.cmd_config_set(cfg, "api_key", "")
        check("不修改" in err, "set 敏感项空值拒绝（留空=不改）")


# ---------- 在线：管理端点命令（MockTransport 打假 API） ----------

def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/status":
        return httpx.Response(200, json={
            "state": "running",
            "channels": [{"name": "webui", "connected": True}],
            "agents": {"total": 2, "active": 1},
            "idle": {"exit_enabled": False, "idle_min": 10, "idle_sec": 0.0},
        })
    if request.url.path == "/api/agents":
        return httpx.Response(200, json=[
            {"name": "alpha", "label": "甲助手", "kind": "chat", "complexity": "simple",
             "active": True, "available": True, "routing_hint": "日常问答"},
        ])
    if request.url.path == "/api/agents/alpha/active" and request.method == "POST":
        return httpx.Response(200, json={"ok": True, "error": None})
    return httpx.Response(404)


def dead(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def html_page(request: httpx.Request) -> httpx.Response:
    """睡眠态托管页：端口有响应但返回 HTML，不是管理接口数据。"""
    return httpx.Response(200, text="<html>服务休息中</html>")


def test_online() -> None:
    cfg = base_cfg()
    tr = httpx.MockTransport(handler)

    out = manage.cmd_status(cfg, transport=tr)
    check("运行中" in out and "webui" in out, "status 展示运行状态与通道")
    check("共 2 个" in out and "已开启 1" in out, "status 展示智能体统计")

    out = manage.cmd_agents(cfg, transport=tr)
    check("甲助手" in out and "已开启" in out, "agents 展示列表与开关")
    check("用途 对话" in out and "档位 简单" in out, "agents 展示用途与档位")

    out = manage.cmd_agent_active(cfg, "alpha", True, transport=tr)
    check("已开启" in out and "alpha" in out, "agent on 返回成功文案")

    tr_dead = httpx.MockTransport(dead)
    out = manage.cmd_status(cfg, transport=tr_dead)
    check("睡眠" in out and "未启动" in out, "在线命令不可达时给出明确提示")
    out = manage.cmd_agent_active(cfg, "alpha", True, transport=tr_dead)
    check("睡眠" in out, "开关命令不可达时同样提示")

    # 端口被睡眠态托管页占用时返回 HTML，命令应降级提示而不是崩溃
    out = manage.cmd_status(cfg, transport=httpx.MockTransport(html_page))
    check("睡眠" in out, "托管页返回 HTML 时 status 给出提示而不是报错")


# ---------- 在线：打开 Web 控制台 ----------

def test_webui() -> None:
    # 未开启 webui 通道时给出开启方式，不尝试打开浏览器
    cfg = base_cfg()
    cfg.webui_enabled = False
    out = manage.cmd_webui(cfg, opener=None)
    check("WEBUI_ENABLED 1" in out, "webui 未开启时提示开启命令")

    # 通配绑定地址应换成本机回环地址；服务不可达时不打开浏览器
    cfg = base_cfg()
    cfg.webui_enabled = True
    cfg.webui_host = "0.0.0.0"
    cfg.webui_port = 9001
    opened: list[str] = []
    out = manage.cmd_webui(cfg, transport=httpx.MockTransport(dead),
                           opener=lambda url: opened.append(url) or True)
    check("服务未运行" in out and "http://127.0.0.1:9001" in out,
          "webui 不可达时给出启动指引与本机地址")
    check(not opened, "webui 不可达时不打开浏览器")

    # 服务可达时打开管理台，并同时给出聊天台地址
    out = manage.cmd_webui(cfg, transport=httpx.MockTransport(handler),
                           opener=lambda url: opened.append(url) or True)
    check(opened == ["http://127.0.0.1:9001"], "webui 可达时打开管理台地址")
    check("已在默认浏览器打开" in out, "webui 成功打开时给出结果提示")
    check("http://127.0.0.1:9001/chat.html" in out, "webui 同时输出聊天台地址")

    # --no-open 等价于 opener 为空：只打印地址
    out = manage.cmd_webui(cfg, transport=httpx.MockTransport(handler), opener=None)
    check("请手动复制" in out and "http://127.0.0.1:9001" in out,
          "webui --no-open 只输出地址")

    # 睡眠态托管页同样可以打开（页面提供唤醒按钮）
    out = manage.cmd_webui(cfg, transport=httpx.MockTransport(html_page), opener=None)
    check("管理台" in out and "服务未运行" not in out, "托管页返回 HTML 时仍可打开控制台")


# ---------- 子进程冒烟（真实进程跑通离线命令） ----------

def test_smoke() -> None:
    r = subprocess.run(
        [sys.executable, "manage.py", "config", "show"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )
    check(r.returncode == 0 and "【常用配置】" in r.stdout and "WEBUI_PORT" in r.stdout,
          "子进程 config show")
    r = subprocess.run(
        [sys.executable, "manage.py", "config", "check"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )
    check(r.returncode == 0, "子进程 config check")
    r = subprocess.run(
        [sys.executable, "manage.py", "status"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )
    check(r.returncode == 0 and r.stdout.strip(), "子进程 status 可运行")
    r = subprocess.run(
        [sys.executable, "manage.py", "webui", "--no-open"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )
    check(r.returncode == 0 and ("管理台" in r.stdout or "控制台" in r.stdout),
          "子进程 webui --no-open 可运行")


def main() -> None:
    test_show()
    test_check()
    test_set()
    test_online()
    test_webui()
    test_smoke()
    print("\n全部通过")


main()
