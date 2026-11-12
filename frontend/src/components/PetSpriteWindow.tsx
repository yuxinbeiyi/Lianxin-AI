import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type WheelEvent } from "react";
import { emit, listen } from "@tauri-apps/api/event";
import { Menu, MenuItem, PredefinedMenuItem } from "@tauri-apps/api/menu";
import {
  currentMonitor, cursorPosition, getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor,
} from "@tauri-apps/api/window";
import { API_ROOT, lianxinApi, type PetManifest, type PetSettingsValues, type PetStateDef } from "../services/lianxinApi";
import { containedImageMask, hitTestMask, type AlphaMask } from "../utils/petMask";
import { PET_LABELS } from "../utils/petWindows";
import "../styles/pet.css";

type Layout = {
  imageW: number; imageH: number; marginX: number; marginTop: number; marginBottom: number;
  windowW: number; windowH: number;
};
type PetStateEvent = { kind?: "thinking" | "speaking" | "emotion" | "base"; state?: string; active?: boolean };

const frameCache = new Map<string, HTMLImageElement[]>();

// 旧版按 KeepAspectRatio 显示（约 95×111）；新版最初的 95×150 会把角色纵向拉长。
// 按源图比例放宽显示框，角色会等比变宽；该系数就是外观调节参数。
const SPRITE_WIDTH_FACTOR = 1.35;

async function loadFrames(def: PetStateDef): Promise<HTMLImageElement[]> {
  const cached = frameCache.get(def.id);
  if (cached) return cached;
  const images = await Promise.all(def.frames.map((path) => new Promise<HTMLImageElement | null>((resolve) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => resolve(image);
    image.onerror = () => {
      console.warn("[pet] frame failed to load", image.src);
      resolve(null);
    };
    image.src = `${API_ROOT}/pet/assets/animations/${path}`;
  })));
  const loaded = images.filter((image): image is HTMLImageElement => image !== null);
  frameCache.set(def.id, loaded);
  return loaded;
}

export function PetSpriteWindow() {
  const [manifest, setManifest] = useState<PetManifest | null>(null);
  const [settings, setSettings] = useState<PetSettingsValues | null>(null);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [scale, setScale] = useState(1);
  const [frames, setFrames] = useState<HTMLImageElement[]>([]);
  const [frameIndex, setFrameIndex] = useState(0);
  const [baseState, setBaseState] = useState("idle");
  const [thinking, setThinking] = useState(false);
  const [thinkingState, setThinkingState] = useState("think");
  const [speaking, setSpeaking] = useState(false);
  const [speakingState, setSpeakingState] = useState("happy");
  const [walkDirection, setWalkDirection] = useState(0);
  const [wheelScale, setWheelScale] = useState<number | null>(null);
  const [frameError, setFrameError] = useState("");

  const positionedRef = useRef(false);
  const maskRef = useRef<AlphaMask | null>(null);
  const ignoreRef = useRef(false);
  const originRef = useRef<{ x: number; y: number } | null>(null);
  const scaleFactorRef = useRef(1);
  const lastMovedRef = useRef(0);

  const renderedState = speaking ? speakingState
    : thinking ? thinkingState
      : walkDirection ? (walkDirection < 0 ? "walk_left" : "walk_right")
        : baseState;

  const currentImage = frames.length ? frames[frameIndex % frames.length] : null;
  const currentStateDef = useMemo(
    () => manifest?.states.find((state) => state.id === renderedState) ?? null,
    [manifest, renderedState],
  );

  useEffect(() => {
    document.documentElement.classList.add("pet-host");
    return () => document.documentElement.classList.remove("pet-host");
  }, []);

  const emitMoved = useCallback(async () => {
    const now = Date.now();
    if (now - lastMovedRef.current < 100) return;
    lastMovedRef.current = now;
    try {
      const window = getCurrentWindow();
      const [position, size] = await Promise.all([window.outerPosition(), window.outerSize()]);
      originRef.current = { x: position.x, y: position.y };
      await emit("pet-moved", {
        label: PET_LABELS.sprite,
        x: position.x, y: position.y, width: size.width, height: size.height,
      });
    } catch {
      // 窗口正在关闭时忽略。
    }
  }, []);

  const applyLayout = useCallback(async (nextScale: number) => {
    if (!manifest) return;
    const [baseW, baseH] = manifest.frameSize;
    const imageW = Math.max(40, Math.round(baseW * SPRITE_WIDTH_FACTOR * nextScale));
    const imageH = Math.max(40, Math.round(baseH * nextScale));
    const marginX = Math.max(4, Math.round(imageW * 0.04));
    const marginTop = Math.max(8, Math.round(18 * nextScale));
    const marginBottom = Math.max(2, Math.round(imageH * 0.02));
    const next: Layout = { imageW, imageH, marginX, marginTop, marginBottom, windowW: imageW + marginX * 2, windowH: marginTop + imageH + marginBottom };
    setLayout(next);
    const window = getCurrentWindow();
    if (!positionedRef.current) {
      const factor = await window.scaleFactor().catch(() => 1);
      scaleFactorRef.current = factor;
      await window.setSize(new LogicalSize(next.windowW, next.windowH)).catch(() => undefined);
      const monitor = await currentMonitor().catch(() => null) ?? await primaryMonitor().catch(() => null);
      if (monitor) {
        const area = monitor.workArea;
        const x = Math.max(area.position.x, area.position.x + area.size.width - next.windowW * factor - 40 * factor);
        const y = Math.max(area.position.y, area.position.y + area.size.height - next.windowH * factor - 60 * factor);
        await window.setPosition(new PhysicalPosition(Math.round(x), Math.round(y))).catch(() => undefined);
      }
      positionedRef.current = true;
    } else {
      const factor = await window.scaleFactor().catch(() => 1);
      scaleFactorRef.current = factor;
      const [position, outer] = await Promise.all([window.outerPosition(), window.outerSize()]);
      const width = next.windowW * factor;
      const height = next.windowH * factor;
      const x = position.x + (outer.width - width) / 2;
      const y = position.y + (outer.height - height);
      await window.setSize(new LogicalSize(next.windowW, next.windowH)).catch(() => undefined);
      await window.setPosition(new PhysicalPosition(Math.round(x), Math.round(y))).catch(() => undefined);
    }
    void emitMoved();
  }, [manifest, emitMoved]);

  useEffect(() => {
    void lianxinApi.petManifest().then(setManifest).catch(() => undefined);
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const result = await lianxinApi.petSettings();
      setSettings(result.values);
    } catch {
      // 保留当前外观；聊天窗口会提示设置读取错误。
    }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);

  useEffect(() => {
    const percent = settings?.spriteScale ?? 100;
    const nextScale = wheelScale ?? Math.max(0.4, Math.min(3, percent / 100));
    setScale(nextScale);
    void applyLayout(nextScale);
  }, [settings?.spriteScale, wheelScale, applyLayout]);

  useEffect(() => {
    if (!currentStateDef) return;
    let cancelled = false;
    void loadFrames(currentStateDef).then((images) => {
      if (cancelled) return;
      setFrames(images);
      setFrameIndex(0);
      setFrameError(images.length ? "" : "桌宠素材加载失败，请检查后端 /api/pet/assets");
    });
    return () => { cancelled = true; };
  }, [currentStateDef]);

  useEffect(() => {
    if (!currentStateDef || !frames.length) return;
    const timer = setInterval(() => {
      setFrameIndex((index) => (index + 1) % frames.length);
    }, currentStateDef.interval);
    return () => clearInterval(timer);
  }, [currentStateDef, frames]);

  useEffect(() => {
    if (!currentImage || !layout) {
      maskRef.current = null;
      return;
    }
    try {
      maskRef.current = containedImageMask(currentImage, layout.imageW, layout.imageH);
    } catch (error) {
      // 跨域图片污染 canvas 或像素读取失败时退回矩形命中，不能因此让窗口崩溃。
      console.warn("[pet] alpha mask unavailable", error);
      maskRef.current = null;
    }
  }, [currentImage, layout]);

  useEffect(() => {
    const window = getCurrentWindow();
    let disposed = false;
    let movedUnlisten: (() => void) | undefined;
    void window.onMoved(({ payload }) => {
      originRef.current = { x: payload.x, y: payload.y };
      void emitMoved();
    }).then((unlisten) => {
      if (disposed) unlisten(); else movedUnlisten = unlisten;
    }).catch(() => undefined);
    void window.scaleFactor().then((factor) => { scaleFactorRef.current = factor; }).catch(() => undefined);
    window.outerPosition().then((position) => { originRef.current = { x: position.x, y: position.y }; }).catch(() => undefined);
    return () => { disposed = true; movedUnlisten?.(); };
  }, [emitMoved]);

  useEffect(() => {
    if (!layout) return;
    let disposed = false;
    const window = getCurrentWindow();
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
        const logicalX = (cursor.x - origin.x) / scaleFactorRef.current;
        const logicalY = (cursor.y - origin.y) / scaleFactorRef.current;
        const inside = hitTestMask(mask, logicalX - layout.marginX, logicalY - layout.marginTop, 2);
        const ignore = !inside;
        if (ignore !== ignoreRef.current) {
          ignoreRef.current = ignore;
          await window.setIgnoreCursorEvents(ignore).catch(() => { ignoreRef.current = !ignore; });
        }
      } catch {
        // 光标读取失败时保持可交互，避免把角色锁死。
        if (ignoreRef.current) {
          ignoreRef.current = false;
          await window.setIgnoreCursorEvents(false).catch(() => undefined);
        }
      }
    };
    const timer = setInterval(() => void tick(), 50);
    return () => { disposed = true; clearInterval(timer); };
  }, [layout]);

  useEffect(() => {
    let disposed = false;
    const unlisteners: Array<() => void> = [];
    const add = (promise: Promise<() => void>) => {
      void promise.then((unlisten) => {
        if (disposed) unlisten(); else unlisteners.push(unlisten);
      }).catch(() => undefined);
    };
    add(listen<PetStateEvent>("pet-state", (event) => {
      const payload = event.payload;
      if (!payload) return;
      if (payload.kind === "thinking") {
        if (payload.state) setThinkingState(payload.state);
        setThinking(Boolean(payload.active));
      } else if (payload.kind === "speaking") {
        if (payload.state) setSpeakingState(payload.state);
        setSpeaking(Boolean(payload.active));
      } else if (payload.kind === "emotion" || payload.kind === "base") {
        if (payload.state) setBaseState(payload.state);
      }
    }));
    add(listen("pet-exit", () => { void getCurrentWindow().destroy(); }));
    add(listen<{ visible?: boolean }>("pet-set-visible", (event) => {
      const window = getCurrentWindow();
      if (event.payload?.visible) void window.show().catch(() => undefined);
      else void window.hide().catch(() => undefined);
    }));
    add(listen("pet-settings-updated", () => {
      setWheelScale(null);
      void loadSettings();
    }));
    return () => { disposed = true; unlisteners.forEach((unlisten) => unlisten()); };
  }, [loadSettings]);

  useEffect(() => {
    if (!walkDirection) return;
    let disposed = false;
    const window = getCurrentWindow();
    const timer = setInterval(async () => {
      if (disposed) return;
      try {
        const factor = await window.scaleFactor();
        const monitor = await currentMonitor();
        const [position, size] = await Promise.all([window.outerPosition(), window.outerSize()]);
        const area = monitor?.workArea;
        let x = position.x + walkDirection * 2 * factor;
        if (area) {
          x = Math.max(area.position.x, Math.min(x, area.position.x + area.size.width - size.width));
        }
        await window.setPosition(new PhysicalPosition(Math.round(x), Math.round(position.y)));
        void emitMoved();
      } catch {
        // 忽略单次移动失败。
      }
    }, 40);
    return () => { disposed = true; clearInterval(timer); };
  }, [walkDirection, emitMoved]);

  const chooseBase = (state: string) => {
    setWalkDirection(0);
    setBaseState(state);
  };

  const showMenu = async () => {
    const items: Array<MenuItem | PredefinedMenuItem> = [];
    items.push(await MenuItem.new({ text: "向左移动", action: () => setWalkDirection(-1) }));
    items.push(await MenuItem.new({ text: "向右移动", action: () => setWalkDirection(1) }));
    items.push(await MenuItem.new({ text: "停止移动", action: () => setWalkDirection(0) }));
    items.push(await PredefinedMenuItem.new({ item: "Separator" }));
    for (const pose of manifest?.selectableStates ?? []) {
      items.push(await MenuItem.new({ text: pose.label, action: () => chooseBase(pose.id) }));
    }
    items.push(await PredefinedMenuItem.new({ item: "Separator" }));
    items.push(await MenuItem.new({ text: "隐藏/显示对话窗口", action: () => { void emit("pet-toggle-chat"); } }));
    items.push(await MenuItem.new({ text: "退出桌宠模式", action: () => { void emit("pet-exit"); void getCurrentWindow().destroy(); } }));
    const menu = await Menu.new({ items });
    await menu.popup().catch(() => undefined);
  };

  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    const current = wheelScale ?? Math.max(0.4, Math.min(3, (settings?.spriteScale ?? 100) / 100));
    const next = Math.max(0.4, Math.min(3, current * (event.deltaY < 0 ? 1.06 : 1 / 1.06)));
    setWheelScale(next);
  };

  const imageStyle = layout ? {
    left: layout.marginX, top: layout.marginTop,
    width: layout.imageW, height: layout.imageH,
    ["--pet-scale" as string]: String(scale),
  } as CSSProperties : undefined;

  return (
    <div
      className="pet-stage"
      onPointerDown={(event) => {
        if (event.button === 0) void getCurrentWindow().startDragging().catch(() => undefined);
        else if (event.button === 1) { event.preventDefault(); void showMenu(); }
      }}
      onContextMenu={(event) => { event.preventDefault(); void emit("pet-toggle-chat"); }}
      onMouseDown={(event) => { if (event.button === 1) event.preventDefault(); }}
      onWheel={onWheel}
    >
      {currentImage && layout && (
        <img
          className={`pet-sprite${speaking ? " is-speaking" : " is-breathing"}`}
          src={currentImage.src}
          alt=""
          draggable={false}
          style={imageStyle}
        />
      )}
      {frameError && <div className="pet-error" role="alert">{frameError}</div>}
    </div>
  );
}
