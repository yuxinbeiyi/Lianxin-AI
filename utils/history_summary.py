"""Shared summary generation for the Qt and native history panels."""


def generate_session_summary(messages: list[dict]) -> str:
    from openai import OpenAI
    from config import get_api_config

    cfg = get_api_config()
    lines = [f"{'主人' if message['role'] == 'user' else '莲心'}：{message['content'][:200]}"
             for message in messages[:60]]
    with OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=90) as client:
        response = client.chat.completions.create(
            model=cfg["model"], max_tokens=80,
            messages=[
                {"role": "system", "content": "你是一个对话摘要助手。请用1~2句简洁的中文概括以下对话的主要内容，不超过50个字。"},
                {"role": "user", "content": "请总结以下对话：\n" + "\n".join(lines)},
            ],
        )
    summary = (response.choices[0].message.content or "").strip()
    if not summary:
        raise ValueError("摘要服务返回了空内容，原摘要未修改")
    return summary
