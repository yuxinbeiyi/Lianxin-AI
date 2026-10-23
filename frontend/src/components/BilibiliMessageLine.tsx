import { Fragment, useState, type MouseEvent } from "react";
import { openBilibiliVideo } from "../services/openBilibiliVideo";
import { splitBilibiliLinks } from "../utils/bilibiliLinks";

export function BilibiliMessageLine({ text }: { text: string }) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");

  const openVideo = async (event: MouseEvent<HTMLAnchorElement>, bvid: string) => {
    event.preventDefault();
    if (opening) return;
    setOpening(true);
    setError("");
    try {
      await openBilibiliVideo(bvid);
    } catch (error) {
      setError(error instanceof Error ? error.message : "暂时无法打开系统浏览器，可以复制视频链接后手动打开。");
    } finally {
      setOpening(false);
    }
  };

  return <>
    {splitBilibiliLinks(text).map((part, index) => part.bvid ? (
      <a key={index} className="bili-video-link"
        href={`https://www.bilibili.com/video/${part.bvid}`}
        title="在系统默认浏览器中打开视频"
        aria-disabled={opening}
        onClick={(event) => void openVideo(event, part.bvid!)}>{part.text}</a>
    ) : <Fragment key={index}>{part.text}</Fragment>)}
    {error && <span className="bili-video-link-error" role="alert">{error}</span>}
  </>;
}
