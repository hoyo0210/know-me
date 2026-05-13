"""兼容旧启动路径 `uvicorn know_me.api_app:app`；请优先使用 `know_me.api.app:app`。"""

from know_me.api.app import app

__all__ = ["app"]
