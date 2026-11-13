// Efectos globales (web y PWA): onda desde el punto del toque en los botones principales y
// en el menú, y temblor del botón de envío cuando aparece un aviso de error (role="alert").
// Sin dependencias; si algo falla, la app sigue igual.
const reducir = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const BOTON = ".button--primary, .button--danger, .button--banner, .nav-link";

function onda(e: PointerEvent) {
  const b = (e.target as Element | null)?.closest?.<HTMLButtonElement>(BOTON);
  if (!b || b.disabled || reducir()) return;
  const r = b.getBoundingClientRect();
  const o = document.createElement("span");
  o.className = b.classList.contains("nav-link") || b.classList.contains("button--banner") ? "qa-onda qa-onda--suave" : "qa-onda";
  o.style.setProperty("--x", `${e.clientX - r.left}px`);
  o.style.setProperty("--y", `${e.clientY - r.top}px`);
  o.style.setProperty("--d", `${Math.max(r.width, r.height) * 2.2}px`);
  b.appendChild(o);
  o.addEventListener("animationend", () => o.remove());
}

export function temblar(el: Element | null | undefined) {
  if (!el || reducir()) return;
  el.classList.remove("qa-temblor");
  void (el as HTMLElement).offsetWidth;
  el.classList.add("qa-temblor");
  el.addEventListener("animationend", () => el.classList.remove("qa-temblor"), { once: true });
}

// Un aviso de error nuevo hace temblar el botón de envío de su formulario.
function vigilarErrores() {
  new MutationObserver((cambios) => {
    for (const cambio of cambios) {
      cambio.addedNodes.forEach((nodo) => {
        if (!(nodo instanceof HTMLElement)) return;
        const aviso = nodo.matches('[role="alert"]') ? nodo : nodo.querySelector('[role="alert"]');
        if (!aviso) return;
        const zona = aviso.closest("form") ?? aviso.closest("section, main");
        temblar(zona?.querySelector('button[type="submit"]'));
      });
    }
  }).observe(document.body, { childList: true, subtree: true });
}

export function iniciarEfectos() {
  try {
    document.addEventListener("pointerdown", onda, { passive: true });
    vigilarErrores();
  } catch {
    // Los efectos son decorativos.
  }
}
