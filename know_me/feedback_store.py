"""
E05 — KM-503：将点赞/点踩等反馈追加写入本地 JSONL（可选能力）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_feedback(path: Path, row: dict[str, Any]) -> None:
    """追加一行 JSON；必要时创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
