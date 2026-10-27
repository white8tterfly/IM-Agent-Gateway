# 配置元数据表与分组视图测试：管理台配置区可编辑化的后端基础（无网络依赖）
# 覆盖：元数据表完整性、常用/高级分组、值格式化、脱敏口径、unknown 归类、ConfigOps 写回
# 用法：python scripts/test_config_meta.py
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import fields

from im_agent_gateway.config import Config
from im_agent_gateway.manage import config_meta
from im_agent_gateway.manage.commands import ConfigOps
from im_agent_gateway.manage.service import _SENSITIVE_NAME_RE, config_grouped_view


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def main() -> None:
    meta = config_meta.CONFIG_META

    # ===== 一、元数据表完整性 =====
    declared = {it.field for it in meta}
    config_fields = {f.name for f in fields(Config)} - {"env_file"}  # env_file 是内部字段
    check(declared == config_fields,
          "元数据表覆盖全部 Config 可配置字段（env_file 除外，多登记或缺登记都不允许）")
    keys = [it.key for it in meta]
    check(len(keys) == len(set(keys)), "key 唯一不重复")
    check(all(k == k.upper() for k in keys), "key 全部大写（写回 .env 的键名）")
    check(all(it.label and it.desc for it in meta), "每项都有通俗名称与说明")

    # ===== 二、脱敏口径 =====
    check(all(_SENSITIVE_NAME_RE.search(it.field) for it in meta if it.sensitive),
          "声明的敏感项全部命中脱敏正则（口径一致）")
    not_sensitive_tokens = [it for it in meta
                            if not it.sensitive and _SENSITIVE_NAME_RE.search(it.field)]
    check({it.key for it in not_sensitive_tokens} == {"QODER_MAX_OUTPUT_TOKENS", "SF_MAX_TOKENS"},
          "max_tokens 两项显式纠正正则误伤（输出上限不是凭证，明文展示）")

    # ===== 三、常用清单与布尔项 =====
    common_keys = [it.key for it in meta if not it.advanced]
    check(common_keys == [
        "AGENTS", "ENABLED_AGENTS", "DEFAULT_AGENT", "AUTO_ROUTE", "JEV_ROUTE",
        "AGENT_KINDS", "AGENT_COMPLEXITY", "COMPLEXITY_ROUTE", "ALLOWED_USERS",
        "IDLE_EXIT", "IDLE_EXIT_MIN", "WAKE_REPLY", "WATCH_AUTOSTART",
        "WORKBUDDY_CWD", "WEBUI_ENABLED", "FIRST_USE_GUIDE", "LANGUAGE", "CHANNELS",
    ], "常用配置 18 项前置（其余全部为高级）")
    bool_keys = {it.key for it in meta if it.type == "bool"}
    check(bool_keys == {
        "AUTO_ROUTE", "JEV_ROUTE", "COMPLEXITY_ROUTE", "IDLE_EXIT",
        "SF_INCLUDE_REASONING", "WATCH_ENABLED", "WATCH_AUTOSTART",
        "WATCH_HTTP_ENABLED", "WEBUI_ENABLED", "FIRST_USE_GUIDE",
    }, "布尔开关项集合正确（页面渲染成开关）")

    # ===== 四、分组视图：分组、脱敏、值格式化 =====
    cfg = Config(bot_id="bot-123", secret="")
    cfg.sf_api_key = "sk-test"
    cfg.allowed_users = {"u1", "u2"}
    cfg.agent_kinds = {"workbuddy": "office"}
    view = config_grouped_view(cfg)
    check(set(view) == {"common", "advanced", "unknown"}, "视图返回三组")
    common = {it["key"]: it for it in view["common"]}
    advanced = {it["key"]: it for it in view["advanced"]}
    check("AGENTS" in common and common["AGENTS"]["value"] == "siliconflow,qoder,workbuddy",
          "常用组含 AGENTS，列表值转逗号串")
    check(common["ALLOWED_USERS"]["value"] == "u1,u2", "集合值转逗号串")
    check(common["AGENT_KINDS"]["value"] == "workbuddy=office", "映射值转 k=v 逗号串")
    check(common["AGENTS"]["label"] and common["AGENTS"]["desc"],
          "每项带通俗名称与说明")
    check(advanced["API_KEY"]["value"] == "******", "敏感值脱敏为 ******")
    check(advanced["SF_MAX_TOKENS"]["value"] == "4096", "max_tokens 明文（纠正误伤）")
    check(all(not it["sensitive"] or it["value"] in ("", "******") for it in view["advanced"]),
          "敏感项值只出现空或掩码")

    # ===== 五、unknown：未登记字段自动归类（只读展示） =====
    partial = [it for it in meta if it.key != "LOG_LEVEL"]
    view2 = config_grouped_view(cfg, meta=partial)
    unknown = {it["field"] for it in view2["unknown"]}
    check("log_level" in unknown, "漏登记字段自动归 unknown（不凭空消失）")
    unk = next(it for it in view2["unknown"] if it["field"] == "log_level")
    check(unk["desc"] == "（暂无说明）", "unknown 项说明为暂无说明")

    # ===== 六、ConfigOps.set：统一写回命令层 =====
    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"
        env.write_text("AGENTS=old\n", encoding="utf-8")
        cfg.env_file = env

        err, hot = ConfigOps.set(cfg, "agents", "workbuddy")
        check(err is None, "登记键写回成功（键名大小写不敏感）")
        check(hot is False, "AGENTS 不在热生效清单，需重启")
        check("AGENTS=workbuddy" in env.read_text(encoding="utf-8"),
              "原位替换写回 .env")

        err, _ = ConfigOps.set(cfg, "NOT_A_KEY", "x")
        check(err is not None and "不支持" in err, "未登记键拒绝修改")

        err, _ = ConfigOps.set(cfg, "API_KEY", "")
        check(err is not None and "清空" in err, "敏感项空值拒绝（留空=不改）")

        err, hot = ConfigOps.set(cfg, "API_KEY", "sk-new")
        check(err is None and "API_KEY=sk-new" in env.read_text(encoding="utf-8"),
              "敏感项填新值正常更换")
        check(hot is True and cfg.sf_api_key == "sk-new", "可热生效项同时更新内存 cfg")

        bad = Path(td) / "no-dir" / ".env"
        cfg.env_file = bad
        err, _ = ConfigOps.set(cfg, "AGENTS", "x")
        check(err is not None and "失败" in err, "写入失败返回可读错误文案")

    print("\n全部通过")


main()
