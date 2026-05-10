"""
命令行入口：把 `build_index` 暴露为可执行命令，便于运维与手工重建（KM-102）。

Typer 说明：
- 本文件定义 `app = typer.Typer()`，下面用 `@app.command("子命令名")` 注册多个子命令。
- 若整个应用只有一个子命令，Typer 会把参数「摊平」到根命令；为避免混淆，这里保留了
  `version` 子命令，使 `know-me-index build-index` 以子命令形式存在。

入口点（见 pyproject.toml [project.scripts]）：
- `know-me-index = "know_me.cli:app"`：安装后可直接敲 `know-me-index`，等价于运行该 Typer app。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from know_me.pipeline import build_index
from know_me.settings import IndexSettings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Know Me — E01 索引构建 CLI")


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
    embed_backend: str | None = typer.Option(
        None,
        "--embed-backend",
        help="覆盖环境变量 KNOW_ME_EMBED_BACKEND：ollama / fake",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    构建索引：封装 IndexSettings.from_env + build_index。

    embed_backend 若为 None，则完全遵循环境变量；若传入则临时覆盖（不修改进程外配置）。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    if embed_backend is not None:
        # frozen dataclass 的浅拷贝替换：只改 embed_backend 字段
        from dataclasses import replace

        env = replace(env, embed_backend=embed_backend)
    try:
        stats = build_index(env, reset=reset)
    except Exception as e:
        logging.getLogger(__name__).exception("索引构建失败：%s", e)
        # Typer 非零退出码：shell 脚本可据此中断 CI
        raise typer.Exit(code=1) from e
    typer.echo(f"完成：{stats}")


@app.command("version")
def version_cmd() -> None:
    """打印包版本号。"""
    from know_me import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
