"""
从 Markdown 加载人设；支持环境变量 `KNOW_ME_PERSONA_DIR` 覆盖目录（须含 IDENTITY.md / SOUL.md）。

默认目录：**仓库根下的 `persona/`**（与 `know_me` 包同级；该目录默认 Git 忽略，克隆后从 **`persona.example/`** 复制或把 `KNOW_ME_PERSONA_DIR` 指向示例目录）。

IDENTITY.md 使用 YAML front matter：`display_name`、`aliases`（列表）、`session_opening`（多行字符串，可用 `{owner_name}`）。
正文为身份描述，可用 `{owner_name}` 占位。
SOUL.md 为纯 Markdown，可用 `{owner_name}` 指代本人。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import frontmatter

_PERSONA_SETUP_HINT = (
    "克隆后请执行：mkdir -p persona && cp persona.example/IDENTITY.md persona.example/SOUL.md persona/"
    "；或设置 KNOW_ME_PERSONA_DIR 指向含上述文件的目录（例如 persona.example）。"
)


def _default_persona_dir() -> Path:
    override = (os.environ.get("KNOW_ME_PERSONA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # 与 know_me 包同级：<root>/persona（人设不放在包内）
    know_me_pkg = Path(__file__).resolve().parent.parent
    return (know_me_pkg.parent / "persona").resolve()


@dataclass(frozen=True)
class Persona:
    """一次加载后的完整人设。"""

    display_name: str
    aliases: tuple[str, ...]
    session_opening: str
    identity_body: str
    soul_body: str


def _fmt(template: str, owner_name: str) -> str:
    return (template or "").replace("{owner_name}", owner_name).strip()


@lru_cache(maxsize=1)
def get_persona() -> Persona:
    base = _default_persona_dir()
    identity_path = base / "IDENTITY.md"
    soul_path = base / "SOUL.md"
    if not identity_path.is_file():
        raise FileNotFoundError(f"缺少人设文件：{identity_path}。{_PERSONA_SETUP_HINT}")
    if not soul_path.is_file():
        raise FileNotFoundError(f"缺少人设文件：{soul_path}。{_PERSONA_SETUP_HINT}")

    post = frontmatter.loads(identity_path.read_text(encoding="utf-8"))
    meta = dict(post.metadata or {})
    display_name = str(meta.get("display_name") or "").strip()
    if not display_name:
        raise ValueError(f"{identity_path} 的 front matter 须设置非空 display_name")

    raw_aliases = meta.get("aliases")
    if raw_aliases is None:
        aliases_list: list[str] = []
    elif isinstance(raw_aliases, str):
        aliases_list = [raw_aliases.strip()] if raw_aliases.strip() else []
    elif isinstance(raw_aliases, (list, tuple)):
        aliases_list = [str(x).strip() for x in raw_aliases if str(x).strip()]
    else:
        aliases_list = []

    # display_name 默认参与「是否在聊本人」的弱信号
    aliases_merged = tuple(dict.fromkeys([display_name, *aliases_list]))

    opening_tpl = str(meta.get("session_opening") or "").strip()
    if not opening_tpl:
        opening_tpl = "您好，我是{owner_name}。\n\n方便的话，请问您怎么称呼？"
    session_opening = _fmt(opening_tpl, display_name)

    identity_body = _fmt(str(post.content or ""), display_name)
    soul_raw = soul_path.read_text(encoding="utf-8")
    soul_body = _fmt(soul_raw, display_name)

    return Persona(
        display_name=display_name,
        aliases=aliases_merged,
        session_opening=session_opening,
        identity_body=identity_body,
        soul_body=soul_body,
    )


def get_session_opening() -> str:
    return get_persona().session_opening


def clear_persona_cache() -> None:
    """测试或热加载时可调用。"""
    get_persona.cache_clear()
