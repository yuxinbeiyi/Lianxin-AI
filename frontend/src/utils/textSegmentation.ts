/**
 * 新旧界面共用的语义分段逻辑（TypeScript 版）。
 *
 * 与 `utils/text_segmentation.py` 保持同一套规则，供新版界面复现旧界面的
 * 「分段发送」体验：一条长回复拆成若干条气泡，逐条送、逐条念。
 */

const SENTENCE_TARGET = 80;
const MERGE_UNDER = 60;
const CONVERSATION_MAX_SEGMENTS = 3;
const CONVERSATION_MERGE_LIMIT = 46;
const CONVERSATION_LENGTH_LIMIT = 220;

/** 回复是否应按「闲聊」节奏切分：短、无代码块、无结构化标记。 */
export function shouldSendAsConversation(text: string): boolean {
  const value = (text || "").trim();
  if (!value || value.length > CONVERSATION_LENGTH_LIMIT || value.includes("```"))
    return false;
  const structuredMarkers = ["\n1.", "\n- ", "\n|", "{", "def ", "Traceback"];
  return !structuredMarkers.some((marker) => value.includes(marker));
}

/** 按完整句子切分，保留句末标点。 */
function splitCompleteSentences(text: string): string[] {
  const parts = text.split(/([。！？!?]+)/);
  const sentences: string[] = [];
  let current = "";
  for (const part of parts) {
    current += part;
    if (/^[。！？!?]+$/.test(part)) {
      const trimmed = current.trim();
      if (trimmed) sentences.push(trimmed);
      current = "";
    }
  }
  const tail = current.trim();
  if (tail) sentences.push(tail);
  return sentences;
}

/**
 * 结构化分段：优先按代码块与段落切，过长段落再按完整句子切，
 * 不按字符数硬截断；过短的碎片会合并回去。
 */
export function splitSemanticText(
  text: string,
  options: { sentenceTarget?: number; mergeUnder?: number } = {},
): string[] {
  const sentenceTarget = options.sentenceTarget ?? SENTENCE_TARGET;
  const mergeUnder = options.mergeUnder ?? MERGE_UNDER;
  const value = (text || "").trim();
  if (!value) return [];

  const structural: Array<{ content: string; isCode: boolean }> = [];
  let current: string[] = [];
  let inCode = false;
  for (const line of value.split("\n")) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        current.push(line);
        structural.push({ content: current.join("\n"), isCode: true });
        current = [];
      } else {
        if (current.length)
          structural.push({ content: current.join("\n"), isCode: false });
        current = [line];
      }
      inCode = !inCode;
    } else {
      current.push(line);
    }
  }
  if (current.length)
    structural.push({ content: current.join("\n"), isCode: inCode });

  const pieces: string[] = [];
  for (const part of structural) {
    const body = part.content.trim();
    if (!body) continue;
    if (part.isCode) {
      pieces.push(body);
      continue;
    }
    for (const raw of body.split("\n\n")) {
      const paragraph = raw.trim();
      if (!paragraph) continue;
      if (paragraph.length <= sentenceTarget) pieces.push(paragraph);
      else pieces.push(...splitCompleteSentences(paragraph));
    }
  }

  const merged: string[] = [];
  let buffer = "";
  for (const piece of pieces) {
    if (piece.startsWith("```")) {
      if (buffer) {
        merged.push(buffer.trim());
        buffer = "";
      }
      merged.push(piece);
    } else if (!buffer) {
      buffer = piece;
    } else if (buffer.length + piece.length < mergeUnder) {
      buffer += ` ${piece}`;
    } else {
      merged.push(buffer.trim());
      buffer = piece;
    }
  }
  if (buffer) merged.push(buffer.trim());
  return merged.filter((item) => item.length > 0);
}

/**
 * 闲聊分段：最多切成几段短气泡；出现代码块或结构化标记时整条发出。
 */
export function splitConversationText(
  text: string,
  options: { maxSegments?: number } = {},
): string[] {
  const maxSegments = options.maxSegments ?? CONVERSATION_MAX_SEGMENTS;
  const value = (text || "").trim();
  if (!value) return [];
  const markers = ["```", "\n1.", "\n- ", "\n|", "{"];
  if (markers.some((marker) => value.includes(marker))) return [value];

  const explicit = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const pieces =
    explicit.length > 1 ? explicit : splitCompleteSentences(value);

  const result: string[] = [];
  let current = "";
  for (const raw of pieces) {
    const piece = raw.trim();
    if (!piece) continue;
    if (!current) current = piece;
    else if (current.length + piece.length <= CONVERSATION_MERGE_LIMIT)
      current += piece;
    else {
      result.push(current);
      current = piece;
    }
  }
  if (current) result.push(current);
  if (result.length <= maxSegments) return result;
  const head = result.slice(0, maxSegments - 1);
  head.push(result.slice(maxSegments - 1).join(""));
  return head;
}
