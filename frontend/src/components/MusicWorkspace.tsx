import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Disc3, ExternalLink, ListMusic, Pause, Play, Power, Repeat, Repeat1, Settings2, Shuffle, SkipBack, SkipForward, Volume2, X } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";

type MusicTrack = { id?: string; title?: string; artist?: string; duration?: number; index?: number };
type LyricLine = { time?: number; text?: string };
type MusicState = {
  active?: boolean; playing?: boolean; paused?: boolean; name?: string; artist?: string; album?: string;
  coverUrl?: string; progress?: number; duration?: number; volume?: number; mode?: string; current_index?: number;
  playlist?: MusicTrack[]; lyrics?: LyricLine[]; style?: string; instrumental?: boolean;
};
type SpaceWallpaper = { id: string; name: string; url: string };
type SpaceSettingsData = {
  wallpapers: SpaceWallpaper[];
  settings: { wallpaper: string; wallpaper_opacity: number; content_mask_opacity: number; fit: string };
};

const LYRIC_DEPTH = [
  { opacity: 1, scale: 1, blur: 0 },
  { opacity: 0.62, scale: 0.97, blur: 0 },
  { opacity: 0.42, scale: 0.93, blur: 0.8 },
  { opacity: 0.26, scale: 0.9, blur: 1.6 },
];

function fmt(sec?: number): string {
  if (sec === undefined || !Number.isFinite(sec)) return "0:00";
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const API_ORIGIN = "http://127.0.0.1:8766";

export function MusicWorkspace({ music, onControl, errorMsg, onDismissError }: { music: MusicState; onControl: (action: string, payload?: Record<string, unknown>) => void; errorMsg?: string; onDismissError?: () => void }) {
  const [tab, setTab] = useState<"queue" | "lyrics">("queue");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [spaceData, setSpaceData] = useState<SpaceSettingsData | null>(null);
  const [wallpaper, setWallpaper] = useState("default");
  const [wallpaperOpacity, setWallpaperOpacity] = useState(70);
  const [maskOpacity, setMaskOpacity] = useState(50);
  const [fit, setFit] = useState<"cover" | "contain">("cover");
  const [settingsMsg, setSettingsMsg] = useState("");
  const [settingsError, setSettingsError] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [killArmed, setKillArmed] = useState(false);
  const [seekDrag, setSeekDrag] = useState<number | null>(null);
  const [seekConfirm, setSeekConfirm] = useState<number | null>(null);
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null);
  const killTimerRef = useRef<number | undefined>(undefined);
  const lyricBoxRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const seekDragRef = useRef<number | null>(null);
  const seekTimerRef = useRef<number | undefined>(undefined);
  const volumeTimerRef = useRef<number | undefined>(undefined);

  const playing = Boolean(music.active);
  const progress = seekDrag ?? (seekConfirm !== null && music.duration ? Math.min(100, (seekConfirm / music.duration) * 100) : (music.duration ? Math.min(100, ((music.progress ?? 0) / music.duration) * 100) : 0));
  const volume = Math.round(music.volume ?? 80);
  const mode = music.mode === "single" ? "single" : music.mode === "shuffle" ? "shuffle" : "sequence";
  const hasLyrics = Boolean(music.lyrics && music.lyrics.length > 0);

  const activeLyricIndex = (music.lyrics ?? []).reduce((acc, line, index) => {
    return (line.time ?? 0) <= (music.progress ?? 0) ? index : acc;
  }, -1);

  useEffect(() => {
    let alive = true;
    lianxinApi.musicSpaceSettings()
      .then((data) => {
        if (!alive) return;
        setSpaceData(data);
        setWallpaper(data.settings.wallpaper ?? "default");
        setWallpaperOpacity(Math.round((data.settings.wallpaper_opacity ?? 0.7) * 100));
        setMaskOpacity(Math.round((data.settings.content_mask_opacity ?? 0.5) * 100));
        setFit(data.settings.fit === "contain" ? "contain" : "cover");
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (tab !== "lyrics") return;
    const box = lyricBoxRef.current;
    const row = box?.querySelector<HTMLElement>(".music-lyric-line.is-active");
    if (box && row) {
      const top = row.offsetTop - box.clientHeight / 2 + row.clientHeight / 2;
      box.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
  }, [activeLyricIndex, tab]);

  const seekFromClientX = (clientX: number) => {
    if (!music.duration) return;
    const bar = progressRef.current;
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const pct = Math.round(ratio * 100);
    seekDragRef.current = pct;
    setSeekDrag(pct);
  };
  const handleSeekDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!music.duration) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    seekDragRef.current = null;
    seekFromClientX(event.clientX);
  };
  const handleSeekMove = (event: PointerEvent<HTMLDivElement>) => {
    if (seekDragRef.current === null) return;
    seekFromClientX(event.clientX);
  };
  const handleSeekUp = () => {
    const pct = seekDragRef.current;
    seekDragRef.current = null;
    if (pct !== null && music.duration) {
      const target = Math.round((pct / 100) * music.duration);
      onControl("seek", { position: target });
      // 保留拖拽位置显示，等待轮询确认后再释放，避免拖拽后复位
      setSeekConfirm(target);
      window.clearTimeout(seekTimerRef.current);
      seekTimerRef.current = window.setTimeout(() => setSeekConfirm(null), 6000);
    } else {
      setSeekDrag(null);
    }
  };
  const handleSeekCancel = () => { seekDragRef.current = null; setSeekDrag(null); setSeekConfirm(null); window.clearTimeout(seekTimerRef.current); };

  useEffect(() => {
    if (seekConfirm === null || !music.duration) return;
    if (Math.abs((music.progress ?? 0) - seekConfirm) < 1.5) {
      window.clearTimeout(seekTimerRef.current);
      setSeekConfirm(null);
      setSeekDrag(null);
    }
  }, [music.progress, music.duration, seekConfirm]);

  useEffect(() => {
    if (volumeDraft === null) return;
    if (Math.abs((music.volume ?? 0) - volumeDraft) < 1.5) {
      window.clearTimeout(volumeTimerRef.current);
      setVolumeDraft(null);
    }
  }, [music.volume, volumeDraft]);
  const toggleMode = () => onControl("mode", { mode: mode === "sequence" ? "shuffle" : mode === "shuffle" ? "single" : "sequence" });

  const wallpaperUrl = (u?: string) => (u ? `${API_ORIGIN}${u.startsWith("/") ? u : `/${u}`}` : "");

  const openWebPlayer = async () => {
    try { await lianxinApi.musicEnsure(); } catch { /* 仍尝试打开 */ }
    window.open("http://127.0.0.1:8765/", "_blank");
  };

  const handleKillMpv = () => {
    if (!killArmed) {
      setKillArmed(true);
      window.clearTimeout(killTimerRef.current);
      killTimerRef.current = window.setTimeout(() => setKillArmed(false), 3000);
      return;
    }
    setKillArmed(false);
    window.clearTimeout(killTimerRef.current);
    onControl("kill-mpv");
  };

  const resetSettings = () => {
    setWallpaper("default");
    setWallpaperOpacity(70);
    setMaskOpacity(50);
    setFit("cover");
  };

  const saveSettings = () => {
    if (settingsSaving) return;
    setSettingsSaving(true);
    setSettingsMsg("");
    setSettingsError(false);
    lianxinApi.saveMusicSpaceSettings({
      wallpaper,
      wallpaper_opacity: wallpaperOpacity / 100,
      content_mask_opacity: maskOpacity / 100,
      fit,
    })
      .then(() => { setSettingsMsg("已保存"); setSettingsSaving(false); })
      .catch(() => { setSettingsMsg("保存失败"); setSettingsError(true); setSettingsSaving(false); });
  };

  return (
    <section className="workspace music-workspace">
      {errorMsg ? (
        <div className="music-error-banner" role="alert">
          <span>{errorMsg}</span>
          <button onClick={onDismissError} title={"关闭"}><X size={14} /></button>
        </div>
      ) : null}
      <div className="music-hero music-hero-vinyl">
        <div className="music-turntable">
          <div className={`music-vinyl ${playing ? "is-spinning" : ""}`}>
            <div className="music-vinyl-grooves" />
            <div className="music-vinyl-sheen" />
            <div className="music-vinyl-cover">
              {music.coverUrl ? <img src={music.coverUrl} alt={"封面"} onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} /> : <Disc3 size={40} />}
            </div>
            <div className="music-vinyl-hub" />
          </div>
          <div className={`music-tonearm ${playing ? "is-down" : ""}`}>
            <span className="music-arm-pivot" />
            <span className="music-arm-body" />
            <span className="music-arm-head" />
          </div>
          <div className="music-eq">
            {Array.from({ length: 18 }).map((_, i) => (
              <span key={i} className={playing ? "is-playing" : ""} style={{ animationDelay: `${(i % 6) * 0.12}s` }} />
            ))}
          </div>
        </div>
        <div className="music-meta">
          <span className="eyebrow">{"莲心 · 音乐空间"}</span>
          <h1>{music.name || "未知曲目"}</h1>
          <p>{music.artist || "未知歌手"}{music.album ? ` \u00b7 ${music.album}` : ""}</p>
          <div className="music-tags">
            {music.style ? <span className="music-tag">{music.style}</span> : null}
            {music.instrumental ? <span className="music-tag">{"纯音乐"}</span> : null}
            <span className="music-tag">{mode === "shuffle" ? "随机播放" : mode === "single" ? "单曲循环" : "顺序播放"}</span>
          </div>
        </div>
        <div className="music-controls">
          <button className="music-btn" title={"上一首"} onClick={() => onControl("previous")}><SkipBack size={20} /></button>
          <button className="music-btn music-btn-primary" title={playing ? "暂停" : "播放"} onClick={() => onControl("toggle")}>{playing ? <Pause size={22} /> : <Play size={22} />}</button>
          <button className="music-btn" title={"下一首"} onClick={() => onControl("next")}><SkipForward size={20} /></button>
          <div className="music-progress" ref={progressRef} onPointerDown={handleSeekDown} onPointerMove={handleSeekMove} onPointerUp={handleSeekUp} onPointerCancel={handleSeekCancel}>
            <div className="music-progress-track"><div className="music-progress-fill" style={{ width: `${progress}%` }} /><div className="music-progress-thumb" style={{ left: `${progress}%` }} /></div>
            <div className="music-time"><span>{seekDrag !== null && music.duration ? fmt((seekDrag / 100) * music.duration) : seekConfirm !== null ? fmt(seekConfirm) : fmt(music.progress)}</span><span>{fmt(music.duration)}</span></div>
          </div>
        </div>
        <div className="music-tools">
          <button className={`music-mode ${mode !== "sequence" ? "is-active" : ""}`} title={mode === "shuffle" ? "单曲循环" : mode === "single" ? "顺序播放" : "随机播放"} onClick={toggleMode}>{mode === "shuffle" ? <Shuffle size={17} /> : mode === "single" ? <Repeat1 size={17} /> : <Repeat size={17} />}<span>{mode === "shuffle" ? "随机播放" : mode === "single" ? "单曲循环" : "顺序播放"}</span></button>
          <div className="music-volume" title={"音量"}>
            <Volume2 size={17} />
            <input type="range" min={0} max={100} value={volumeDraft ?? volume} onChange={(e) => { const v = Number(e.target.value); setVolumeDraft(v); window.clearTimeout(volumeTimerRef.current); volumeTimerRef.current = window.setTimeout(() => setVolumeDraft(null), 6000); onControl("volume", { volume: v / 100 }); }} />
            <span>{volumeDraft ?? volume}</span>
          </div>
          <button className="music-btn music-btn-outline" title={"打开 Web 播放器"} onClick={openWebPlayer}><ExternalLink size={16} />{"Web 播放器"}</button>
          <button className={`music-btn music-btn-kill ${killArmed ? "is-armed" : ""}`} title={killArmed ? "再次点击确认停止" : "停止后台播放器"} onClick={handleKillMpv}><Power size={16} />{killArmed ? "确认停止" : "停止 mpv"}</button>
          <button className="music-btn music-btn-outline" title={"音乐空间设置"} onClick={() => setSettingsOpen(true)}><Settings2 size={16} />{"设置"}</button>
        </div>
      </div>

      <div className="music-panel">
        <div className="music-panel-tabs">
          <button className={`music-panel-tab ${tab === "queue" ? "is-active" : ""}`} onClick={() => setTab("queue")}><ListMusic size={15} />{"播放队列"}</button>
          <button className={`music-panel-tab ${tab === "lyrics" ? "is-active" : ""}`} onClick={() => setTab("lyrics")}><Disc3 size={15} />{"歌词"}</button>
        </div>
        {tab === "queue" ? (
          <div className="music-queue">
            {music.playlist && music.playlist.length > 0 ? music.playlist.map((item) => (
              <button className={`music-queue-item ${item.index === music.current_index ? "is-current" : ""}`} key={`${item.id ?? item.index}`} onClick={() => onControl("select", { index: item.index })}>
                <span className="music-queue-index">{item.index === music.current_index ? (playing ? <Play size={13} /> : <Pause size={13} />) : String((item.index ?? 0) + 1).padStart(2, "0")}</span>
                <span className="music-queue-copy"><strong>{item.title || "未知曲目"}</strong>{item.artist ? <em>{item.artist}</em> : null}</span>
                <span className="music-queue-duration">{fmt(item.duration)}</span>
              </button>
            )) : <div className="music-empty">{"当前没有播放列表，请在 Web 播放器中添加歌曲"}</div>}
          </div>
        ) : (
          <div className="music-lyrics music-lyrics-waterfall" ref={lyricBoxRef}>
            <div className="music-lyric-track">
              <span className="music-lyric-track-dot" style={{ top: `${hasLyrics ? ((activeLyricIndex + 0.5) / (music.lyrics?.length || 1)) * 100 : 0}%` }} />
            </div>
            {hasLyrics ? music.lyrics!.map((line, index) => {
              const depth = Math.min(3, Math.abs(index - activeLyricIndex));
              const cfg = LYRIC_DEPTH[depth];
              return (
                <p className={`music-lyric-line ${index === activeLyricIndex ? "is-active" : ""}`}
                   key={`${line.time ?? index}-${index}`}
                   style={{ opacity: cfg.opacity, transform: `scale(${cfg.scale})`, filter: cfg.blur ? `blur(${cfg.blur}px)` : undefined }}>
                  {line.text}
                </p>
              );
            }) : <div className="music-empty">{music.instrumental ? "纯音乐无声，莲心将与你一起聆听" : "暂无歌词"}</div>}
          </div>
        )}
      </div>

      {settingsOpen && (
        <div className="music-settings-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="music-settings-panel" onClick={(e) => e.stopPropagation()}>
            <div className="music-settings-head">
              <strong>{"音乐空间设置"}</strong>
              <button className="music-settings-close" onClick={() => setSettingsOpen(false)} title={"关闭"}><X size={16} /></button>
            </div>
            <div className="music-settings-body">
              <div className="music-settings-block">
                <span className="music-settings-label">{"壁纸预览"}</span>
                <div className="music-wallpaper-strip">
                  {(spaceData?.wallpapers ?? []).map((w) => (
                    <button key={w.id} className={`music-wallpaper-item ${w.id === wallpaper ? "is-active" : ""}`} onClick={() => setWallpaper(w.id)} title={w.name}>
                      {w.url ? <img src={wallpaperUrl(w.url)} alt={w.name} loading="lazy" /> : <span className="music-wallpaper-default"><Disc3 size={16} /></span>}
                      <em>{w.name}</em>
                    </button>
                  ))}
                </div>
              </div>
              <label className="music-settings-row">
                <span>{"壁纸透明度"}</span>
                <input type="range" min={0} max={100} value={wallpaperOpacity} onChange={(e) => setWallpaperOpacity(Number(e.target.value))} />
                <strong>{wallpaperOpacity}%</strong>
              </label>
              <label className="music-settings-row">
                <span>{"内容区域遮罩"}</span>
                <input type="range" min={0} max={100} value={maskOpacity} onChange={(e) => setMaskOpacity(Number(e.target.value))} />
                <strong>{maskOpacity}%</strong>
              </label>
              <label className="music-settings-row">
                <span>{"填充方式"}</span>
                <select value={fit} onChange={(e) => setFit(e.target.value === "contain" ? "contain" : "cover")}>
                  <option value="cover">{"铺满"}</option>
                  <option value="contain">{"完整显示"}</option>
                </select>
              </label>
            </div>
            <div className="music-settings-foot">
              <span className={`music-settings-msg ${settingsError ? "is-error" : ""}`}>{settingsMsg}</span>
              <button className="music-btn" onClick={resetSettings}>{"恢复默认"}</button>
              <button className="music-btn music-btn-primary" onClick={saveSettings} disabled={settingsSaving}>{settingsSaving ? "保存中…" : "保存设置"}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
