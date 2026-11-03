export type PersonaPreviewSection = { name: string; body: string; key: string };

export function parsePersonaPreview(text: string, layers: readonly string[]): PersonaPreviewSection[] {
  const sections: PersonaPreviewSection[] = [];
  for (const chunk of text.split(/\r?\n\r?\n(?=【)/)) {
    if (!chunk.trim()) continue;
    const match = chunk.match(/^【([^】]+)】\r?\n?([\s\S]*)$/);
    if (match && layers.includes(match[1])) {
      sections.push({ name: match[1], body: match[2].trim(), key: `${match[1]}-${sections.length}` });
    } else if (sections.length) {
      sections[sections.length - 1].body += `\n\n${chunk.trim()}`;
    } else {
      sections.push({ name: "人格草稿", body: chunk.trim(), key: "draft" });
    }
  }
  return sections.filter((section) => section.body);
}
