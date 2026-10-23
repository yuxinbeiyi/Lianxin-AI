"""Bilibili API-python transport shared with the dsh-bili-scroll plugin."""

import asyncio
import threading


class BilibiliApiUnavailable(RuntimeError):
    """Raised when the DSH-compatible dependency is not installed."""


def _run_coroutine(coroutine):
    """Run an async bilibili-api call from both sync and async host threads."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result = []
    failure = []

    def runner():
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as error:  # propagate the original library error
            failure.append(error)

    thread = threading.Thread(target=runner, name="lianxin-bilibili-api", daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0] if result else None


def _credential_from_dict(credential):
    try:
        from bilibili_api import Credential
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error

    return Credential(
        sessdata=str(credential.get("sessdata", "") or ""),
        bili_jct=str(credential.get("bili_jct", "") or ""),
        buvid3=str(credential.get("buvid3", "") or ""),
        buvid4=str(credential.get("buvid4", "") or ""),
        dedeuserid=str(credential.get("dedeuserid", "") or ""),
    )


def send_video_comment(credential, aid, text):
    """Send a top-level video comment using DSH's bilibili-api call."""
    try:
        from bilibili_api import comment
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error

    result = _run_coroutine(comment.send_comment(
        text=str(text),
        oid=int(aid),
        type_=comment.CommentResourceType.VIDEO,
        credential=_credential_from_dict(credential),
    ))
    if not isinstance(result, dict):
        raise ValueError("bilibili-api returned a non-object response")
    return result


def search_videos(credential, keyword, page_size=10):
    """Search videos through the same library used by the DSH MCP."""
    try:
        from bilibili_api import search
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error

    result = _run_coroutine(search.search_by_type(
        keyword=str(keyword),
        search_type=search.SearchObjectType.VIDEO,
        page=1,
        page_size=max(1, min(int(page_size), 42)),
        order_type=search.OrderVideo.TOTALRANK,
    ))
    return result if isinstance(result, dict) else {}


def get_video_info(credential, bvid):
    try:
        from bilibili_api import video
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error
    return _run_coroutine(video.Video(bvid=str(bvid), credential=_credential_from_dict(credential)).get_info())


def get_video_comments(credential, aid, page_index=1, num=20):
    try:
        from bilibili_api import comment
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error
    result = _run_coroutine(comment.get_comments(
        oid=int(aid),
        type_=comment.CommentResourceType.VIDEO,
        page_index=max(1, int(page_index)),
        order=comment.OrderType.LIKE,
        credential=_credential_from_dict(credential),
    ))
    if not isinstance(result, dict):
        return []
    return list((result.get("replies") or [])[:max(1, int(num))])


def get_video_subtitle(credential, bvid, cid=None):
    try:
        from bilibili_api import video
    except (ImportError, OSError) as error:
        raise BilibiliApiUnavailable("bilibili-api-python is not installed") from error
    instance = video.Video(bvid=str(bvid), credential=_credential_from_dict(credential))
    return _run_coroutine(instance.get_subtitle(cid=cid))


def get_received_replies(credential):
    from bilibili_api import session
    return _run_coroutine(session.get_replies(credential=_credential_from_dict(credential)))


def get_received_mentions(credential, last_id=None, at_time=None):
    from bilibili_api import session
    return _run_coroutine(session.get_at(credential=_credential_from_dict(credential), last_id=last_id, at_time=at_time))


def get_video_info_by_aid(credential, aid):
    from bilibili_api import video
    return _run_coroutine(video.Video(aid=int(aid), credential=_credential_from_dict(credential)).get_info())


def get_comment_thread(credential, aid, root, page=1):
    from bilibili_api import comment
    instance = comment.Comment(oid=int(aid), type_=comment.CommentResourceType.VIDEO,
                               rpid=int(root), credential=_credential_from_dict(credential))
    result = _run_coroutine(instance.get_sub_comments(page_index=int(page), page_size=20))
    if not isinstance(result, dict):
        raise ValueError("Invalid comment thread response")
    return result


def send_comment_reply(credential, aid, root, parent, text):
    from bilibili_api import comment
    result = _run_coroutine(comment.send_comment(
        text=str(text), oid=int(aid), type_=comment.CommentResourceType.VIDEO,
        root=int(root), parent=int(parent), credential=_credential_from_dict(credential),
    ))
    if not isinstance(result, dict):
        raise ValueError("Invalid comment reply response")
    return result
