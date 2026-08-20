"""
会话首条 greeting（开场白）——**固定模板**，不调用大模型。

**产品流程**（与 Web 欢迎页、`POST /session` 对齐）：
1. 招聘方在客户端录入「称呼 + 身份」并由 HTTP 层写入会话元数据。
2. **本模块**：根据称呼、身份与求职者 persona 名称拼出一段简短、书面化的欢迎语，作为 API 的 `opening` 返回，前端以 assistant 气泡展示。

与 Agent 主对话的 system（`prompts_agent` / persona）**分离**。
"""

from __future__ import annotations

from know_me.persona.loader import get_persona


def fallback_session_opening(viewer_display_name: str, viewer_role: str) -> str:
    """根据访客称呼、身份与求职者展示名生成欢迎语（第一人称本人口吻，与 persona/IDENTITY 一致）。"""
    owner = get_persona().display_name
    dn = (viewer_display_name or "").strip()
    rl = (viewer_role or "").strip()
    greet = f"{dn}您好，" if dn else "您好，"
    role_note = f"了解到您这边是「{rl}」。" if rl else ""
    return (
        f"{greet}我是{owner}。"
        f"{role_note}"
        "您方便的话，直接说下您这边**招聘岗位**（或 JD 要点），"
        "或想了解我哪段经历、匹配度，我按点聊。"
    )
