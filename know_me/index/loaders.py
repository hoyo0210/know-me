"""
语料加载器：只负责「从磁盘读出结构化字段」，不做切分、不做嵌入。

目录约定：
  corpus_root/
    <任意一级子目录名>/**/*.md

在语料根下**自动扫描**全部一级子目录（不含隐藏目录）；每个子目录递归收集 `*.md`。
子目录名写入 metadata 的 `corpus_kind`（历史常用名见 `know_me.core.types.CORPUS_KINDS`）。

每个 .md 可选带 YAML front matter（--- ... ---），正文在第二个 --- 之后；
python-frontmatter 会把 YAML 解析为 dict，正文为字符串。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import frontmatter

from know_me.core.types import RawDocument

log = logging.getLogger(__name__)


def _iter_corpus_subdir_names(corpus_root: Path) -> list[str]:
    """语料根下的一级子目录名，排序后遍历，保证构建顺序稳定。"""
    names: list[str] = []
    try:
        entries = list(corpus_root.iterdir())
    except OSError as e:
        raise FileNotFoundError(f"无法读取语料根目录：{corpus_root}") from e
    for p in sorted(entries, key=lambda x: x.name):
        if p.is_dir() and not p.name.startswith("."):
            names.append(p.name)
    return names


def iter_markdown_documents(corpus_root: Path) -> Iterator[RawDocument]:
    """
    扫描语料根下全部一级子目录，遍历其中 Markdown，产出 RawDocument 流。

    为何用生成器（yield）：语料多时不必一次性占满内存。
    """
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"语料根目录不存在或不是目录：{corpus_root}")

    for kind in _iter_corpus_subdir_names(corpus_root):
        base = corpus_root / kind
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
