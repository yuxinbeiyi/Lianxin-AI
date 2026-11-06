import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/globals.css";
import "./styles/global-settings.css";
import { App } from "./App";
import { VisionWindow } from "./components/VisionPanel";
import { SettingsWindow } from "./components/SettingsWindow";
import { isSettingsWindowId } from "./utils/settingsWindow";

const isVisionWindow = window.location.hash === "#vision";
const settingsWindowId = window.location.hash.startsWith("#settings/") ? window.location.hash.slice("#settings/".length) : "";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isVisionWindow ? <VisionWindow /> : isSettingsWindowId(settingsWindowId) ? <SettingsWindow id={settingsWindowId} /> : <App />}
  </StrictMode>,
);
