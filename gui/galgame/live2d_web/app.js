(async function () {
  "use strict";
  let app;
  let model;
  let state = "idle";
  let disposed = false;
  let ready = false;
  let currentMotion;
  let displayedMouth = 0;
  let lastFrame = performance.now();

  function fail(error) {
    document.getElementById("error").textContent = "Live2D 加载失败：" + String(error);
    console.error("LIANXIN_LIVE2D_ERROR:" + String(error));
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

  window.lianxinLive2D = {
    setState: function (nextState, visible) {
      state = nextState === "talking" ? "talking" : "idle";
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
        mouth: displayedMouth,
        arm: core.getParameterValueById("ParamArmSway"),
        breath: core.getParameterValueById("ParamBreath"),
        drawables: core.getDrawableCount(), rendering: app.ticker.started,
      };
    },
    dispose: function () {
      if (disposed) return;
      disposed = true;
      window.removeEventListener("resize", fit);
      if (model) model.destroy();
      if (app) app.destroy(false);
    },
  };

  try {
    if (!window.Live2DCubismCore || !window.PIXI || !PIXI.live2d) throw new Error("Cubism 播放器依赖未加载");
    const settings = await readSettings(window.lianxinModelUrl);
    settings.url = window.lianxinModelUrl;
    const motions = settings.FileReferences.Motions;
    const allMotions = Object.values(motions).flat();
    const speaking = allMotions.find(function (entry) { return /lianxinV3SpeakingBlinkStrongerArmSway/.test(entry.File); });
    const rest = allMotions.find(function (entry) { return /lianxinV3StrongerArmSway/.test(entry.File); });
    if (!speaking || !rest) throw new Error("v17 说话/摆臂动作文件缺失");
    settings.FileReferences.Motions = { Rest: [rest], Speaking: [speaking] };
    app = new PIXI.Application({
      view: document.getElementById("stage"), width: window.innerWidth, height: window.innerHeight,
      backgroundAlpha: 0, antialias: true, resolution: window.devicePixelRatio || 1,
    });
    model = await PIXI.live2d.Live2DModel.from(settings, { autoInteract: false, motionPreload: "ALL" });
    if (disposed) { model.destroy(); return; }
    model.internalModel.breath = undefined;
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
    playState();
    console.info("LIANXIN_LIVE2D_READY");
  } catch (error) {
    if (app) app.stop();
    fail(error);
  }
})();
