# -*- coding: utf-8 -*-
"""括号卫生：清理莲心回复中的全角括号旁白。

背景：人格档案要求语言有"画面感"，而模型习惯用（推眼镜）（投影闪烁）这类
舞台旁白实现画面感；再加上【表情：】情绪标签本身就是括号元信息，模型会把
全角括号旁白当成合法输出元素随机泄漏进正文。用户明确要求：除颜文字外，
回复中不得出现括号包裹的动作、神态、音效、场景或心理描写。

分类规则（宁可清理旁白，只放行明确的非旁白内容）：
- 内容含颜文字特征字符（∀ ω Д ´ ` ・ ≧ ≦ ﾉ ⌒ ▽ ￣ 等）→ 颜文字，保留；
  颜文字实际几乎都用半角括号，天然不进本模块的处理范围；
- 内容中 ASCII 字母/数字占比过半（如 （ASR）（VGA）（v1.5））→ 技术缩写
  注释，保留；含少量数字的中文旁白（如 （停顿0.3秒））仍会被清理；
- 其余全角括号内容 → 旁白，移除；整行都由旁白构成的，连行移除。

【表情：XX】情绪标签由 GUI 单独剥除，不属于本模块职责。
"""

from __future__ import annotations

import re

# 颜文字特征字符。中文旁白不会包含这些字符（省略号"……"故意不在列）。
_KAOMOJI_HINT_RE = re.compile(r"[∀ωД≧≦´`・ﾉ⌒▽￣｀]")
_ASCII_RE = re.compile(r"[A-Za-z0-9]")
_FW_GROUP_RE = re.compile(r"（[^（）]*）")
_LINE_OF_GROUPS_RE = re.compile(r"^(?:\s*（[^（）]*）)+\s*$")


def _is_kaomoji(inner: str) -> bool:
    return bool(_KAOMOJI_HINT_RE.search(inner))


def _is_technical(inner: str) -> bool:
    """ASCII 字母/数字占比过半才视为技术缩写注释。"""
    chars = [c for c in inner if not c.isspace()]
    if not chars:
        return False
    ascii_count = sum(1 for c in chars if _ASCII_RE.match(c))
    return ascii_count / len(chars) >= 0.5


def _keep_group(group: str) -> bool:
    inner = group[1:-1]
    return _is_kaomoji(inner) or _is_technical(inner)


def _strip_once(text: str) -> str:
    return _FW_GROUP_RE.sub(
        lambda m: m.group(0) if _keep_group(m.group(0)) else "",
        text,
    )


def strip_parenthetical_asides(text: str) -> str:
    """移除全角括号旁白；颜文字与技术缩写注释原样保留。"""
    if not text or "（" not in text:
        return text

    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped_line = line.strip()
        if _LINE_OF_GROUPS_RE.match(stripped_line):
            groups = _FW_GROUP_RE.findall(stripped_line)
            if all(_keep_group(g) for g in groups):
                out_lines.append(line)  # 纯颜文字/技术注释行，保留
            # 含旁白组的独立行：整行删除
            continue
        new_line = _strip_once(line)
        if new_line != line and not new_line.strip() and line.strip():
            continue  # 行内内容被整体剥空 → 删行
        out_lines.append(new_line)

    result = "\n".join(out_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
