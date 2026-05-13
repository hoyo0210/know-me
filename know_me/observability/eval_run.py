"""
E05 — KM-502：评测集批量跑 RAG，生成可对比的 JSON 报告。

默认走 `answer_with_rag`（无工具方差，适合 chunk / prompt 调整后的回归）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from know_me.rag.rag_answer import answer_with_rag
from know_me.core.settings import IndexSettings


def load_cases_jsonl(path: Path) -> list[dict[str, Any]]:
    """每行一个 JSON 对象；空行与 `#` 开头行忽略。"""
    if not path.is_file():
        raise FileNotFoundError(f"评测集不存在：{path}")
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def _keyword_hits(answer: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if k and k in answer]


def run_eval_report(
    settings: IndexSettings,
    cases_path: Path,
    *,
    top_k: int | None = None,
) -> dict[str, Any]:
    """对每条用例调用 RAG，汇总延迟、引用与可选关键词命中。"""
    cases = load_cases_jsonl(cases_path)
    rows: list[dict[str, Any]] = []
    for c in cases:
        q = str(c.get("question", "")).strip()
        t0 = time.perf_counter()
        err: str | None = None
        ans_text = ""
        citations: list[dict[str, Any]] = []
        if not q:
            err = "缺少 question 字段"
            latency_ms = 0.0
        else:
            try:
                ans = answer_with_rag(settings, q, top_k=top_k)
                ans_text = ans.answer_text
                citations = ans.citations
            except Exception as e:
                err = str(e)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        expect_kw = c.get("expect_keywords")
        kw_list = expect_kw if isinstance(expect_kw, list) else []
        hits = _keyword_hits(ans_text, [str(x) for x in kw_list]) if kw_list else []

        rows.append(
            {
                "id": c.get("id"),
                "bucket": c.get("bucket"),
                "question": q,
                "latency_ms": latency_ms,
                "error": err,
                "answer_preview": (ans_text[:800] + "…") if len(ans_text) > 800 else ans_text,
                "citation_count": len(citations),
                "chunk_ids": [x.get("chunk_id") for x in citations if isinstance(x, dict)],
                "expect_keywords": kw_list or None,
                "keyword_hits": hits or None,
            },
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": str(cases_path.resolve()),
        "embed_model": settings.openai_embed_model,
        "chat_model": settings.openai_chat_model,
        "results": rows,
    }
