"""User-supplied recall subjects shared by retrieval and query planning."""

from __future__ import annotations

import re


_GENERIC = frozenset({
    "项目", "工程", "仓库", "方案", "事情", "要求", "时候", "最近", "甲方",
    "印象", "有点", "记忆", "记录", "聊天记录", "背景线索", "同事", "朋友", "卡住",
})


def recall_followup(text: str) -> bool:
    if re.search(r"换个话题|另一个人|另外一位|不说这个|不聊这个", text):
        return False
    return bool(re.search(
        r"(?:他|她|他们|她们|那个|这个|这件|那件|这人|那人|健忘|忘了|印象|"
        r"不记得|记得.{0,12}(?:说|提|聊)|(?:说|提|聊)过|跟你说|跟你提)", text,
    ))


def recall_subjects(text: str) -> list[str]:
    value = str(text or "")[:2400]
    subjects = []
    candidates = re.findall(r'[“「『"]([^”」』"\n]{2,24})[”」』"]', value)
    try:
        from jieba import posseg
        candidates.extend(item.word for item in posseg.cut(value) if item.flag.startswith("nr"))
    except ImportError:
        pass
    candidates.extend(re.findall(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_-]{1,23}(?![A-Za-z0-9_])", value))
    for match in re.finditer(r"([^，。！？；\n]{1,35}?)(?:项目|工程|仓库|方案)", value):
        phrase = match[1].strip().rstrip("的")
        candidates.append(re.split(r"那个|这个|之前|说过|讨论过|讨论|关于|就是|跟你|最近|的", phrase)[-1].strip())
    for candidate in candidates:
        if (2 <= len(candidate) <= 24 and candidate not in _GENERIC
                and candidate in value and candidate not in subjects):
            subjects.append(candidate)
    return subjects[:4]


def recall_terms(text: str) -> list[str]:
    try:
        from jieba.analyse import extract_tags
        words = extract_tags(text, topK=8, allowPOS=("n", "nr", "nrfg", "nrt", "ns", "nt", "nz", "eng"))
    except ImportError:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}", text)
    return [word[:64] for word in words if word not in _GENERIC][:4]
