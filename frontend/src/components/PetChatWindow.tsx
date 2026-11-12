import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { emit, listen } from "@tauri-apps/api/event";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { currentMonitor, getCurrentWindow, LogicalSize, PhysicalPosition } from "@tauri-apps/api/window";
import { Mic, Send, Settings, Square, Volume2, VolumeX } from "lucide-react";
import { lianxinApi, type PetSettingsState, type PetSettingsValues } from "../services/lianxinApi";
import { openSettingsWindow } from "../utils/settingsWindow";
import { PET_LABELS } from "../utils/petWindows";
import "../styles/pet.css";

type ChatMessage = { id?: number; role?: string; content?: string; timestamp?: string };
type PetStatePayload = { kind: "thinking" | "speaking" | "emotion" | "base"; state?: string; active?: boolean };
type PetMovedPayload = { label?: string; x: number; y: number; width: number; height: number };

const DEFAULT_VALUES: PetSettingsValues = {
  fontSize: 12, fontBold: false, typingSpeed: 40, panelWidth: 360, panelHeight: 300,
  panelOpacity: 92, spriteScale: 100, speakingBounce: true, autoShowDialog: true,
};
type ResizeDirection = "East" | "North" | "NorthEast" | "NorthWest" | "South" | "SouthEast" | "SouthWest" | "West";
const RESIZE_DIRECTIONS: Record<string, ResizeDirection> = {
  n: "North", s: "South", w: "West", e: "East",
  nw: "NorthWest", ne: "NorthEast", sw: "SouthWest", se: "SouthEast",
};

export function PetChatWindow() {
  const windowRef = useRef(getCurrentWindow());
  const [values, setValues] = useState<PetSettingsValues>(DEFAULT_VALUES);
  const [reply, setReply] = useState("");
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("就绪");
  const [statusActive, setStatusActive] = useState(false);
  const showStatus = (text: string, active = false) => { setStatus(text); setStatusActive(active); };
  const [sending, setSending] = useState(false);
  const [autoSend, setAutoSend] = useState(true);
  const [voiceActive, setVoiceActive] = useState(false);
  const [muted, setMuted] = useState(false);

  const replyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const typingTimer = useRef<number | null>(null);
  const typingSpeedRef = useRef(DEFAULT_VALUES.typingSpeed);
  const settingsRef = useRef(DEFAULT_VALUES);
  const scaleFactorRef = useRef(1);
  const sendingRef = useRef(false);
  const sessionIdRef = useRef<number | null>(null);
  const cursorRef = useRef(0);
  const baselineRef = useRef(false);
  const pollingRef = useRef(false);
  const thinkingRef = useRef(false);
  const speakingRef = useRef(false);
  const voiceCursorRef = useRef(0);
  const voiceActiveRef = useRef(false);
  const autoSendRef = useRef(true);
  const draftRef = useRef("");
  const pendingReplyRef = useRef<{ text: string; at: number } | null>(null);
  const selfReplyRef = useRef<{ text: string; at: number } | null>(null);
  const lastAssistantRef = useRef<{ text: string; at: number } | null>(null);
  const petEventCursorRef = useRef(0);
  const positionedRef = useRef(false);

  useEffect(() => {
    document.documentElement.classList.add("chat-host");
    return () => document.documentElement.classList.remove("chat-host");
  }, []);

  useEffect(() => { draftRef.current = draft; }, [draft]);
  useEffect(() => { autoSendRef.current = autoSend; }, [autoSend]);
  useEffect(() => { voiceActiveRef.current = voiceActive; }, [voiceActive]);
  useEffect(() => { settingsRef.current = values; typingSpeedRef.current = values.typingSpeed; }, [values]);
  useEffect(() => () => { if (typingTimer.current) clearInterval(typingTimer.current); }, []);

  const emitState = useCallback((payload: PetStatePayload) => {
    void emit("pet-state", payload).catch(() => undefined);
  }, []);

  const behaviorState = async (payload: { event: "thinking" | "speaking" | "emotion" | "expression"; text?: string; emotion?: string }) => {
    try {
      return (await lianxinApi.petBehavior(payload)).state;
    } catch {
      return undefined;
    }
  };

  const scrollReplyToBottom = () => {
    const area = replyRef.current;
    if (area) area.scrollTop = area.scrollHeight;
  };

  const startTypewriter = useCallback((text: string) => {
    if (typingTimer.current) clearInterval(typingTimer.current);
    setReply("");
    let index = 0;
    typingTimer.current = setInterval(() => {
      index += 1;
      setReply(text.slice(0, index));
      scrollReplyToBottom();
      if (index >= text.length && typingTimer.current) {
        clearInterval(typingTimer.current);
        typingTimer.current = null;
      }
    }, Math.max(5, typingSpeedRef.current));
  }, []);

  const displayReply = useCallback(async (text: string, options: { speak: boolean; self?: boolean }) => {
    const content = String(text || "").trim();
    if (!content) return;
    const at = Date.now();
    if (options.self) selfReplyRef.current = { text: content, at };
    lastAssistantRef.current = { text: content, at };
    startTypewriter(content);
    showStatus(options.speak ? "正在回复" : "收到新消息", options.speak);
    setStatusActive(options.speak);
    const emotion = await behaviorState({ event: "emotion", text: content });
    if (emotion) emitState({ kind: "emotion", state: emotion });
    void emit("pet-preview", { text: content }).catch(() => undefined);
    if (options.speak) void lianxinApi.speak(content).catch(() => undefined);
    if (settingsRef.current.autoShowDialog) void windowRef.current.show().catch(() => undefined);
  }, [emitState, startTypewriter]);

  const followPet = useCallback(async (payload: PetMovedPayload, settings: PetSettingsValues, factor: number) => {
    const chatWidth = settings.panelWidth * factor;
    const chatHeight = settings.panelHeight * factor;
    const monitor = await currentMonitor().catch(() => null);
    const area = monitor?.workArea;
    let x = payload.x - chatWidth - 20 * factor;
    let y = payload.y + payload.height - chatHeight;
    if (area) {
      x = Math.max(area.position.x, Math.min(x, area.position.x + area.size.width - chatWidth));
      y = Math.max(area.position.y, Math.min(y, area.position.y + area.size.height - chatHeight));
    }
    await windowRef.current.setPosition(new PhysicalPosition(Math.round(x), Math.round(y))).catch(() => undefined);
  }, []);

  const positionBesidePet = useCallback(async (settings: PetSettingsValues, factor: number) => {
    const pet = (await WebviewWindow.getByLabel(PET_LABELS.sprite).catch(() => null))
      ?? (await WebviewWindow.getByLabel(PET_LABELS.live2d).catch(() => null));
    if (!pet) return;
    const [position, size] = await Promise.all([pet.outerPosition(), pet.outerSize()]);
    await followPet({ x: position.x, y: position.y, width: size.width, height: size.height }, settings, factor);
  }, [followPet]);

  const applyValues = useCallback(async (next: PetSettingsValues) => {
    setValues(next);
    typingSpeedRef.current = next.typingSpeed;
    settingsRef.current = next;
    try {
      const window = windowRef.current;
      const factor = await window.scaleFactor();
      scaleFactorRef.current = factor;
      await window.setSize(new LogicalSize(next.panelWidth, next.panelHeight));
      await positionBesidePet(next, factor);
      positionedRef.current = true;
    } catch {
      // 尺寸应用失败不阻塞聊天。
    }
  }, [positionBesidePet]);

  const loadSettings = useCallback(async () => {
    try {
      const state: PetSettingsState = await lianxinApi.petSettings();
      await applyValues(state.values);
    } catch (cause) {
      showStatus(`设置读取失败：${String(cause)}`, false);
    }
  }, [applyValues]);

  useEffect(() => { void loadSettings(); }, [loadSettings]);
  useEffect(() => {
    void lianxinApi.settingsPanels().then((state) => setMuted(Boolean(state.global.silentMode))).catch(() => undefined);
  }, []);
  useEffect(() => { const area = replyRef.current; if (area) area.scrollTop = area.scrollHeight; }, [reply]);

  const send = async () => {
    const text = draftRef.current.trim();
    if (!text || sendingRef.current) return;
    setDraft("");
    draftRef.current = "";
    setSending(true);
    sendingRef.current = true;
    pendingReplyRef.current = null;
    showStatus("莲心正在思考", true);
    setStatusActive(true);
    emitState({ kind: "thinking", active: true, state: await behaviorState({ event: "thinking" }) });
    try {
      const result = await lianxinApi.petChat(text);
      const content = String(result?.message?.content || "").trim();
      if (!content) throw new Error("莲心没有返回内容");
      await displayReply(content, { speak: true, self: true });
    } catch (cause) {
      const pending = pendingReplyRef.current as { text: string; at: number } | null;
      if (pending?.text) {
        await displayReply(pending.text, { speak: false, self: true });
      } else {
        setDraft(text);
        draftRef.current = text;
        showStatus(`发送失败：${String(cause)}`, false);
        setStatusActive(false);
      }
    } finally {
      sendingRef.current = false;
      setSending(false);
      emitState({ kind: "thinking", active: false });
      if (!speakingRef.current) {
        showStatus("就绪", false);
        setStatusActive(false);
      }
    }
  };

  const pollMessages = async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      const state = await lianxinApi.status();
      if (!state.sessionId) return;
      if (sessionIdRef.current !== state.sessionId) {
        sessionIdRef.current = state.sessionId;
        cursorRef.current = 0;
        baselineRef.current = false;
        lastAssistantRef.current = null;
      }
      const result = await lianxinApi.messages(state.sessionId, cursorRef.current);
      const items = result.items as ChatMessage[];
      const assistant = items.filter((item) => item.role === "assistant" && String(item.content || "").trim()).at(-1);
      const latestId = items.reduce((max, item) => Math.max(max, Number(item.id) || 0), cursorRef.current);
      if (!baselineRef.current) {
        baselineRef.current = true;
        cursorRef.current = latestId;
      } else if (assistant) {
        const text = String(assistant.content || "").trim();
        const now = Date.now();
        const sameAsSelf = selfReplyRef.current?.text === text && now - selfReplyRef.current.at < 15000;
        const sameAsLast = lastAssistantRef.current?.text === text && now - lastAssistantRef.current.at < 15000;
        cursorRef.current = latestId;
        if (sendingRef.current) pendingReplyRef.current = { text, at: now };
        else if (!sameAsSelf && !sameAsLast) await displayReply(text, { speak: false });
      } else {
        cursorRef.current = latestId;
      }
      if (state.busy !== thinkingRef.current && !sendingRef.current) {
        thinkingRef.current = state.busy;
        emitState({ kind: "thinking", active: state.busy, state: state.busy ? await behaviorState({ event: "thinking" }) : undefined });
      }
    } catch {
      // 后端暂不可用时保留当前气泡状态，下一轮重试。
    } finally {
      pollingRef.current = false;
    }
  };

  const pollTts = async () => {
    try {
      const state = await lianxinApi.ttsStatus();
      const speaking = Boolean(state.speaking);
      if (speaking === speakingRef.current) return;
      speakingRef.current = speaking;
      if (speaking) {
        showStatus("正在回复", true);
        setStatusActive(true);
        emitState({ kind: "speaking", active: true, state: await behaviorState({ event: "speaking" }) });
      } else {
        emitState({ kind: "speaking", active: false });
        if (!sendingRef.current) {
          showStatus("就绪", false);
          setStatusActive(false);
        }
      }
    } catch {
      // 忽略单次轮询失败。
    }
  };

  const pollPetEvents = async () => {
    try {
      const result = await lianxinApi.petEvents(petEventCursorRef.current);
      if (!petEventCursorRef.current) {
        petEventCursorRef.current = result.latest;
        return;
      }
      petEventCursorRef.current = result.latest;
      for (const event of result.items) {
        if (event.type === "expression" && event.emotion) {
          const state = await behaviorState({ event: "expression", emotion: event.emotion });
          if (state) emitState({ kind: "base", state });
        }
      }
    } catch {
      // 忽略单次轮询失败。
    }
  };

  useEffect(() => {
    const messageTimer = setInterval(() => { void pollMessages(); }, 800);
    const ttsTimer = setInterval(() => { void pollTts(); }, 1000);
    const eventTimer = setInterval(() => { void pollPetEvents(); }, 1200);
    return () => {
      clearInterval(messageTimer);
      clearInterval(ttsTimer);
      clearInterval(eventTimer);
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      void lianxinApi.voiceEvents(voiceCursorRef.current).then((result) => {
        voiceCursorRef.current = result.latest;
        if (!voiceActiveRef.current) return;
        for (const event of result.items) {
          if (event.type !== "voice.input") continue;
          setVoiceActive(false);
          if (event.content) {
            setDraft(event.content);
            draftRef.current = event.content;
            if (autoSendRef.current) void send();
          } else {
            showStatus("未识别到语音", false);
          }
        }
      }).catch(() => undefined);
    }, 700);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let disposed = false;
    const unlisteners: Array<() => void> = [];
    const add = (promise: Promise<() => void>) => {
      void promise.then((unlisten) => { if (disposed) unlisten(); else unlisteners.push(unlisten); }).catch(() => undefined);
    };
    add(listen<PetMovedPayload>("pet-moved", (event) => {
      if (!event.payload) return;
      void followPet(event.payload, settingsRef.current, scaleFactorRef.current);
    }));
    add(listen("pet-toggle-chat", () => {
      const window = windowRef.current;
      void window.isVisible().then((visible) => visible ? window.hide() : window.show()).catch(() => undefined);
    }));
    add(listen("pet-exit", () => { void windowRef.current.destroy(); }));
    add(listen<{ visible?: boolean }>("pet-set-visible", (event) => {
      const window = windowRef.current;
      if (event.payload?.visible) void window.show().catch(() => undefined);
      else void window.hide().catch(() => undefined);
    }));
    add(listen("pet-settings-updated", () => { void loadSettings(); }));
    add(windowRef.current.onCloseRequested((event) => {
      event.preventDefault();
      void windowRef.current.hide().catch(() => undefined);
    }));
    return () => { disposed = true; unlisteners.forEach((unlisten) => unlisten()); };
  }, [followPet, loadSettings]);

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(100, Math.max(36, element.scrollHeight + 2))}px`;
  }, [draft]);

  const toggleVoice = async () => {
    try {
      if (voiceActive) {
        await lianxinApi.stopVoiceInput();
        setVoiceActive(false);
        showStatus("已停止聆听", false);
      } else {
        await lianxinApi.startVoiceInput();
        setVoiceActive(true);
        showStatus("正在聆听", true);
        setStatusActive(true);
      }
    } catch (cause) {
      setVoiceActive(false);
      showStatus(`语音失败：${String(cause)}`, false);
    }
  };

  const toggleMute = async () => {
    const next = !muted;
    setMuted(next);
    try {
      await lianxinApi.saveSettingsPanels({ global: { silentMode: next }, sound: { silentMode: next } });
      if (next) await lianxinApi.stopSpeaking();
    } catch (cause) {
      setMuted(!next);
      showStatus(`静音设置失败：${String(cause)}`, false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  const onPanelPointerDown = (event: PointerEvent) => {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, button, label, .pet-chat-reply")) return;
    void windowRef.current.startDragging().catch(() => undefined);
  };

  const opacity = Math.max(0.3, Math.min(1, values.panelOpacity / 100));
  const panelStyle = { "--pet-opacity": String(opacity) } as CSSProperties;
  const textStyle: CSSProperties = { fontSize: values.fontSize, fontWeight: values.fontBold ? 700 : 500 };

  return (
    <div className="pet-chat-window">
      <div className="pet-chat-panel" style={panelStyle} onPointerDown={onPanelPointerDown}>
        {(Object.keys(RESIZE_DIRECTIONS) as Array<keyof typeof RESIZE_DIRECTIONS>).map((edge) => (
          <div
            key={edge}
            className={`pet-chat-edge is-${edge}`}
            onPointerDown={(event) => {
              event.preventDefault();
              event.stopPropagation();
              void windowRef.current.startResizeDragging(RESIZE_DIRECTIONS[edge]).catch(() => undefined);
            }}
          />
        ))}
        <div ref={replyRef} className="pet-chat-reply" style={textStyle}>{reply}</div>
        <textarea
          ref={inputRef}
          className="pet-chat-input"
          style={textStyle}
          value={draft}
          placeholder="说点什么吧（Enter 发送，Shift+Enter 换行）"
          onChange={(event) => { setDraft(event.target.value); draftRef.current = event.target.value; }}
          onKeyDown={onKeyDown}
          disabled={sending}
        />
        <div className="pet-chat-bar">
          <span className={`pet-chat-status${statusActive ? " is-active" : ""}`}>● {status}</span>
          <button className={`pet-chat-btn${voiceActive ? " is-active" : ""}`} onClick={() => void toggleVoice()} title={voiceActive ? "停止录音" : "点击录音，自动转换为文字"} aria-label="语音输入">
            {voiceActive ? <Square size={14} /> : <Mic size={14} />}
          </button>
          <label className="pet-chat-autosend">
            <input type="checkbox" checked={autoSend} onChange={(event) => setAutoSend(event.target.checked)} />
            自动发送
          </label>
          <button className="pet-chat-btn" onClick={() => { void openSettingsWindow("pet").catch(() => undefined); }} title="桌宠设置" aria-label="桌宠设置">
            <Settings size={14} />
          </button>
          <button className="pet-chat-btn" onClick={() => void toggleMute()} title={muted ? "已静音，点击取消静音" : "点击静音"} aria-label="静音">
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          <button className="pet-chat-send" onClick={() => void send()} disabled={sending || !draft.trim()}>
            {sending ? "…" : <><Send size={13} />发送</>}
          </button>
        </div>
      </div>
    </div>
  );
}
