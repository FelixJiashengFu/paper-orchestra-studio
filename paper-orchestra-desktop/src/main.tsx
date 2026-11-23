import React from "react";
import ReactDOM from "react-dom/client";
// Global tokens and shared classes first, so component stylesheets imported by
// App (runs.css, settings.css) come later in the bundle and win ties.
import "./styles.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
