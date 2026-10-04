import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Bug as BugIcon, Check, CircleCheck, CircleX, GitPullRequest, Plus, RefreshCw, Send, UserCheck, Wrench } from "lucide-react";
import { api, type Bug, type Epic, type Execution, type User, type WorkItem } from "./api";
import { formatDate, friendlyError, Modal, PageHeading } from "./ui";

/** Flujo del backend: abierto → asignado → en fix → listo para retest → cerrado (o reabierto). */
export const BUG_STATUS: Record<string, string> = {
  open: "Abierto",
  assigned: "Asignado",
  in_fix: "En fix",
  ready_for_retest: "Listo para retest",
  closed: "Cerrado",
  reopened: "Reabierto",
};
const FIX_STATUS: Record<string, string> = { in_progress: "En curso", ready_for_retest: "Listo para retest" };
const SEVERITIES = [
  { value: "blocker", label: "Bloqueante" },
  { value: "critical", label: "Crítica" },
  { value: "major", label: "Mayor" },
  { value: "minor", label: "Menor" },
  { value: "trivial", label: "Trivial" },
];
const FILTERS = [
  { value: "active", label: "Activos" },
  { value: "ready_for_retest", label: "Para retest" },
  { value: "closed", label: "Cerrados" },
  { value: "all", label: "Todos" },
];

export interface BugDraft {
  caseKey: string;
  executionKey?: string;
}

type ModalState =
  | { type: "report" }
  | { type: "detail"; bugKey: string }
  | { type: "fix"; bugKey: string }
  | null;

function severityLabel(value: string): string {
  return SEVERITIES.find((item) => item.value === value)?.label ?? value;
}

function person(value?: { display_name?: string; email?: string } | null): string {
  return value?.display_name ?? value?.email ?? "—";
}

export function BugsWorkspace({
  user,
  epics,
  selectedEpic,
  onSelectEpic,
  draft,
  onDraftUsed,
}: {
  user: User;
  epics: Epic[];
  selectedEpic: string;
  onSelectEpic: (key: string) => void;
  draft: BugDraft | null;
  onDraftUsed: () => void;
}) {
  const isManager = user.role === "admin" || user.role === "administrador";
  const isUser = user.role === "usuario";
  const [bugs, setBugs] = useState<Bug[]>([]);
  const [cases, setCases] = useState<WorkItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("active");
  const [modal, setModal] = useState<ModalState>(null);
  const [modalError, setModalError] = useState("");
  const [saving, setSaving] = useState(false);
  const [reportCase, setReportCase] = useState("");
  const [reportExecution, setReportExecution] = useState("");
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [assigneeChoice, setAssigneeChoice] = useState("");

  const epic = epics.find((item) => item.key === selectedEpic);
  const isMember = Boolean(epic?.members.some((member) => member.user_id === user.id));
  const detail = modal && modal.type !== "report" ? bugs.find((bug) => bug.key === modal.bugKey) : undefined;
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    for (const bug of bugs) result[bug.status] = (result[bug.status] ?? 0) + 1;
    return result;
  }, [bugs]);
  const visible = bugs.filter((bug) =>
    filter === "all" ? true : filter === "active" ? bug.status !== "closed" : bug.status === filter,
  );

  async function load(epicKey = selectedEpic) {
    if (!epicKey) {
      setBugs([]);
      setCases([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [loadedBugs, items] = await Promise.all([api.epicBugs(epicKey), api.workItems(epicKey)]);
      setBugs(loadedBugs);
      setCases(items.filter((item) => item.kind === "test_case"));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(selectedEpic);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEpic]);

  // Llega desde una ejecución fallida: abre el reporte con el CP y la ejecución ya elegidos.
  useEffect(() => {
    if (!draft || !isUser) return;
    openReport(draft.caseKey, draft.executionKey ?? "");
    onDraftUsed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  useEffect(() => {
    if (!reportCase) {
      setExecutions([]);
      return;
    }
    api.caseExecutions(reportCase).then(setExecutions).catch(() => setExecutions([]));
  }, [reportCase]);

  function openReport(caseKey = "", executionKey = "") {
    setModalError("");
    setNotice("");
    setReportCase(caseKey);
    setReportExecution(executionKey);
    setModal({ type: "report" });
  }

  function openDetail(bugKey: string) {
    setModalError("");
    setAssigneeChoice("");
    setModal({ type: "detail", bugKey });
  }

  function delivery(status?: string): string {
    return status === "pending" ? " El aviso por correo quedó pendiente de reintento." : " Se notificó por correo a la épica.";
  }

  /** Ejecuta una acción del flujo y deja el detalle abierto con los datos actualizados. */
  async function act(action: () => Promise<{ notification_status?: string }>, message: string, keepOpen?: string) {
    setSaving(true);
    setModalError("");
    try {
      const result = await action();
      setNotice(`${message}${delivery(result.notification_status)}`);
      await load();
      setModal(keepOpen ? { type: "detail", bugKey: keepOpen } : null);
    } catch (actionError) {
      setModalError(friendlyError(actionError));
    } finally {
      setSaving(false);
    }
  }

  function actions(bug: Bug) {
    const fixesReady = bug.fixes.length > 0 && bug.fixes.every((fix) => fix.status === "ready_for_retest");
    const canWork = isUser && isMember;
    const canFlow = canWork || isManager;
    return (
      <div className="bug-actions">
        {isManager && ["open", "reopened"].includes(bug.status) && (
          <div className="bug-assign">
            <select aria-label={`Responsable de ${bug.key}`} className="text-input select-input select-input--small" onChange={(event) => setAssigneeChoice(event.target.value)} value={assigneeChoice}>
              <option value="">Elige responsable…</option>
              {epic?.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
            </select>
            <button className="button button--small button--primary" disabled={saving || !assigneeChoice} onClick={() => void act(() => api.transitionBug(bug.key, { status: "assigned", assignee_id: Number(assigneeChoice) }), `${bug.key} quedó asignado.`, bug.key)} type="button"><UserCheck size={14} /> Asignar</button>
          </div>
        )}
        {canWork && ["open", "assigned", "reopened"].includes(bug.status) && (
          <button className="button button--small button--quiet" disabled={saving} onClick={() => void act(() => api.transitionBug(bug.key, { status: "in_fix" }), `${bug.key} pasó a en fix.`, bug.key)} type="button"><Wrench size={14} /> Iniciar corrección</button>
        )}
        {canWork && ["assigned", "in_fix", "reopened"].includes(bug.status) && (
          <button className="button button--small button--quiet" disabled={saving} onClick={() => { setModalError(""); setModal({ type: "fix", bugKey: bug.key }); }} type="button"><GitPullRequest size={14} /> Registrar fix</button>
        )}
        {canFlow && bug.status === "in_fix" && (
          <button className="button button--small button--primary" disabled={saving || !fixesReady} title={fixesReady ? "" : "Todos los fix deben estar listos para retest"} onClick={() => void act(() => api.transitionBug(bug.key, { status: "ready_for_retest" }), `${bug.key} quedó listo para retest.`, bug.key)} type="button"><Send size={14} /> Enviar a retest</button>
        )}
        {canFlow && bug.status === "ready_for_retest" && (
          <>
            <button className="button button--small button--primary" disabled={saving} onClick={() => void act(() => api.transitionBug(bug.key, { status: "closed", retest_passed: true }), `Retest aprobado: ${bug.key} quedó cerrado.`, bug.key)} type="button"><CircleCheck size={14} /> Retest aprobado</button>
            <button className="button button--small button--danger" disabled={saving} onClick={() => void act(() => api.transitionBug(bug.key, { status: "reopened", retest_passed: false }), `Retest fallido: ${bug.key} quedó reabierto.`, bug.key)} type="button"><CircleX size={14} /> Retest fallido</button>
          </>
        )}
      </div>
    );
  }

  return (
    <>
      <PageHeading
        eyebrow="ASEGURAMIENTO DE CALIDAD"
        title="Bugs y fixes"
        description="Defectos encontrados en la ejecución de los CP, su corrección y el retest que los cierra."
        actions={isUser && epic && isMember ? <button className="button button--primary" onClick={() => openReport()} type="button"><Plus size={16} /> Reportar bug</button> : undefined}
      />
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}

      <section className="surface-card quality-toolbar">
        <div className="select-wrap"><label htmlFor="bug-epic-select">ÉPICA</label><select className="text-input select-input" id="bug-epic-select" value={selectedEpic} onChange={(event) => onSelectEpic(event.target.value)}><option value="">Selecciona una épica</option>{epics.map((item) => <option key={item.key} value={item.key}>{item.key} · {item.title}</option>)}</select></div>
        {epic && (
          <div className="epic-context">
            <div className="segmented" role="group" aria-label="Filtrar bugs">
              {FILTERS.map((item) => <button className={filter === item.value ? "segmented--active" : ""} key={item.value} onClick={() => setFilter(item.value)} type="button">{item.label}</button>)}
            </div>
            <button className="icon-button" onClick={() => void load()} aria-label="Actualizar bugs" title="Actualizar" type="button"><RefreshCw className={loading ? "spin" : ""} size={16} /></button>
          </div>
        )}
      </section>

      {!epic ? (
        <div className="surface-card empty-state quality-empty"><div className="empty-icon"><BugIcon size={19} /></div><strong>{epics.length ? "Elige una épica" : "Todavía no hay épicas"}</strong><p>Vas a ver los bugs reportados sobre sus CP y el estado de cada fix.</p></div>
      ) : (
        <>
          <section className="surface-card bug-stats" aria-label="Bugs por estado">
            {Object.entries(BUG_STATUS).map(([status, label]) => <div key={status}><strong>{counts[status] ?? 0}</strong><span>{label}</span></div>)}
          </section>

          <section className="surface-card tasks-card">
            <div className="card-heading"><div><h2>Bugs de la épica</h2><p>{isUser ? "Repórtalos desde una ejecución fallida o con el botón Reportar bug." : "Asigna cada bug a un integrante; el equipo registra el fix y hace el retest."}</p></div><span className="priority-tag">{visible.length} de {bugs.length}</span></div>
            {visible.length === 0 ? (
              <div className="empty-state"><div className="empty-icon"><BugIcon size={19} /></div><strong>{bugs.length ? "Ningún bug con este filtro" : "Sin bugs reportados"}</strong><p>{bugs.length ? "Cambia el filtro para ver el resto." : "Cuando un CP falle, el equipo puede reportarlo aquí."}</p></div>
            ) : (
              <div className="task-list">
                {visible.map((bug) => (
                  <button className="bug-row" key={bug.key} onClick={() => openDetail(bug.key)} type="button">
                    <span className={`severity-tag severity-tag--${bug.severity}`}>{severityLabel(bug.severity)}</span>
                    <span className="task-copy"><strong>{bug.title}</strong><small>{bug.key} · {bug.case_key} · {bug.assignee ? person(bug.assignee) : "Sin asignar"} · {bug.fixes.length} fix</small></span>
                    <span className={`status-tag status-tag--${bug.status}`}>{BUG_STATUS[bug.status] ?? bug.status}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {modal?.type === "report" && epic && (
        <Modal eyebrow="NUEVO BUG" title="Reportar bug" onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void act(async () => {
              const created = await api.createBug({
                case_key: reportCase,
                execution_key: reportExecution || null,
                title: String(form.get("title")),
                description: String(form.get("description")),
                severity: String(form.get("severity")),
              });
              return created;
            }, "Bug reportado.");
          }}>
            <label className="field-label" htmlFor="bug-case">Caso de prueba</label>
            <select className="text-input select-input" id="bug-case" onChange={(event) => { setReportCase(event.target.value); setReportExecution(""); }} required value={reportCase}>
              <option value="">Selecciona el CP</option>
              {cases.map((item) => <option key={item.key} value={item.key}>{item.key} · {item.title}</option>)}
            </select>
            <label className="field-label" htmlFor="bug-execution">Ejecución (opcional)</label>
            <select className="text-input select-input" disabled={!reportCase} id="bug-execution" onChange={(event) => setReportExecution(event.target.value)} value={reportExecution}>
              <option value="">{executions.length ? "Sin ejecución asociada" : "El CP no tiene ejecuciones"}</option>
              {executions.map((item) => <option key={item.key} value={item.key}>{item.key} · {item.passed ? "aprobada" : "fallida"} · {formatDate(item.created_at_epoch)}</option>)}
            </select>
            <label className="field-label" htmlFor="bug-title">Título</label>
            <input className="text-input" id="bug-title" maxLength={200} name="title" required />
            <label className="field-label" htmlFor="bug-description">Descripción</label>
            <textarea className="text-input" id="bug-description" name="description" placeholder="Qué se esperaba, qué ocurrió y cómo reproducirlo." required rows={4} />
            <label className="field-label" htmlFor="bug-severity">Severidad</label>
            <select className="text-input select-input" defaultValue="major" id="bug-severity" name="severity">
              {SEVERITIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving || !reportCase} type="submit"><BugIcon size={16} /> Reportar</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "fix" && detail && (
        <Modal eyebrow={`FIX PARA ${detail.key}`} title="Registrar fix" onClose={() => openDetail(detail.key)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void act(() => api.createFix(detail.key, { title: String(form.get("title")), description: String(form.get("description")) }), `Fix registrado para ${detail.key}.`, detail.key);
          }}>
            <label className="field-label" htmlFor="fix-title">Título</label>
            <input className="text-input" id="fix-title" maxLength={200} name="title" required />
            <label className="field-label" htmlFor="fix-description">Qué se corrigió</label>
            <textarea className="text-input" id="fix-description" name="description" required rows={4} />
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => openDetail(detail.key)} type="button">Volver</button><button className="button button--primary" disabled={saving} type="submit"><GitPullRequest size={16} /> Registrar fix</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "detail" && detail && (
        <Modal eyebrow={`${detail.key} · ${detail.case_key}`} title={detail.title} wide onClose={() => setModal(null)}>
          <div className="bug-detail">
            <div className="bug-meta">
              <span className={`severity-tag severity-tag--${detail.severity}`}>{severityLabel(detail.severity)}</span>
              <span className={`status-tag status-tag--${detail.status}`}>{BUG_STATUS[detail.status] ?? detail.status}</span>
              <small>Reportado por {person(detail.created_by)} · {formatDate(detail.created_at_epoch)}</small>
              <small>Responsable: {detail.assignee ? person(detail.assignee) : "sin asignar"}</small>
              {detail.execution_key && <small>Ejecución {detail.execution_key}</small>}
            </div>
            <p className="bug-description">{detail.description}</p>
            {actions(detail)}
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}

            <h3>Fixes</h3>
            {detail.fixes.length === 0 ? <p className="field-hint">Todavía no hay fixes registrados.</p> : detail.fixes.map((fix) => (
              <div className="fix-row" key={fix.key}>
                <div className="task-copy"><strong>{fix.title}</strong><small>{fix.key} · {person(fix.created_by)} · {fix.description}</small></div>
                <span className={`status-tag status-tag--${fix.status}`}>{FIX_STATUS[fix.status] ?? fix.status}</span>
                {isUser && isMember && fix.status === "in_progress" && <button className="button button--small button--quiet" disabled={saving} onClick={() => void act(() => api.markFixReady(detail.key, fix.key), `${fix.key} quedó listo para retest.`, detail.key)} type="button">Marcar listo</button>}
              </div>
            ))}

            <h3>Historial</h3>
            <ol className="bug-history">
              {detail.history.map((event, index) => (
                <li key={`${event.at_epoch}-${index}`}>
                  <strong>{event.from ? `${BUG_STATUS[event.from] ?? FIX_STATUS[event.from] ?? event.from} → ` : ""}{BUG_STATUS[event.to] ?? FIX_STATUS[event.to] ?? event.to}</strong>
                  <small>{person(event.actor)} · {formatDate(event.at_epoch)}{event.fix_key ? ` · ${event.fix_key}` : ""}{event.retest_passed === true ? " · retest aprobado" : event.retest_passed === false ? " · retest fallido" : ""}</small>
                </li>
              ))}
            </ol>
          </div>
        </Modal>
      )}
    </>
  );
}
