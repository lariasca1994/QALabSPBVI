import { useEffect, useRef, useState } from "react";

const DURACION = 700;
const NUMERO = /\d+/;
const quieto = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Muestra un valor ya formateado ("05", "12") y su cifra cuenta desde 0 al aparecer. */
export function Cifra({ valor }: { valor: string }) {
  const [animado, setAnimado] = useState<{ de: string; texto: string } | null>(null);
  const marco = useRef(0);

  useEffect(() => {
    const m = valor.match(NUMERO);
    if (!m || quieto()) return undefined;
    const destino = Number(m[0]);
    const ancho = m[0].length;
    const inicio = performance.now();
    const paso = (t: number) => {
      const avance = Math.min(1, (t - inicio) / DURACION);
      const actual = String(Math.round(destino * (1 - (1 - avance) ** 3))).padStart(ancho, "0");
      setAnimado({ de: valor, texto: valor.replace(NUMERO, actual) });
      if (avance < 1) marco.current = requestAnimationFrame(paso);
    };
    marco.current = requestAnimationFrame(paso);
    return () => cancelAnimationFrame(marco.current);
  }, [valor]);

  if (animado?.de === valor) return <>{animado.texto}</>;
  return <>{NUMERO.test(valor) && !quieto() ? valor.replace(NUMERO, (d) => "0".repeat(d.length)) : valor}</>;
}
