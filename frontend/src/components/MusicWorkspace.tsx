import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Disc3, ExternalLink, ListMusic, Pause, Play, Power, Repeat, Repeat1, Settings2, Shuffle, SkipBack, SkipForward, Volume2, X } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";

type MusicTrack = { id?: string; title?: string; artist?: string; duration?: number; index?: number; coverUrl?: string };
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
type FeedbackSettings = { enabled: boolean; delaySeconds: number; minimumListenSeconds: number; cooldownSeconds: number; autoSpeak: boolean; saveToChat: boolean };
type MusicStatsTrack = { source?: string; track_id?: string; name?: string; artist?: string; seconds?: number; play_count?: number; last_played?: string };
type MusicStats = {
  total_seconds: number; total_hours: number;
  today_seconds?: number; week_seconds?: number; play_count?: number; feedback_count?: number;
  most_played: { name: string; seconds: number } | null;
  tracks?: MusicStatsTrack[]; recent?: MusicStatsTrack[];
};
type UserPlaylist = { id: string; name: string; trackCount?: number; creator?: string; coverUrl?: string };
type PlaylistDetail = { id: string; name: string; creator?: string; trackCount?: number; tracks?: Array<{ id: string; name?: string; artist?: string; album?: string; durationMs?: number; coverUrl?: string }> };

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

function fmtSpan(sec?: number): string {
  const s = Math.max(0, Math.floor(sec ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分钟`;
  return `${s} 秒`;
}

function fmtWhen(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const MUSIC_SOURCE_LABEL: Record<string, string> = { netease: "网易云", local: "本地" };

const API_ORIGIN = "http://127.0.0.1:8766";
const MUSIC_EVENT_CURSOR_KEY = "lianxin.musicEventsCursor";
const PLAYLIST_PAGE_SIZE = 24;

export function MusicWorkspace({ music, onControl, onRefresh, errorMsg, onDismissError }: { music: MusicState; onControl: (action: string, payload?: Record<string, unknown>) => void; onRefresh?: () => void; errorMsg?: string; onDismissError?: () => void }) {
  const [tab, setTab] = useState<"queue" | "lyrics" | "playlists" | "feedback" | "stats">("queue");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [spaceData, setSpaceData] = useState<SpaceSettingsData | null>(null);
  const [wallpaper, setWallpaper] = useState("default");
  const [wallpaperOpacity, setWallpaperOpacity] = useState(70);
  const [maskOpacity, setMaskOpacity] = useState(50);
  const [fit, setFit] = useState<"cover" | "contain">("cover");
  const [settingsMsg, setSettingsMsg] = useState("");
  const [settingsError, setSettingsError] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [feedbackSettings, setFeedbackSettings] = useState<FeedbackSettings | null>(null);
  const [stats, setStats] = useState<MusicStats | null>(null);
  const [lastFeedback, setLastFeedback] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState("idle");
  const [playlists, setPlaylists] = useState<UserPlaylist[]>([]);
  const [playlistLoading, setPlaylistLoading] = useState(false);
  const [selectedPlaylist, setSelectedPlaylist] = useState<PlaylistDetail | null>(null);
  const [playlistDetailLoading, setPlaylistDetailLoading] = useState(false);
  const [playlistPlayLoading, setPlaylistPlayLoading] = useState(false);
  const [playlistError, setPlaylistError] = useState("");
  const [playlistPage, setPlaylistPage] = useState(0);
  const [playlistTotal, setPlaylistTotal] = useState(0);
  const [playlistHasMore, setPlaylistHasMore] = useState(false);
  const [killArmed, setKillArmed] = useState(false);
  const [seekDrag, setSeekDrag] = useState<number | null>(null);
  const [seekConfirm, setSeekConfirm] = useState<number | null>(null);
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null);
  const [coverFailed, setCoverFailed] = useState(false);
  const killTimerRef = useRef<number | undefined>(undefined);
  const lyricBoxRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const seekDragRef = useRef<number | null>(null);
  const seekTimerRef = useRef<number | undefined>(undefined);
  const volumeTimerRef = useRef<number | undefined>(undefined);
  const eqRef = useRef<HTMLCanvasElement>(null);
  const spectrumRef = useRef<number[] | null>(null);

  const playing = Boolean(music.active);
  const progress = seekDrag ?? (seekConfirm !== null && music.duration ? Math.min(100, (seekConfirm / music.duration) * 100) : (music.duration ? Math.min(100, ((music.progress ?? 0) / music.duration) * 100) : 0));
  const volume = Math.round(music.volume ?? 80);
  const mode = music.mode === "single" ? "single" : music.mode === "shuffle" ? "shuffle" : "sequence";
  const hasLyrics = Boolean(music.lyrics && music.lyrics.length > 0);
  const currentQueueCover = music.current_index !== undefined && music.current_index >= 0
    ? music.playlist?.[music.current_index]?.coverUrl
    : undefined;
  const vinylCover = music.coverUrl || currentQueueCover;

  useEffect(() => { setCoverFailed(false); }, [vinylCover]);

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

  const loadPlaylists = (page = 0) => {
    setPlaylistLoading(true);
    lianxinApi.musicPlaylists(page * PLAYLIST_PAGE_SIZE, PLAYLIST_PAGE_SIZE)
      .then((data) => {
        setPlaylists(data.playlists ?? []);
        setPlaylistTotal(typeof data.total === "number" ? data.total : (data.playlists ?? []).length);
        setPlaylistHasMore(Boolean(data.hasMore));
        setPlaylistPage(page);
      })
      .catch(() => undefined)
      .finally(() => setPlaylistLoading(false));
  };

  useEffect(() => {
    if (tab !== "playlists" || playlists.length) return;
    loadPlaylists(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, playlists.length]);

  const playlistPageCount = Math.max(1, Math.ceil(playlistTotal / PLAYLIST_PAGE_SIZE));
  const playlistPageNumbers = (() => {
    const total = playlistPageCount;
    const current = playlistPage;
    if (total <= 7) return Array.from({ length: total }, (_, i) => i);
    const picked = new Set<number>([0, total - 1, current, current - 1, current + 1]);
    const sorted = [...picked].filter((page) => page >= 0 && page < total).sort((a, b) => a - b);
    const result: Array<number | "..."> = [];
    let previous = -1;
    for (const page of sorted) {
      if (previous >= 0 && page - previous > 1) result.push("...");
      result.push(page);
      previous = page;
    }
    return result;
  })();

  useEffect(() => {
    let alive = true;
    let cursor = Number(window.localStorage.getItem(MUSIC_EVENT_CURSOR_KEY) || 0) || 0;
    const poll = async () => {
      try {
        const [settings, stat] = await Promise.all([lianxinApi.musicFeedbackSettings(), lianxinApi.musicStats()]);
        if (!alive) return;
        setFeedbackSettings(settings);
        setStats(stat);
        const events = await lianxinApi.musicEvents(cursor);
        if (!alive) return;
        cursor = events.latest;
        try { window.localStorage.setItem(MUSIC_EVENT_CURSOR_KEY, String(cursor)); } catch { /* ignore */ }
        const latest = [...events.items].reverse().find((event) => event.type === "music.feedback_ready" && event.content);
        if (latest?.content) setLastFeedback(latest.content);
        const status = [...events.items].reverse().find((event) => event.status);
        if (status?.status) setFeedbackStatus(status.status);
      } catch { /* bridge may be offline */ }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { alive = false; window.clearInterval(timer); };
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

  // Poll the backend spectrum (WASAPI loopback) while playing.
  useEffect(() => {
    if (!playing) {
      spectrumRef.current = null;
      return;
    }
    let alive = true;
    let timer = 0;
    const poll = async () => {
      if (!alive) return;
      try {
        const res = await fetch(`${API_ORIGIN}/api/music/spectrum`);
        if (!alive) return;
        const data = await res.json();
        if (alive && data && data.on && Array.isArray(data.bars)) {
          spectrumRef.current = data.bars as number[];
        } else if (alive) {
          spectrumRef.current = null;
        }
      } catch {
        if (alive) spectrumRef.current = null;
      } finally {
        if (alive) timer = window.setTimeout(poll, 40);
      }
    };
    poll();
    return () => { alive = false; window.clearTimeout(timer); spectrumRef.current = null; };
  }, [playing]);

  // Canvas waveform: real spectrum when available, simulated fallback otherwise.
  useEffect(() => {
    const canvas = eqRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = 224;
    const H = 26;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const N = 28;
    const sim = new Float32Array(N).fill(0.08);
    let prev = new Float32Array(N).fill(0.05);
    let raf = 0;
    let last = performance.now();

    const barPath = (x: number, y: number, w: number, h: number, r: number) => {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    };

    const tick = (now: number) => {
      const dt = Math.min(0.12, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, W, H);
      const live = playing ? spectrumRef.current : null;
      const values = new Float32Array(N);
      for (let i = 0; i < N; i++) {
        let target: number;
        if (live && live.length === N) {
          target = Math.min(1, Math.max(0, Number(live[i]) || 0));
        } else if (playing) {
          sim[i] += (0.08 + Math.random() * 0.92 - sim[i]) * Math.min(1, dt * 9);
          target = sim[i];
        } else {
          target = 0.05;
        }
        values[i] = prev[i] + (target - prev[i]) * Math.min(1, dt * 14);
      }
      prev = values;
      const gap = 1.5;
      const bw = (W - gap * (N - 1)) / N;
      for (let i = 0; i < N; i++) {
        const bh = Math.max(2, values[i] * H);
        const x = i * (bw + gap);
        const y = H - bh;
        const grad = ctx.createLinearGradient(0, y, 0, H);
        grad.addColorStop(0, "#69d7c3");
        grad.addColorStop(1, "rgba(61, 189, 169, 0.15)");
        ctx.fillStyle = grad;
        barPath(x, y, bw, bh, Math.min(2, bw / 2));
        ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); };
  }, [playing]);


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
    try {
      const res = await lianxinApi.openPlayer();
      if (!res.ok) console.error("打开 Web 播放器失败", res);
    } catch (err) {
      console.error("打开 Web 播放器失败", err);
    }
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
    Promise.all([
      lianxinApi.saveMusicSpaceSettings({
      wallpaper,
      wallpaper_opacity: wallpaperOpacity / 100,
      content_mask_opacity: maskOpacity / 100,
      fit,
      }),
      feedbackSettings ? lianxinApi.saveMusicFeedbackSettings(feedbackSettings) : Promise.resolve(null),
    ])
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
              {vinylCover && !coverFailed ? <img src={vinylCover} alt={"封面"} onError={() => setCoverFailed(true)} /> : <Disc3 size={40} />}
            </div>
            <div className="music-vinyl-hub" />
          </div>
          <div className={`music-tonearm ${playing ? "is-down" : ""}`}>
            <span className="music-arm-pivot" />
            <span className="music-arm-body" />
            <span className="music-arm-head" />
          </div>
          <div className="music-eq">
            <canvas ref={eqRef} />
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
          <button className={`music-panel-tab ${tab === "feedback" ? "is-active" : ""}`} onClick={() => setTab("feedback")}><Disc3 size={15} />{"听歌反馈"}</button>
          <button className={`music-panel-tab ${tab === "stats" ? "is-active" : ""}`} onClick={() => setTab("stats")}><ListMusic size={15} />{"统计"}</button>
          <button className={`music-panel-tab ${tab === "playlists" ? "is-active" : ""}`} onClick={() => setTab("playlists")}><ListMusic size={15} />{"我的歌单"}</button>
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
        ) : tab === "lyrics" ? (
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
        ) : tab === "playlists" ? (
          <div className="music-queue">
            <div className="music-playlist-bar">
              <span className="music-playlist-count">{playlistTotal ? `共 ${playlistTotal} 个歌单` : "我的歌单"}</span>
              <button className="music-btn music-btn-outline" onClick={() => loadPlaylists(playlistPage)} disabled={playlistLoading}>{playlistLoading ? "加载中..." : "刷新"}</button>
            </div>
            {selectedPlaylist ? <>
              {selectedPlaylist.trackCount && (selectedPlaylist.tracks?.length ?? 0) < selectedPlaylist.trackCount ? <p className="music-playlist-hint">{`共 ${selectedPlaylist.trackCount} 首，当前显示前 ${selectedPlaylist.tracks?.length ?? 0} 首`}</p> : null}
              <div className="music-feedback-panel"><p>{selectedPlaylist.name}</p><button className="music-btn music-btn-primary" disabled={playlistPlayLoading} onClick={() => { setPlaylistPlayLoading(true); setPlaylistError(""); lianxinApi.musicPlayPlaylist(selectedPlaylist.id).then(() => onRefresh?.()).catch((error) => setPlaylistError(String(error?.message || "歌单播放失败"))).finally(() => setPlaylistPlayLoading(false)); }}>{playlistPlayLoading ? "正在加载播放队列..." : "播放此歌单"}</button>{playlistError ? <p className="music-error-text">{playlistError}</p> : null}</div>
              {(selectedPlaylist.tracks ?? []).map((track, index) => <div className="music-queue-item" key={`${track.id}-${index}`}><span className="music-queue-index">{String(index + 1).padStart(2, "0")}</span><span className="music-queue-copy"><strong>{track.name || "未知歌曲"}</strong><em>{track.artist || "未知歌手"}</em></span><span className="music-track-actions"><button className="music-btn music-btn-small" title="下一首播放" onClick={() => onControl("queue-insert-next", { track })}>下一首</button><button className="music-btn music-btn-small" title="加入播放队列" onClick={() => onControl("queue-append", { track })}>加入</button></span></div>)}
              <button className="music-btn" onClick={() => setSelectedPlaylist(null)}>返回歌单列表</button>
            </> : playlistLoading ? <div className="music-empty">正在加载歌单...</div> : playlists.length ? playlists.map((item) => <button className="music-queue-item" key={item.id} onClick={() => { setPlaylistDetailLoading(true); lianxinApi.musicPlaylist(item.id).then((data) => setSelectedPlaylist(data.playlist ?? { id: item.id, name: item.name, tracks: data.tracks ?? [] })).catch(() => undefined).finally(() => setPlaylistDetailLoading(false)); }}><span className="music-queue-copy"><strong>{item.name}</strong><em>{playlistDetailLoading ? "正在加载详情..." : `${item.trackCount ?? 0} 首歌曲${item.creator ? ` · ${item.creator}` : ""}`}</em></span></button>) : <div className="music-empty">暂无歌单或网易云尚未登录，请点击“加载歌单”重试</div>}
            {!selectedPlaylist && playlistPageCount > 1 ? (
              <div className="music-pager">
                <button className="music-pager-btn" disabled={playlistPage === 0 || playlistLoading} onClick={() => loadPlaylists(playlistPage - 1)}>上一页</button>
                {playlistPageNumbers.map((item, index) => item === "..." ? <span className="music-pager-gap" key={`playlist-gap-${index}`}>…</span> : <button className={`music-pager-btn ${item === playlistPage ? "is-active" : ""}`} key={item} disabled={playlistLoading} onClick={() => loadPlaylists(Number(item))}>{Number(item) + 1}</button>)}
                <button className="music-pager-btn" disabled={!playlistHasMore || playlistLoading} onClick={() => loadPlaylists(playlistPage + 1)}>下一页</button>
              </div>
            ) : null}
          </div>
        ) : tab === "feedback" ? (
          <div className="music-feedback-panel">
            <div className="music-empty">{feedbackSettings?.enabled ? `听歌反馈：${feedbackStatus === "waiting" ? "莲心正在听这首歌" : feedbackStatus === "analyzing" ? "莲心正在整理对这首歌的感受" : feedbackStatus === "busy" ? "莲心正在回复你的消息" : feedbackStatus === "ready" ? `莲心对《${music.name || "这首歌"}》留下了评论` : feedbackStatus === "skipped" ? "这首歌播放时间较短，莲心暂时没有发表评论" : "已启用"}` : "听歌反馈已停用"}</div>
            <p>{lastFeedback || "等待下一次听歌反馈"}</p>
          </div>
        ) : (
          <div className="music-feedback-panel music-stats-panel">
            <div className="music-stats-grid">
              <div className="music-stats-cell"><span>{"今日听歌"}</span><strong>{fmtSpan(stats?.today_seconds)}</strong></div>
              <div className="music-stats-cell"><span>{"本周听歌"}</span><strong>{fmtSpan(stats?.week_seconds)}</strong></div>
              <div className="music-stats-cell"><span>{"累计听歌"}</span><strong>{fmtSpan(stats?.total_seconds)}</strong></div>
              <div className="music-stats-cell"><span>{"播放次数"}</span><strong>{`${stats?.play_count ?? 0} 次`}</strong></div>
              <div className="music-stats-cell"><span>{"听歌反馈"}</span><strong>{`${stats?.feedback_count ?? 0} 条`}</strong></div>
              <div className="music-stats-cell"><span>{"最常听"}</span><strong title={stats?.most_played?.name || ""}>{stats?.most_played?.name || "暂无记录"}</strong></div>
            </div>
            {stats?.most_played ? <p className="music-stats-note">{`《${stats.most_played.name}》累计 ${fmtSpan(stats.most_played.seconds)}`}</p> : null}
            <p className="music-stats-title">{"最近在听"}</p>
            {(stats?.recent ?? []).length > 0 ? (
              <div className="music-stats-list">
                {(stats?.recent ?? []).slice(0, 6).map((item, index) => (
                  <div className="music-stats-row" key={`recent-${item.source ?? ""}-${item.track_id ?? index}`}>
                    <span className="music-stats-badge">{MUSIC_SOURCE_LABEL[item.source ?? ""] || item.source || "未知"}</span>
                    <span className="music-stats-copy"><strong>{item.name || "未知曲目"}</strong>{item.artist ? <em>{item.artist}</em> : null}</span>
                    <span className="music-stats-when">{fmtWhen(item.last_played)}</span>
                  </div>
                ))}
              </div>
            ) : <div className="music-empty">{"暂无记录"}</div>}
            <p className="music-stats-title">{"常听曲目"}</p>
            {(stats?.tracks ?? []).length > 0 ? (
              <div className="music-stats-list">
                {(stats?.tracks ?? []).slice(0, 6).map((item, index) => (
                  <div className="music-stats-row" key={`track-${item.source ?? ""}-${item.track_id ?? index}`}>
                    <span className="music-stats-badge">{MUSIC_SOURCE_LABEL[item.source ?? ""] || item.source || "未知"}</span>
                    <span className="music-stats-copy"><strong>{item.name || "未知曲目"}</strong>{item.artist ? <em>{item.artist}</em> : null}</span>
                    <span className="music-stats-when">{`${fmtSpan(item.seconds)} · ${item.play_count ?? 0} 次`}</span>
                  </div>
                ))}
              </div>
            ) : <div className="music-empty">{"暂无记录"}</div>}
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
              <div className="music-settings-block">
                <span className="music-settings-label">{"听歌反馈"}</span>
                <label className="music-settings-row"><span>{"启用反馈"}</span><input type="checkbox" checked={Boolean(feedbackSettings?.enabled)} onChange={(e) => setFeedbackSettings((current) => current ? { ...current, enabled: e.target.checked } : current)} /></label>
                <label className="music-settings-row"><span>{"自动朗读"}</span><input type="checkbox" checked={Boolean(feedbackSettings?.autoSpeak)} onChange={(e) => setFeedbackSettings((current) => current ? { ...current, autoSpeak: e.target.checked } : current)} /></label>
                <label className="music-settings-row"><span>{"写入聊天记录"}</span><input type="checkbox" checked={Boolean(feedbackSettings?.saveToChat)} onChange={(e) => setFeedbackSettings((current) => current ? { ...current, saveToChat: e.target.checked } : current)} /></label>
              </div>
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
