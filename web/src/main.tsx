// *flips the big ON switch* OwO web/src/main.tsx is where the web UI STARTS! ^w^
// The browser loads this first. It imports React, the map library's CSS, VALOR's own styles and the App, applies
// the saved light/dark theme, and mounts the app into #root *wags tail*
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// The CSS of the map library (@xyflow/react), then VALOR's own styles (imported CSS ends up in the bundle) UwU
import "@xyflow/react/dist/style.css";
import "./styles.css";
// App = the whole application (pages, navigation, sign-in) >w<
import App from "./App";

// Entry point: applies the theme the person picked (before the first paint, so it does not flash) and mounts the app.

// Read the saved theme. localStorage can throw (private mode, blocked storage), hence the try. Setting
// data-theme on <html> lets the CSS switch colors before anything is drawn OwO
try {
  const theme = localStorage.getItem("valor-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {
  /* storage unavailable: follow the system theme */
}

// Mount the app into #root. StrictMode adds extra development-only checks (it renders nothing itself); the "!"
// tells TypeScript the element certainly exists ^w^
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
