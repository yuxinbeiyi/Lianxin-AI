import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Maximize2, Minimize2, X } from "lucide-react";
import { ConstellationPanel, DataTidePanel, RipplePanel } from "../App";
import { settingsWindows, type SettingsWindowId } from "../utils/settingsWindow";
import { GlobalSettingsPanel } from "./GlobalSettingsPanel";
import { ApiConfigPanel } from "./ApiConfigPanel";
import { DecisionPanel } from "./DecisionPanel";
import { PersonaPanel } from "./PersonaPanel";
import { RESIZE_DIRECTIONS } from "./VisionPanel";
import "../styles/settings-window.css";

export function SettingsWindow({ id }: { id: SettingsWindowId }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const closing = useRef(false);
  const [closeError, setCloseError] = useState("");
  const desktopWindow = useMemo(() => "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null, []);
  const close = useCallback(() => {
    if (!desktopWindow) { window.close(); return; }
    if (closing.current) return;
    closing.current = true;
    setCloseError("");
    void desktopWindow.destroy().catch((error) => {
      closing.current = false;
      setCloseError(`关闭窗口失败，请重启莲心后重试：${String(error)}`);
    });
  }, [desktopWindow]);
  const requestClose = useCallback(() => {
    const button = contentRef.current?.querySelector<HTMLButtonElement>("[data-settings-close]");
    if (button) button.click();
    else close();
  }, [close]);
  const startDragging = () => { void desktopWindow?.startDragging().catch(() => undefined); };
  const minimize = () => { void desktopWindow?.minimize().catch(() => undefined); };
  const toggleMaximize = () => { void desktopWindow?.toggleMaximize().catch(() => undefined); };

  useEffect(() => {
    document.documentElement.classList.add("settings-window-mode");
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void desktopWindow?.onCloseRequested((event) => {
      event.preventDefault();
      if (closing.current) return;
      requestClose();
    }).then((release) => {
      if (disposed) release();
      else unlisten = release;
    }).catch(() => undefined);
    return () => {
      disposed = true;
      unlisten?.();
      document.documentElement.classList.remove("settings-window-mode");
    };
  }, [desktopWindow, requestClose]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== "http://127.0.0.1:8766") return;
      const frames = contentRef.current?.querySelectorAll("iframe");
      if (!frames || !Array.from(frames).some((frame) => frame.contentWindow === event.source)) return;
      const data = event.data as { source?: string; action?: string } | null;
      if (data?.source !== "lianxin-embedded-window") return;
      if (data.action === "close") requestClose();
      else if (data.action === "minimize") void desktopWindow?.minimize().catch(() => undefined);
      else if (data.action === "maximize") void desktopWindow?.toggleMaximize().catch(() => undefined);
      else if (data.action === "drag") void desktopWindow?.startDragging().catch(() => undefined);
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [desktopWindow, requestClose]);

  const panels = {
    settings: GlobalSettingsPanel,
    api: ApiConfigPanel,
    decision: DecisionPanel,
    persona: PersonaPanel,
    constellation: ConstellationPanel,
    ripple: RipplePanel,
    "data-tide": DataTidePanel,
  };
  const Panel = panels[id];
  return <main className="vision-window settings-window">
    <header className="vision-titlebar" onMouseDown={(event) => { if (event.button === 0) startDragging(); }} onDoubleClick={toggleMaximize}>
      <span className="vision-titlebar-title">{settingsWindows[id].title}</span>
      <span className="vision-titlebar-hint">拖动标题栏，可移出主窗口</span>
      <div className="vision-titlebar-actions" onMouseDown={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
        <button type="button" title="最小化" onClick={minimize}><Minimize2 size={15} /></button>
        <button type="button" title="最大化 / 还原" onClick={toggleMaximize}><Maximize2 size={15} /></button>
        <button type="button" className="is-close" title="关闭" onClick={requestClose}><X size={15} /></button>
      </div>
    </header>
    {closeError && <p className="settings-window-error" role="alert">{closeError}</p>}
    <div ref={contentRef} className="settings-window-content"><Panel onClose={close} /></div>
    {(Object.keys(RESIZE_DIRECTIONS) as Array<keyof typeof RESIZE_DIRECTIONS>).map((direction) => <div key={direction} className={`vision-resize-edge is-${direction}`} onPointerDown={(event) => {
      event.preventDefault();
      event.stopPropagation();
      void desktopWindow?.startResizeDragging(RESIZE_DIRECTIONS[direction]).catch(() => undefined);
    }} />)}
  </main>;
}
