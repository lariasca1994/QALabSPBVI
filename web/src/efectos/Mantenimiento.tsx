import { useEffect, useState } from "react";
import { ArrowLeftRight, Check, WifiOff } from "lucide-react";

const TOTAL = 15;

/**
 * Pantalla de mantenimiento: aparece cuando la API no responde al abrir la app (sin red hacia
 * ella o 502/503/504). Si el equipo no tiene internet, la misma pantalla dice "Sin conexión" y
 * reintenta en cuanto vuelve la red. No reintenta acciones del usuario: consulta /api/health cada 15 s, con
 * cuenta regresiva visible, y recarga la página cuando la API vuelve.
 */
export function Mantenimiento() {
  const [resto, setResto] = useState(TOTAL);
  const [revisando, setRevisando] = useState(false);
  const [sinRed, setSinRed] = useState(() => !navigator.onLine);

  async function revisar() {
    setRevisando(true);
    try {
      const respuesta = await fetch("/api/health", { cache: "no-store", credentials: "include" });
      if (respuesta.ok) {
        window.location.reload();
        return;
      }
    } catch {
      // sigue sin responder
    }
    setRevisando(false);
    setResto(TOTAL);
  }

  useEffect(() => {
    const reloj = window.setInterval(() => setResto((r) => (r > 0 ? r - 1 : 0)), 1000);
    const conRed = () => { setSinRed(false); void revisar(); };
    const desconectado = () => setSinRed(true);
    window.addEventListener("online", conRed);
    window.addEventListener("offline", desconectado);
    return () => {
      window.clearInterval(reloj);
      window.removeEventListener("online", conRed);
      window.removeEventListener("offline", desconectado);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.title = `${sinRed ? "Sin conexión" : "En mantenimiento"} · Laboratorio de pagos inmediatos`;
  }, [sinRed]);

  useEffect(() => {
    if (resto === 0 && !revisando) void revisar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resto]);

  return (
    <main className="mnt">
      <section className="mnt-caja" role="status" aria-live="polite">
        <div className="mnt-orbe" aria-hidden="true">
          <span /><span /><span />
          <b>{sinRed ? <WifiOff size={26} /> : <ArrowLeftRight size={26} />}</b>
        </div>
        <p className="mnt-etiqueta">{sinRed ? "SIN SEÑAL" : "LABORATORIO EN PAUSA"}</p>
        <h1>{sinRed ? "Sin conexión" : "Estamos en mantenimiento"}</h1>
        <p className="mnt-texto">
          {sinRed
            ? "Tu equipo no tiene internet, así que no se pueden consultar llaves, pagos ni casos de prueba."
            : "El servicio del laboratorio no está respondiendo en este momento, así que no se pueden consultar llaves, pagos ni casos de prueba."}
        </p>
        <div className="mnt-barra" role="progressbar" aria-label="Esperando a que el servicio responda"><i /></div>
        <p className="mnt-nota">{sinRed ? "La página vuelve sola cuando haya red." : "Si el servicio estaba dormido, suele volver en menos de un minuto."}</p>
        <ul className="mnt-promesas">
          {[
            "Tus llaves, pagos y casos de prueba siguen guardados.",
            "Los pagos son atómicos: se aplican completos o no se aplican.",
            sinRed ? "Esta página se recarga sola cuando vuelva la conexión." : "Esta página se recarga sola cuando el servicio vuelva.",
          ].map((texto) => (
            <li key={texto}><Check size={17} aria-hidden="true" /><span>{texto}</span></li>
          ))}
        </ul>
        <div className="mnt-pie">
          <span className="mnt-cuenta">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle className="anillo-fondo" cx="12" cy="12" r="9" />
              <circle className="anillo-valor" cx="12" cy="12" r="9" pathLength={1} style={{ strokeDashoffset: revisando ? 0 : 1 - resto / TOTAL }} />
            </svg>
            <span>{revisando ? "Comprobando…" : `Reintento automático en ${resto} s`}</span>
          </span>
          <button className="button button--primary" disabled={revisando} onClick={() => void revisar()} type="button">
            Reintentar ahora
          </button>
        </div>
      </section>
    </main>
  );
}
