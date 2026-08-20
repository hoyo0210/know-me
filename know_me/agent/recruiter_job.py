"""
招聘方「招聘岗位」可信度判断与 Agent system 附加上下文。

岗位可能缺失、胡乱填写或与「求职方向」混淆；仅 **可信** 岗位才在 system 中视为「岗位已明确」并放开敏感口径。
"""

from __future__ import annotations

import re

# 整句命中即不可信（小写比较，原文为中文时另判）
_GARBAGE_EXACT_LOWER: frozenset[str] = frozenset(
    {
        "test",
        "testing",
        "null",
        "none",
        "n/a",
        "na",
        "abc",
        "asd",
        "asdf",
        "xxx",
        "111",
        "123",
        "666",
        "888",
    }
)

_GARBAGE_EXACT_ZH: frozenset[str] = frozenset(
    {
        "测试",
        "试试",
        "随便",
        "随意",
        "无",
        "没有",
        "暂无",
        "待定",
        "未知",
        "不明",
        "哈哈",
        "啊啊啊",
        "。。。",
        "...",
        "——",
        "-",
        "找工作",
        "求职",
        "应聘",
        "找工作中",
        "看机会",
        "随便填",
        "填着玩",
        "测试一下",
    }
)

# 求职者口吻 / 非岗位名（子串命中且总长较短时判不可信）
_JOB_SEEKER_RE = re.compile(
    r"(找工作|求职中|在看机会|看机会呢|投简历|海投|换工作|跳槽中)",
)

# 至少含一个「像岗位名」的片段：2+ 汉字，或 2+ 连续字母（如 AI、Java）
_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{2,}")


def normalize_recruiter_job_title(raw: str | None) -> str:
    if raw is None:
        return ""
    t = str(raw).strip().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", t)[:256].rstrip()


def is_credible_recruiter_job_title(raw: str | None) -> bool:
    """
    招聘岗位是否足以视为「已明确」（启发式，非 NLP）。

    空、过短、纯符号/数字、重复字符、常见占位、求职者口吻等 → False。
    """
    s = normalize_recruiter_job_title(raw)
    if not s:
        return False
    if s in _GARBAGE_EXACT_ZH:
        return False
    low = s.lower()
    if low in _GARBAGE_EXACT_LOWER:
        return False
    # 纯数字、标点、空白
    if re.fullmatch(r"[\d\s\W_]+", s, flags=re.UNICODE):
        return False
    compact = s.replace(" ", "")
    if len(compact) < 2:
        return False
    # 同一字符重复（如 aaa、1111）
    if len(compact) >= 3 and len(set(compact)) == 1:
        return False
    if _JOB_SEEKER_RE.search(s) and len(compact) <= 12:
        return False
    if not _MEANINGFUL_RE.search(s):
        return False
    # 极短且无中文：如 x、ok（单字母除外 AI 已由 MEANINGFUL 覆盖 2 letter）
    if len(compact) <= 3 and not re.search(r"[\u4e00-\u9fff]", s):
        return False
    return True


def build_recruiter_context_suffix(job_title: str | None, contact: str | None) -> str:
    """并入 Agent system：可信岗位 → 岗位已明确；不可信但有填写 → 明示按未明确处理。"""
    job = normalize_recruiter_job_title(job_title)
    ct: str | None = None
    if contact is not None:
        c = str(contact).strip().replace("\r", " ").replace("\n", " ")
        ct = c[:128].rstrip() if c else None

    if not job:
        return ""

    if not is_credible_recruiter_job_title(job):
        excerpt = job if len(job) <= 48 else job[:48] + "…"
        lines = [
            "\n\n【招聘岗位说明 · 不可信】",
            f"招聘方填写的岗位为「{excerpt}」，过于简略、无效、像占位或与**招聘岗位**无关，**不视为岗位已明确**。",
            "凡语料中「岗位未明确时不得展开敏感内容」（薪酬、缺点完整话术、离职原因细节等）的规则，**仍适用**。",
            "**缺点题**：未明确岗位/方向→只输出定稿原文(禁止改写)；已明确→技术/管理F、其它C；见 system。",
            "**薪酬/离职等**：可电话对齐或说明需结合岗位再谈，**禁止**问「您在看哪个方向」。",
            "**禁止**把胡乱填写当作可信岗位去展开敏感答案或编造匹配度。",
        ]
        if ct:
            lines.append(f"联系方式（已记录，可选使用）：{ct}")
        return "\n".join(lines)

    lines = ["\n\n【本会话招聘岗位 · 招聘方提供 · 已校验】", f"岗位：{job}"]
    if ct:
        lines.append(f"联系方式（招聘方自愿留下）：{ct}")
    lines.append(
        "以上表示招聘方**正在招聘的岗位**，不是问求职者「在看什么方向」。"
        "凡语料中「岗位未明确时不得展开敏感口径」的规则，**在本会话视为岗位已明确**（除非用户正文否定或更换岗位）。"
        "若用户后续消息表明岗位填写有误，以**最新可信表述**为准。"
        "勿再向招聘方追问「您在看哪个方向」；若需细化可问职责补充，而非求职方向。"
    )
    return "\n".join(lines)
