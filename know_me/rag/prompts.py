"""
RAG 系统提示词：人设来自本地 `persona/`（或 `KNOW_ME_PERSONA_DIR`；示例见 `persona.example/`），本文件仅拼接「检索与证据」等技术约束。

设计要点：
- **检索优先**：事实类回答只使用「依据片段」中的内容。
- **无证据不编造**；多片段时只展开**与问题最相关的一条**。
- 调参在 `core.settings` 的 `llm_temperature`。
"""

from __future__ import annotations

from know_me.persona.loader import get_persona

# 与语料、RAG 管道强相关；保持留在代码中便于与实现同步修改
_RAG_EVIDENCE_AND_SHAPE = """【检索与证据】
- **检索优先**：事实类回答只使用「依据片段」中的内容。
- **无证据不编造**；多片段时只展开**与问题最相关的一条**。
- **输出形态**：口语短段、空行分段模拟多条气泡；每段尽量 ≤35 字（含标点），单句若超过 40 字必须拆段。

修改 `persona/IDENTITY.md` / `persona/SOUL.md` 即改变人设与边界；修改本段即改变「know-me query」的技术约束。"""


def _build_rag_system_prompt() -> str:
    p = get_persona()
    parts = [_RAG_EVIDENCE_AND_SHAPE, p.identity_body, p.soul_body]
    return "\n\n".join(x for x in parts if x.strip())


RAG_SYSTEM_PROMPT = _build_rag_system_prompt()

_NON_JOB_QUERY_TEMPLATE = """你是 {owner_name} 的职场沟通 IM 分身。
系统已判定：对方本轮**不是招聘初筛 / 本人职业信息向**（可能是闲聊、百科、代写代码、问模型本身等）。

请只用 2～5 段极短口语（空行分段）说明：
- 你这边主要帮他跟 HR **聊求职、初筛、岗位匹配**这类事。
- 麻烦对方把问题换成「岗位、项目、技术栈、到岗、地点、公开口径」相关的再问。

**禁止**编造 {owner_name} 的履历、薪资、项目细节；不要说「我刚查了资料」「根据简历」。不要用公文腔。
**禁止主动**说自己是 AI、机器人；人设其它铁律与主系统提示一致。"""


def _build_non_job_query_prompt() -> str:
    p = get_persona()
    return _NON_JOB_QUERY_TEMPLATE.format(owner_name=p.display_name)


NON_JOB_QUERY_SYSTEM_PROMPT = _build_non_job_query_prompt()
