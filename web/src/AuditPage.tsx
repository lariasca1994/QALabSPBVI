import { useEffect, useState, type FormEvent } from "react";
import { Download, RefreshCw, ScrollText, Search } from "lucide-react";
import { api, type ApiLog } from "./api";
import { friendlyError, PageHeading } from "./ui";

function statusClass(code: number): string {
  if (code >= 500) return "status-tag--reopened";
  if (code >= 400) return "status-tag--ready_for_retest";
  return "status-tag--closed";
}

export function AuditPage() {
  const [logs, setLogs] = useState<ApiLog[]>([]);
  const [operationId, setOperationId] = useState("");
  const [statusCode, setStatusCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load(filters = { operation_id: operationId.trim(), status_code: statusCode }) {
    setLoading(true);
    setError("");
    try {
      setLogs(await api.auditLogs(filters));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load({ operation_id: "", status_code: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function exportCsv() {
    setError("");
    try {
      const blob = await api.auditExport(operationId.trim() || undefined);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "qalabspbvi-logs.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (exportError) {
      setError(friendlyError(exportError));
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="TRAZABILIDAD"
        title="Auditoría de solicitudes"
        description="Cada solicitud a la API con su método, ruta, código y duración. Filtra por identificador de operación para ver todos los mensajes de un pago."
        actions={<button className="button button--quiet" onClick={() => void exportCsv()} type="button"><Download size={16} /> Exportar CSV</button>}
      />
      {error && <div className="alert alert--error" role="alert">{error}</div>}
      <section className="surface-card quality-toolbar">
        <form className="audit-filters" onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void load(); }}>
          <div className="select-wrap"><label htmlFor="audit-operation">OPERACIÓN</label><input className="text-input" id="audit-operation" maxLength={100} onChange={(event) => setOperationId(event.target.value)} placeholder="operation_id" value={operationId} /></div>
          <div className="select-wrap"><label htmlFor="audit-status">CÓDIGO HTTP</label><input className="text-input" id="audit-status" inputMode="numeric" maxLength={3} onChange={(event) => setStatusCode(event.target.value.replace(/\D/g, ""))} placeholder="Todos" value={statusCode} /></div>
          <button className="button button--primary" disabled={loading} type="submit"><Search size={15} /> Filtrar</button>
          <button className="icon-button" aria-label="Actualizar registro" onClick={() => void load()} title="Actualizar" type="button"><RefreshCw className={loading ? "spin" : ""} size={16} /></button>
        </form>
      </section>
      <section className="surface-card tasks-card">
        <div className="card-heading"><div><h2>Solicitudes</h2><p>Las más recientes primero. El encabezado X-Request-ID de cada respuesta permite ubicarla aquí.</p></div><span className="priority-tag">{logs.length}</span></div>
        {logs.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><ScrollText size={19} /></div><strong>Sin registros</strong><p>{loading ? "Consultando…" : "No hay solicitudes con estos filtros."}</p></div>
        ) : (
          <div className="users-table-wrap">
            <table className="users-table audit-table">
              <thead><tr><th>Fecha</th><th>Método</th><th>Ruta</th><th>Código</th><th>ms</th><th>Usuario</th><th>Operación</th></tr></thead>
              <tbody>
                {logs.map((item) => (
                  <tr key={item.id}>
                    <td>{new Date(item.created_at).toLocaleString("es-CO")}</td>
                    <td><span className="method-tag">{item.method}</span></td>
                    <td><code title={item.path}>{item.route ?? item.path}</code></td>
                    <td><span className={`status-tag ${statusClass(item.status_code)}`}>{item.status_code}</span></td>
                    <td>{Math.round(item.duration_ms)}</td>
                    <td>{item.user_id ?? "—"}</td>
                    <td>{item.operation_id ? <button className="link-button" onClick={() => { setOperationId(item.operation_id ?? ""); void load({ operation_id: item.operation_id ?? "", status_code: "" }); }} type="button">{item.operation_id}</button> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
