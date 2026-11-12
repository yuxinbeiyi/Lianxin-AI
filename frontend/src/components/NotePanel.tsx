import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Pin, RefreshCw, Save, Search, X } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";
import { countMatches, segmentsOf } from "../utils/noteText";

type Props = { onClose: () => void };

const SAVE_DELAY = 800;

/** 备忘本原生面板：自动保存、查找高亮、外部刷新、系统级置顶。 */
export function NotePanel({ onClose }: Props) {
  const desktopWindow = useMemo(() => ("__TAURI_INTERNALS__" in window ? getCurrentWindow() : null), []);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef("");
  const persistedRef = useRef("");
  const inFlightRef = useRef<Promise<boolean> | null>(null);
  const [content, setContent] = useState("");
  const [persisted, setPersisted] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [savedAt, setSavedAt] = useState("");
  const [pinned, setPinned] = useState(false);
  const [pinError, setPinError] = useState("");
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(false);
  const [suppressSave, setSuppressSave] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const result = await lianxinApi.note();
      contentRef.current = result.content;
      persistedRef.current = result.content;
      setContent(result.content);
      setPersisted(result.content);
      setLoaded(true);
      setSaveError("");
      setSavedAt("");
    } catch (error) {
      setLoaded(false);
      setLoadError(String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    void desktopWindow?.isAlwaysOnTop().then(setPinned).catch(() => undefined);
  }, [desktopWindow]);

  const save = useCallback(async () => {
    if (!loaded) return false;
    // 同一时间只允许一个保存请求，避免旧内容晚到覆盖新内容。
    if (inFlightRef.current) await inFlightRef.current;
    const payload = contentRef.current;
    if (payload === persistedRef.current) return true;
    const request = (async () => {
      setSaving(true);
      setSaveError("");
      try {
        await lianxinApi.saveNote(payload);
        persistedRef.current = payload;
        setPersisted(payload);
        setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
        return true;
      } catch (error) {
        setSaveError(String(error));
        return false;
      } finally {
        setSaving(false);
      }
    })();
    inFlightRef.current = request;
    try {
      return await request;
    } finally {
      if (inFlightRef.current === request) inFlightRef.current = null;
    }
  }, [loaded]);

  useEffect(() => {
    if (!loaded || saving || saveError || suppressSave || content === persisted) return;
    const timer = window.setTimeout(() => { void save(); }, SAVE_DELAY);
    return () => window.clearTimeout(timer);
  }, [content, loaded, persisted, save, saveError, saving, suppressSave]);

  useEffect(() => {
    const area = editorRef.current;
    const backdrop = backdropRef.current;
    if (area && backdrop) {
      backdrop.scrollTop = area.scrollTop;
      backdrop.scrollLeft = area.scrollLeft;
    }
  }, [highlight]);

  const close = async () => {
    if (loaded && contentRef.current !== persistedRef.current) {
      const ok = await save();
      if (!ok && contentRef.current !== persistedRef.current && !window.confirm("备忘本保存失败，关闭将丢失未保存的修改。仍要关闭吗？")) return;
    }
    onClose();
  };

  const refresh = async () => {
    if (loaded && contentRef.current !== persistedRef.current && !window.confirm("当前有未保存的修改，刷新会覆盖这些内容。继续吗？")) return;
    setQuery("");
    setHighlight(false);
    setSuppressSave(true);
    try {
      await load();
    } finally {
      setSuppressSave(false);
    }
  };

  const togglePin = async () => {
    if (!desktopWindow) return;
    try {
      const next = !pinned;
      await desktopWindow.setAlwaysOnTop(next);
      setPinned(next);
      setPinError("");
    } catch (error) {
      setPinError(`切换置顶失败：${String(error)}`);
    }
  };

  const matches = useMemo(() => countMatches(content, query), [content, query]);
  const segments = useMemo(
    () => (highlight && query.trim() ? segmentsOf(content, query) : null),
    [content, highlight, query],
  );
  const status = loadError
    ? "读取失败"
    : loading
      ? "正在读取…"
      : saving
        ? "保存中…"
        : saveError
          ? "保存失败"
          : content !== persisted
            ? "未保存"
            : savedAt
              ? `已保存 ${savedAt}`
              : "已读取";

  return (
    <div className="overlay">
      <section className="overlay-panel note-panel" role="dialog" aria-modal="true" aria-labelledby="note-title">
        <button data-settings-close className="overlay-close" onClick={() => void close()} aria-label="关闭备忘本">
          <X size={18} />
        </button>
        <header className="panel-heading">
          <div>
            <span className="eyebrow">莲心记录</span>
            <h2 id="note-title">备忘本</h2>
            <p className="note-hint">修改后约 0.8 秒自动保存；关闭窗口前会等待最后一次保存完成。</p>
          </div>
          <div className="note-actions">
            <span
              className={`note-status${loadError || saveError ? " is-error" : ""}`}
              role="status"
              aria-live="polite"
              title={loadError || saveError || undefined}
            >
              {status}
            </span>
            <button className="secondary-button" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw size={14} />刷新
            </button>
            <button
              className={`secondary-button${pinned ? " is-active" : ""}`}
              onClick={() => void togglePin()}
              disabled={!desktopWindow}
              aria-pressed={pinned}
              title={desktopWindow ? (pinned ? "取消窗口置顶" : "将窗口置顶") : "浏览器预览不支持系统级置顶，请在桌面窗口中使用"}
            >
              <Pin size={14} />{pinned ? "已置顶" : "置顶"}
            </button>
          </div>
        </header>
        {loadError ? (
          <div className="panel-error" role="alert">
            读取备忘本失败：{loadError}。为避免用空内容覆盖原文件，编辑与保存已停用。
            <button className="secondary-button" onClick={() => void load()}>重试读取</button>
          </div>
        ) : (
          <>
            <div className="note-editor-wrap">
              {segments && (
                <div ref={backdropRef} className="note-editor-backdrop" aria-hidden="true">
                  {segments.map((part, index) => part.match ? <mark key={index}>{part.text}</mark> : part.text)}
                </div>
              )}
              <textarea
                ref={editorRef}
                className={`note-editor${segments ? " is-highlighting" : ""}`}
                value={content}
                onChange={(event) => {
                  setContent(event.target.value);
                  contentRef.current = event.target.value;
                }}
                onScroll={(event) => {
                  const backdrop = backdropRef.current;
                  if (!backdrop) return;
                  backdrop.scrollTop = event.currentTarget.scrollTop;
                  backdrop.scrollLeft = event.currentTarget.scrollLeft;
                }}
                placeholder="写下想交给莲心记住的内容…"
                aria-label="备忘内容"
                disabled={!loaded}
                spellCheck={false}
              />
            </div>
            <footer className="note-toolbar">
              <div className="note-find">
                <Search size={14} aria-hidden="true" />
                <input
                  aria-label="查找关键词"
                  value={query}
                  placeholder="输入关键词"
                  onChange={(event) => {
                    setQuery(event.target.value);
                    if (!event.target.value.trim()) setHighlight(false);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && query.trim()) {
                      event.preventDefault();
                      setHighlight(true);
                    }
                  }}
                />
                <button className="secondary-button" onClick={() => setHighlight(query.trim().length > 0)} disabled={!query.trim()}>高亮</button>
                <button className="secondary-button" onClick={() => setHighlight(false)} disabled={!highlight}>清除高亮</button>
                {query.trim() && <span className="note-match-count" aria-live="polite">{matches} 处匹配</span>}
              </div>
              <div className="note-save-actions">
                {saveError && <span className="note-status is-error" role="alert" title={saveError}>保存失败</span>}
                <button
                  className="secondary-button"
                  onClick={() => void save()}
                  disabled={!loaded || saving || (content === persisted && !saveError)}
                >
                  {saving ? "保存中…" : <><Save size={14} />{saveError ? "重试保存" : "立即保存"}</>}
                </button>
              </div>
            </footer>
            {pinError && <p className="note-status is-error" role="alert">{pinError}</p>}
          </>
        )}
      </section>
    </div>
  );
}
