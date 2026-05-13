"""
E05 — KM-501：结构化运行追踪（单行 JSON → stderr）。

由 `KNOW_ME_STRUCTURED_TRACE=1` 控制开关；便于日志采集与回归对比（与 `know-me eval` 互补）。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from know_me import __version__
from know_me.core.settings import IndexSettings


def emit_structured_trace(settings: IndexSettings, record: dict[str, Any]) -> None:
    """输出一条 JSON 日志（整行），字段与 PRD §5.7 对齐；未开启时 no-op。"""
    if not settings.structured_trace_enabled:
        return
    payload: dict[str, Any] = {
        "ts_ms": int(time.time() * 1000),
        "app_version": __version__,
        **record,
    }
    line = json.dumps(payload, ensure_ascii=False, default=str)
    print(line, file=sys.stderr, flush=True)
