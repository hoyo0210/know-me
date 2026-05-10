from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import frontmatter

from know_me.types import CORPUS_KINDS, CorpusKind, RawDocument

log = logging.getLogger(__name__)


def iter_markdown_documents(corpus_root: Path) -> Iterator[RawDocument]:
    """
    遍历 corpus_root 下首批支持的类型目录中的 Markdown 文件。

    约定目录：corpus_root/{about_me,faq,hr_faq,hr_screening}/**/*.md
    """
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"语料根目录不存在或不是目录：{corpus_root}")

    for kind in CORPUS_KINDS:
        base = corpus_root / kind
        if not base.is_dir():
            log.debug("跳过缺失的类型目录：%s", base)
            continue
        for path in sorted(base.rglob("*.md")):
            if path.is_dir():
                continue
            post = frontmatter.load(path)
            rel = path.relative_to(corpus_root).as_posix()
            mtime = None
            try:
                mtime_sec = path.stat().st_mtime
                from datetime import datetime, timezone

                mtime = datetime.fromtimestamp(mtime_sec, tz=timezone.utc).date().isoformat()
            except OSError:
                pass
            yield RawDocument(
                corpus_kind=kind,
                source=rel,
                path=path,
                body=str(post.content or ""),
                front_matter=dict(post.metadata or {}),
            )
