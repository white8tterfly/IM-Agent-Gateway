# 测试管理端点：状态 / Agent 列表与开关 / 任务事件 / 日志尾部 / 配置脱敏（FastAPI TestClient + Fake，无网络）
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.manage.api import create_router
from im_agent_gateway.manage.service import ManageService
from im_agent_gateway.router.dispatcher import MessageRouter


class FakeAgent(BaseAgent):
    """模拟 Agent：返回带用量的固定结果，不做真实任务。"""

    usage_text = "tokens 输入 10 / 输出 20"

    def __init__(self, name: str, label: str, available: bool = True) -> None:
        super().__init__()
        self.name = name
        self.label = label
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def submit(self, chat_key: str, query: str, cwd: str = "", on_output=None, model: str = ""):
        async def _done() -> AgentResult:
            if on_output is not None:
                await on_output(f"[{query}]")
            return AgentResult(ok=True, text="done", usage=FakeAgent.usage_text)
        return _done()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False


class FakeChannel(IMChannel):
    """模拟通道：只记录回复文本，不建立连接。"""

    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.sent.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.sent.append(content)

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1", user_id="u1",
            chat_id="c1", chat_type="single", msg_type="text",
            content=content, reply_token="r1",
        )


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"
        log = Path(td) / "run.log"
        log.write_text("line1\nline2\nline3\n", encoding="utf-8")

        cfg = Config(bot_id="", secret="")
        cfg.env_file = env
        cfg.jev_route = False
        cfg.sf_api_key = "sk-test-secret"        # 敏感：应脱敏
        cfg.feishu_app_secret = "fs-test-secret"  # 敏感：应脱敏
        cfg.qodercn_pat = "pat-test-123"          # 敏感（PAT 凭证）：应脱敏
        cfg.bot_id = "bot-123"                    # 非敏感 ID：应明文展示

        registry = AgentRegistry()
        registry.register(FakeAgent("alpha", "甲"), active=True)
        registry.register(FakeAgent("beta", "乙", available=False), active=False)

        router = MessageRouter(cfg, registry)
        service = ManageService(cfg, registry, router, gateway=None, log_file=log)
        router.on_task_event = service.on_task_event

        app = FastAPI()
        app.include_router(create_router(service))
        client = TestClient(app)

        # GET /api/status
        r = client.get("/api/status")
        check(r.status_code == 200, "status 返回 200")
        body = r.json()
        check(body["state"] == "running", "status 标记在线态")
        check(body["idle"]["exit_enabled"] is False and "idle_sec" in body["idle"], "status 含空闲信息")
        check(body["channels"] == [], "无网关时通道列表为空")

        # GET /api/agents
        agents = client.get("/api/agents").json()
        check(len(agents) == 2, "agents 返回全部注册项")
        check(agents[0]["name"] == "alpha" and agents[0]["active"] is True
              and agents[0]["available"] is True, "agents 含 alpha 开关与可用状态")
        check(agents[1]["available"] is False and agents[1]["active"] is False, "agents 含 beta 不可用状态")
        check(all(k in agents[0] for k in ("name", "label", "kind", "active", "available", "routing_hint")),
              "agents 字段齐全")

        # POST /api/agents/{name}/active：与 IM 指令同一管理命令层，含 .env 写回
        r = client.post("/api/agents/alpha/active", json={"active": False})
        check(r.json()["ok"] is True, "关闭 alpha 成功")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=\n", "关闭后写回空活跃列表")
        r = client.post("/api/agents/alpha/active", json={"active": True})
        check(r.json()["ok"] is True, "重新开启 alpha 成功")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=alpha\n", "开启后写回活跃列表")
        r = client.post("/api/agents/nope/active", json={"active": True})
        check(r.json()["ok"] is False and "未注册" in r.json()["error"], "未知 Agent 返回错误")

        # 任务事件：跑一条消息后 tasks 里出现最近任务
        tasks = client.get("/api/tasks").json()
        check(tasks["inflight"] == 0 and tasks["recent"] == [], "初始无任务记录")
        ch = FakeChannel()
        ch.on_message = router.handle_msg
        await ch.on_message(ch.make_msg("你好"))
        tasks = client.get("/api/tasks").json()
        check(tasks["inflight"] == 0, "任务完成后 inflight 归零")
        check(len(tasks["recent"]) == 1, "最近任务进入环形缓冲")
        evt = tasks["recent"][0]
        check(evt["agent"] == "alpha" and evt["ok"] is True, "任务事件含 Agent 名与结果状态")
        check(evt["usage"] == FakeAgent.usage_text, "任务事件含用量")
        check(evt["model"] == "fake-model" and evt["im"] == "fake", "任务事件含模型与来源通道")
        check("elapsed_sec" in evt and "started_at" in evt, "任务事件含开始时刻与耗时")

        # MessageRouter 构造参数注入 on_task_event 也可用（router 契约）
        events: list[dict] = []
        router2 = MessageRouter(cfg, registry, on_task_event=events.append)
        ch2 = FakeChannel()
        ch2.on_message = router2.handle_msg
        await ch2.on_message(ch2.make_msg("在吗"))
        check(len(events) == 1 and events[0]["chat_key"] == "s:u1",
              "MessageRouter 支持 on_task_event 构造注入")

        # GET /api/logs?lines=N
        r = client.get("/api/logs?lines=2")
        check(r.json()["lines"] == ["line2", "line3"], "logs 返回尾部 N 行")

        # GET /api/config：key/secret/token/PAT 类值脱敏
        view = client.get("/api/config").json()
        check(view["sf_api_key"] == "******", "sf_api_key 脱敏")
        check(view["feishu_app_secret"] == "******", "feishu_app_secret 脱敏")
        check(view["qodercn_pat"] == "******", "PAT 凭证脱敏")
        check(view["secret"] == "", "空凭证保持空串（不误显示为已配置）")
        check(view["bot_id"] == "bot-123", "非敏感 ID 明文展示")
        check(view["channels"] == ["wecom"] and "idle_exit_min" in view, "常规配置正常展示")

        # 日志文件缺失 → 空列表
        svc2 = ManageService(cfg, registry, router, log_file=Path(td) / "none.log")
        check(svc2.logs_tail(10) == [], "日志文件缺失时返回空列表")

        # gateway 通道连接状态
        gw = Gateway()
        gw.register(FakeChannel())
        svc3 = ManageService(cfg, registry, router, gateway=gw, log_file=log)
        st = svc3.status()
        check(st["channels"] == [{"name": "fake", "connected": True}], "status 含通道连接状态")

        await router.close()
        await router2.close()

    print("\n全部通过")


asyncio.run(main())
