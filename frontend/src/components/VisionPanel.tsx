import { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2, X } from "lucide-react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { lianxinApi, type VisionEvent, type VisionState } from "../services/lianxinApi";

const VISION_FRAME_URL = "http://127.0.0.1:8766/api/vision/frame";
const VISION_TRACKING_FRAME_URL = "http://127.0.0.1:8766/api/vision/tracking/frame";

function formatVisionDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function VisionPanel() {
  const [state, setState] = useState<VisionState | null>(null);
  const [events, setEvents] = useState<VisionEvent[]>([]);
  const [enrollName, setEnrollName] = useState("");
  const [cooldownDraft, setCooldownDraft] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [feedKey, setFeedKey] = useState(0);
  const [trackingFeedKey, setTrackingFeedKey] = useState(0);
  const cursor = useRef(0);

  useEffect(() => {
    let alive = true;
    const sync = () =>
      void lianxinApi
        .visionState()
        .then((next) => {
          if (alive) setState(next);
        })
        .catch(() => undefined);
    sync();
    const timer = window.setInterval(sync, 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      void lianxinApi
        .visionEvents(cursor.current)
        .then((result) => {
          if (!alive || !result.items.length) return;
          cursor.current = result.latest;
          setEvents((current) => [...current, ...result.items].slice(-120));
        })
        .catch(() => undefined);
    poll();
    const timer = window.setInterval(poll, 700);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const applyState = (task: Promise<VisionState>) => {
    setNotice("");
    void task.then(setState).catch((error) => setNotice(String(error)));
  };
  const applyTracking = (mode: "esp32" | "simulate" | "off") => {
    setNotice("");
    void lianxinApi
      .visionTracking(mode)
      .then(setState)
      .catch((error) => setNotice(String(error)));
  };
  const running = Boolean(state?.running);
  const loading = Boolean(state?.loading);
  const tracking = state?.tracking;
  const cooldown = cooldownDraft ?? state?.cooldown ?? 7;
  const statusPhase = state?.phase === "error" ? state?.error : loading ? "正在加载视觉模型…" : "摄像头尚未启动";

  return (
    <div className="vision-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">能力与感知</span>
          <h2>视觉理解</h2>
          <p className="muted-copy">人脸、手势与陪伴检测都在后台运行；窗口关闭后，莲心仍会回应视觉事件。</p>
        </div>
      </div>
      <div className="vision-layout">
        <div className="vision-log-column">
          <h3>实时事件</h3>
          <div className="vision-log">
            {events.length === 0 ? (
              <span className="muted-copy">视觉事件会显示在这里，例如：用户进入、离开、手势识别、陌生人提醒。</span>
            ) : (
              events.map((event) => (
                <div className="vision-log-row" key={event.id}>
                  <time>{new Date(event.timestamp * 1000).toLocaleTimeString()}</time>
                  <span>{event.message}</span>
                </div>
              ))
            )}
          </div>
        </div>
        <div className="vision-center">
          <div className="vision-stage">
            {running ? (
              <img
                className="vision-feed"
                key={feedKey}
                src={VISION_FRAME_URL}
                alt="视觉感知画面"
                onError={() => {
                  if (running) window.setTimeout(() => setFeedKey((value) => value + 1), 1500);
                }}
              />
            ) : (
              <div className="vision-placeholder">{statusPhase}</div>
            )}
          </div>
          {tracking?.active ? (
            <div className="vision-stage vision-tracking-stage">
              <img
                className="vision-feed"
                key={trackingFeedKey}
                src={VISION_TRACKING_FRAME_URL}
                alt="肩载人脸追踪画面"
                onError={() => {
                  if (tracking.active) window.setTimeout(() => setTrackingFeedKey((value) => value + 1), 1500);
                }}
              />
            </div>
          ) : null}
        </div>
        <div className="vision-side">
          <div className="vision-status">
            <span>摄像头</span><strong>{state?.camera || "-"}</strong>
            <span>帧率</span><strong>{state?.fps ? `${state.fps} fps` : "-"}</strong>
            <span>人脸</span><strong>{state?.face || "未启用"}</strong>
            <span>手势</span><strong>{state?.gesture || "未启用"}</strong>
            <span>陪伴</span><strong>{state?.companion || "未启用"}</strong>
            <span>视频时间</span><strong>{formatVisionDuration(state?.videoDuration ?? 0)}</strong>
            <span>今日陪伴</span><strong>{formatVisionDuration(state?.todayPresence ?? 0)}</strong>
          </div>
          <div className="vision-settings">
            {(
              [
                ["face", "人脸识别", "识别本人 / 陌生人，驱动陪伴事件"],
                ["gesture", "手势识别", "OK、点赞、挥手会触发莲心回应"],
                ["companion", "陪伴检测", "离开、返回与久坐提醒"],
              ] as const
            ).map(([name, label, description]) => {
              const enabled = Boolean(state?.features?.[name]);
              return (
                <div className="setting-row" key={name}>
                  <div>
                    <strong>{label}</strong>
                    <span>{description}</span>
                  </div>
                  <button
                    className={`toggle ${enabled ? "is-on" : ""}`}
                    onClick={() => applyState(lianxinApi.visionFeature(name, !enabled))}
                    aria-label={`切换${label}`}
                  >
                    <span />
                  </button>
                </div>
              );
            })}
            <label className="vision-field">
              人脸推理设备
              <select
                value={state?.device || "CPU"}
                onChange={(event) => applyState(lianxinApi.visionDevice(event.target.value))}
              >
                <option value="CPU">CPU</option>
                <option value="GPU">GPU</option>
              </select>
            </label>
            <label className="vision-field">
              手势冷却
              <span className="vision-slider">
                <input
                  type="range"
                  min={3}
                  max={30}
                  value={cooldown}
                  onChange={(event) => setCooldownDraft(Number(event.target.value))}
                  onPointerUp={() => {
                    if (cooldownDraft !== null) {
                      applyState(lianxinApi.visionCooldown(cooldownDraft));
                      setCooldownDraft(null);
                    }
                  }}
                />
                <span>{cooldown} 秒</span>
              </span>
            </label>
          </div>
          <div className="vision-actions">
            <button className="secondary-button" disabled={running || loading} onClick={() => applyState(lianxinApi.visionStart())}>
              {loading ? "加载中…" : "启动"}
            </button>
            <button className="secondary-button" disabled={!running && !loading} onClick={() => applyState(lianxinApi.visionStop())}>
              停止
            </button>
          </div>
          <div className="vision-enroll">
            <input
              value={enrollName}
              disabled={!running}
              placeholder="输入你的称呼（例如：雨心）"
              onChange={(event) => setEnrollName(event.target.value)}
            />
            <button
              className="secondary-button"
              disabled={!running || !enrollName.trim()}
              onClick={() => {
                const name = enrollName.trim();
                setNotice("");
                void lianxinApi
                  .visionEnroll(name)
                  .then(() => {
                    setEnrollName("");
                    setNotice(`开始录入：${name}（请正对摄像头保持 15 帧）`);
                  })
                  .catch((error) => setNotice(String(error)));
              }}
            >
              录入本人
            </button>
          </div>
          <div className="vision-tracking">
            <strong>肩载人脸追踪</strong>
            <div className="vision-actions">
              <button
                className={`secondary-button ${tracking?.active && !tracking.simulated ? "is-active" : ""}`}
                onClick={() => applyTracking("esp32")}
              >
                ESP32 追踪
              </button>
              <button
                className={`secondary-button ${tracking?.active && tracking.simulated ? "is-active" : ""}`}
                onClick={() => applyTracking("simulate")}
              >
                本机模拟
              </button>
              <button className="secondary-button" disabled={!tracking?.active} onClick={() => applyTracking("off")}>
                停止追踪
              </button>
            </div>
            {tracking?.message ? <span className="vision-notice">{tracking.message}</span> : null}
            {tracking?.status ? <span className="muted-copy">{tracking.status}</span> : null}
          </div>
          {notice ? <span className="vision-notice">{notice}</span> : null}
        </div>
      </div>
    </div>
  );
}

const RESIZE_DIRECTIONS = {
  n: "North",
  s: "South",
  e: "East",
  w: "West",
  ne: "NorthEast",
  nw: "NorthWest",
  se: "SouthEast",
  sw: "SouthWest",
} as const;

export function VisionWindow() {
  useEffect(() => {
    document.documentElement.classList.add("vision-window-mode");
    return () => document.documentElement.classList.remove("vision-window-mode");
  }, []);
  const desktopWindow = "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;
  const startDragging = () => {
    if (desktopWindow) void desktopWindow.startDragging().catch(() => undefined);
  };
  const minimize = () => {
    if (desktopWindow) void desktopWindow.minimize().catch(() => undefined);
  };
  const toggleMaximize = () => {
    if (desktopWindow) void desktopWindow.toggleMaximize().catch(() => undefined);
  };
  const close = () => {
    if (desktopWindow) void desktopWindow.close().catch(() => undefined);
  };
  const startResize = (direction: keyof typeof RESIZE_DIRECTIONS) => {
    if (desktopWindow) void desktopWindow.startResizeDragging(RESIZE_DIRECTIONS[direction]).catch(() => undefined);
  };
  return (
    <main className="vision-window">
      <header
        className="vision-titlebar"
        onMouseDown={(event) => {
          if (event.button === 0) startDragging();
        }}
        onDoubleClick={toggleMaximize}
      >
        <span className="vision-titlebar-title">视觉理解</span>
        <span className="vision-titlebar-hint">莲心 · 视觉感知</span>
        <div className="vision-titlebar-actions">
          <button type="button" title="最小化" onMouseDown={(event) => event.stopPropagation()} onClick={minimize}>
            <Minimize2 size={15} />
          </button>
          <button type="button" title="最大化" onMouseDown={(event) => event.stopPropagation()} onClick={toggleMaximize}>
            <Maximize2 size={15} />
          </button>
          <button type="button" className="is-close" title="关闭" onMouseDown={(event) => event.stopPropagation()} onClick={close}>
            <X size={15} />
          </button>
        </div>
      </header>
      <div className="vision-window-body">
        <VisionPanel />
      </div>
      {(Object.keys(RESIZE_DIRECTIONS) as Array<keyof typeof RESIZE_DIRECTIONS>).map((direction) => (
        <div
          key={direction}
          className={`vision-resize-edge is-${direction}`}
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
            startResize(direction);
          }}
        />
      ))}
    </main>
  );
}
