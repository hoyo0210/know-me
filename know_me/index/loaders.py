"""
语料加载器：只负责「从磁盘读出结构化字段」，不做切分、不做嵌入。

目录约定（与 backlog KM-101 一致）：
  corpus_root/
    about_me/**/*.md
    faq/**/*.md
    hr_faq/**/*.md
    hr_screening/**/*.md

每个 .md 可选带 YAML front matter（--- ... ---），正文在第二个 --- 之后；
python-frontmatter 会把 YAML 解析为 dict，正文为字符串。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import frontmatter

from know_me.core.types import CORPUS_KINDS, RawDocument

log = logging.getLogger(__name__)


def iter_markdown_documents(corpus_root: Path) -> Iterator[RawDocument]:
    """
    按固定类型目录遍历所有 Markdown，产出 RawDocument 流。

    为何用生成器（yield）：语料多时不必一次性占满内存。
    """
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"语料根目录不存在或不是目录：{corpus_root}")

    for kind in CORPUS_KINDS:
        base = corpus_root / kind
        if not base.is_dir():
            # 某类语料尚未创建目录时跳过，不算错误（例如暂时没有 hr_faq）
            log.debug("跳过缺失的类型目录：%s", base)
            continue
        # sorted：保证每次构建遍历顺序一致，便于 diff 日志与排查
        for path in sorted(base.rglob("*.md")):
            if path.is_dir():
                continue
            # post.content = 正文；post.metadata = YAML 头（可能为空 dict）
            post = frontmatter.load(path)
            # source 用「相对 corpus_root 的路径」便于人类阅读与日志展示
            rel = path.relative_to(corpus_root).as_posix()
            # 日期默认值在 splitting.build_chunk_metadata 中用文件 mtime 计算，loader 只负责原文与 YAML
            yield RawDocument(
                corpus_kind=kind,
                source=rel,
                path=path,
                body=str(post.content or ""),
                front_matter=dict(post.metadata or {}),
            )
