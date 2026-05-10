from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from know_me.pipeline import build_index
from know_me.settings import IndexSettings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Know Me — E01 索引构建 CLI")


def _configure_logging(verbose: bool) -> None:
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
    embed_backend: str | None = typer.Option(
        None,
        "--embed-backend",
        help="覆盖环境变量 KNOW_ME_EMBED_BACKEND：ollama / fake",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """一键/命令式重建索引（KM-102）；失败时非零退出码。"""
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    if embed_backend is not None:
        from dataclasses import replace

        env = replace(env, embed_backend=embed_backend)
    try:
        stats = build_index(env, reset=reset)
    except Exception as e:
        logging.getLogger(__name__).exception("索引构建失败：%s", e)
        raise typer.Exit(code=1) from e
    typer.echo(f"完成：{stats}")


@app.command("version")
def version_cmd() -> None:
    """打印包版本（用于确认 Typer 多子命令入口）。"""
    from know_me import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
