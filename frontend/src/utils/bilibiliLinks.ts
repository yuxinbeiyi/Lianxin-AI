export type BilibiliLinkPart = { text: string; bvid?: string };

export function splitBilibiliLinks(text: string): BilibiliLinkPart[] {
  const parts: BilibiliLinkPart[] = [];
  const pattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>]+)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    let bvid = "";
    try {
      const url = new URL(match[2] || match[3]);
      if (url.protocol === "https:" && ["bilibili.com", "www.bilibili.com"].includes(url.hostname)
        && !url.username && !url.password && !url.port) {
        bvid = url.pathname.match(/^\/video\/(BV[A-Za-z0-9]{10})\/?$/)?.[1] || "";
      }
    } catch {
      continue;
    }
    if (!bvid) continue;
    if (match.index! > cursor) parts.push({ text: text.slice(cursor, match.index) });
    parts.push({ text: match[1] || "打开视频 ↗", bvid });
    cursor = match.index! + match[0].length;
  }
  if (cursor < text.length || !parts.length) parts.push({ text: text.slice(cursor) });
  return parts;
}
