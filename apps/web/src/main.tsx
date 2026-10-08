import "./styles/tokens.css";
import "./styles/global.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { useStations } from "./features/stations/store";

// A chunk from an older build is gone: the page is out of date.
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  useStations.getState().setStatus("idle", "new_version");
});

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
