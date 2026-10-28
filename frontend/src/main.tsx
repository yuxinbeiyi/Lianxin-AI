import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/globals.css";
import { App } from "./App";
import { VisionWindow } from "./components/VisionPanel";

const isVisionWindow = window.location.hash === "#vision";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isVisionWindow ? <VisionWindow /> : <App />}
  </StrictMode>,
);
