import { useState, type FormEvent } from "react";
import { FileSearch, RefreshCw, Undo2 } from "lucide-react";
import { api } from "./api";
import { friendlyError } from "./ui";

export interface OperationResult {
  method: string;
  path: string;
  status: number;
  data: unknown;
}

type Action = "status" | "return" | "cancel" | "resolve" | "investigation" | "notifications" | "statement";

const ACTIONS: Array<{ value: Action; label: string; hint: string }> = [
  { value: "status", label: "Estado para el cliente · pain.002", hint: "GET /payments/{operación}/status-report" },
  { value: "return", label: "Devolver pago · pacs.004", hint: "POST /payments/{operación}/returns" },
  { value: "cancel", label: "Solicitar cancelación · camt.056", hint: "POST /payments/{operación}/cancellation-requests" },
  { value: "resolve", label: "Resolver investigación · camt.029", hint: "POST /investigations/{cancelación}/resolution" },
  { value: "investigation", label: "Consultar investigación · camt.029", hint: "GET /investigations/{cancelación}" },
  { value: "notifications", label: "Notificaciones de cuenta · camt.054", hint: "GET /accounts/{cuenta}/notifications" },
  { value: "statement", label: "Extracto conciliado · camt.053", hint: "GET /accounts/{cuenta}/statement" },
];
// Códigos ISO 20022 que admite el laboratorio (mismas listas que la API).
const RETURN_REASONS = [
  ["MD06", "Reembolso solicitado por el cliente"],
  ["AM05", "Pago duplicado"],
  ["AC01", "Número de cuenta incorrecto"],
  ["AC04", "Cuenta cerrada"],
  ["AC06", "Cuenta bloqueada"],
  ["MS02", "No especificado, cliente"],
  ["MS03", "No especificado, entidad"],
];
const CANCELLATION_REASONS = [
  ["DUPL", "Pago duplicado"],
  ["CUST", "Solicitada por el cliente"],
  ["FRAD", "Origen fraudulento"],
  ["TECH", "Problema técnico"],
  ["UPAY", "Pago indebido"],
  ["AM09", "Monto errado"],
  ["AC03", "Cuenta del beneficiario inválida"],
];
const REJECTION_REASONS = [
  ["CUST", "Decisión del beneficiario"],
  ["NOAS", "Sin respuesta del beneficiario"],
  ["AM04", "Fondos insuficientes para devolver"],
  ["ARDT", "El pago ya fue devuelto"],
  ["AC04", "Cuenta cerrada"],
  ["LEGL", "Decisión legal"],
  ["NOOR", "No se recibió el pago original"],
];

function newId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
}

export function PaymentOperations({
  onResult,
  onNotice,
  onError,
}: {
  onResult: (result: OperationResult) => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [action, setAction] = useState<Action>("status");
  const [busy, setBusy] = useState(false);
  const [returnId, setReturnId] = useState(() => newId("dev"));
  const [cancellationId, setCancellationId] = useState(() => newId("cxl"));
  const [accepted, setAccepted] = useState("true");
  const current = ACTIONS.find((item) => item.value === action)!;
  const needsOperation = ["status", "return", "cancel"].includes(action);
  const needsCancellation = ["resolve", "investigation"].includes(action);
  const needsAccount = ["notifications", "statement"].includes(action);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const field = (name: string) => String(values.get(name) ?? "").trim();
    const operation = encodeURIComponent(field("operation_id"));
    const cancellation = encodeURIComponent(field("cancellation_id"));
    const account = encodeURIComponent(field("account_id"));
    let method: "GET" | "POST" = "GET";
    let path = "";
    let body: unknown;
    let message = "Consulta realizada.";
    if (action === "status") path = `/payments/${operation}/status-report`;
    if (action === "return") {
      const amount = field("amount_cents");
      if (amount && (!Number.isSafeInteger(Number(amount)) || Number(amount) <= 0)) {
        onError("El monto a devolver debe ser un entero positivo de centavos, o quedar vacío para devolver todo.");
        return;
      }
      method = "POST";
      path = `/payments/${operation}/returns`;
      body = { return_id: field("return_id"), reason_code: field("reason_code"), ...(amount ? { amount_cents: Number(amount) } : {}) };
      message = "Devolución registrada: el dinero volvió a la cuenta de origen.";
    }
    if (action === "cancel") {
      method = "POST";
      path = `/payments/${operation}/cancellation-requests`;
      body = { cancellation_id: field("cancellation_id"), reason_code: field("reason_code") };
      message = "Solicitud de cancelación enviada: la investigación quedó pendiente del SPBVI receptor.";
    }
    if (action === "resolve") {
      method = "POST";
      path = `/investigations/${cancellation}/resolution`;
      body = accepted === "true" ? { accepted: true } : { accepted: false, rejection_reason: field("rejection_reason") };
      message = accepted === "true" ? "Cancelación aceptada: se devolvió el pago con motivo FOCR." : "Cancelación rechazada con el motivo indicado.";
    }
    if (action === "investigation") path = `/investigations/${cancellation}`;
    if (action === "notifications") {
      const filter = field("filter_operation_id");
      path = `/accounts/${account}/notifications${filter ? `?operation_id=${encodeURIComponent(filter)}` : ""}`;
    }
    if (action === "statement") path = `/accounts/${account}/statement`;

    setBusy(true);
    onError("");
    onNotice("");
    try {
      const response = await api.paymentOperation(method, path, body);
      onResult({ method, path, status: response.status, data: response.data });
      onNotice(response.data.replayed ? "Solicitud idempotente reconocida: se devolvió el resultado existente." : message);
      if (action === "return") setReturnId(newId("dev"));
      if (action === "cancel") setCancellationId(newId("cxl"));
    } catch (requestError) {
      onError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface-card operation-card">
      <div className="card-heading"><div><h2>Operaciones sobre un pago</h2><p>Devoluciones, cancelaciones, investigaciones y reportes ISO 20022.</p></div><FileSearch size={19} className="muted-icon" /></div>
      <form className="form-stack operation-form" onSubmit={(event) => void submit(event)}>
        <label className="field-label" htmlFor="operation-action">Operación</label>
        <select className="text-input select-input" id="operation-action" value={action} onChange={(event) => setAction(event.target.value as Action)}>
          {ACTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <p className="field-hint"><code>{current.hint}</code></p>
        {needsOperation && (
          <>
            <label className="field-label" htmlFor="operation-payment-id">Identificador de operación del pago</label>
            <input className="text-input" id="operation-payment-id" name="operation_id" required maxLength={100} />
          </>
        )}
        {action === "return" && (
          <>
            <label className="field-label" htmlFor="operation-return-id">Identificador de la devolución</label>
            <div className="inline-input-action">
              <input className="text-input" id="operation-return-id" name="return_id" value={returnId} onChange={(event) => setReturnId(event.target.value)} required maxLength={100} />
              <button className="icon-button" type="button" aria-label="Generar otro identificador de devolución" onClick={() => setReturnId(newId("dev"))}><RefreshCw size={16} /></button>
            </div>
            <label className="field-label" htmlFor="operation-return-amount">Monto a devolver (centavos)</label>
            <input className="text-input" id="operation-return-amount" name="amount_cents" type="number" min="1" step="1" inputMode="numeric" placeholder="Vacío: devuelve todo lo pendiente" />
            <label className="field-label" htmlFor="operation-return-reason">Motivo</label>
            <select className="text-input select-input" id="operation-return-reason" name="reason_code">{RETURN_REASONS.map(([code, label]) => <option key={code} value={code}>{code} · {label}</option>)}</select>
          </>
        )}
        {action === "cancel" && (
          <>
            <label className="field-label" htmlFor="operation-cancellation-new">Identificador de la cancelación</label>
            <div className="inline-input-action">
              <input className="text-input" id="operation-cancellation-new" name="cancellation_id" value={cancellationId} onChange={(event) => setCancellationId(event.target.value)} required maxLength={100} />
              <button className="icon-button" type="button" aria-label="Generar otro identificador de cancelación" onClick={() => setCancellationId(newId("cxl"))}><RefreshCw size={16} /></button>
            </div>
            <label className="field-label" htmlFor="operation-cancel-reason">Motivo</label>
            <select className="text-input select-input" id="operation-cancel-reason" name="reason_code">{CANCELLATION_REASONS.map(([code, label]) => <option key={code} value={code}>{code} · {label}</option>)}</select>
          </>
        )}
        {needsCancellation && (
          <>
            <label className="field-label" htmlFor="operation-cancellation-id">Identificador de la cancelación</label>
            <input className="text-input" id="operation-cancellation-id" name="cancellation_id" required maxLength={100} />
          </>
        )}
        {action === "resolve" && (
          <>
            <label className="field-label" htmlFor="operation-decision">Decisión del SPBVI receptor</label>
            <select className="text-input select-input" id="operation-decision" value={accepted} onChange={(event) => setAccepted(event.target.value)}>
              <option value="true">Aceptar: devolver el pago (pacs.004 FOCR)</option>
              <option value="false">Rechazar con motivo</option>
            </select>
            {accepted === "false" && (
              <>
                <label className="field-label" htmlFor="operation-rejection">Motivo del rechazo</label>
                <select className="text-input select-input" id="operation-rejection" name="rejection_reason">{REJECTION_REASONS.map(([code, label]) => <option key={code} value={code}>{code} · {label}</option>)}</select>
              </>
            )}
          </>
        )}
        {needsAccount && (
          <>
            <label className="field-label" htmlFor="operation-account">Cuenta</label>
            <input className="text-input" id="operation-account" name="account_id" required maxLength={100} />
          </>
        )}
        {action === "notifications" && (
          <>
            <label className="field-label" htmlFor="operation-filter">Solo de esta operación (opcional)</label>
            <input className="text-input" id="operation-filter" name="filter_operation_id" maxLength={100} />
          </>
        )}
        <button className="button button--primary" disabled={busy} type="submit"><Undo2 size={15} />{busy ? "Procesando…" : "Ejecutar operación"}</button>
      </form>
    </section>
  );
}
