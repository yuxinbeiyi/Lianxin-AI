/* ============================================================
   莲心 · 音乐盒  ——  第二阶段前端逻辑
   数据流：Python -> bridge.getState / push_state(JSON) -> applyState()
   交互流：前端 -> bridge.(togglePlay/next/seek/...) -> Qt 信号
   ============================================================ */
(function () {
  "use strict";

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return (m < 10 ? "0" + m : "" + m) + ":" + (s < 10 ? "0" + s : "" + s);
  }

  function getMode() {
    var p = new URLSearchParams(window.location.search);
    return p.get("mode") === "full" ? "full" : "compact";
  }

  /* ---------- SVG 图标 ---------- */
  var ICONS = {
    play: '<svg viewBox="0 0 24 24"><path d="M8 5.14v13.72c0 .8.87 1.3 1.56.9l10.2-6.86a1.05 1.05 0 0 0 0-1.8L9.56 4.24A1.05 1.05 0 0 0 8 5.14z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M7 5h3.4v14H7zM13.6 5H17v14h-3.4z"/></svg>',
    prev: '<svg viewBox="0 0 24 24"><path d="M6 5h2.2v14H6zM20 5.6v12.8c0 .85-.94 1.34-1.65.87l-10.2-6.4a1.03 1.03 0 0 1 0-1.74l10.2-6.4c.71-.47 1.65.02 1.65.87z"/></svg>',
    next: '<svg viewBox="0 0 24 24"><path d="M15.8 5H18v14h-2.2zM4 5.6v12.8c0 .85.94 1.34 1.65.87l10.2-6.4a1.03 1.03 0 0 0 0-1.74L5.65 4.73C4.94 4.26 4 4.75 4 5.6z"/></svg>',
    vol: '<svg viewBox="0 0 24 24"><path d="M4 9v6h3.4L13 19.4V4.6L7.4 9H4z"/><path d="M15.5 8.2a4.6 4.6 0 0 1 0 7.6l1.2 1.3a6.4 6.4 0 0 0 0-10.2l-1.2 1.3z"/><path d="M17.9 5.5l-1.2 1.3a7.8 7.8 0 0 1 0 10.4l1.2 1.3a9.6 9.6 0 0 0 0-13z"/></svg>',
    muted: '<svg viewBox="0 0 24 24"><path d="M4 9v6h3.4L13 19.4V4.6L7.4 9H4z"/><path d="M16.4 8.9l1.6 1.6 1.6-1.6 1.1 1.1-1.6 1.6 1.6 1.6-1.1 1.1-1.6-1.6-1.6 1.6-1.1-1.1 1.6-1.6-1.6-1.6z"/></svg>',
    repeat: '<svg viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>',
    repeatOne: '<svg viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/><circle cx="12" cy="16" r="2.1" fill="#fff"/><path d="M12 14l-1.3 1.9h2.6z" fill="#2fd9a0"/></svg>',
    shuffle: '<svg viewBox="0 0 24 24"><path d="M14.8 6h3.4l-1.4 1.4 1.4 1.4 3.8-3.8-3.8-3.8-1.4 1.4 1.4 1.4h-3.4c-2.2 0-3.8 1.2-5 2.6l1.5 1.5c.9-1 2-2 3.5-2.1zM3.4 7.4h2.4c1.2 0 2.2.4 3 1.1l1.6 1.6 1.4-1.4-1.5-1.5c-1.2-1.1-2.7-1.8-4.5-1.8H3.4zM14.8 18h3.4l-1.4-1.4 1.4-1.4 3.8 3.8-3.8 3.8-1.4-1.4 1.4-1.4h-3.4c-1.5-.1-2.6-1.1-3.5-2.1l-7-7c-.8-.7-1.8-1.1-3-1.1H3.4v2h2.4c.8 0 1.4.3 2 .9l7 7z"/></svg>',
    expand: '<svg viewBox="0 0 24 24"><path d="M4 8V4h4v2H6v2H4zm4 12H4v-4h2v2h2v2zm12-4v4h-4v-2h2v-2h2zm-4-12h4v4h-2V6h-2V4z"/></svg>',
    trackPlay: '<svg viewBox="0 0 24 24"><path d="M8 5.14v13.72c0 .8.87 1.3 1.56.9l10.2-6.86a1.05 1.05 0 0 0 0-1.8L9.56 4.24A1.05 1.05 0 0 0 8 5.14z"/></svg>'
  };

  /* ---------- 状态 ---------- */
  var MODE = getMode();
  var state = {
    playing: false,
    current_index: -1,
    title: "",
    artist: "",
    album: "",
    duration: 0,
    position: 0,
    playlist: [],
    loop_mode: "list",
    volume: 0.5,
    wallpaper: "",
    has_playlist: false,
    favorite: false,
    space_background: "",
    space_settings: null,
    error: ""
  };
  var bridge = null;
  var refs = {};
  var currentTab = "all";
  var lastSpaceBg = "";
  var spaceDirty = false; // 壁纸预览中标记，防止播放进度推送覆盖预览

  /* ---------- 元素引用 ---------- */
  function collectRefs() {
    if (MODE === "compact") {
      refs = {
        card: qs("#cardA"),
        turntable: qs("#turntableA"),
        tonearm: qs("#tonearmA"),
        vinyl: qs("#vinylA"),
        eq: qs("#eqA"),
        songInfo: qs("#songInfoA"),
        title: qs("#titleA"),
        sub: qs("#subA"),
        curTime: qs("#curTimeA"),
        durTime: qs("#durTimeA"),
        progress: qs("#progressA"),
        fill: qs("#progressFillA"),
        thumb: qs("#progressThumbA"),
        tip: qs("#progressTipA"),
        play: qs("#playA"),
        prev: qs("#prevA"),
        next: qs("#nextA"),
        modeBtn: qs("#modeBtnA"),
        listBtn: qs("#listBtnA"),
        volBtn: qs("#volBtnA"),
        expandBtn: qs("#expandBtnA"),
        playlistPop: qs("#playlistPopA"),
        popList: qs("#popListA"),
        popEmpty: qs("#popEmptyA"),
        volPop: qs("#volPopA"),
        volSlider: qs("#volSliderA"),
        volVal: qs("#volValA"),
        statusLine: qs("#statusLineA")
      };
    } else {
      refs = {
        tonearm: qs("#tonearmB"),
        vinyl: qs("#vinylB"),
        title: qs("#titleB"),
        sub: qs("#subB"),
        curTime: qs("#curTimeB"),
        durTime: qs("#durTimeB"),
        progress: qs("#progressB"),
        fill: qs("#progressFillB"),
        thumb: qs("#progressThumbB"),
        tip: qs("#progressTipB"),
        play: qs("#playB"),
        prev: qs("#prevB"),
        next: qs("#nextB"),
        modeBtn: qs("#modeBtn"),
        back: qs("#backBtn"),
        list: qs("#listB"),
        listEmpty: qs("#listEmptyB"),
        eqCanvas: qs("#eqCanvas"),
        eqIdle: qs("#eqIdleB"),
        volBtn: qs("#volBtnB"),
        volSlider: qs("#volSliderB"),
        vinylCol: qs(".vinyl-col"),
        spaceBg: qs("#spaceBg"),
        vinylCover: qs("#vinylCoverB"),
        heartInfo: qs("#heartInfo"),
        heartBottom: qs("#heartBottom"),
        favInfoBtn: qs("#favInfoBtn"),
        favInfoLabel: qs("#favInfoLabel"),
        favBottomBtn: qs("#favBottomBtn"),
        tabAll: qs("#tabAll"),
        tabFav: qs("#tabFav"),
        album: qs("#albumB"),
        lyric: qs("#lyricB"),
        spaceMask: qs("#spaceMask"),
        settingsBtn: qs("#settingsBtn"),
        settingsBackdrop: qs("#settingsBackdrop"),
        settingsPanel: qs("#settingsPanel"),
        settingsClose: qs("#settingsClose"),
        settingsStatus: qs("#settingsStatus"),
        spaceWallpaperStrip: qs("#spaceWallpaperStrip"),
        spaceWallpaperOpacity: qs("#spaceWallpaperOpacity"),
        spaceWallpaperOpacityValue: qs("#spaceWallpaperOpacityValue"),
        spaceMaskOpacity: qs("#spaceMaskOpacity"),
        spaceMaskOpacityValue: qs("#spaceMaskOpacityValue"),
        spaceFit: qs("#spaceFit"),
        spaceSettingsSave: qs("#spaceSettingsSave"),
        spaceSettingsReset: qs("#spaceSettingsReset"),
        winMinBtn: qs("#winMinBtn"),
        winMaxBtn: qs("#winMaxBtn"),
        winCloseBtn: qs("#winCloseBtn"),
        nowThumb: qs("#nowThumb"),
        nowTitle: qs("#nowTitleB")
      };
    }
  }

  /* ---------- 渲染：播放列表 ---------- */
  function renderPlaylistInto(container, emptyEl) {
    container.innerHTML = "";
    var all = state.playlist || [];
    var list = all;
    if (MODE === "full" && currentTab === "fav") {
      list = all.filter(function (it) { return it.favorite; });
    }
    if (!list.length) {
      if (emptyEl) {
        emptyEl.textContent = (MODE === "full" && currentTab === "fav") ? "还没有收藏的歌曲" : "暂无歌曲";
        emptyEl.hidden = false;
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    list.forEach(function (item, i) {
      var row = document.createElement("div");
      row.className = "track" + (item.index === state.current_index ? " active" : "");
      var index = document.createElement("span");
      index.className = "t-index";
      index.textContent = (i < 9 ? "0" + (i + 1) : i + 1);
      row.appendChild(index);
      if (item.index === state.current_index) {
        var icon = document.createElement("span");
        icon.className = "t-icon";
        icon.innerHTML = state.playing ? ICONS.trackPlay : ICONS.pause;
        row.appendChild(icon);
      }
      var name = document.createElement("span");
      name.className = "t-name";
      name.textContent = item.title || "未命名";
      name.title = item.title || "";
      row.appendChild(name);
      var dur = document.createElement("span");
      dur.className = "t-dur";
      dur.textContent = fmt(item.duration || 0);
      row.appendChild(dur);
      if (item.favorite) {
        var heart = document.createElement("span");
        heart.className = "t-heart";
        heart.textContent = "\u2665";
        row.appendChild(heart);
      }
      (function (idx) {
        row.addEventListener("click", function (e) {
          e.stopPropagation();
          if (bridge) bridge.selectTrack(idx);
        });
      })(item.index);
      container.appendChild(row);
    });
  }

  /* ---------- 渲染：歌曲元信息（专辑 / 歌词） ---------- */
  function renderSongMeta() {
    if (MODE !== "full") return;
    if (refs.album) {
      var album = state.album || "";
      refs.album.textContent = album ? ("专辑 · " + album) : "专辑 · 未知";
    }
    if (refs.lyric) {
      // 预留：一句歌词氛围（后续可接入歌词数据）
    }
  }

  /* ---------- 渲染：收藏状态 ---------- */
  function renderFavorite() {
    var fav = !!state.favorite;
    if (refs.heartInfo) refs.heartInfo.classList.toggle("on", fav);
    if (refs.heartBottom) refs.heartBottom.classList.toggle("on", fav);
    if (refs.favInfoBtn) refs.favInfoBtn.classList.toggle("on", fav);
    if (refs.favInfoLabel) refs.favInfoLabel.textContent = fav ? "已收藏" : "收藏";
    if (refs.favBottomBtn) refs.favBottomBtn.title = fav ? "取消收藏" : "收藏";
  }

  /* ---------- 渲染：音乐空间背景（参考莲心自习室的壁纸调节方案） ---------- */
  function clamp01(v) {
    v = Number(v);
    if (isNaN(v)) return 0.7;
    return Math.max(0, Math.min(1, v));
  }

  function findSpaceWallpaper(wallpapers, id) {
    if (!wallpapers) return null;
    for (var i = 0; i < wallpapers.length; i++) {
      if (wallpapers[i].id === id) return wallpapers[i];
    }
    return null;
  }

  function currentSpaceVisuals() {
    var s = state.space_settings || {};
    var settings = s.settings || {};
    var wallpaperId = settings.wallpaper || "default";
    var item = findSpaceWallpaper(s.wallpapers, wallpaperId);
    var url = (item && item.url) || state.space_background || "";
    var opacity = clamp01(settings.wallpaper_opacity != null ? settings.wallpaper_opacity : 0.7);
    var mask = clamp01(settings.content_mask_opacity != null ? settings.content_mask_opacity : 0.5);
    var fit = settings.fit === "contain" ? "contain" : "cover";
    // 与自习室一致：有效遮罩 = 1 - 壁纸透明度 x (1 - 内容遮罩)
    return {
      url: url,
      opacity: opacity,
      mask: mask,
      effective: 1 - opacity * (1 - mask),
      fit: fit
    };
  }

  function renderSpaceBackground() {
    if (MODE !== "full") return;
    var v = currentSpaceVisuals();
    var key = v.url + "|" + v.opacity + "|" + v.mask + "|" + v.fit;
    if (key === lastSpaceBg) return;
    lastSpaceBg = key;
    var root = document.documentElement;
    root.style.setProperty("--space-wallpaper", v.url ? ("url(\"" + v.url + "\")") : "none");
    root.style.setProperty("--space-wallpaper-size", v.fit);
    root.style.setProperty("--space-wallpaper-opacity", String(v.opacity));
    root.style.setProperty("--space-effective-mask", String(v.effective));
  }

  /* ---------- 渲染：音乐空间设置面板 ---------- */
  function renderSpaceSettings() {
    if (MODE !== "full") return;
    renderSpaceBackground();
    renderWallpaperStrip();
  }

  function renderWallpaperStrip() {
    var strip = refs.spaceWallpaperStrip;
    if (!strip) return;
    var s = state.space_settings || {};
    var settings = s.settings || {};
    var wallpapers = s.wallpapers || [];
    strip.innerHTML = "";
    if (!wallpapers.length) {
      strip.innerHTML = '<div class="settings-empty">没有可用的壁纸</div>';
      return;
    }
    wallpapers.forEach(function (item) {
      var btn = document.createElement("button");
      btn.className = "wallpaper-option" + (item.id === settings.wallpaper ? " active" : "");
      btn.dataset.wallpaper = item.id;
      btn.title = item.name;
      var img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      if (item.url) img.src = item.url;
      else img.style.background = "linear-gradient(135deg,#16202e,#0d1320)";
      var strong = document.createElement("strong");
      strong.textContent = item.name;
      btn.appendChild(img);
      btn.appendChild(strong);
      btn.addEventListener("click", function () {
        spaceSettingsPreview(item.id, null, null, null);
      });
      strip.appendChild(btn);
    });
  }

  /* ---------- 音乐空间设置：实时预览（不落盘） ---------- */
  function spaceSettingsPreview(wallpaperId, opacity, mask, fit) {
    var s = state.space_settings || { settings: {} };
    s.settings = s.settings || {};
    if (wallpaperId != null) s.settings.wallpaper = wallpaperId;
    if (opacity != null) s.settings.wallpaper_opacity = opacity;
    if (mask != null) s.settings.content_mask_opacity = mask;
    if (fit != null) s.settings.fit = fit;
    state.space_settings = s;
    spaceDirty = true;
    renderSpaceBackground();
    renderWallpaperStrip();
  }

  function syncSpaceSettingsControls() {
    if (MODE !== "full") return;
    var settings = (state.space_settings || {}).settings || {};
    if (refs.spaceWallpaperOpacity) {
      var v = Math.round(clamp01(settings.wallpaper_opacity != null ? settings.wallpaper_opacity : 0.7) * 100);
      refs.spaceWallpaperOpacity.value = v;
      if (refs.spaceWallpaperOpacityValue) refs.spaceWallpaperOpacityValue.textContent = v + "%";
    }
    if (refs.spaceMaskOpacity) {
      var m = Math.round(clamp01(settings.content_mask_opacity != null ? settings.content_mask_opacity : 0.5) * 100);
      refs.spaceMaskOpacity.value = m;
      if (refs.spaceMaskOpacityValue) refs.spaceMaskOpacityValue.textContent = m + "%";
    }
    if (refs.spaceFit) refs.spaceFit.value = settings.fit === "contain" ? "contain" : "cover";
  }

  function toggleSpaceSettingsPanel(open) {
    if (MODE !== "full" || !refs.settingsPanel) return;
    var show = (open === undefined) ? refs.settingsPanel.hidden : !!open;
    if (show) syncSpaceSettingsControls();
    refs.settingsPanel.hidden = !show;
    if (refs.settingsBackdrop) refs.settingsBackdrop.hidden = !show;
  }

  function saveSpaceSettings() {
    if (!bridge) return;
    var s = state.space_settings || { settings: {} };
    var settings = s.settings || {};
    bridge.saveSpaceSettings(
      settings.wallpaper || "default",
      clamp01(settings.wallpaper_opacity != null ? settings.wallpaper_opacity : 0.7),
      clamp01(settings.content_mask_opacity != null ? settings.content_mask_opacity : 0.5),
      settings.fit === "contain" ? "contain" : "cover",
      function (json) {
        try {
          var payload = JSON.parse(json);
          if (payload && payload.settings) {
            state.space_settings = payload;
            spaceDirty = false;
            renderSpaceBackground();
            renderWallpaperStrip();
          }
        } catch (e) { console.error(e); }
      }
    );
    if (refs.settingsStatus) {
      refs.settingsStatus.textContent = "已保存";
      clearTimeout(saveSpaceSettings._t);
      saveSpaceSettings._t = setTimeout(function () {
        if (refs.settingsStatus) refs.settingsStatus.textContent = "";
      }, 1800);
    }
  }

  /* ---------- 切换播放列表 Tab ---------- */
  function setTab(tab) {
    if (MODE !== "full") return;
    currentTab = tab;
    if (refs.tabAll) refs.tabAll.classList.toggle("active", tab === "all");
    if (refs.tabFav) refs.tabFav.classList.toggle("active", tab === "fav");
    renderPlaylistInto(refs.list, refs.listEmpty);
  }

  /* ---------- 渲染：模式按钮 ---------- */
  function renderModeButton(btn) {
    if (!btn) return;
    var map = { list: ICONS.repeat, one: ICONS.repeatOne, random: ICONS.shuffle };
    var tip = { list: "列表循环", one: "单曲循环", random: "随机播放" };
    btn.innerHTML = map[state.loop_mode] || ICONS.repeat;
    btn.title = tip[state.loop_mode] || "播放模式";
    if (btn.classList.contains("link")) {
      btn.classList.toggle("active", state.loop_mode === "one" || state.loop_mode === "random");
    }
  }

  /* ---------- 渲染：进度 ---------- */
  function renderProgress() {
    var total = state.duration || 0;
    var ratio = total > 0 ? Math.min(1, Math.max(0, (state.position || 0) / total)) : 0;
    refs.fill.style.width = (ratio * 100).toFixed(2) + "%";
    refs.thumb.style.left = (ratio * 100).toFixed(2) + "%";
    refs.curTime.textContent = fmt(state.position);
    refs.durTime.textContent = fmt(total);
  }

  /* ---------- 渲染：音量 ---------- */
  function renderVolume() {
    var v = Math.round((state.volume || 0) * 100);
    if (refs.volSlider) refs.volSlider.value = v;
    if (refs.volVal) refs.volVal.textContent = v + "%";
    if (refs.volBtn) {
      refs.volBtn.innerHTML = (v <= 0) ? ICONS.muted : ICONS.vol;
      refs.volBtn.title = v <= 0 ? "静音" : "音量 " + v + "%";
    }
  }

  /* ---------- 渲染：控制按钮图标 ---------- */
  function renderControlIcons() {
    var enabled = state.has_playlist;
    if (refs.play) {
      refs.play.innerHTML = state.playing ? ICONS.pause : ICONS.play;
      refs.play.disabled = !enabled;
    }
    if (refs.prev) { refs.prev.innerHTML = ICONS.prev; refs.prev.disabled = !enabled; }
    if (refs.next) { refs.next.innerHTML = ICONS.next; refs.next.disabled = !enabled; }
  }

  /* ---------- 渲染：唱机状态 ---------- */
  function renderTurntable() {
    var playing = state.playing && state.has_playlist;
    if (refs.turntable) refs.turntable.classList.toggle("playing", playing);
    else if (refs.vinylCol) refs.vinylCol.classList.toggle("playing", playing);
    if (refs.vinyl) refs.vinyl.classList.toggle("playing", playing);
    if (refs.eq) refs.eq.classList.toggle("playing", playing);
    if (refs.eqIdle) refs.eqIdle.hidden = playing;
    setVinylSpin(playing);
  }

  /* ---------- 黑胶旋转：JS 驱动（暂停时保持当前角度不归位） ---------- */
  var vinylSpin = { angle: 0, raf: null, playing: false, last: 0 };
  function applyVinylTransform() {
    if (refs.vinyl) refs.vinyl.style.transform = "rotate(" + vinylSpin.angle + "deg)";
  }
  function startVinylSpin() {
    if (vinylSpin.raf) return;
    vinylSpin.playing = true;
    vinylSpin.last = 0;
    function tick(ts) {
      if (vinylSpin.last) {
        var dt = (ts - vinylSpin.last) / 1000;
        vinylSpin.angle = (vinylSpin.angle + (360 / 30) * dt) % 360;
      }
      vinylSpin.last = ts;
      applyVinylTransform();
      if (vinylSpin.playing) vinylSpin.raf = requestAnimationFrame(tick);
      else { vinylSpin.raf = null; vinylSpin.last = 0; }
    }
    vinylSpin.raf = requestAnimationFrame(tick);
  }
  function stopVinylSpin() {
    vinylSpin.playing = false;
    if (vinylSpin.raf) { cancelAnimationFrame(vinylSpin.raf); vinylSpin.raf = null; }
    vinylSpin.last = 0;
    applyVinylTransform();
  }
  function setVinylSpin(playing) {
    if (playing) startVinylSpin();
    else stopVinylSpin();
  }

  /* ---------- 渲染：歌曲信息 ---------- */
  function renderSongInfo() {
    var has = state.has_playlist && state.current_index >= 0;
    var title = "";
    var sub = "";
    if (!state.has_playlist) {
      title = "未在播放";
      sub = "还没有可播放的歌曲";
    } else if (has) {
      title = state.title || "未命名";
      var parts = [];
      if (state.artist) parts.push(state.artist);
      if (state.album) parts.push(state.album);
      sub = parts.join(" · ");
    } else {
      title = "未在播放";
      sub = "从播放列表选择一首歌";
    }
    refs.title.textContent = title;
    refs.sub.textContent = sub;
    if (refs.nowTitle) refs.nowTitle.textContent = title;
  }

  /* ---------- 渲染：状态提示 ---------- */
  function renderError() {
    if (state.error && refs.statusLine) {
      refs.statusLine.hidden = false;
      refs.statusLine.textContent = state.error;
    } else if (refs.statusLine) {
      refs.statusLine.hidden = true;
    }
  }

  /* ---------- 脏标志（位掩码） ---------- */
  var D_PROGRESS  = 1 << 0;
  var D_CONTROL   = 1 << 1;
  var D_TURNTABLE = 1 << 2;
  var D_SONG      = 1 << 3;
  var D_VOLUME    = 1 << 4;
  var D_MODE      = 1 << 5;
  var D_FAVORITE  = 1 << 6;
  var D_PLAYLIST  = 1 << 7;
  var D_SETTINGS  = 1 << 8;
  var D_ERROR     = 1 << 9;
  var DIRTY_ALL   = (1 << 10) - 1;

  function computeDirty(next) {
    var d = 0;
    if ("position" in next && next.position !== state.position) d |= D_PROGRESS;
    if ("duration" in next && next.duration !== state.duration) d |= D_PROGRESS;
    if ("playing" in next && next.playing !== state.playing) d |= (D_CONTROL | D_TURNTABLE | D_PROGRESS);
    if ("has_playlist" in next && next.has_playlist !== state.has_playlist) d |= (D_CONTROL | D_TURNTABLE);
    if ("title" in next && next.title !== state.title) d |= D_SONG;
    if ("artist" in next && next.artist !== state.artist) d |= D_SONG;
    if ("album" in next && next.album !== state.album) d |= D_SONG;
    if ("current_index" in next && next.current_index !== state.current_index) d |= D_SONG;
    if ("volume" in next && next.volume !== state.volume) d |= D_VOLUME;
    if ("loop_mode" in next && next.loop_mode !== state.loop_mode) d |= D_MODE;
    if ("favorite" in next && next.favorite !== state.favorite) d |= D_FAVORITE;
    if ("playlist" in next && next.playlist !== state.playlist) d |= D_PLAYLIST;
    if ("space_settings" in next && next.space_settings !== state.space_settings) d |= D_SETTINGS;
    if ("space_background" in next && next.space_background !== state.space_background) d |= D_SETTINGS;
    if ("wallpaper" in next && next.wallpaper !== state.wallpaper) d |= D_SETTINGS;
    if ("error" in next && next.error !== state.error) d |= D_ERROR;
    return d || D_PROGRESS;
  }

  /* ---------- 总渲染 ---------- */
  function render(dirty) {
    if (!dirty) dirty = DIRTY_ALL;
    if (dirty & D_CONTROL)   renderControlIcons();
    if (dirty & D_PROGRESS)  renderProgress();
    if (dirty & D_VOLUME)    renderVolume();
    if (dirty & D_SONG)      renderSongInfo();
    if (dirty & D_TURNTABLE) renderTurntable();
    if (dirty & D_MODE)      renderModeButton(refs.modeBtn);
    if (dirty & D_FAVORITE)  renderFavorite();
    if (dirty & D_SETTINGS)  renderSpaceSettings();
    if (dirty & D_SONG)      renderSongMeta();
    if (dirty & D_TURNTABLE) restartEqAnim();
    if (dirty & D_ERROR)     renderError();
    if (dirty & D_PLAYLIST) {
      if (MODE === "compact") {
        renderPlaylistInto(refs.popList, refs.popEmpty);
      } else {
        renderPlaylistInto(refs.list, refs.listEmpty);
      }
    }
  }

  /* ---------- 进度条交互 ---------- */
  function bindSeek(progressEl) {
    if (!progressEl) return;
    var dragging = false;
    function ratioFromEvent(e) {
      var rect = progressEl.getBoundingClientRect();
      if (rect.width <= 0) return 0;
      var r = (e.clientX - rect.left) / rect.width;
      return Math.min(1, Math.max(0, r));
    }
    progressEl.addEventListener("click", function (e) {
      if (!bridge || !state.duration) return;
      bridge.seek(ratioFromEvent(e) * state.duration);
    });
    progressEl.addEventListener("pointerdown", function (e) {
      if (!state.duration) return;
      dragging = true;
      progressEl.setPointerCapture(e.pointerId);
      if (bridge) bridge.seek(ratioFromEvent(e) * state.duration);
    });
    progressEl.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var r = ratioFromEvent(e);
      refs.fill.style.width = (r * 100).toFixed(2) + "%";
      refs.thumb.style.left = (r * 100).toFixed(2) + "%";
      if (state.duration && bridge) bridge.seek(r * state.duration);
    });
    progressEl.addEventListener("pointerup", function () { dragging = false; });
    progressEl.addEventListener("mousemove", function (e) {
      if (!refs.tip) return;
      var r = ratioFromEvent(e);
      refs.tip.style.left = (r * 100).toFixed(2) + "%";
      refs.tip.textContent = fmt(r * (state.duration || 0));
    });
  }

  /* ---------- 事件绑定 ---------- */
  function bindEvents() {
    if (refs.play) {
      refs.play.addEventListener("click", function () {
        if (bridge) bridge.togglePlay();
      });
    }
    if (refs.prev) {
      refs.prev.addEventListener("click", function () {
        if (bridge) bridge.previous();
      });
    }
    if (refs.next) {
      refs.next.addEventListener("click", function () {
        if (bridge) bridge.next();
      });
    }
    if (refs.modeBtn) {
      refs.modeBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var order = { list: "random", random: "one", one: "list" };
        if (bridge) bridge.setPlayMode(order[state.loop_mode] || "list");
      });
    }
    if (refs.expandBtn) {
      refs.expandBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (bridge) bridge.openMusicSpace();
      });
    }
    if (refs.back) {
      refs.back.addEventListener("click", function () {
        if (bridge) bridge.closeMusicSpace();
      });
    }
    if (refs.favInfoBtn) {
      refs.favInfoBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (bridge) bridge.toggleFavorite();
      });
    }
    if (refs.favBottomBtn) {
      refs.favBottomBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (bridge) bridge.toggleFavorite();
      });
    }
    if (refs.tabAll) {
      refs.tabAll.addEventListener("click", function () { setTab("all"); });
    }
    if (refs.tabFav) {
      refs.tabFav.addEventListener("click", function () { setTab("fav"); });
    }
    if (refs.listBtn) {
      refs.listBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        refs.playlistPop.hidden = !refs.playlistPop.hidden;
        if (refs.volPop) refs.volPop.hidden = true;
      });
    }
    if (refs.volBtn) {
      refs.volBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (refs.volPop) {
          refs.volPop.hidden = !refs.volPop.hidden;
          if (refs.playlistPop) refs.playlistPop.hidden = true;
        } else {
          if (bridge) {
            var next = state.volume > 0 ? 0 : 0.6;
            bridge.setVolume(next);
          }
        }
      });
    }
    if (refs.volSlider) {
      refs.volSlider.addEventListener("input", function () {
        if (bridge) bridge.setVolume(parseFloat(refs.volSlider.value) / 100);
      });
      refs.volSlider.addEventListener("change", function () {
        if (bridge) bridge.setVolume(parseFloat(refs.volSlider.value) / 100);
      });
    }
    if (refs.volPop) {
      refs.volPop.addEventListener("click", function (e) { e.stopPropagation(); });
    }
    if (refs.playlistPop) {
      refs.playlistPop.addEventListener("click", function (e) { e.stopPropagation(); });
    }
    /* 点击唱机 / 歌曲信息 / 整卡也可进入音乐空间（仅 Mode A） */
    if (MODE === "compact") {
      [refs.turntable, refs.songInfo].forEach(function (el) {
        if (!el) return;
        el.addEventListener("click", function () {
          if (bridge) bridge.openMusicSpace();
        });
      });
      document.addEventListener("click", function (e) {
        if (refs.playlistPop && !refs.playlistPop.hidden &&
            !refs.playlistPop.contains(e.target) && e.target !== refs.listBtn) {
          refs.playlistPop.hidden = true;
        }
        if (refs.volPop && !refs.volPop.hidden &&
            !refs.volPop.contains(e.target) && e.target !== refs.volBtn) {
          refs.volPop.hidden = true;
        }
      });
    }
    if (refs.settingsBtn) {
      refs.settingsBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleSpaceSettingsPanel();
      });
    }
    if (refs.settingsClose) {
      refs.settingsClose.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleSpaceSettingsPanel(false);
      });
    }
    if (refs.settingsBackdrop) {
      refs.settingsBackdrop.addEventListener("click", function () {
        toggleSpaceSettingsPanel(false);
      });
    }
    if (refs.spaceWallpaperOpacity) {
      refs.spaceWallpaperOpacity.addEventListener("input", function () {
        var v = parseFloat(refs.spaceWallpaperOpacity.value) / 100;
        if (refs.spaceWallpaperOpacityValue) refs.spaceWallpaperOpacityValue.textContent = Math.round(v * 100) + "%";
        spaceSettingsPreview(null, v, null, null);
      });
    }
    if (refs.spaceMaskOpacity) {
      refs.spaceMaskOpacity.addEventListener("input", function () {
        var v = parseFloat(refs.spaceMaskOpacity.value) / 100;
        if (refs.spaceMaskOpacityValue) refs.spaceMaskOpacityValue.textContent = Math.round(v * 100) + "%";
        spaceSettingsPreview(null, null, v, null);
      });
    }
    if (refs.spaceFit) {
      refs.spaceFit.addEventListener("change", function () {
        spaceSettingsPreview(null, null, null, refs.spaceFit.value);
      });
    }
    if (refs.spaceSettingsSave) {
      refs.spaceSettingsSave.addEventListener("click", function () {
        saveSpaceSettings();
      });
    }
    if (refs.spaceSettingsReset) {
      refs.spaceSettingsReset.addEventListener("click", function () {
        spaceSettingsPreview("default", 0.7, 0.5, "cover");
        syncSpaceSettingsControls();
        saveSpaceSettings();
      });
    }
    bindSeek(refs.progress);
  }

  /* ---------- 微型音律（Mode A） ---------- */
  function buildMiniEq() {
    if (MODE !== "compact" || !refs.eq) return;
    var n = 20;
    refs.eq.innerHTML = "";
    for (var i = 0; i < n; i++) {
      var bar = document.createElement("span");
      bar.style.animationDelay = (i * 0.13).toFixed(2) + "s";
      refs.eq.appendChild(bar);
    }
  }

  /* ---------- Mode B 音律 Canvas ---------- */
  var eqAnim = null;
  var eqBars = [];
  function eqIsPlaying() {
    return state.playing && state.has_playlist && (state.volume || 0) > 0.01;
  }
  function startEqAnim() {
    if (MODE !== "full" || !refs.eqCanvas || eqAnim) return;
    var ctx = refs.eqCanvas.getContext("2d");
    var n = 48;
    eqBars = new Array(n).fill(0);
    function ensureSize() {
      var cssW = refs.eqCanvas.clientWidth || 400;
      var cssH = refs.eqCanvas.clientHeight || 60;
      var dpr = window.devicePixelRatio || 1;
      if (refs.eqCanvas.width !== Math.round(cssW * dpr)) {
        refs.eqCanvas.width = Math.round(cssW * dpr);
        refs.eqCanvas.height = Math.round(cssH * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: cssW, h: cssH };
    }
    function drawIdle() {
      var d = ensureSize();
      ctx.clearRect(0, 0, d.w, d.h);
      ctx.fillStyle = "rgba(47,217,160,0.22)";
      ctx.fillRect(0, d.h / 2 - 1, d.w, 2);
    }
    function loop() {
      if (!eqIsPlaying()) {
        eqAnim = null;
        drawIdle();
        return;
      }
      var d = ensureSize();
      ctx.clearRect(0, 0, d.w, d.h);
      var gap = 2, bw = (d.w - gap * (n - 1)) / n;
      var mid = d.h / 2;
      var amp = (state.volume || 0.5) * mid * 0.9;
      var grad = ctx.createLinearGradient(0, 0, 0, mid);
      grad.addColorStop(0, "#6df0c0");
      grad.addColorStop(1, "#18b98a");
      for (var i = 0; i < n; i++) {
        var gauss = Math.exp(-Math.pow((i - n / 2) / (n / 3.2), 2));
        var target = gauss * amp * (0.6 + Math.random() * 0.4);
        eqBars[i] = eqBars[i] * 0.72 + target * 0.28;
        var bh = Math.min(mid, Math.max(2, eqBars[i]));
        var x = i * (bw + gap), y = mid - bh;
        ctx.fillStyle = grad;
        ctx.fillRect(x, y, bw, bh);
      }
      eqAnim = requestAnimationFrame(loop);
    }
    drawIdle();
    if (eqIsPlaying()) loop();
  }
  function restartEqAnim() {
    if (MODE !== "full" || !refs.eqCanvas) return;
    if (eqIsPlaying() && !eqAnim) startEqAnim();
  }
  function stopEqAnim() {
    if (eqAnim) { cancelAnimationFrame(eqAnim); eqAnim = null; }
  }

  /* ---------- 状态注入 ---------- */
  function applyState(payload) {
    if (!payload) return;
    var next = (typeof payload === "string") ? JSON.parse(payload) : payload;
    if (next.space_settings && spaceDirty) {
      var local = state.space_settings || { settings: {} };
      local.settings = local.settings || {};
      local.wallpapers = (next.space_settings && next.space_settings.wallpapers) || local.wallpapers || [];
      next.space_settings = local;
    }
    var dirty = computeDirty(next);
    Object.keys(next).forEach(function (k) { state[k] = next[k]; });
    render(dirty);
  }

  /* ---------- 初始化 ---------- */
  function init() {
    collectRefs();
    if (MODE === "compact") {
      qs("#app-compact").hidden = false;
      qs("#app-full").hidden = true;
    } else {
      qs("#app-compact").hidden = true;
      qs("#app-full").hidden = false;
    }
    buildMiniEq();
    bindEvents();
    render();
    startEqAnim();
  }

  function connect() {
    if (typeof QWebChannel === "undefined") {
      setTimeout(connect, 50);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.musicBridge;
      if (!bridge) { console.error("musicBridge not found"); return; }
      init();
      bridge.getState(function (json) {
        try { applyState(JSON.parse(json)); } catch (e) { console.error(e); }
      });
    });
  }

  window.lianxinMusic = {
    applyState: applyState,
    getMode: MODE,
    isReady: function () { return !!bridge; }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();