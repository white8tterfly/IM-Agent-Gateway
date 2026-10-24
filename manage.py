"""管理 CLI：本地查看 / 校验 / 修改配置（离线可用），在线命令走管理端点。

与 IM 指令、Web 管理台共享同一套管理命令层与端点，三个入口行为一致：
- 离线命令直接读项目 .env（config show / check / set）
- 在线命令打 webui 通道的管理端点（status / agents / agent on|off），
  服务睡眠中或未启动时给出明确提示
"""

from __future__ import annotations

import argparse
import shutil
import sys

import httpx

from im_agent_gateway.config import Config, load_config
from im_agent_gateway.manage.commands import ConfigOps
from im_agent_gateway.manage.service import config_grouped_view

# 在线命令不可达时的统一提示（服务睡眠、未启动或 Web 控制台未开启）
OFFLINE_TIP = (
    "服务睡眠中或未启动。"
    "在线命令需要服务在运行且开启 Web 控制台（WEBUI_ENABLED=1）："
    "可给机器人发条消息唤醒服务，或在服务所在电脑上执行 python main.py。"
)

# 智能体用途与复杂度档位的中文展示名（agents 列表输出用）
KIND_CN = {"chat": "对话", "coding": "编码", "office": "办公"}
LEVEL_CN = {"simple": "简单", "mid": "中等", "complex": "复杂"}


# ---------- 离线命令 ----------

def cmd_config_show(cfg: Config) -> str:
    """展示当前生效配置：常用/高级分组，每项带说明（与 /api/config 同一视图）。"""
    view = config_grouped_view(cfg)
    lines: list[str] = []
    # 三组依次渲染：常用配置 → 高级设置 → 未登记（只读）；空组跳过不输出标题
    for title, group in (("常用配置", view["common"]),
                         ("高级设置", view["advanced"]),
                         ("未登记（只读）", view["unknown"])):
        if not group:
            continue
        lines.append(f"【{title}】")
        for it in group:
            lines.append(f"{it['label']}（{it['key']}）= {it['value']}")
            lines.append(f"  说明：{it['desc']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def cmd_config_check(cfg: Config) -> str:
    """校验通道必填项、白名单与智能体可用性，输出可读建议。

    「问题」只统计影响服务启动或消息可达的硬伤（通道缺必填、白名单冲突）；
    智能体缺凭证 / 缺命令行属于可用性提示，不算配置问题。
    """
    lines: list[str] = ["配置检查", f"通道（{len(cfg.channels)} 个）：{'、'.join(cfg.channels) or '无'}"]
    problems: list[str] = []

    # 通道必填项
    if "wecom" in cfg.channels:
        miss = [k for k, v in (("BOT_ID", cfg.bot_id), ("SECRET", cfg.secret)) if not v]
        lines.append(f"  - 企微（wecom）：{'必填项齐全' if not miss else '缺少 ' + '、'.join(miss)}")
        problems.extend(f"企微（wecom）缺少 {k}" for k in miss)
    if "feishu" in cfg.channels:
        miss = [k for k, v in (("FEISHU_APP_ID", cfg.feishu_app_id),
                               ("FEISHU_APP_SECRET", cfg.feishu_app_secret)) if not v]
        lines.append(f"  - 飞书（feishu）：{'必填项齐全' if not miss else '缺少 ' + '、'.join(miss)}")
        problems.extend(f"飞书（feishu）缺少 {k}" for k in miss)
    if "webui" in cfg.channels:
        lines.append(f"  - Web 控制台（webui）：http://{cfg.webui_host}:{cfg.webui_port}")

    # 白名单
    if cfg.allowed_users:
        lines.append(f"白名单：{'、'.join(sorted(cfg.allowed_users))}")
        if "webui" in cfg.channels and cfg.webui_user_id not in cfg.allowed_users:
            problems.append(
                f"Web 控制台已接入，但允许名单里没有 {cfg.webui_user_id}，"
                "聊天台的消息会被拒绝；请把 webui 加进 ALLOWED_USERS")
    else:
        lines.append("白名单：未设置（所有人可用）")

    # 智能体可用性（提示，不算问题）
    lines.append(f"智能体（{len(cfg.agents)} 个）：{'、'.join(cfg.agents) or '无'}")
    notes: list[str] = []
    if "siliconflow" in cfg.agents and not cfg.sf_api_key:
        notes.append("  - siliconflow：未配置 API_KEY，暂不可用")
    if "qoder" in cfg.agents and shutil.which(cfg.qoder_bin) is None:
        notes.append(f"  - qoder：未找到命令行工具（{cfg.qoder_bin}），暂不可用")
    if "workbuddy" in cfg.agents and shutil.which(cfg.workbuddy_bin) is None:
        notes.append(f"  - workbuddy：未找到命令行工具（{cfg.workbuddy_bin}），暂不可用")
    lines.extend(notes or ["  - 已声明的智能体均满足前置条件"])

    # 睡眠托管组合提示
    if cfg.watch_enabled and cfg.watch_http_enabled and "webui" not in cfg.channels:
        lines.append("提示：睡眠态托管已开启但 webui 通道未接入，唤醒后服务端没有控制台页面")

    if problems:
        lines.append(f"结论：发现 {len(problems)} 个问题")
        lines.extend(f"  - {p}" for p in problems)
    else:
        lines.append("结论：一切正常")
    return "\n".join(lines)


def cmd_config_set(cfg: Config, key: str, value: str) -> str:
    """把键值写回项目 .env：走统一命令层 ConfigOps（与 Web 管理台同一实现）。

    校验口径与 POST /api/config 完全一致：未登记键拒绝修改，
    敏感项留空视为「不修改」而非清空，避免误清凭证。
    """
    err, hot = ConfigOps.set(cfg, key, value)
    if err is not None:
        return err
    if hot:
        return f"已写入 {key.strip().upper()}={value} 到 {cfg.env_file}（该配置可热生效，运行中的服务会即时采用新值）"
    return f"已写入 {key.strip().upper()}={value} 到 {cfg.env_file}（重启服务后生效）"


# ---------- 在线命令（管理端点） ----------

def _fetch(cfg: Config, path: str, transport=None, method: str = "GET",
           json_body: dict | None = None):
    """打管理端点；网络不可达时抛 httpx.HTTPError，由调用方统一提示。"""
    with httpx.Client(base_url=f"http://{cfg.webui_host}:{cfg.webui_port}",
                      transport=transport, timeout=3.0) as c:
        return c.request(method, path, json=json_body).json()


def cmd_status(cfg: Config, transport=None) -> str:
    try:
        st = _fetch(cfg, "/api/status", transport)
    except httpx.HTTPError:
        return OFFLINE_TIP
    channels = "、".join(
        f"{c['name']}（{'已连接' if c['connected'] else '未连接'}）"
        for c in st.get("channels", [])) or "无"
    lines = [
        "服务状态：运行中",
        f"消息通道：{channels}",
        f"智能体：共 {st['agents']['total']} 个，已开启 {st['agents']['active']} 个",
    ]
    idle = st.get("idle") or {}
    if idle.get("exit_enabled"):
        remain = max(0, idle["idle_min"] * 60 - idle.get("idle_sec", 0))
        lines.append(f"空闲自动休息：约 {remain // 60} 分钟内没有新消息将自动休息")
    return "\n".join(lines)


def cmd_agents(cfg: Config, transport=None) -> str:
    try:
        agents = _fetch(cfg, "/api/agents", transport)
    except httpx.HTTPError:
        return OFFLINE_TIP
    lines = [f"智能体（{len(agents)} 个）："]
    for a in agents:
        state = "已开启" if a["active"] else "已关闭"
        note = "" if a["available"] else "（暂不可用）"
        # 用途与档位展示为通俗中文；旧版服务可能不带 complexity 字段，缺省视为未配置
        kind = KIND_CN.get(a.get("kind", ""), a.get("kind", "") or "未设置")
        level = a.get("complexity", "")
        level = LEVEL_CN.get(level, level) if level else ""
        extra = f" · 用途 {kind}" + (f" · 档位 {level}" if level else "")
        lines.append(f"- {a['label']}（{a['name']}）：{state}{note}{extra}")
    lines.append("开关方式：python manage.py agent on|off <名称>")
    return "\n".join(lines)


def cmd_agent_active(cfg: Config, name: str, active: bool, transport=None) -> str:
    """开关智能体：与 IM 指令、Web 管理台同一端点，写回 .env。"""
    try:
        res = _fetch(cfg, f"/api/agents/{name}/active", transport,
                     method="POST", json_body={"active": active})
    except httpx.HTTPError:
        return OFFLINE_TIP
    if not res.get("ok"):
        return f"操作没有成功：{res.get('error') or '未知原因'}"
    return f"已{'开启' if active else '关闭'} {name}（已写回 .env，重启后保持）"


# ---------- 命令行入口 ----------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="manage.py", description="IM-Agent Gateway 管理命令行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cfg = sub.add_parser("config", help="配置管理（离线可用）")
    cfg_sub = p_cfg.add_subparsers(dest="sub", required=True)
    cfg_sub.add_parser("show", help="展示当前生效配置（敏感值已隐藏）")
    cfg_sub.add_parser("check", help="检查配置并给出建议")
    p_set = cfg_sub.add_parser("set", help="修改配置并写回项目 .env")
    p_set.add_argument("key", help="配置键（大小写不限，自动转大写）")
    p_set.add_argument("value", help="配置值")

    sub.add_parser("status", help="查看服务状态（需要服务在运行）")
    sub.add_parser("agents", help="查看智能体列表（需要服务在运行）")
    p_agent = sub.add_parser("agent", help="开启 / 关闭智能体（需要服务在运行）")
    p_agent.add_argument("action", choices=["on", "off"])
    p_agent.add_argument("name", help="智能体注册名")

    args = parser.parse_args(argv)

    # 配置加载：残缺时 check 仍可用（把缺失项作为检查结果输出），其余命令退出
    try:
        cfg = load_config()
    except RuntimeError as exc:
        if args.cmd == "config" and args.sub == "check":
            print(f"配置检查\n- {exc}\n请先补齐必填配置，再执行其他命令")
            return 0
        print(f"配置加载失败：{exc}")
        return 1

    if args.cmd == "config":
        if args.sub == "show":
            print(cmd_config_show(cfg))
        elif args.sub == "check":
            print(cmd_config_check(cfg))
        else:
            print(cmd_config_set(cfg, args.key, args.value))
    elif args.cmd == "status":
        print(cmd_status(cfg))
    elif args.cmd == "agents":
        print(cmd_agents(cfg))
    else:
        print(cmd_agent_active(cfg, args.name, args.action == "on"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
