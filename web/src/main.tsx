import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./efectos/efectos.css";
import { iniciarEfectos } from "./efectos/efectos";
import { registerServiceWorker } from "./pwa";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

registerServiceWorker();
iniciarEfectos();
