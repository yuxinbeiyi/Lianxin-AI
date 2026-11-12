export type NoteTextSegment = { text: string; match: boolean };

/** 把备忘正文按关键词切成普通/命中片段，供高亮背景层渲染。 */
export function segmentsOf(text: string, query: string): NoteTextSegment[] {
  const needle = query.trim();
  if (!needle) return [{ text, match: false }];
  const haystack = text.toLowerCase();
  const target = needle.toLowerCase();
  const parts: NoteTextSegment[] = [];
  let cursor = 0;
  for (;;) {
    const index = haystack.indexOf(target, cursor);
    if (index < 0) {
      parts.push({ text: text.slice(cursor), match: false });
      return parts;
    }
    parts.push({ text: text.slice(cursor, index), match: false });
    parts.push({ text: text.slice(index, index + needle.length), match: true });
    cursor = index + needle.length;
  }
}

export function countMatches(text: string, query: string) {
  return segmentsOf(text, query).filter((part) => part.match).length;
}
