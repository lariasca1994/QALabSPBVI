import { useRef, type CSSProperties, type ReactNode } from "react";

/**
 * Cambia de pantalla con transición: el contenido nuevo entra por bloques desde el lado hacia
 * donde se navega (según `orden`). Solo anima la entrada: el contenido anterior se quita en el
 * acto, así nunca queda un formulario viejo visible que se pueda llenar por error. Con
 * prefers-reduced-motion no se anima (regla de efectos.css / styles.css).
 */
export function Transicion({ clave, orden = 0, children, className = "" }: {
  clave: string;
  orden?: number;
  children: ReactNode;
  className?: string;
}) {
  const previo = useRef({ clave, orden, dir: 1 });
  if (previo.current.clave !== clave) {
    previo.current = { clave, orden, dir: orden >= previo.current.orden ? 1 : -1 };
  }
  return (
    <div
      key={clave}
      className={`transicion transicion-entra ${className}`}
      style={{ "--dir": previo.current.dir } as CSSProperties}
    >
      {children}
    </div>
  );
}
