"""
命令行入口：E01 `build-index`、E02 `query`、E03 `chat` / `serve`、`version`。

入口点（见 pyproject.toml [project.scripts]）：
- `know-me` / `know-me-index` 均调用 `main()`（会先加载 `.env`，再进入 Typer）。

环境变量加载（重要）：
- `main()` 内先 `_load_dotenv()`：从**当前工作目录**向上查找 `.env` 并 `load_dotenv(override=False)`。
- 因此你在仓库根目录执行 `know-me build-index` 时，根目录的 `.env` 会被读入；
  已存在于**进程环境**的变量不会被覆盖（便于 CI 注入密钥）。
- 若仍提示未配置 `KNOW_ME_OPENAI_EMBED_MODEL`，请确认：`.env` 路径、变量名拼写、或在 shell 里 `export` 后再运行。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import typer
from dotenv import load_dotenv

from know_me.agent.agent_chat import iter_agent_chat_events, run_agent_chat_blocking
from know_me.agent.context_window import refresh_conversation_summary_if_needed
from know_me.agent.prompts_agent import SESSION_OPENING_ASK_IDENTITY
from know_me.observability.eval_run import run_eval_report
from know_me.rag.job_intent import is_greeting_only_message
from know_me.index.pipeline import build_index
from know_me.rag.rag_answer import RAGStreamSession, answer_with_rag
from know_me.agent.sessions import make_chat_session_store
from know_me.core.settings import IndexSettings
from know_me.core.types_rag import RAGAnswer

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Know Me — E01/E02/E03/E05 CLI（索引 + RAG + HTTP + 评测）",
)


def _load_dotenv() -> None:
    """从当前工作目录向上查找 `.env` 并载入（不覆盖已在环境中的变量）。"""
    here = Path.cwd().resolve()
    for d in [here, *list(here.parents)[:16]]:
        candidate = d / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def _try_stdout_line_buffering() -> None:
    """尽量让 stdout 按行 flush，流式输出时终端能逐段显示。"""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass


def _configure_logging(verbose: bool) -> None:
    """stderr 打日志；普通模式下回答正文走 stdout，便于管道重定向。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@app.command("build-index")
def build_index_cmd(
    corpus_root: Path = typer.Option(
        Path("corpus"),
        "--corpus-root",
        help="语料根目录（自动扫描其下各一级子目录中的 Markdown）",
    ),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 持久化目录"),
    reset: bool = typer.Option(False, "--reset", help="重建前删除已有集合"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E01：构建向量索引（加载 → 切分 → 嵌入 → Chroma）。

    依赖：`KNOW_ME_OPENAI_EMBED_MODEL` 等（见 `know_me/settings.py`）。配置来自环境 + 本命令行选项。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    try:
        stats = build_index(env, reset=reset)
    except Exception as e:
        logging.getLogger(__name__).exception("索引构建失败：%s", e)
        raise typer.Exit(code=1) from e
    typer.echo(f"完成：{stats}")


@app.command("query")
def query_cmd(
    question: str = typer.Argument(..., help="要向个人知识库提的问题"),
    corpus_root: Path = typer.Option(
        Path("corpus"),
        "--corpus-root",
        help="语料根目录（自动扫描一级子目录；与建索引时一致）",
    ),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 持久化目录"),
    top_k: int | None = typer.Option(None, "--top-k", help="覆盖 KNOW_ME_RAG_TOP_K"),
    no_stream: bool = typer.Option(False, "--no-stream", help="关闭流式：一次性拉取全文"),
    json_out: bool = typer.Option(False, "--json", help="stdout 输出完整 JSON（自动使用非流式）"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E02：向量检索 + 基于片段的 LLM 回答。

    默认 **流式**：正文 token/片段逐块写到 stdout（便于「边生成边看」）；引用 JSON 仍在末尾写到 stderr。
    `--json` 或 `--no-stream` 时改为非流式，便于脚本解析。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    # 尽早失败，避免进入 HTTP 后才报难懂的 4xx
    if not env.openai_embed_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_EMBED_MODEL。", err=True)
        raise typer.Exit(code=1)
    if not env.openai_chat_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_CHAT_MODEL（对话模型 id）。", err=True)
        raise typer.Exit(code=1)

    buffered = json_out or no_stream
    try:
        if buffered:
            ans = answer_with_rag(env, question, top_k=top_k)
        else:
            _try_stdout_line_buffering()
            if not is_greeting_only_message(question.strip()):
                typer.echo("「正在检索语料并连接模型，请稍候…」", err=True)
            session = RAGStreamSession(env, question, top_k=top_k)
            for part in session.iter_assistant_text():
                sys.stdout.write(part)
                sys.stdout.flush()
            ans = RAGAnswer(
                answer_text=session.full_text,
                retrieved=session.retrieved,
                citations=session.citations,
            )
    except Exception as e:
        logging.getLogger(__name__).exception("query 失败：%s", e)
        raise typer.Exit(code=1) from e

    if json_out:
        typer.echo(json.dumps(asdict(ans), ensure_ascii=False, indent=2))
    elif buffered:
        typer.echo(ans.answer_text)
        if ans.citations:
            typer.echo("\n---\n引用结构（source / date / distance）：", err=True)
            typer.echo(json.dumps(ans.citations, ensure_ascii=False, indent=2), err=True)
    else:
        # 流式：正文已在循环中写出；仅补充引用到 stderr
        if ans.citations:
            typer.echo("\n---\n引用结构（source / date / distance）：", err=True)
            typer.echo(json.dumps(ans.citations, ensure_ascii=False, indent=2), err=True)


@app.command("chat")
def chat_cmd(
    corpus_root: Path = typer.Option(
        Path("corpus"),
        "--corpus-root",
        help="语料根目录（自动扫描一级子目录；与建索引时一致）",
    ),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 持久化目录"),
    top_k: int | None = typer.Option(None, "--top-k", help="覆盖 KNOW_ME_RAG_TOP_K"),
    no_stream: bool = typer.Option(False, "--no-stream", help="每轮关闭流式，一次性打印全文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E03：终端**多轮**对话（Agent + `search_personal_knowledge` / `ask_user_clarify`，与 `POST /chat` 同一套编排）。

    单轮、无工具、纯 RAG 仍可用 `know-me query`。
    退出：空行、`/exit`、`/quit`，或 Ctrl+D / Ctrl+C。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    if not env.openai_embed_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_EMBED_MODEL。", err=True)
        raise typer.Exit(code=1)
    if not env.openai_chat_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_CHAT_MODEL（对话模型 id）。", err=True)
        raise typer.Exit(code=1)

    if not no_stream:
        _try_stdout_line_buffering()

    store = make_chat_session_store(env)
    sid = store.ensure_session(None)
    typer.echo(SESSION_OPENING_ASK_IDENTITY, err=True)
    typer.echo("", err=True)
    typer.echo(
        f"多轮会话已开启（session_id={sid}，最多保留 {env.chat_history_max_turns} 轮）。\n"
        "输入 /exit 或 /quit 结束；Ctrl+D 退出。",
        err=True,
    )
    if env.disclaimer_text.strip():
        typer.echo(env.disclaimer_text.strip(), err=True)

    while True:
        try:
            line = input("你: ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n已退出。", err=True)
            break
        user_text = line.strip()
        if not user_text:
            continue
        if user_text in ("/exit", "/quit"):
            typer.echo("再见。", err=True)
            break

        hist = store.history(sid)
        if env.agent_summary_mode == "lazy":
            refresh_conversation_summary_if_needed(env, store, sid)
        conv_summary, _ = store.get_conversation_summary(sid)
        try:
            if no_stream:

                def _chat_status(ev: dict) -> None:
                    m = ev.get("message")
                    if isinstance(m, str) and m.strip():
                        typer.echo(m, err=True)

                result = run_agent_chat_blocking(
                    env,
                    hist,
                    user_text,
                    top_k=top_k,
                    on_status=_chat_status,
                    preface_shown=True,
                    conversation_summary=conv_summary,
                )
                if result.get("error"):
                    typer.echo(f"错误：{result['error']}", err=True)
                    continue
                typer.echo(result.get("answer", ""))
                cites = result.get("citations") or []
                if cites:
                    typer.echo("\n---\n引用结构（source / date / distance）：", err=True)
                    typer.echo(json.dumps(cites, ensure_ascii=False, indent=2), err=True)
                clarify = result.get("clarify")
                if clarify:
                    typer.echo(f"\n[澄清] {clarify}", err=True)
                ans = str(result.get("answer_stored") or result.get("answer") or "").strip()
                mid_store = str(result.get("message_id") or "").strip() or None
                if ans:
                    store.append_turn(sid, user_text, ans, assistant_message_id=mid_store)
                    if env.agent_summary_mode == "after_turn":
                        refresh_conversation_summary_if_needed(env, store, sid)
            else:
                st: dict[str, object] = {"full": "", "err": False, "mid": ""}
                citations: list[dict] = []
                for ev in iter_agent_chat_events(
                    env,
                    hist,
                    user_text,
                    top_k=top_k,
                    preface_shown=True,
                    conversation_summary=conv_summary,
                ):
                    t = ev.get("type")
                    if t == "status" and isinstance(ev.get("message"), str) and ev["message"].strip():
                        typer.echo(ev["message"], err=True)
                    elif t == "clarify" and isinstance(ev.get("question"), str):
                        typer.echo(f"\n[澄清] {ev['question']}\n", err=True)
                    elif t == "citations" and isinstance(ev.get("items"), list):
                        citations = list(ev["items"])
                    elif t == "delta" and isinstance(ev.get("text"), str):
                        sys.stdout.write(ev["text"])
                        sys.stdout.flush()
                    elif t == "done":
                        st["full"] = str(ev.get("answer_stored") or ev.get("answer") or "")
                        st["mid"] = str(ev.get("message_id") or "")
                    elif t == "error":
                        st["err"] = True
                        typer.echo(f"\n错误：{ev.get('message', '')}\n", err=True)
                typer.echo()
                if not st["err"] and str(st["full"]).strip():
                    mid_s = str(st["mid"] or "").strip() or None
                    store.append_turn(sid, user_text, str(st["full"]), assistant_message_id=mid_s)
                    if env.agent_summary_mode == "after_turn":
                        refresh_conversation_summary_if_needed(env, store, sid)
                if citations:
                    typer.echo("---\n引用结构（source / date / distance）：", err=True)
                    typer.echo(json.dumps(citations, ensure_ascii=False, indent=2), err=True)
        except Exception as e:
            logging.getLogger(__name__).exception("chat 失败：%s", e)
            typer.echo(f"本轮失败：{e}", err=True)


@app.command("eval")
def eval_cmd(
    cases: Path = typer.Option(Path("eval/cases.jsonl"), "--cases", help="JSONL 评测用例（每行一个 JSON）"),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="报告 JSON 路径；默认 eval/report-<UTC>.json",
    ),
    corpus_root: Path = typer.Option(
        Path("corpus"),
        "--corpus-root",
        help="语料根目录（自动扫描其下各一级子目录中的 Markdown）",
    ),
    chroma_path: Path = typer.Option(Path("data/chroma"), "--chroma-path", help="Chroma 目录"),
    top_k: int | None = typer.Option(None, "--top-k", help="覆盖 KNOW_ME_RAG_TOP_K"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E05：对评测集逐条调用非流式 RAG，生成含延迟与 chunk 引用的 JSON 报告（KM-502 回归基线）。
    """
    _configure_logging(verbose)
    env = IndexSettings.from_env(corpus_root=corpus_root.resolve(), chroma_path=chroma_path.resolve())
    if not env.openai_embed_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_EMBED_MODEL。", err=True)
        raise typer.Exit(code=1)
    if not env.openai_chat_model.strip():
        typer.echo("错误：未设置 KNOW_ME_OPENAI_CHAT_MODEL。", err=True)
        raise typer.Exit(code=1)
    try:
        report = run_eval_report(env, cases.resolve(), top_k=top_k)
    except Exception as e:
        logging.getLogger(__name__).exception("eval 失败：%s", e)
        raise typer.Exit(code=1) from e
    dest = out
    if dest is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = Path("eval") / f"report-{stamp}.json"
    else:
        dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"已写入：{dest}")


@app.command("serve")
def serve_cmd(
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        help="监听地址；0.0.0.0 为所有网卡（可用本机局域网 IP 访问），仅本机改为 127.0.0.1",
    ),
    port: int = typer.Option(8000, "--port", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="开发热重载"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    E03：启动 HTTP 服务（`GET /health`、`POST /chat`、`POST /ingest`）。

    Web 聊天界面与 API 同源：浏览器打开 `http://<host>:<port>/`。
    OpenAPI 交互文档：`http://<host>:<port>/docs`
    """
    _configure_logging(verbose)
    _load_dotenv()
    import uvicorn

    uvicorn.run("know_me.api.app:app", host=host, port=port, reload=reload)


@app.command("version")
def version_cmd() -> None:
    """打印包版本号。"""
    from know_me import __version__

    typer.echo(__version__)


def main() -> None:
    """setuptools 控制台入口：必须先加载 `.env`，再解析子命令。"""
    _load_dotenv()
    app()


if __name__ == "__main__":
    main()
