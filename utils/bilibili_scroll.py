"""DSH-compatible Bilibili browsing primitives for proactive surfing."""

import json
from urllib.request import Request, urlopen

from utils.bilibili_api_transport import (
    get_video_comments,
    get_video_info,
    get_video_subtitle,
    search_videos,
)


class BilibiliScrollClient:
    def __init__(self, credential):
        self.credential = credential

    def search(self, keyword, limit=10):
        result = search_videos(self.credential, keyword, page_size=max(1, limit))
        videos = []
        for item in (result.get("result") or [])[:limit]:
            title = str(item.get("title", "")).replace('<em class="keyword">', "").replace("</em>", "")
            bvid = str(item.get("bvid", "") or "")
            if not bvid:
                continue
            videos.append({
                "bvid": bvid,
                "aid": int(item.get("aid") or 0),
                "title": title,
                "author": str(item.get("author", "") or ""),
                "play_count": item.get("play", 0),
                "like_count": item.get("like", 0),
                "review_count": item.get("review", 0),
                "description": str(item.get("description", "") or "")[:500],
                "link": f"https://www.bilibili.com/video/{bvid}",
            })
        return videos

    def enrich(self, bvid, comment_limit=20):
        info = get_video_info(self.credential, bvid)
        aid = int(info.get("aid") or 0)
        cid = int(info.get("cid") or 0)
        if not cid and info.get("pages"):
            cid = int((info["pages"] or [{}])[0].get("cid") or 0)
        comments_error = ""
        try:
            comments = get_video_comments(self.credential, aid, page_index=1, num=comment_limit) if aid else []
        except Exception as error:
            comments = []
            comments_error = f"{type(error).__name__}: {error}"[:180]
        subtitle_text = ""
        subtitle_error = ""
        try:
            subtitle_info = get_video_subtitle(self.credential, bvid, cid=cid) if cid else {}
            subtitles = subtitle_info.get("subtitles") or []
            target = next((item for item in subtitles if item.get("lan") in {"ai-zh", "zh-CN", "zh"}), None)
            target = target or (subtitles[0] if subtitles else None)
            if target:
                subtitle_url = str(target.get("subtitle_url", ""))
                if subtitle_url.startswith("//"):
                    subtitle_url = "https:" + subtitle_url
                request = Request(subtitle_url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.bilibili.com/",
                })
                with urlopen(request, timeout=15) as response:
                    body = json.loads(response.read().decode("utf-8")).get("body") or []
                subtitle_text = "\n".join(str(item.get("content", "")).strip() for item in body if item.get("content"))
                subtitle_text = subtitle_text[:12000]
            else:
                subtitle_error = "no_subtitle"
        except Exception as error:
            subtitle_error = f"{type(error).__name__}: {error}"[:180]

        normalized_comments = []
        for item in comments:
            member = item.get("member") or {}
            content = item.get("content") or {}
            normalized_comments.append({
                "rpid": item.get("rpid", 0),
                "user": member.get("uname", ""),
                "content": content.get("message", ""),
                "like": item.get("like", 0),
                "reply_count": item.get("rcount", 0),
            })

        stat = info.get("stat") or {}
        return {
            "bvid": bvid,
            "aid": aid,
            "cid": cid,
            "title": str(info.get("title", "")).strip(),
            "author": str((info.get("owner") or {}).get("name", "")).strip(),
            "description": str(info.get("desc", "") or "").strip()[:500],
            "play_count": stat.get("view", 0),
            "review_count": stat.get("reply", 0),
            "link": f"https://www.bilibili.com/video/{bvid}",
            "subtitle": subtitle_text,
            "subtitle_error": subtitle_error,
            "comments": normalized_comments,
            "comments_error": comments_error,
        }

    def crawl(self, keyword, limit=5):
        results = []
        for video in self.search(keyword, limit):
            enriched = self.enrich(video["bvid"])
            results.append({**video, **enriched})
        return results
