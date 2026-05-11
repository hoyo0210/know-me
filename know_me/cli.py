"""
命令行入口：E01 `build-index`、E02 `query`（检索 + 生成）、`version`。

入口点（见 pyproject.toml [project.scripts]）：
- `know-me` / `know-me-index` 均指向本 Typer app。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from know_me.pipeline import build_index
from know_me.rag_answer import answer_with_rag
from know_me.settings import IndexSettings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Know Me — E01/E02 CLI（索引 + RAG 问答）")


def _configure_logging(verbose: bool) -> None:
    """stderr 打日志、stdout 留给统计结果，方便管道重定向。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@app.command("build-index")
def build_index_cmd(
    corpus_root: Path = typer.Option(Path("corpus"), "--corpus-root", help="语料根目录"),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 持久化目录"),
    reset: bool = typer.Option(False, "--reset", help="重建前删除已有集合"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    构建索引：封装 IndexSettings.from_env + build_index。

    嵌入服务依赖环境变量 KNOW_ME_OPENAI_*（见 know_me/settings.py）；未配置模型名将失败并提示。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    try:
        stats = build_index(env, reset=reset)
    except Exception as e:
        logging.getLogger(__name__).exception("索引构建失败：%s", e)
        # Typer 非零退出码：shell 脚本可据此中断 CI
        raise typer.Exit(code=1) from e
    typer.echo(f"完成：{stats}")


@app.command("query")
def query_cmd(
    question: str = typer.Argument(..., help="要向个人知识库提的问题"),
    corpus_root: Path = typer.Option(Path("corpus"), "--corpus-root", help="语料根目录（与索引一致时可不改）"),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 持久化目录"),
    top_k: int | None = typer.Option(None, "--top-k", help="覆盖 KNOW_ME_RAG_TOP_K"),
    json_out: bool = typer.Option(False, "--json", help="将回答与引用以 JSON 打印到 stdout"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E02：向量检索 + 基于片段的 LLM 回答（需 KNOW_ME_OPENAI_EMBED_MODEL 与 KNOW_ME_OPENAI_CHAT_MODEL）。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    if not env.openai_embed_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_EMBED_MODEL。", err=True)
        raise typer.Exit(code=1)
    if not env.openai_chat_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_CHAT_MODEL（对话模型 id）。", err=True)
        raise typer.Exit(code=1)
    try:
        ans = answer_with_rag(env, question, top_k=top_k)
    except Exception as e:
        logging.getLogger(__name__).exception("query 失败：%s", e)
        raise typer.Exit(code=1) from e
    if json_out:
        typer.echo(json.dumps(asdict(ans), ensure_ascii=False, indent=2))
    else:
        typer.echo(ans.answer_text)
        if ans.citations:
            typer.echo("\n---\n引用结构（source / date / distance）：", err=True)
            typer.echo(json.dumps(ans.citations, ensure_ascii=False, indent=2), err=True)


@app.command("version")
def version_cmd() -> None:
    """打印包版本号。"""
    from know_me import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
