import { useEffect, useState } from "react";

const faltanApp = () => 30 - (Math.floor(Date.now() / 1000) % 30);

/**
 * Tiempo del código: con la app autenticadora, cuánto falta para que muestre otro (cada 30 s);
 * con el correo, cuánto falta para que venza (desde que se mostró este paso o se reenvió).
 */
export function AnilloCodigo({ minutos }: { minutos?: number }) {
  const [inicio] = useState(() => Date.now());
  const [ahora, setAhora] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setAhora(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  let resto: number;
  let total: number;
  let texto: string;
  if (minutos) {
    total = minutos * 60;
    resto = Math.max(0, Math.round((inicio + total * 1000 - ahora) / 1000));
    texto = resto
      ? `Código de un solo uso · vence en ${Math.floor(resto / 60)}:${String(resto % 60).padStart(2, "0")}`
      : "El código venció. Pide uno nuevo.";
  } else {
    total = 30;
    resto = faltanApp();
    texto = `La app muestra un código nuevo en ${resto} s`;
  }
  const urgente = resto <= (minutos ? 60 : 5);
  return (
    <p className={`anillo${urgente ? " anillo--urgente" : ""}`}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle className="anillo-fondo" cx="12" cy="12" r="9" />
        <circle className="anillo-valor" cx="12" cy="12" r="9" pathLength={1} style={{ strokeDashoffset: 1 - resto / total }} />
      </svg>
      <span>{texto}</span>
    </p>
  );
}
