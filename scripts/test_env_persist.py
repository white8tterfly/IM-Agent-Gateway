# 测试 .env 写回：改值保注释、缺键追加、原子替换、只动项目 .env（不依赖网络）
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.config import ENV_FILE, PARENT_ENV_FILE
from im_agent_gateway.manage.persist import set_env_value


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def main() -> None:
    # 全程用临时目录里的 .env，绝不触碰真实配置文件
    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"

        # 1. 文件不存在：新建并写入
        set_env_value(env, "ENABLED_AGENTS", "a,b")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=a,b\n", "文件不存在时新建写入")

        # 2. 已有键：原位替换，注释/空行/其余键与行序原样保留
        env.write_text("# 顶部注释\n\nCHANNELS=wecom\nOTHER=1\n", encoding="utf-8")
        set_env_value(env, "CHANNELS", "feishu,webui")
        text = env.read_text(encoding="utf-8")
        check(text == "# 顶部注释\n\nCHANNELS=feishu,webui\nOTHER=1\n", "改值原位替换且其余内容不变")

        # 3. 复杂值（含分号/空格/冒号）整行写入
        env.write_text("WORKSPACES=old\n", encoding="utf-8")
        set_env_value(env, "WORKSPACES", "demo=e:/x y;work=e:/w")
        check(env.read_text(encoding="utf-8") == "WORKSPACES=demo=e:/x y;work=e:/w\n", "复杂值整行写入")

        # 4. 末行无换行：追加不黏行
        env.write_text("A=1", encoding="utf-8")
        set_env_value(env, "K", "v")
        check(env.read_text(encoding="utf-8") == "A=1\nK=v\n", "末行无换行时追加不黏行")

        # 5. 空值正常写入
        env.write_text("A=1\nK=old\n", encoding="utf-8")
        set_env_value(env, "K", "")
        check(env.read_text(encoding="utf-8") == "A=1\nK=\n", "空值正常写入")

        # 6. CRLF 行尾保留（未改动的行字节不变）
        env.write_bytes(b"A=1\r\nK=old\r\nB=2\r\n")
        set_env_value(env, "K", "new")
        check(env.read_bytes() == b"A=1\r\nK=new\r\nB=2\r\n", "CRLF 行尾原样保留")

        # 7. 相近前缀键不误伤：只改精确匹配的键
        env.write_text("KEY=1\nKEY_X=2\n", encoding="utf-8")
        set_env_value(env, "KEY", "9")
        check(env.read_text(encoding="utf-8") == "KEY=9\nKEY_X=2\n", "相近前缀键不误伤")

        # 8. 原子替换：经 os.replace 落盘，结束后无 .tmp 残留
        real_replace = os.replace
        calls: list[tuple[str, str]] = []

        def spy(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        os.replace = spy
        try:
            set_env_value(env, "KEY", "10")
        finally:
            os.replace = real_replace
        check(len(calls) == 1 and calls[0][1] == str(env), "写回经 os.replace 原子替换")
        check(not env.with_name(env.name + ".tmp").exists(), "结束后无 .tmp 残留")

        # 9. 写回目标是项目目录的 .env：与上一级凭证文件是不同文件
        repo_root = Path(__file__).resolve().parent.parent
        check(ENV_FILE != PARENT_ENV_FILE and ENV_FILE.parent == repo_root,
              "默认目标为项目 .env，不触碰上一级凭证文件")

    print("\n全部通过")


main()
