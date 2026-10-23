"""Keep conversational shares separate from local comment receipts."""

import re


def comment_submitted(result):
    return bool(result.get("sent") and result.get("rpid")
                and result.get("status") in {"sent_verified", "sent_unverified"})


def share_action(result):
    if comment_submitted(result):
        return "已提交一条提及用户的评论。可自然说我留言了、顺手@了你；不能说对方已看到或收到通知。"
    if result.get("status") == "unknown":
        return "发送结果未知，不能说留言成功或@了用户；如提到这件事，只说还没确认，不再重复发送。"
    return "没有提交成功的评论，只能分享视频与感受，不能声称自己留言、评论、回复或@了用户。"


def valid_share(text, comment_text, result):
    if not text or text.strip().upper() == "SKIP":
        return False
    if re.search(r"https?://|www\.|```|评论\s*(?:正文|ID|编号)|(?:rpid|send_status|sent_verified|sent_unverified|cookie|sessdata|bili_jct)\b|uid\s*=|核验|核对|回读|已提交|尚待|作为.{0,8}(?:AI|助手)|任务(?:完成|回执)", text, re.I):
        return False
    if comment_submitted(result) and not re.search(r"@\s*(?:了|一下|你)", text):
        return False
    if re.search(r"(?:你|主号).{0,8}(?:已经|应该|肯定|一定|能|会|就|已|可以).{0,8}(?:收到|看到)|通知.{0,6}(?:送达|收到)|@我的", text):
        return False
    if "@" in text and not comment_submitted(result):
        return False
    if not comment_submitted(result) and re.search(r"(?:留|发|写|评|回).{0,6}(?:评论|留言|回复|两句|几句)|(?:评论|留言|回复).{0,4}(?:了|啦|过|成功)", text):
        return False
    comment_body = re.sub(r"^@\S+\s+", "", comment_text).strip()
    if comment_body and comment_body in text:
        return False
    return True


def fallback_share(video, result):
    title = re.sub(r"\s+", " ", str(video.get("title") or "这个视频")).strip()
    if comment_submitted(result):
        return f"刷到《{title}》，我在底下留了两句，还顺手 @ 了你（￣▽￣）"
    if result.get("status") == "unknown":
        return f"刷到《{title}》，想分享给你。刚才那条留言还没确认有没有发出去，我先不重复发啦（￣▽￣）"
    if result.get("status") not in {None, "", "read_only", "comment_skipped", "not_attempted"}:
        return f"刷到《{title}》，想分享给你。这次没能把留言发出去，先把视频带回来啦（￣▽￣）"
    return f"刷到《{title}》，感觉可能对你的口味，带回来给你看看（￣▽￣）"
