# 临时验证：WORKSPACES 解析 + resolve_cli_argv + 模型列表获取
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.config import _parse_workspaces, load_config
from im_agent_gateway.agents.qoder.runner import resolve_cli_argv


def test_parse():
    out = _parse_workspaces("projA=e:/proj-a;e:/proj-b;third=c:/tmp")
    print("parsed:", out)
    assert out["projA"] == Path("e:/proj-a")
    assert out["proj-b"] == Path("e:/proj-b")
    assert out["third"] == Path("c:/tmp")
    print("parse OK")


async def test_models():
    cfg = load_config()
    print("argv:", resolve_cli_argv([cfg.qoder_bin, "--list-models"]))
    proc = await asyncio.create_subprocess_exec(
        *resolve_cli_argv([cfg.qoder_bin, "--list-models"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    import re
    models = []
    for ln in out.decode("utf-8", errors="replace").splitlines():
        ln = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).strip()
        if ln and ln.upper() != "MODEL":
            models.append(ln)
    print("models:", models)
    assert models, "no models"


if __name__ == "__main__":
    test_parse()
    asyncio.run(test_models())
