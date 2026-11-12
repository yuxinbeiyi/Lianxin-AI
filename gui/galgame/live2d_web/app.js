(async function () {
  "use strict";
  const host = (window.parent && window.parent !== window) ? window.parent : null;
  function postToHost(message) {
    if (!host) return;
    try { host.postMessage(Object.assign({ source: "lianxin-live2d" }, message), "*"); } catch (_) { /* ignore */ }
  }
  let maskTimer = null;
  let maskDebounce = null;
  let app;
  let model;
  let state = "idle";
  let disposed = false;
  let ready = false;
  let visible = true;
  let currentMotion;
  let displayedMouth = 0;
  let lastFrame = performance.now();
  let thinking = false;
  let thinkingUntil = 0;
  let thinkingWeight = 0;
  let tracking = { x: 0, y: 0 };
  let gaze = { x: 0, y: 0 };
  let action;
  let lastInteraction = -Infinity;
  let expression = "normal";
  let expressionUntil = 0;
  const expressions = {};
  const expressionWeights = {};
  const actionCurves = {};
  let frameValues = {};

  function react(name) {
    if (!ready || disposed) return false;
    const now = performance.now();
    if (name === "normal") {
      expression = "normal";
      action = undefined;
      return true;
    }
    if (Object.prototype.hasOwnProperty.call(expressions, name)) {
      expression = name;
      expressionUntil = now + 4500;
      return true;
    }
    if (!["nod", "shake", "headpat", "tap"].includes(name) || now - lastInteraction < 800) return false;
    lastInteraction = now;
    action = { name: name, started: now };
    if (name === "headpat" || name === "tap") {
      expression = name === "headpat" ? "smile" : "surprised";
      expressionUntil = now + 2600;
    }
    return true;
  }

  function sampleCurve(curve, seconds) {
    const points = curve.Segments;
    for (let index = 2; index < points.length; index += 3) {
      const start = index === 2 ? 0 : index - 2;
      const end = index + 1;
      if (seconds <= points[end]) {
        const progress = Math.max(0, Math.min(1, (seconds - points[start]) / (points[end] - points[start])));
        const blend = progress * progress * (3 - 2 * progress);
        return points[start + 1] + (points[end + 1] - points[start + 1]) * blend;
      }
    }
    return points[points.length - 1];
  }

  function updateInteraction(core, now, elapsed) {
    if (now > expressionUntil) expression = "normal";
    if (now > thinkingUntil) thinking = false;
    const smoothing = 1 - Math.exp(-elapsed / 180);
    gaze.x += (tracking.x - gaze.x) * smoothing;
    gaze.y += (tracking.y - gaze.y) * smoothing;
    core.addParameterValueById("ParamEyeBallX", gaze.x * 0.45);
    core.addParameterValueById("ParamEyeBallY", gaze.y * 0.3);
    core.addParameterValueById("ParamAngleX", gaze.x * 4);
    core.addParameterValueById("ParamAngleY", gaze.y * 3);
    thinkingWeight += ((thinking && state !== "talking" ? 1 : 0) - thinkingWeight) * smoothing;
    core.addParameterValueById("ParamAngleZ", Math.sin(now / 900) * 1.5 * thinkingWeight);
    core.addParameterValueById("ParamEyeBallY", 0.12 * thinkingWeight);
    if (action) {
      const seconds = (now - action.started) / 1000;
      if (seconds >= 2) {
        action = undefined;
      } else {
        const group = action.name === "shake" ? "shake" : "nod";
        for (const curve of actionCurves[group] || []) {
          if (!/^Param(Angle|BodyAngle)/.test(curve.Id)) continue;
          const strength = curve.Id.startsWith("ParamBodyAngle") ? 0.12 : curve.Id === "ParamAngleY" ? 0.4 : 0.18;
          core.addParameterValueById(curve.Id, sampleCurve(curve, seconds) * strength);
        }
      }
    }
    for (const [name, parameters] of Object.entries(expressions)) {
      const target = expression === name ? 1 : 0;
      expressionWeights[name] += (target - expressionWeights[name]) * smoothing;
      const weight = expressionWeights[name];
      for (const parameter of parameters) {
        if (parameter.Id === "ParamMouthOpenY") continue;
        const base = core.getParameterValueById(parameter.Id);
        const value = parameter.Blend === "Add" ? base + parameter.Value * weight
          : parameter.Blend === "Multiply" ? base * (1 + (parameter.Value - 1) * weight)
            : base + (parameter.Value - base) * weight;
        core.setParameterValueById(parameter.Id, value);
      }
    }
    frameValues = {};
    for (const id of ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeBallX", "ParamEyeBallY", "ParamMouthForm", "ParamEyeLOpen", "ParamEyeROpen"]) {
      frameValues[id] = core.getParameterValueById(id);
    }
  }

  function fail(error) {
    document.getElementById("error").textContent = "Live2D 加载失败：" + String(error);
    console.error("LIANXIN_LIVE2D_ERROR:" + String(error));
    postToHost({ type: "error", message: String(error) });
  }

  function readSettings(url) {
    return new Promise(function (resolve, reject) {
      const request = new XMLHttpRequest();
      request.open("GET", url);
      request.onload = function () {
        if (request.status !== 0 && request.status !== 200) {
          reject(new Error("模型读取失败：" + request.status));
          return;
        }
        try { resolve(JSON.parse(request.responseText)); } catch (error) { reject(error); }
      };
      request.onerror = function () { reject(new Error("无法读取本地模型")); };
      request.send();
    });
  }

  function fit() {
    if (!model || disposed) return;
    model.anchor.set(0.5, 0.5);
    model.scale.set(Math.min(window.innerWidth / model.internalModel.width, window.innerHeight / model.internalModel.height) * 0.98);
    model.position.set(window.innerWidth / 2, window.innerHeight / 2);
    app.renderer.resize(window.innerWidth, window.innerHeight);
    if (ready) scheduleMaskSampling();
  }

  function playState() {
    const group = state === "talking" ? "Speaking" : "Rest";
    if (!ready || disposed || currentMotion === group) return;
    currentMotion = group;
    model.internalModel.motionManager.stopAllMotions();
    model.motion(group, 0, PIXI.live2d.MotionPriority.FORCE).then(function (started) {
      if (!started && !disposed) {
        currentMotion = undefined;
        fail(new Error("无法播放动作：" + group));
      }
    }).catch(function (error) {
      currentMotion = undefined;
      if (!disposed) fail(error);
    });
  }

  function sampleMaskOnce() {
    if (disposed || !ready || !app || !model) return;
    try {
      const width = 64;
      const height = 96;
      const source = app.renderer.extract.canvas(app.stage);
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return;
      context.clearRect(0, 0, width, height);
      context.drawImage(source, 0, 0, width, height);
      const pixels = context.getImageData(0, 0, width, height).data;
      const values = [];
      for (let index = 0; index < width * height; index += 1) {
        values.push(pixels[index * 4 + 3] > 24 ? 1 : 0);
      }
      postToHost({ type: "mask", width: width, height: height, values: values });
    } catch (error) {
      console.warn("LIANXIN_LIVE2D_MASK_ERROR:" + String(error));
    }
  }

  function startMaskSampling() {
    if (!host || disposed || !ready || !app || !model) return;
    if (maskTimer) { clearInterval(maskTimer); maskTimer = null; }
    postToHost({ type: "mask-reset" });
    let samples = 0;
    const sample = function () {
      sampleMaskOnce();
      samples += 1;
      if (samples >= 5 && maskTimer) { clearInterval(maskTimer); maskTimer = null; }
    };
    sample();
    maskTimer = setInterval(sample, 250);
  }

  function scheduleMaskSampling() {
    if (!host) return;
    if (maskDebounce) clearTimeout(maskDebounce);
    maskDebounce = setTimeout(function () { maskDebounce = null; startMaskSampling(); }, 320);
  }

  window.lianxinLive2D = {
    react: react,
    setTracking: function (x, y) {
      tracking = { x: Math.max(-1, Math.min(1, Number(x) || 0)), y: Math.max(-1, Math.min(1, Number(y) || 0)) };
    },
    setThinking: function (active) {
      thinking = Boolean(active);
      thinkingUntil = performance.now() + 90000;
    },
    setState: function (nextState, isVisible) {
      state = nextState === "talking" ? "talking" : "idle";
      visible = Boolean(isVisible);
      if (!ready || disposed) return;
      if (visible) {
        app.start();
        playState();
      } else {
        app.stop();
      }
    },
    diagnostics: function () {
      if (!ready) return { ready: false, disposed: disposed };
      const core = model.internalModel.coreModel;
      return {
        ready: true, state: state, motion: currentMotion, disposed: disposed,
        thinking: thinking, action: action ? action.name : null, expression: expression,
        tracking: tracking, gaze: gaze, parameters: frameValues,
        mouth: displayedMouth,
        arm: core.getParameterValueById("ParamArmSway"),
        breath: core.getParameterValueById("ParamBreath"),
        drawables: core.getDrawableCount(), rendering: app.ticker.started,
      };
    },
    dispose: function () {
      if (disposed) return;
      disposed = true;
      if (maskTimer) { clearInterval(maskTimer); maskTimer = null; }
      if (maskDebounce) { clearTimeout(maskDebounce); maskDebounce = null; }
      window.removeEventListener("resize", fit);
      if (app) app.stop();
      if (model) model.destroy({ texture: true, baseTexture: true });
      if (app) app.destroy(false);
    },
  };

  if (host) {
    window.addEventListener("message", function (event) {
      if (event.source !== host) return;
      const data = event.data || {};
      if (data.source !== "lianxin-live2d-host") return;
      const api = window.lianxinLive2D;
      if (!api) return;
      try {
        if (data.type === "react") api.react(String(data.name || ""));
        else if (data.type === "tracking") api.setTracking(Number(data.x) || 0, Number(data.y) || 0);
        else if (data.type === "thinking") api.setThinking(Boolean(data.active));
        else if (data.type === "state") api.setState(String(data.state || "idle"), Boolean(data.visible));
        else if (data.type === "dispose") api.dispose();
      } catch (error) {
        console.warn("LIANXIN_LIVE2D_HOST_ERROR:" + String(error));
      }
    });
  }

  try {
    if (!window.Live2DCubismCore || !window.PIXI || !PIXI.live2d) throw new Error("Cubism 播放器依赖未加载");
    const settings = await readSettings(window.lianxinModelUrl);
    settings.url = window.lianxinModelUrl;
    const expressionDefinitions = settings.FileReferences.Expressions || [];
    const expressionNames = ["smile", "sad", "angry", "surprised"];
    for (const name of expressionNames) {
      const entry = expressionDefinitions.find(function (item) { return item.File.endsWith("." + name + ".exp3.json"); });
      if (!entry) continue;
      expressions[name] = (await readSettings(new URL(entry.File, window.lianxinModelUrl).href)).Parameters;
      expressionWeights[name] = 0;
    }
    const motions = settings.FileReferences.Motions;
    for (const [name, group] of [["nod", "Nod"], ["shake", "Shake"]]) {
      if (motions[group] && motions[group][0]) {
        actionCurves[name] = (await readSettings(new URL(motions[group][0].File, window.lianxinModelUrl).href)).Curves;
      }
    }
    const allMotions = Object.values(motions).flat();
    const speaking = allMotions.find(function (entry) { return /lianxinV3SpeakingBlinkStrongerArmSway/.test(entry.File); });
    const rest = allMotions.find(function (entry) { return /lianxinV3StrongerArmSway/.test(entry.File); });
    if (!speaking || !rest) throw new Error("v17 说话/摆臂动作文件缺失");
    settings.FileReferences.Motions = { Rest: [rest], Speaking: [speaking] };
    if (disposed) return;
    app = new PIXI.Application({
      view: document.getElementById("stage"), width: window.innerWidth, height: window.innerHeight,
      backgroundAlpha: 0, antialias: true, resolution: window.devicePixelRatio || 1,
      autoStart: false,
    });
    model = await PIXI.live2d.Live2DModel.from(settings, { autoUpdate: false, autoInteract: false, motionPreload: "ALL" });
    if (disposed) { model.destroy({ texture: true, baseTexture: true }); return; }
    app.ticker.add(function () {
      model.update(app.ticker.deltaMS);
    }, undefined, PIXI.UPDATE_PRIORITY.HIGH);
    model.internalModel.breath = undefined;
    model.internalModel.motionManager.expressionManager.stopAllExpressions();
    model.internalModel.on("beforeModelUpdate", function () {
      const now = performance.now();
      const elapsed = Math.min(100, now - lastFrame);
      lastFrame = now;
      const core = model.internalModel.coreModel;
      if (state === "talking") {
        displayedMouth = core.getParameterValueById("ParamMouthOpenY");
      } else {
        displayedMouth *= Math.exp(-elapsed / 70);
        if (displayedMouth < 0.001) displayedMouth = 0;
        core.setParameterValueById("ParamMouthOpenY", displayedMouth);
        if (model.internalModel.eyeBlink) {
          model.internalModel.eyeBlink.updateParameters(core, elapsed / 1000);
        }
      }
      updateInteraction(core, now, elapsed);
    });
    app.stage.addChild(model);
    model.internalModel.motionManager.on("motionFinish", function () {
      if (disposed) return;
      currentMotion = undefined;
      requestAnimationFrame(playState);
    });
    window.addEventListener("resize", fit);
    fit();
    ready = true;
    window.lianxinLive2D.setState(state, visible);
    console.info("LIANXIN_LIVE2D_READY");
    postToHost({ type: "ready" });
    startMaskSampling();
  } catch (error) {
    if (app) app.stop();
    fail(error);
  }
})();
