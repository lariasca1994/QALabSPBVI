import { useEffect, useState } from "react";

const N = 6;

/**
 * Código de 6 dígitos dibujado como seis casillas. Es el mismo <input> de siempre (mismo id,
 * etiqueta, autocompletado y pegado): las casillas solo lo muestran encima. Cada casilla
 * rebota al llenarse; con `error` el grupo tiembla.
 */
export function CampoCodigo({ id, value, onChange, error = false }: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  error?: boolean;
}) {
  const [foco, setFoco] = useState(false);
  const [pulso, setPulso] = useState(-1);

  useEffect(() => {
    if (pulso < 0) return undefined;
    const t = window.setTimeout(() => setPulso(-1), 300);
    return () => window.clearTimeout(t);
  }, [pulso]);

  return (
    <div className={`casillas${value.length === N ? " casillas--completa" : ""}${error ? " casillas--error" : ""}`}>
      <input
        autoComplete="one-time-code"
        className="text-input code-input"
        id={id}
        inputMode="numeric"
        maxLength={N}
        onBlur={() => setFoco(false)}
        onChange={(event) => {
          const limpio = event.target.value.replace(/\D/g, "").slice(0, N);
          if (limpio.length > value.length) setPulso(limpio.length - 1);
          onChange(limpio);
        }}
        onFocus={() => setFoco(true)}
        placeholder="· · · · · ·"
        required
        type="text"
        value={value}
      />
      <div className="casillas-vista" aria-hidden="true">
        {Array.from({ length: N }, (_, i) => (
          <span
            className={[
              value[i] ? "llena" : "",
              foco && i === Math.min(value.length, N - 1) ? "activa" : "",
              pulso === i ? "pulso" : "",
            ].join(" ")}
            key={i}
          >{value[i] ?? ""}</span>
        ))}
      </div>
    </div>
  );
}
