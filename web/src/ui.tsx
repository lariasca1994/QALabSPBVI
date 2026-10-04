import type { ReactNode } from "react";
import { X } from "lucide-react";
import { ApiError } from "./api";

export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "No se pudo conectar con la API. Verifica que el backend esté disponible.";
}

export function formatDate(epoch?: number): string {
  if (!epoch) return "Reciente";
  return new Intl.DateTimeFormat("es-CO", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(epoch * 1000));
}

export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div><div className="eyebrow eyebrow--muted">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}

export function Modal({
  title,
  eyebrow = "GESTIÓN DE ACCESO",
  wide = false,
  onClose,
  children,
}: {
  title: string;
  eyebrow?: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }} role="presentation">
      <section aria-labelledby="modal-title" aria-modal="true" className={`modal-card ${wide ? "modal-card--wide" : ""}`} role="dialog">
        <div className="modal-heading"><div><span className="eyebrow eyebrow--muted">{eyebrow}</span><h2 id="modal-title">{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Cerrar" type="button"><X size={18} /></button></div>
        {children}
      </section>
    </div>
  );
}
