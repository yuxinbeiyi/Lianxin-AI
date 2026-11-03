"""莲心的可验证自我认知：能力、边界与当前成长状态。"""

from __future__ import annotations


def build_self_knowledge_context(persona_id: str = "", scene: str = "main_chat") -> str:
    from brain.capability_knowledge import get_capability_index

    index = get_capability_index()
    lines = [
        "【自我认知】",
        f"当前可用 {index.available}/{index.total} 项能力，覆盖：{'、'.join(index.categories[:8])}。",
        "用户明确询问自身能力、功能或是否支持某操作时，必须调用 query_capabilities 查询实时目录后再回答。",
        "回答时区分可直接使用、需要启用和当前不可用；不得凭空承诺。",
        "涉及照片、文件、记忆或外部发送时，必须说明用途并尊重用户是否同意。",
    ]
    if persona_id:
        try:
            from brain.persona.growth import get_persona_growth_service
            growth = get_persona_growth_service().dynamic_context(persona_id, scene)
            if growth:
                lines.append(growth)
        except Exception:
            pass
    return "\n".join(lines)
