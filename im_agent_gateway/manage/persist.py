"""配置持久化：把运行时变更写回项目 .env。

原则：
- 只写项目目录的 .env（由调用方传入路径，通常为 Config.env_file），
  绝不触碰上一级凭证文件
- 已有 KEY= 行原位替换，注释、空行、其余键与行序原样保留
- 不存在的键追加到文件末尾
- 先写临时文件再 os.replace 原子替换，避免其他进程读到写一半的内容
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def set_env_value(path: Path, key: str, value: str) -> None:
    """把 key=value 写入 .env 文件（已有则原位替换，没有则末尾追加）。

    - 未改动行的字节原样保留（含 CRLF 行尾）
    - 原子落盘：写 <name>.tmp 后 os.replace，中途失败不会破坏原文件
    """
    path = Path(path)
    if path.is_file():
        # newline="" 关闭换行归一，保留每行原始行尾，写回时其余行字节不变
        with path.open("r", encoding="utf-8", newline="") as f:
            text = f.read()
    else:
        text = ""

    # 精确匹配「行首空白 + KEY + 可选空白 + =」，不会误伤 KEY_X= 这类前缀相近的键
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")

    lines = text.splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        # 拆出行尾（\r\n / \n / 无），只重写键值内容，行尾跟随原行
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        body = line[: len(line) - len(ending)]
        if key_re.match(body):
            lines[i] = f"{key}={value}{ending}"
            replaced = True
            break

    if not replaced:
        if lines and not text.endswith(("\n", "\r")):
            # 原文件末行没有换行：补上，避免追加的键黏在上一行尾部
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(lines))
    # 同盘 os.replace 具备原子性：并发写时最后写入者胜出，不会产生半截文件
    os.replace(tmp, path)
