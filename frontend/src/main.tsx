import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/globals.css";
import "./styles/global-settings.css";
import { App } from "./App";
import { VisionWindow } from "./components/VisionPanel";
import { SettingsWindow } from "./components/SettingsWindow";
import { PetSpriteWindow } from "./components/PetSpriteWindow";
import { PetLive2DWindow } from "./components/PetLive2DWindow";
import { PetChatWindow } from "./components/PetChatWindow";
import { isSettingsWindowId } from "./utils/settingsWindow";

const isVisionWindow = window.location.hash === "#vision";
const petRoute = window.location.hash.startsWith("#pet/") ? window.location.hash.slice("#pet/".length) : "";
const settingsWindowId = window.location.hash.startsWith("#settings/") ? window.location.hash.slice("#settings/".length) : "";

// 透明桌面窗口必须在首帧之前就切到对应的宿主样式，否则会闪出主界面的深色背景。
if (petRoute) document.documentElement.classList.add(petRoute === "chat" ? "chat-host" : "pet-host");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isVisionWindow ? <VisionWindow />
      : petRoute === "sprite" ? <PetSpriteWindow />
        : petRoute === "live2d" ? <PetLive2DWindow />
          : petRoute === "chat" ? <PetChatWindow />
            : isSettingsWindowId(settingsWindowId) ? <SettingsWindow id={settingsWindowId} />
              : <App />}
  </StrictMode>,
);
