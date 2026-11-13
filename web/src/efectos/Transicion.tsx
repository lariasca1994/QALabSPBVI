import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

const SALIDA = 170;
const reducir = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Cambia de pantalla con transición: la que sale se desenfoca y se corre; la nueva entra
 * por bloques desde el lado hacia donde se navega (según `orden`). Mientras sale se sigue
 * mostrando el contenido anterior. Con prefers-reduced-motion el cambio es inmediato.
 */
export function Transicion({ clave, orden = 0, children, className = "" }: {
  clave: string;
  orden?: number;
  children: ReactNode;
  className?: string;
}) {
  const [mostrada, setMostrada] = useState({ clave, dir: 1 });
  const [fase, setFase] = useState<"entra" | "sale">("entra");
  const anterior = useRef<ReactNode>(children);
  const ordenPrevio = useRef(orden);

  useEffect(() => {
    if (clave === mostrada.clave) {
      setFase("entra");
      return undefined;
    }
    const dir = orden >= ordenPrevio.current ? 1 : -1;
    ordenPrevio.current = orden;
    if (reducir()) {
      setMostrada({ clave, dir });
      return undefined;
    }
    setFase("sale");
    const t = window.setTimeout(() => { setMostrada({ clave, dir }); setFase("entra"); }, SALIDA);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clave]);

  const actual = clave === mostrada.clave;
  if (actual) anterior.current = children;
  return (
    <div
      key={mostrada.clave}
      className={`transicion ${fase === "sale" || !actual ? "transicion-sale" : "transicion-entra"} ${className}`}
      style={{ "--dir": mostrada.dir } as CSSProperties}
    >
      {actual ? children : anterior.current}
    </div>
  );
}
