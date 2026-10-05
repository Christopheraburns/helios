import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

// A page chunk from a build that has since been replaced: load the current build
// once instead of leaving a blank page.
window.addEventListener("vite:preloadError", (event) => {
  const key = "helios.reloadedForNewBuild";
  let reloaded = false;
  try {
    reloaded = sessionStorage.getItem(key) === window.location.pathname;
    sessionStorage.setItem(key, window.location.pathname);
  } catch {
    reloaded = true;
  }
  if (reloaded) return;
  event.preventDefault();
  window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
