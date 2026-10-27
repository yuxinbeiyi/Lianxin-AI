/**
 * 新版界面的文本分段逻辑（TypeScript 版）。
 *
 * 短日常对话按完整句子拆分，长内容、结构化回复与工作类请求保持完整。
 */

const SENTENCE_TARGET = 80;
const MERGE_UNDER = 60;
const CONVERSATION_MAX_SEGMENTS = 3;
const CONVERSATION_MERGE_LIMIT = 46;
const CONVERSATION_LENGTH_LIMIT = 220;

/** 回复是否应按「闲聊」节奏切分：短、无代码块、无结构化标记。 */
export function shouldSendAsConversation(text: string, userPrompt = ""): boolean {
  const value = (text || "").trim();
  const prompt = (userPrompt || "").trim();
  if (!value || value.length > CONVERSATION_LENGTH_LIMIT || /```|~~~/.test(value))
    return false;
  const structuredPattern = /(^|\n)\s*(?:#{1,6}\s|[-*+\u2022]\s|\d+[.)\u3001]\s*|[\u4e00-\u5341]+[\u3001\uff09]|>\s|\|)|\|[^\n]+\|/m;
  if (structuredPattern.test(value) || /\b(?:Traceback|def\s+\w+|class\s+\w+)\b/.test(value))
    return false;
  const workIntentPattern = /\u67e5\u627e|\u67e5\u8d44\u6599|\u641c\u7d22|\u68c0\u7d22|\u6587\u732e|\u8bba\u6587|\u5206\u6790|\u65b9\u6848|\u5b9e\u73b0|\u4ee3\u7801|\u5f00\u53d1|\u5de5\u4f5c|\u62a5\u544a|\u603b\u7ed3|\u6574\u7406|\u7ffb\u8bd1|\u6da6\u8272|\u8ba1\u5212|\u8bbe\u8ba1|\u8c03\u8bd5|\u4fee\u590d|\u6d4b\u8bd5|\u8868\u683c|\u63a8\u5bfc|\u8ba1\u7b97|\u8be6\u7ec6\u89e3\u91ca|\b(?:research|paper|report|debug|implement|translate|code)\b/i;
  if (/```|~~~/.test(prompt) || structuredPattern.test(prompt) || workIntentPattern.test(prompt)) return false;
  return true;
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
