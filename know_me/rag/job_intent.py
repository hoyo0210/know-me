"""
判断本轮用户输入是否应检索个人语料（Chroma / RAG / search_personal_knowledge）。

目标：招聘初筛、本人职业信息相关 → 检索；闲聊、百科、代写代码、问模型本身等 → 不检索。

策略：复用 `retrieval.is_hr_intent` + 轻量关键词 / 正则；**宁可少量误检索**（多耗一次嵌入），
避免漏掉措辞不典型的 HR 问法。明显离题或纯寒暄则跳过。
"""

from __future__ import annotations

import re
from functools import lru_cache

from know_me.rag.retrieval import is_hr_intent

# 整句匹配的短寒暄（整句命中即视为纯寒暄，避免「打个招呼」仍走模型）
_GREETING_EXACT = frozenset(
    {
        "打个招呼",
        "招呼一下",
        "先打个招呼",
        "哈喽",
        "嗨",
        "嘿",
        "哈喽呀",
        "你好呀",
    },
)

_GREETING_RE = re.compile(
    r"^\s*(你好|您好|哈喽|在吗|在么|在嘛|hi|hello|嗨|早上好|上午好|下午好|晚上好|"
    r"谢谢|多谢|感谢|好的|好滴|嗯嗯|嗯好|ok|okay|哈喽哈喽|在不在|再见|拜拜)([！!。.…~～\s哒哈呀哦额]*)?\s*$",
    re.I,
)

_JOB_WORDS_BASE = (
    "求职",
    "找工作",
    "看机会",
    "换工作",
    "投递",
    "简历",
    "面试",
    "招聘",
    "招人",
    "候选人",
    "初筛",
    "一面",
    "二面",
    "终面",
    "技术面",
    "背调",
    "背景调查",
    "薪资",
    "薪酬",
    "工资",
    "月薪",
    "年薪",
    "总包",
    "期权",
    "股权",
    "加班",
    "远程",
    "混合办公",
    "到岗",
    "入职",
    "离职",
    "试用期",
    "offer",
    "全职",
    "兼职",
    "外包",
    "合同工",
    "猎头",
    "内推",
    "职位",
    "岗位",
    "职级",
    "工作内容",
    "汇报对象",
    "技术栈",
    "项目经验",
    "工作经历",
    "履历",
    "自我介绍",
    "优缺点",
    "职业规划",
    "团队",
    "管理",
    "带人",
    "研发",
    "架构",
    "交付",
    "know me",
    "knowme",
    "数字分身",
    "个人语料",
    "知识库",
    "语料",
)


@lru_cache(maxsize=1)
def _job_signal_words() -> tuple[str, ...]:
    """求职向弱信号词：含 persona 中的 display_name / aliases。"""
    from know_me.persona.loader import get_persona

    p = get_persona()
    base = _JOB_WORDS_BASE
    seen = set(base)
    extra: list[str] = []
    for a in p.aliases:
        t = (a or "").strip()
        if t and t not in seen:
            seen.add(t)
            extra.append(t)
    return base + tuple(extra)


@lru_cache(maxsize=1)
def _about_person_leading_pattern() -> re.Pattern[str]:
    """匹配「在问谁」：含 persona 别名。"""
    from know_me.persona.loader import get_persona

    p = get_persona()
    parts: list[str] = ["你", "您", "本人", "候选人"]
    for a in p.aliases:
        t = (a or "").strip()
        if t and t not in parts:
            parts.append(t)
    inner = "|".join(re.escape(x) for x in parts)
    return re.compile(rf"({inner})", re.I)


@lru_cache(maxsize=1)
def _what_is_spot_words() -> tuple[str, ...]:
    """「什么是 X」里若出现这些词，更像在聊求职/本人而非纯百科。"""
    base = ("简历", "岗位", "面试", "初筛", "你", "您", "本人")
    from know_me.persona.loader import get_persona

    p = get_persona()
    extra = tuple(a for a in p.aliases if len(a) <= 16)
    return base + extra


_TECH_TERMS = (
    "java",
    "spring",
    "docker",
    "k8s",
    "kubernetes",
    "mysql",
    "redis",
    "postgres",
    "mongodb",
    "python",
    "golang",
    "vue",
    "react",
    "微服务",
    "rag",
    "agent",
    "langchain",
    "llamaindex",
    "大模型",
    "向量",
    "chrom",
)

_TECH_Q_MARK = re.compile(r"(吗|么|呢|嘛|没|是否|会不会|有没有|熟|了解|用过|写过|做过|擅长)")

_OFF_DOMAIN = (
    "天气",
    "气温",
    "下雨",
    "台风",
    "股票",
    "彩票",
    "讲个笑话",
    "冷笑话",
    "翻译一下",
    "英译",
    "中译",
    "译成英文",
    "写一首",
    "写首诗",
    "写一段代码",
    "帮我写代码",
    "leetcode",
    "力扣",
    "用python",
    "用 java",
    "写一个函数",
    "排序算法",
    "快速排序",
    "二叉树",
    "地球",
    "月球",
    "太阳系",
    "圆周率",
    "黑洞",
    "你是谁开发的",
    "什么模型",
    "chatgpt",
    "gpt-4",
    "gpt4",
    "claude",
    "通义千问",
    "文心一言",
    "李白",
    "杜甫",
    "史记",
    "第二次世界大战",
)


def _is_pure_greeting(q: str) -> bool:
    s = q.strip()
    if s in _GREETING_EXACT:
        return True
    if len(s) > 28:
        return False
    return bool(_GREETING_RE.match(s))


def is_greeting_only_message(query: str) -> bool:
    """纯寒暄短句：可走本地固定回复，无需调用 LLM（显著降低首包延迟）。"""
    return _is_pure_greeting(query)


def greeting_fast_answer(query: str) -> str:
    """纯寒暄的本地 IM 短答（与职场分身人设一致，不调用模型）。"""
    t = (query or "").strip()
    sl = t.lower()
    if any(x in t for x in ("谢谢", "感谢", "多谢")):
        return "不客气哈～\n\n您要聊岗位相关随时说。"
    if any(x in t for x in ("再见", "拜拜")):
        return "好嘞，回头聊～"
    if any(x in t for x in ("在吗", "在么", "在嘛", "在不在")):
        return "在的～您请讲。"
    if "打招呼" in t and len(t) <= 12:
        return "在的哈～\n\n您请讲，我这边帮您跟 HR 聊初筛这块。"
    if any(x in sl for x in ("hi", "hello")) and len(t) <= 16:
        return "Hi～在的，您请讲。"
    if "早上好" in t or "上午好" in t:
        return "早上好～在的，您请讲。"
    if "下午好" in t:
        return "下午好～在的，您请讲。"
    if "晚上好" in t:
        return "晚上好～在的，您请讲。"
    if any(x in t for x in ("好的", "好滴", "嗯嗯", "嗯好")) or sl in ("ok", "okay"):
        return "好滴～您请讲。"
    return "您好，在的～\n\n您有问题随时问我就行，岗位履历这块我能帮忙对接。"


def _about_person_career(q: str) -> bool:
    if _about_person_leading_pattern().search(q):
        if re.search(
            r"(项目|经历|履历|简历|工作|公司|离职|入职|薪资|期望|到岗|加班|远程|技术|栈|管理|团队|负责|几年|介绍|做过|"
            r"后端|前端|语言|熟悉|主要)",
            q,
        ):
            return True
    if re.search(r"(介绍一下|聊聊|说说).{0,8}(你|您|自己|本人)", q):
        return True
    return False


def _tech_screening_heuristic(q: str) -> bool:
    if not _TECH_Q_MARK.search(q):
        return False
    ql = q.lower()
    return any(t in ql for t in _TECH_TERMS) or any(t in q for t in ("微服务", "大模型", "向量库"))


def _meta_model_question(q: str) -> bool:
    sl = q.lower()
    if re.search(r"\b(what model|who (made|built) you|which llm)\b", sl):
        return True
    return any(x in q for x in ("你是什么模型", "用的哪个模型", "谁训练的你", "底层用的啥模型"))


def _clearly_off_domain(q: str) -> bool:
    if any(x in q for x in _OFF_DOMAIN):
        return True
    if _meta_model_question(q):
        return True
    return False


def _generic_what_is_without_person(q: str) -> bool:
    """「什么是 X」类百科题：未指向本人履历 / 求职时倾向不检索。"""
    s = q.strip()
    if not (s.startswith("什么是") or s.startswith("啥是")):
        return False
    if _about_person_career(q) or is_hr_intent(q):
        return False
    if any(w in q for w in _what_is_spot_words()):
        return False
    return len(s) <= 80


def _privacy_or_safety_needs_corpus(q: str) -> bool:
    """涉隐私、越权索取等：仍走语料，用公开边界口径答复。"""
    return any(
        x in q
        for x in (
            "密码",
            "银行卡",
            "门牌",
            "身份证",
            "银行卡号",
            "手机号",
            "家庭住址",
            "具体住址",
        )
    )


def should_retrieve_personal_corpus(query: str) -> bool:
    """
    是否应对本轮问题走个人语料检索。

    False：纯寒暄、明显离题 / 百科 / 写代码 / 问模型、泛化的「什么是」短问句等。
    True：HR 信号、求职词、指向本人的职业问法、典型技术面确认句式等。
    """
    q = (query or "").strip()
    if not q:
        return False
    if _privacy_or_safety_needs_corpus(q):
        return True
    if is_hr_intent(q):
        return True
    if _is_pure_greeting(q):
        return False
    if any(w in q for w in _job_signal_words()):
        return True
    if _about_person_career(q):
        return True
    if _tech_screening_heuristic(q):
        return True
    if _generic_what_is_without_person(q):
        return False
    if _clearly_off_domain(q):
        return False
    # 其余偏短句：多数仍可能是 HR 非常规措辞，保留检索
    if len(q) <= 14:
        return True
    # 较长且未命中任何求职信号：倾向不检索，避免无关长文拉噪声
    return False
