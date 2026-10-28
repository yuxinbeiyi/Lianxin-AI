type PreparedSpeech = {
  prepared: boolean;
  id?: string;
  muted?: boolean;
  cancelled?: boolean;
};

type SpeechSequence = {
  prepare: (text: string, generation: number) => Promise<PreparedSpeech>;
  play: (id: string) => Promise<{ played: boolean }>;
  speak: (text: string, generation: number) => Promise<unknown>;
  reveal: (count: number) => void;
  notify?: () => Promise<unknown>;
  isCurrent: () => boolean;
  beforePlay: () => Promise<void>;
};

export async function playSpeechSequence(
  parts: string[],
  generation: number,
  sequence: SpeechSequence,
): Promise<void> {
  if (!parts.length || !sequence.isCurrent()) return;
  let notified = false;
  const reveal = (count: number) => {
    sequence.reveal(count);
    if (!notified && count > 0) {
      notified = true;
      void sequence.notify?.().catch(() => undefined);
    }
  };
  let prepared = await sequence.prepare(parts[0], generation).catch(() => null);
  for (let index = 0; index < parts.length; index += 1) {
    if (!sequence.isCurrent()) return;
    const preparedId = prepared?.id;
    if (!prepared?.prepared || !preparedId) {
      reveal(parts.length);
      if (!prepared?.muted && !prepared?.cancelled)
        await sequence.speak(parts.slice(index).join(" "), generation).catch(() => undefined);
      return;
    }
    reveal(index + 1);
    await sequence.beforePlay();
    if (!sequence.isCurrent()) return;
    const playback = sequence.play(preparedId).catch(() => ({ played: false }));
    const nextPreparation = index + 1 < parts.length
      ? sequence.prepare(parts[index + 1], generation).catch(() => null)
      : Promise.resolve(null);
    const played = await playback;
    if (!sequence.isCurrent()) return;
    if (!played.played) {
      reveal(parts.length);
      return;
    }
    if (index + 1 >= parts.length) return;
    prepared = await nextPreparation;
  }
}
