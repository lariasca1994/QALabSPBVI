import { useEffect, useState } from "react";

// PWA: registro del service worker e instalación de la app.
// El service worker no hace sincronización en segundo plano, ni push, ni consultas
// periódicas: solo responde cuando el usuario navega, así nada mantiene despiertas la API
// ni las bases (capas gratuitas).

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  });
}

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export function isStandalone(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

function isIos(): boolean {
  return /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

/**
 * "native": Chrome/Edge (Android, Windows, macOS, Linux) ofrecen el diálogo de instalación.
 * "ios": Safari no lo ofrece; se instala con Compartir → Agregar a inicio.
 * null: ya instalada o el navegador no permite instalarla.
 */
export function useInstallPrompt(): { mode: "native" | "ios" | null; install: () => Promise<void> } {
  const [prompt, setPrompt] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(isStandalone);
  useEffect(() => {
    const onPrompt = (event: Event) => {
      event.preventDefault();
      setPrompt(event as InstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setPrompt(null);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);
  const mode = installed ? null : prompt ? "native" : isIos() ? "ios" : null;
  return {
    mode,
    install: async () => {
      if (!prompt) return;
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice.outcome === "accepted") setInstalled(true);
      setPrompt(null);
    },
  };
}
