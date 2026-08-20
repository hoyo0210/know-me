"""人设加载器：默认读取本地 `persona/`（Git 忽略），示例见仓库根 `persona.example/`。"""

from know_me.persona.loader import get_persona, get_session_opening

__all__ = ["get_persona", "get_session_opening"]
