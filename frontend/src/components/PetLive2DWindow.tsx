import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import { emit, listen } from "@tauri-apps/api/event";
import { CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu } from "@tauri-apps/api/menu";
import {
  currentMonitor, cursorPosition, getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor,
} from "@tauri-apps/api/window";
import { API_ROOT, lianxinApi, type PetSettingsState, type PetSettingsValues } from "../services/lianxinApi";
import { gridMask, hitTestMask, type AlphaMask } from "../utils/petMask";
import { PET_LABELS } from "../utils/petWindows";
import "../styles/pet.css";

type Layout = { width: number; height: number };
type PetStatePayload = { kind?: "thinking" | "speaking" | "emotion" | "base"; state?: string; active?: boolean };
type PlayerMessage = Record<string, unknown> & { source?: string; type?: string };
type MaskMessage = { width: number; height: number; values: number[] };

const REACTIONS: Array<[string, string]> = [
  ["点头", "nod"], ["摇头", "shake"], ["摸摸头", "headpat"], ["微笑", "smile"],
  ["难过", "sad"], ["生气", "angry"], ["惊讶", "surprised"], ["恢复自然", "normal"],
];

export function PetLive2DWindow() {
  const windowRef = useRef(getCurrentWindow());
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const layoutRef = useRef<Layout>({ width: 370, height: 660 });
  const scaleFactorRef = useRef(1);
  const originRef = useRef<{ x: number; y: number } | null>(null);
  const maskRef = useRef<AlphaMask | null>(null);
  const ignoreRef = useRef(false);
  const draggingRef = useRef(false);
  const pressRef = useRef<{ x: number; y: number; at: number } | null>(null);
  const lastInteractionRef = useRef(0);
  const readyRef = useRef(false);
  const speakingRef = useRef(false);
  const thinkingRef = useRef(false);
  const talkingRef = useRef(false);
  const visibleRef = useRef(true);
  const trackingEnabledRef = useRef(true);
  const previewUntilRef = useRef(0);
  const previewTimerRef = useRef<number | null>(null);
  const lastTrackingRef = useRef({ x: 0, y: 0 });
  const readyTimerRef = useRef<number | null>(null);
  const lastMovedRef = useRef(0);
  const positionedRef = useRef(false);
  const settingsRef = useRef<PetSettingsValues | null>(null);
  const walkDirectionRef = useRef(0);
  const walkTimerRef = useRef<number | null>(null);

  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState<PetSettingsValues | null>(null);
  const [wheelScale, setWheelScale] = useState<number | null>(null);
  const [trackingEnabled, setTrackingEnabled] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    document.documentElement.classList.add("pet-host");
    return () => document.documentElement.classList.remove("pet-host");
  }, []);

  useEffect(() => { trackingEnabledRef.current = trackingEnabled; }, [trackingEnabled]);

  const sendToPlayer = useCallback((message: PlayerMessage) => {
    iframeRef.current?.contentWindow?.postMessage({ source: "lianxin-live2d-host", ...message }, "*");
  }, []);

  const updateTalking = useCallback(() => {
    const talking = speakingRef.current || Date.now() < previewUntilRef.current;
    if (talking === talkingRef.current) return;
    talkingRef.current = talking;
    sendToPlayer({ type: "state", state: talking ? "talking" : "idle", visible: visibleRef.current });
  }, [sendToPlayer]);

  const startPreview = useCallback((milliseconds: number) => {
    previewUntilRef.current = Date.now() + milliseconds;
    updateTalking();
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => {
      previewTimerRef.current = null;
      updateTalking();
    }, milliseconds);
  }, [updateTalking]);

  const emitMoved = useCallback(async () => {
    const now = Date.now();
    if (now - lastMovedRef.current < 100) return;
    lastMovedRef.current = now;
    try {
      const [position, size] = await Promise.all([windowRef.current.outerPosition(), windowRef.current.outerSize()]);
      originRef.current = { x: position.x, y: position.y };
      await emit("pet-moved", { label: PET_LABELS.live2d, x: position.x, y: position.y, width: size.width, height: size.height });
    } catch {
      // ignore while closing.
    }
  }, []);

  const applyLayout = useCallback(async (nextScale: number) => {
    const window = windowRef.current;
    const factor = await window.scaleFactor().catch(() => 1);
    scaleFactorRef.current = factor;
    const monitor = await currentMonitor().catch(() => null) ?? await primaryMonitor().catch(() => null);
    const area = monitor?.workArea;
    const maxHeight = area ? Math.max(200, Math.round(area.size.height / factor - 70)) : 620;
    const height = Math.max(200, Math.min(Math.round(620 * nextScale), maxHeight));
    const width = Math.max(110, Math.round(height * 0.56));
    const next: Layout = { width, height };
    layoutRef.current = next;
    maskRef.current = null;
    if (!positionedRef.current) {
      await window.setSize(new LogicalSize(width, height)).catch(() => undefined);
      if (area) {
        const x = Math.max(area.position.x, area.position.x + area.size.width - width * factor - 40 * factor);
        const y = Math.max(area.position.y, area.position.y + area.size.height - height * factor - 60 * factor);
        await window.setPosition(new PhysicalPosition(Math.round(x), Math.round(y))).catch(() => undefined);
      }
      positionedRef.current = true;
    } else {
      const [position, outer] = await Promise.all([window.outerPosition(), window.outerSize()]);
      const physicalWidth = width * factor;
      const physicalHeight = height * factor;
      await window.setSize(new LogicalSize(width, height)).catch(() => undefined);
      await window.setPosition(new PhysicalPosition(
        Math.round(position.x + (outer.width - physicalWidth) / 2),
        Math.round(position.y + (outer.height - physicalHeight)),
      )).catch(() => undefined);
    }
    void emitMoved();
  }, [emitMoved]);

  const loadSettings = useCallback(async () => {
    try {
      const state: PetSettingsState = await lianxinApi.petSettings();
      setSettings(state.values);
      settingsRef.current = state.values;
    } catch (cause) {
      setError(`设置读取失败：${String(cause)}`);
    }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);

  useEffect(() => {
    const percent = settings?.spriteScale ?? 100;
    const next = wheelScale ?? Math.max(0.4, Math.min(3, percent / 100));
    void applyLayout(next);
  }, [settings?.spriteScale, wheelScale, applyLayout]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      const frame = iframeRef.current;
      if (!frame || event.source !== frame.contentWindow) return;
      const data = event.data as PlayerMessage | null;
      if (!data || data.source !== "lianxin-live2d") return;
      if (data.type === "ready") {
        readyRef.current = true;
        setReady(true);
        setError("");
        if (readyTimerRef.current) { clearTimeout(readyTimerRef.current); readyTimerRef.current = null; }
        sendToPlayer({ type: "state", state: "idle", visible: visibleRef.current });
        sendToPlayer({ type: "thinking", active: thinkingRef.current && !speakingRef.current });
        return;
      }
      if (data.type === "error") {
        setError(String(data.message || "Live2D 加载失败"));
        return;
      }
      if (data.type === "mask-reset") {
        maskRef.current = null;
        return;
      }
      if (data.type === "mask") {
        const payload = data as PlayerMessage & MaskMessage;
        const next = gridMask(Number(payload.width) || 0, Number(payload.height) || 0, Array.isArray(payload.values) ? payload.values : []);
        const previous = maskRef.current;
        if (previous && previous.width === next.width && previous.height === next.height) {
          for (let index = 0; index < previous.data.length; index += 1) {
            previous.data[index] = previous.data[index] || next.data[index];
          }
        } else {
          maskRef.current = next;
        }
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [sendToPlayer]);

  useEffect(() => {
    if (readyTimerRef.current) clearTimeout(readyTimerRef.current);
    readyTimerRef.current = setTimeout(() => {
      readyTimerRef.current = null;
      if (!readyRef.current) setError("Live2D 加载超时，请检查 Live2D/ 资源与后端日志");
    }, 15000);
    return () => { if (readyTimerRef.current) { clearTimeout(readyTimerRef.current); readyTimerRef.current = null; } };
  }, [reloadKey]);

  useEffect(() => {
    let disposed = false;
    let movedUnlisten: (() => void) | undefined;
    const window = windowRef.current;
    void window.onMoved(({ payload }) => {
      originRef.current = { x: payload.x, y: payload.y };
      void emitMoved();
    }).then((unlisten) => { if (disposed) unlisten(); else movedUnlisten = unlisten; }).catch(() => undefined);
    window.outerPosition().then((position) => { originRef.current = { x: position.x, y: position.y }; }).catch(() => undefined);
    return () => { disposed = true; movedUnlisten?.(); };
  }, [emitMoved]);

  useEffect(() => {
    let disposed = false;
    const window = windowRef.current;
    const trackingTimer = setInterval(async () => {
      if (disposed) return;
      const frame = iframeRef.current;
      if (!frame || !readyRef.current) return;
      if (!visibleRef.current || !trackingEnabledRef.current || draggingRef.current) {
        if (lastTrackingRef.current.x !== 0 || lastTrackingRef.current.y !== 0) {
          lastTrackingRef.current = { x: 0, y: 0 };
          sendToPlayer({ type: "tracking", x: 0, y: 0 });
        }
        return;
      }
      try {
        const cursor = await cursorPosition();
        let origin = originRef.current;
        if (!origin) {
          const position = await window.outerPosition();
          origin = { x: position.x, y: position.y };
          originRef.current = origin;
        }
        const x = (cursor.x - origin.x) / scaleFactorRef.current;
        const y = (cursor.y - origin.y) / scaleFactorRef.current;
        const { width, height } = layoutRef.current;
        const nearby = x > -180 && x < width + 180 && y > -180 && y < height + 180;
        const nextX = nearby ? Math.max(-1, Math.min(1, (x - width / 2) / Math.max(1, width * 0.8))) : 0;
        const nextY = nearby ? Math.max(-1, Math.min(1, (height * 0.28 - y) / Math.max(1, height * 0.65))) : 0;
        if (Math.abs(nextX - lastTrackingRef.current.x) > 0.01 || Math.abs(nextY - lastTrackingRef.current.y) > 0.01) {
          lastTrackingRef.current = { x: nextX, y: nextY };
          sendToPlayer({ type: "tracking", x: nextX, y: nextY });
        }
      } catch {
        // Cursor lookups are best-effort.
      }
    }, 100);
    const visibilityTimer = setInterval(async () => {
      if (disposed) return;
      try {
        const visible = await window.isVisible();
        if (visible !== visibleRef.current) {
          visibleRef.current = visible;
          sendToPlayer({ type: "state", state: talkingRef.current ? "talking" : "idle", visible });
        }
      } catch {
        // ignore.
      }
    }, 500);
    return () => { disposed = true; clearInterval(trackingTimer); clearInterval(visibilityTimer); };
  }, [sendToPlayer]);

  useEffect(() => {
    if (!ready) return;
    let disposed = false;
    const window = windowRef.current;
    const tick = async () => {
      if (disposed) return;
      const mask = maskRef.current;
      if (!mask) return;
      try {
        const cursor = await cursorPosition();
        let origin = originRef.current;
        if (!origin) {
          const position = await window.outerPosition();
          origin = { x: position.x, y: position.y };
          originRef.current = origin;
        }
        const x = (cursor.x - origin.x) / scaleFactorRef.current;
        const y = (cursor.y - origin.y) / scaleFactorRef.current;
        const { width, height } = layoutRef.current;
        const inside = hitTestMask(mask, (x / Math.max(1, width)) * mask.width, (y / Math.max(1, height)) * mask.height, 2);
        const ignore = !inside;
        if (ignore !== ignoreRef.current) {
          ignoreRef.current = ignore;
          await window.setIgnoreCursorEvents(ignore).catch(() => { ignoreRef.current = !ignore; });
        }
      } catch {
        if (ignoreRef.current) {
          ignoreRef.current = false;
          await window.setIgnoreCursorEvents(false).catch(() => undefined);
        }
      }
    };
    const timer = setInterval(() => void tick(), 50);
    return () => { disposed = true; clearInterval(timer); };
  }, [ready]);

  useEffect(() => {
    let disposed = false;
    const unlisteners: Array<() => void> = [];
    const add = (promise: Promise<() => void>) => {
      void promise.then((unlisten) => { if (disposed) unlisten(); else unlisteners.push(unlisten); }).catch(() => undefined);
    };
    add(listen<PetStatePayload>("pet-state", (event) => {
      const payload = event.payload;
      if (!payload) return;
      if (payload.kind === "thinking") {
        thinkingRef.current = Boolean(payload.active);
        sendToPlayer({ type: "thinking", active: thinkingRef.current && !speakingRef.current });
      } else if (payload.kind === "speaking") {
        speakingRef.current = Boolean(payload.active);
        if (speakingRef.current) {
          thinkingRef.current = false;
          sendToPlayer({ type: "thinking", active: false });
        }
        updateTalking();
      }
    }));
    add(listen<{ text?: string }>("pet-preview", (event) => {
      const text = String(event.payload?.text || "").trim();
      if (!text || speakingRef.current) return;
      sendToPlayer({ type: "react", name: "nod" });
      startPreview(Math.max(1500, Math.min(14000, text.length * 60)));
    }));
    add(listen("pet-exit", () => { void windowRef.current.destroy(); }));
    add(listen<{ visible?: boolean }>("pet-set-visible", (event) => {
      const window = windowRef.current;
      if (event.payload?.visible) void window.show().catch(() => undefined);
      else void window.hide().catch(() => undefined);
    }));
    add(listen("pet-settings-updated", () => { setWheelScale(null); void loadSettings(); }));
    return () => { disposed = true; unlisteners.forEach((unlisten) => unlisten()); };
  }, [loadSettings, sendToPlayer, startPreview, updateTalking]);

  useEffect(() => {
    const window = windowRef.current;
    return () => { clearTimeout(previewTimerRef.current ?? undefined); sendToPlayer({ type: "dispose" }); };
  }, [sendToPlayer]);

  const reaction = (name: string) => {
    sendToPlayer({ type: "react", name });
  };

  const showMenu = async () => {
    const reactionItems = await Promise.all(REACTIONS.map(([label, name]) => MenuItem.new({ text: label, action: () => reaction(name) })));
    const reactionMenu = await Submenu.new({ text: "互动动作与表情", items: reactionItems });
    const tracking = await CheckMenuItem.new({
      text: "视线跟随鼠标（附近）",
      checked: trackingEnabledRef.current,
      action: () => setTrackingEnabled((value) => !value),
    });
    const items: Array<MenuItem | PredefinedMenuItem | CheckMenuItem | Submenu> = [
      reactionMenu,
      tracking,
      await PredefinedMenuItem.new({ item: "Separator" }),
      await MenuItem.new({ text: "向左移动", action: () => setWalk(-1) }),
      await MenuItem.new({ text: "向右移动", action: () => setWalk(1) }),
      await MenuItem.new({ text: "停止移动", action: () => setWalk(0) }),
      await PredefinedMenuItem.new({ item: "Separator" }),
      await MenuItem.new({ text: "试播说话动作", action: () => { sendToPlayer({ type: "react", name: "nod" }); startPreview(12000); } }),
      await MenuItem.new({ text: "停止试播", action: () => { previewUntilRef.current = 0; updateTalking(); } }),
      await MenuItem.new({ text: "隐藏/显示对话窗口", action: () => { void emit("pet-toggle-chat"); } }),
      await MenuItem.new({ text: "退出 Live2D 模式", action: () => { void emit("pet-exit"); void windowRef.current.destroy(); } }),
    ];
    const menu = await Menu.new({ items });
    await menu.popup().catch(() => undefined);
  };

  const setWalk = (direction: number) => {
    walkDirectionRef.current = direction;
    if (!direction) {
      if (walkTimerRef.current) { clearInterval(walkTimerRef.current); walkTimerRef.current = null; }
      return;
    }
    if (walkTimerRef.current) clearInterval(walkTimerRef.current);
    walkTimerRef.current = setInterval(async () => {
      try {
        const window = windowRef.current;
        const factor = await window.scaleFactor();
        const monitor = await currentMonitor();
        const [position, size] = await Promise.all([window.outerPosition(), window.outerSize()]);
        const area = monitor?.workArea;
        let x = position.x + walkDirectionRef.current * 2 * factor;
        if (area) x = Math.max(area.position.x, Math.min(x, area.position.x + area.size.width - size.width));
        await window.setPosition(new PhysicalPosition(Math.round(x), Math.round(position.y)));
        void emitMoved();
      } catch {
        // ignore.
      }
    }, 40);
  };

  useEffect(() => () => { if (walkTimerRef.current) clearInterval(walkTimerRef.current); }, []);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button === 0) {
      pressRef.current = { x: event.clientX, y: event.clientY, at: Date.now() };
      draggingRef.current = false;
    } else if (event.button === 1) {
      event.preventDefault();
      void showMenu();
    }
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const press = pressRef.current;
    if (!press) return;
    if (!draggingRef.current && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) {
      draggingRef.current = true;
      void windowRef.current.startDragging().catch(() => undefined);
      setTimeout(() => { draggingRef.current = false; }, 1500);
    }
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const press = pressRef.current;
    pressRef.current = null;
    if (!press || draggingRef.current || event.button !== 0) {
      draggingRef.current = false;
      return;
    }
    const elapsed = Date.now() - press.at;
    const distance = Math.hypot(event.clientX - press.x, event.clientY - press.y);
    if (elapsed < 800 && distance <= 4 && Date.now() - lastInteractionRef.current >= 800) {
      lastInteractionRef.current = Date.now();
      reaction(event.clientY < layoutRef.current.height * 0.38 ? "headpat" : "tap");
    }
    draggingRef.current = false;
  };

  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    const current = wheelScale ?? Math.max(0.4, Math.min(3, (settingsRef.current?.spriteScale ?? 100) / 100));
    const next = Math.max(0.4, Math.min(3, current * (event.deltaY < 0 ? 1.06 : 1 / 1.06)));
    setWheelScale(next);
  };

  return (
    <div
      className="pet-stage"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => { pressRef.current = null; draggingRef.current = false; }}
      onContextMenu={(event) => { event.preventDefault(); void emit("pet-toggle-chat"); }}
      onWheel={onWheel}
    >
      <iframe
        key={reloadKey}
        ref={iframeRef}
        className="pet-live2d-frame"
        src={`${API_ROOT}/live2d/html`}
        title="莲心 Live2D"
        onLoad={() => {
          if (readyRef.current) return;
          if (readyTimerRef.current) clearTimeout(readyTimerRef.current);
          readyTimerRef.current = setTimeout(() => {
            readyTimerRef.current = null;
            if (!readyRef.current) setError("Live2D 加载超时，请检查 Live2D/ 资源与后端日志");
          }, 12000);
        }}
      />
      <div className="pet-live2d-overlay" />
      {error && (
        <div className="pet-error" role="alert">
          {error}
          <button
            type="button"
            style={{ marginLeft: 8, color: "#ffe1e1", background: "transparent", border: "1px solid rgba(255,180,180,.5)", borderRadius: 6, padding: "2px 8px", cursor: "pointer", fontSize: 11 }}
            onClick={() => { setError(""); setReady(false); readyRef.current = false; setReloadKey((key) => key + 1); }}
          >
            重试
          </button>
        </div>
      )}
    </div>
  );
}
