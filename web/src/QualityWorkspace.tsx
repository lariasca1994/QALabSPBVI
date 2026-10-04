import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Activity,
  BookOpen,
  Braces,
  Check,
  ClipboardCheck,
  History,
  ListChecks,
  LoaderCircle,
  Plus,
  RefreshCw,
  UserPlus,
  Users,
} from "lucide-react";
import {
  api,
  type CaseDefinition,
  type Epic,
  type Execution,
  type User,
  type WorkItem,
} from "./api";
import { formatDate, friendlyError, Modal, PageHeading } from "./ui";

type Role = User["role"];
type ModalState =
  | { type: "epic" }
  | { type: "members" }
  | { type: "story" }
  | { type: "case"; storyKey: string }
  | { type: "definition"; testCase: WorkItem }
  | { type: "history"; testCase: WorkItem }
  | { type: "task" }
  | null;

const PRIORITIES = [
  { value: "highest", label: "Máxima" },
  { value: "high", label: "Alta" },
  { value: "medium", label: "Media" },
  { value: "low", label: "Baja" },
  { value: "lowest", label: "Mínima" },
];
const TASK_STATUS: Record<string, string> = { open: "Abierta", in_progress: "En curso", done: "Hecha" };
const NEXT_TASK_STATUS: Record<string, { status: string; label: string }> = {
  open: { status: "in_progress", label: "Iniciar" },
  in_progress: { status: "done", label: "Completar" },
};
const EMPTY_DEFINITION: CaseDefinition = {
  request_method: "GET",
  request_path: "/health",
  request_query: {},
  request_headers: {},
  request_body: null,
  expected_status_codes: [200],
  expected_response: { status: "ok" },
};

function priorityLabel(value?: string): string {
  return PRIORITIES.find((item) => item.value === value)?.label ?? "Media";
}

function lines(value: FormDataEntryValue | null): string[] {
  return String(value ?? "").split("\n").map((line) => line.trim()).filter(Boolean);
}

function definitionOf(testCase: WorkItem): CaseDefinition {
  return {
    request_method: testCase.request?.method ?? "GET",
    request_path: testCase.request?.path ?? "/",
    request_query: testCase.request?.query ?? {},
    request_headers: testCase.request?.headers ?? {},
    request_body: testCase.request?.body ?? null,
    expected_status_codes: testCase.expected_status_codes ?? [200],
    expected_response: testCase.expected_response ?? null,
  };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Valida el JSON del contrato REST antes de enviarlo; el backend vuelve a validarlo. */
function parseDefinition(text: string): CaseDefinition {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`El JSON no es válido: ${(error as Error).message}`);
  }
  if (!isPlainObject(parsed)) throw new Error("La definición debe ser un objeto JSON.");
  const method = parsed.request_method;
  const path = parsed.request_path;
  if (typeof method !== "string" || !["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase())) {
    throw new Error("request_method debe ser GET, POST, PUT, PATCH o DELETE.");
  }
  if (typeof path !== "string" || !path.startsWith("/")) {
    throw new Error("request_path debe ser una ruta relativa de la API que empiece con /.");
  }
  const query = parsed.request_query ?? {};
  const headers = parsed.request_headers ?? {};
  if (!isPlainObject(query)) throw new Error("request_query debe ser un objeto JSON.");
  if (!isPlainObject(headers) || Object.values(headers).some((value) => typeof value !== "string")) {
    throw new Error("request_headers debe ser un objeto con valores de texto.");
  }
  const body = parsed.request_body ?? null;
  if (body !== null && typeof body !== "object") throw new Error("request_body debe ser un objeto, una lista o null.");
  const statuses = parsed.expected_status_codes;
  if (!Array.isArray(statuses) || statuses.length === 0 || statuses.some((code) => !Number.isInteger(code) || code < 100 || code > 599)) {
    throw new Error("expected_status_codes debe ser una lista de códigos HTTP (100–599).");
  }
  const expected = parsed.expected_response ?? null;
  if (expected !== null && typeof expected !== "object") throw new Error("expected_response debe ser un objeto, una lista o null.");
  return {
    request_method: method.toUpperCase(),
    request_path: path,
    request_query: query,
    request_headers: headers as Record<string, string>,
    request_body: body,
    expected_status_codes: statuses as number[],
    expected_response: expected,
  };
}

function DefinitionEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  let problem = "";
  try {
    parseDefinition(value);
  } catch (error) {
    problem = (error as Error).message;
  }
  return (
    <div className="definition-editor">
      <div className="definition-editor-head">
        <label className="field-label" htmlFor="case-definition">Contrato REST del CP (JSON)</label>
        <button
          className="text-button"
          disabled={Boolean(problem)}
          onClick={() => onChange(JSON.stringify(JSON.parse(value), null, 2))}
          type="button"
        >
          <Braces size={14} /> Formatear
        </button>
      </div>
      <textarea
        aria-describedby="case-definition-help"
        className="text-input json-input"
        id="case-definition"
        onChange={(event) => onChange(event.target.value)}
        rows={16}
        spellCheck={false}
        value={value}
      />
      <p className={`field-hint ${problem ? "field-hint--error" : "field-hint--ok"}`} id="case-definition-help">
        {problem || "JSON válido. Se ejecuta como una solicitud HTTP a la API REST del laboratorio."}
      </p>
      <p className="field-hint">
        Las credenciales no se escriben en el JSON: usá referencias como <code>{"{{secret:PAYMENTS_API_TOKEN}}"}</code>, que se resuelven al ejecutar.
      </p>
    </div>
  );
}

export function ExecutionResult({ execution }: { execution: Execution }) {
  return (
    <section className={`surface-card execution-card ${execution.passed ? "execution-card--passed" : "execution-card--failed"}`}>
      <div className="card-heading"><div><h2>Resultado de ejecución</h2><p>{execution.key} · {execution.passed ? "Aprobado" : "Fallido"}</p></div><span className={`result-pill ${execution.passed ? "result-pill--passed" : "result-pill--failed"}`}>{execution.passed ? "APROBADO" : "FALLIDO"}</span></div>
      <div className="request-summary"><span className="method-pill">{execution.request?.method ?? "HTTP"}</span><code>{execution.request?.url ?? "Solicitud ejecutada"}</code><strong>{execution.result?.status_code ?? "—"}</strong><small>{execution.result?.duration_ms ?? "—"} ms</small></div>
      <details className="response-details"><summary>Ver respuesta registrada</summary><pre>{JSON.stringify(execution.result?.body ?? execution.result ?? {}, null, 2)}</pre></details>
    </section>
  );
}

export function QualityWorkspace({
  user,
  epics,
  selectedEpic,
  onSelectEpic,
  onEpicsChanged,
}: {
  user: User;
  epics: Epic[];
  selectedEpic: string;
  onSelectEpic: (key: string) => void;
  onEpicsChanged: () => Promise<void>;
}) {
  const role: Role = user.role;
  const isAdmin = role === "admin";
  const isManager = role === "admin" || role === "administrador";
  const createsWork = role === "administrador";
  const [items, setItems] = useState<WorkItem[]>([]);
  const [loadingItems, setLoadingItems] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modal, setModal] = useState<ModalState>(null);
  const [modalError, setModalError] = useState("");
  const [saving, setSaving] = useState(false);
  const [team, setTeam] = useState<User[]>([]);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [executingKey, setExecutingKey] = useState("");
  const [definitionText, setDefinitionText] = useState("");

  const epic = epics.find((item) => item.key === selectedEpic);
  const stories = useMemo(() => items.filter((item) => item.kind === "story"), [items]);
  const cases = useMemo(() => items.filter((item) => item.kind === "test_case"), [items]);
  // CP sin HU válida (datos previos o incompletos): se muestran aparte para no perderlos.
  const orphanCases = useMemo(
    () => cases.filter((testCase) => !stories.some((story) => story.key === testCase.story_key)),
    [cases, stories],
  );
  const storyGroups = useMemo(
    () => [
      ...stories,
      ...(orphanCases.length ? [{ key: "", kind: "story", title: "CP sin HU asociada", priority: "medium" } as WorkItem] : []),
    ],
    [stories, orphanCases.length],
  );
  const tasks = useMemo(() => items.filter((item) => item.kind === "task"), [items]);
  const openTasks = tasks.filter((task) => task.status !== "done").length;

  async function loadItems(epicKey = selectedEpic) {
    if (!epicKey) {
      setItems([]);
      return;
    }
    setLoadingItems(true);
    setError("");
    try {
      setItems(await api.workItems(epicKey));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoadingItems(false);
    }
  }

  useEffect(() => {
    setExecution(null);
    void loadItems(selectedEpic);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEpic]);

  useEffect(() => {
    if (!isManager) return;
    api.users().then(setTeam).catch(() => setTeam([]));
  }, [isManager]);

  function open(next: ModalState) {
    setModalError("");
    setNotice("");
    if (next?.type === "case") setDefinitionText(JSON.stringify(EMPTY_DEFINITION, null, 2));
    if (next?.type === "definition") setDefinitionText(JSON.stringify(definitionOf(next.testCase), null, 2));
    setModal(next);
  }

  async function save(action: () => Promise<string>) {
    setSaving(true);
    setModalError("");
    try {
      const message = await action();
      setModal(null);
      setNotice(message);
      await loadItems();
    } catch (saveError) {
      setModalError(saveError instanceof Error && !("status" in saveError) ? saveError.message : friendlyError(saveError));
    } finally {
      setSaving(false);
    }
  }

  function delivery(status?: string): string {
    return status === "pending" ? " El aviso por correo quedó pendiente de reintento." : " Se notificó por correo a la épica.";
  }

  async function runCase(caseKey: string) {
    setExecutingKey(caseKey);
    setError("");
    try {
      setExecution(await api.executeCase(caseKey));
    } catch (runError) {
      setError(friendlyError(runError));
    } finally {
      setExecutingKey("");
    }
  }

  async function moveTask(task: WorkItem) {
    const next = NEXT_TASK_STATUS[task.status ?? "open"];
    if (!next) return;
    setError("");
    try {
      await api.transitionTask(task.key, next.status);
      setNotice(`${task.key} pasó a ${TASK_STATUS[next.status].toLowerCase()}.`);
      await loadItems();
    } catch (moveError) {
      setError(friendlyError(moveError));
    }
  }

  async function reassignTask(task: WorkItem, assigneeId: number) {
    setError("");
    try {
      const updated = await api.assignTask(task.key, assigneeId);
      setNotice(`${task.key} asignada a ${updated.assignee?.display_name ?? "la persona elegida"}.${delivery((updated as { notification_status?: string }).notification_status)}`);
      await loadItems();
    } catch (assignError) {
      setError(friendlyError(assignError));
    }
  }

  const candidates = team.filter((member) => member.is_active);

  return (
    <>
      <PageHeading
        eyebrow="ASEGURAMIENTO DE CALIDAD"
        title="Épicas, historias y pruebas"
        description="Primero la planeación (épica, HU, tareas y CP); después la ejecución automatizada de cada CP contra la API REST."
        actions={isAdmin ? <button className="button button--primary" onClick={() => open({ type: "epic" })} type="button"><Plus size={16} /> Nueva épica</button> : undefined}
      />
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}

      <section className="surface-card quality-toolbar">
        <div className="select-wrap"><label htmlFor="epic-select">ÉPICA</label><select className="text-input select-input" id="epic-select" value={selectedEpic} onChange={(event) => onSelectEpic(event.target.value)}><option value="">Seleccioná una épica</option>{epics.map((item) => <option key={item.key} value={item.key}>{item.key} · {item.title}</option>)}</select></div>
        {epic && (
          <div className="epic-context">
            <span className="epic-key">{epic.key}</span>
            <span>{epic.members?.length ?? 0} integrantes</span>
            {isManager && <button className="button button--small button--quiet" onClick={() => open({ type: "members" })} type="button"><Users size={14} /> Integrantes</button>}
            <button className="icon-button" onClick={() => void loadItems()} aria-label="Actualizar épica" title="Actualizar" type="button"><RefreshCw className={loadingItems ? "spin" : ""} size={16} /></button>
          </div>
        )}
      </section>

      {!epic ? (
        <div className="surface-card empty-state quality-empty"><div className="empty-icon"><ClipboardCheck size={19} /></div><strong>{epics.length ? "Elegí una épica para empezar" : "Todavía no hay épicas"}</strong><p>{epics.length ? "Vas a ver sus historias de usuario, casos de prueba y tareas." : isAdmin ? "Creá la primera épica y asociá a su equipo." : "Cuando el admin te asocie a una épica, aparecerá aquí."}</p></div>
      ) : (
        <>
          <section className="surface-card epic-summary">
            <div className="epic-summary-copy">
              <span className="eyebrow eyebrow--muted">ÉPICA {epic.key}</span>
              <h2>{epic.title}</h2>
              {epic.description && <p>{epic.description}</p>}
              <div className="member-chips">{epic.members.map((member) => <span className="member-chip" key={member.user_id}>{member.display_name ?? member.email}</span>)}</div>
            </div>
            <div className="epic-stats">
              <div><strong>{stories.length}</strong><span>HU</span></div>
              <div><strong>{cases.length}</strong><span>CP</span></div>
              <div><strong>{openTasks}</strong><span>Tareas abiertas</span></div>
            </div>
          </section>

          <section className="surface-card hierarchy-card">
            <div className="card-heading">
              <div><h2>Historias de usuario y casos de prueba</h2><p>Cada HU agrupa sus CP ejecutables.</p></div>
              {createsWork && <button className="button button--small button--primary" onClick={() => open({ type: "story" })} type="button"><Plus size={14} /> Nueva HU</button>}
            </div>
            {storyGroups.length === 0 ? (
              <div className="empty-state"><div className="empty-icon"><BookOpen size={19} /></div><strong>Sin historias de usuario</strong><p>{createsWork ? "Creá la primera HU para empezar a escribir sus CP." : "El administrador crea las HU de la épica."}</p></div>
            ) : storyGroups.map((story) => {
              const storyCases = story.key ? cases.filter((testCase) => testCase.story_key === story.key) : orphanCases;
              return (
                <article className="story-block" key={story.key || "sin-hu"}>
                  <div className="story-head">
                    <div className="story-icon"><BookOpen size={16} /></div>
                    <div className="story-copy"><strong>{story.title}</strong><small>{story.key ? `${story.key} · ` : ""}{storyCases.length} CP{story.key ? ` · prioridad ${priorityLabel(story.priority).toLowerCase()}` : ""}</small></div>
                    {createsWork && story.key && <button className="button button--small button--quiet" onClick={() => open({ type: "case", storyKey: story.key })} type="button"><Plus size={14} /> CP</button>}
                  </div>
                  {story.acceptance_criteria && story.acceptance_criteria.length > 0 && (
                    <details className="story-criteria"><summary>Criterios de aceptación ({story.acceptance_criteria.length})</summary><ul>{story.acceptance_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul></details>
                  )}
                  {storyCases.length > 0 && (
                    <div className="case-list case-list--nested">
                      {storyCases.map((testCase) => (
                        <div className="case-row" key={testCase.key}>
                          <div className="case-status-icon"><ClipboardCheck size={15} /></div>
                          <div className="case-copy"><strong>{testCase.title}</strong><small><span className="method-tag">{testCase.request?.method ?? "HTTP"}</span> {testCase.request?.path ?? ""} · {testCase.key} · v{testCase.version ?? 1}</small></div>
                          <span className="priority-tag">{priorityLabel(testCase.priority)}</span>
                          <div className="case-actions">
                            <button className="button button--small button--quiet" onClick={() => open({ type: "definition", testCase })} type="button" title="Editar el JSON del CP"><Braces size={14} /> JSON</button>
                            {(testCase.versions?.length ?? 0) > 0 && <button className="icon-button" onClick={() => open({ type: "history", testCase })} aria-label={`Historial de ${testCase.key}`} title="Historial de versiones" type="button"><History size={15} /></button>}
                            <button className="button button--small button--primary" disabled={executingKey === testCase.key} onClick={() => void runCase(testCase.key)} type="button">
                              {executingKey === testCase.key ? <LoaderCircle className="spin" size={14} /> : <Activity size={14} />}
                              {executingKey === testCase.key ? "Ejecutando" : "Ejecutar"}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </section>

          <section className="surface-card tasks-card">
            <div className="card-heading">
              <div><h2>Tareas</h2><p>Trabajo asignado a los integrantes de la épica.</p></div>
              {isManager && <button className="button button--small button--primary" onClick={() => open({ type: "task" })} type="button"><Plus size={14} /> Nueva tarea</button>}
            </div>
            {tasks.length === 0 ? (
              <div className="empty-state"><div className="empty-icon"><ListChecks size={19} /></div><strong>Sin tareas</strong><p>{isManager ? "Creá una tarea y asignala a un integrante." : "Cuando te asignen tareas, aparecerán aquí."}</p></div>
            ) : (
              <div className="task-list">
                {tasks.map((task) => {
                  const next = NEXT_TASK_STATUS[task.status ?? "open"];
                  const mine = task.assignee?.user_id === user.id;
                  return (
                    <div className="task-row" key={task.key}>
                      <div className="task-copy"><strong>{task.title}</strong><small>{task.key} · {task.assignee?.display_name ?? "Sin asignar"}</small></div>
                      <span className={`status-tag status-tag--${task.status ?? "open"}`}>{TASK_STATUS[task.status ?? "open"]}</span>
                      <div className="case-actions">
                        {isManager && (
                          <select aria-label={`Asignar ${task.key}`} className="text-input select-input select-input--small" onChange={(event) => { if (event.target.value) void reassignTask(task, Number(event.target.value)); }} value="">
                            <option value="">{task.assignee ? "Reasignar…" : "Asignar…"}</option>
                            {epic.members.filter((member) => member.user_id !== task.assignee?.user_id).map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
                          </select>
                        )}
                        {next && (isManager || mine || !task.assignee) && <button className="button button--small button--quiet" onClick={() => void moveTask(task)} type="button">{next.label}</button>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          {execution && <ExecutionResult execution={execution} />}
        </>
      )}

      {modal?.type === "epic" && (
        <Modal eyebrow="GESTIÓN QA" title="Nueva épica" onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void save(async () => {
              const created = await api.createEpic({
                title: String(form.get("title")),
                description: String(form.get("description")),
                member_ids: form.getAll("member_ids").map(Number),
              });
              await onEpicsChanged();
              onSelectEpic(created.key);
              return `Épica ${created.key} creada.${delivery((created as { notification_status?: string }).notification_status)}`;
            });
          }}>
            <label className="field-label" htmlFor="epic-title">Título</label>
            <input className="text-input" id="epic-title" name="title" required maxLength={200} />
            <label className="field-label" htmlFor="epic-description">Descripción</label>
            <textarea className="text-input" id="epic-description" name="description" required rows={3} />
            <fieldset className="member-picker"><legend className="field-label">Integrantes</legend>
              {candidates.filter((member) => member.id !== user.id).map((member) => <label className="check-row" key={member.id}><input name="member_ids" type="checkbox" value={member.id} /> {member.display_name} <small>{member.role}</small></label>)}
            </fieldset>
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><Plus size={16} /> Crear épica</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "members" && epic && (
        <Modal eyebrow="GESTIÓN QA" title={`Integrantes de ${epic.key}`} onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void save(async () => {
              await api.updateEpicMembers(epic.key, form.getAll("member_ids").map(Number));
              await onEpicsChanged();
              return "Integrantes actualizados. Se notificó por correo al equipo de la épica.";
            });
          }}>
            <p className="field-hint">Quien guarda queda incluido automáticamente. Todos los integrantes reciben el aviso por correo.</p>
            <fieldset className="member-picker"><legend className="field-label">Equipo</legend>
              {candidates.filter((member) => member.id !== user.id).map((member) => <label className="check-row" key={member.id}><input defaultChecked={epic.members.some((current) => current.user_id === member.id)} name="member_ids" type="checkbox" value={member.id} /> {member.display_name} <small>{member.role}</small></label>)}
            </fieldset>
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><UserPlus size={16} /> Guardar integrantes</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "story" && epic && (
        <Modal eyebrow="GESTIÓN QA" title="Nueva historia de usuario" onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void save(async () => {
              const created = await api.createStory(epic.key, {
                title: String(form.get("title")),
                description: String(form.get("description")),
                priority: String(form.get("priority")),
                acceptance_criteria: lines(form.get("acceptance_criteria")),
              });
              return `HU ${created.key} creada.${delivery((created as { notification_status?: string }).notification_status)}`;
            });
          }}>
            <label className="field-label" htmlFor="story-title">Título</label>
            <input className="text-input" id="story-title" name="title" required maxLength={200} placeholder="Como pagador quiero…" />
            <label className="field-label" htmlFor="story-description">Descripción</label>
            <textarea className="text-input" id="story-description" name="description" required rows={3} />
            <label className="field-label" htmlFor="story-priority">Prioridad</label>
            <select className="text-input select-input" defaultValue="medium" id="story-priority" name="priority">{PRIORITIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
            <label className="field-label" htmlFor="story-criteria">Criterios de aceptación</label>
            <textarea className="text-input" id="story-criteria" name="acceptance_criteria" required rows={4} placeholder="Un criterio por línea" />
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><Plus size={16} /> Crear HU</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "case" && (
        <Modal eyebrow="GESTIÓN QA" title={`Nuevo CP para ${modal.storyKey}`} wide onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const storyKey = modal.storyKey;
            void save(async () => {
              const definition = parseDefinition(definitionText);
              const steps = lines(form.get("steps")).map((line) => {
                const [action, expected] = line.split("=>").map((part) => part.trim());
                return { action, expected: expected || "Se cumple el paso." };
              });
              const created = await api.createTestCase(storyKey, {
                ...definition,
                title: String(form.get("title")),
                description: String(form.get("description")),
                priority: String(form.get("priority")),
                preconditions: lines(form.get("preconditions")),
                steps,
                expected_result: String(form.get("expected_result")),
              });
              return `CP ${created.key} creado.${delivery((created as { notification_status?: string }).notification_status)}`;
            });
          }}>
            <div className="form-columns">
              <div className="form-stack">
                <label className="field-label" htmlFor="case-title">Título</label>
                <input className="text-input" id="case-title" name="title" required maxLength={200} />
                <label className="field-label" htmlFor="case-description">Descripción</label>
                <textarea className="text-input" id="case-description" name="description" required rows={2} />
                <label className="field-label" htmlFor="case-priority">Prioridad</label>
                <select className="text-input select-input" defaultValue="medium" id="case-priority" name="priority">{PRIORITIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
                <label className="field-label" htmlFor="case-preconditions">Precondiciones</label>
                <textarea className="text-input" id="case-preconditions" name="preconditions" rows={2} placeholder="Una por línea" />
                <label className="field-label" htmlFor="case-steps">Pasos</label>
                <textarea className="text-input" id="case-steps" name="steps" required rows={3} placeholder="Acción => resultado esperado (uno por línea)" />
                <label className="field-label" htmlFor="case-expected">Resultado esperado</label>
                <textarea className="text-input" id="case-expected" name="expected_result" required rows={2} />
              </div>
              <DefinitionEditor value={definitionText} onChange={setDefinitionText} />
            </div>
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><Plus size={16} /> Crear CP</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "definition" && (
        <Modal eyebrow={`CP ${modal.testCase.key} · VERSIÓN ${modal.testCase.version ?? 1}`} title="Editar JSON del caso" wide onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const testCase = modal.testCase;
            void save(async () => {
              const updated = await api.updateCaseDefinition(testCase.key, {
                ...parseDefinition(definitionText),
                change_note: String(form.get("change_note")),
              });
              return `${updated.key} quedó en la versión ${updated.version}. La anterior se guardó en el historial.${delivery((updated as { notification_status?: string }).notification_status)}`;
            });
          }}>
            <DefinitionEditor value={definitionText} onChange={setDefinitionText} />
            <label className="field-label" htmlFor="change-note">Motivo del cambio</label>
            <input className="text-input" id="change-note" name="change_note" required maxLength={500} placeholder="Ej.: nuevo monto límite en el cuerpo" />
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><Check size={16} /> Guardar nueva versión</button></div>
          </form>
        </Modal>
      )}

      {modal?.type === "history" && (
        <Modal eyebrow={`CP ${modal.testCase.key}`} title="Historial de versiones" wide onClose={() => setModal(null)}>
          <div className="version-list">
            <div className="version-row version-row--current"><strong>v{modal.testCase.version ?? 1} · vigente</strong><small>{modal.testCase.request?.method} {modal.testCase.request?.path}</small></div>
            {[...(modal.testCase.versions ?? [])].reverse().map((version) => (
              <details className="version-row" key={version.version}>
                <summary><strong>v{version.version}</strong> · {version.request.method} {version.request.path} <small>reemplazada el {formatDate(version.replaced_at_epoch)} por {version.replaced_by?.display_name ?? version.replaced_by?.email ?? "—"}</small></summary>
                {version.change_note && <p className="field-hint">Motivo: {version.change_note}</p>}
                <pre>{JSON.stringify({ request: version.request, expected_status_codes: version.expected_status_codes, expected_response: version.expected_response ?? null }, null, 2)}</pre>
              </details>
            ))}
          </div>
        </Modal>
      )}

      {modal?.type === "task" && epic && (
        <Modal eyebrow="GESTIÓN QA" title="Nueva tarea" onClose={() => setModal(null)}>
          <form className="form-stack modal-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const assignee = String(form.get("assignee_id"));
            void save(async () => {
              const created = await api.createTask(epic.key, {
                title: String(form.get("title")),
                description: String(form.get("description")),
                assignee_id: assignee ? Number(assignee) : null,
              });
              return `Tarea ${created.key} creada.${delivery((created as { notification_status?: string }).notification_status)}`;
            });
          }}>
            <label className="field-label" htmlFor="task-title">Título</label>
            <input className="text-input" id="task-title" name="title" required maxLength={200} />
            <label className="field-label" htmlFor="task-description">Descripción</label>
            <textarea className="text-input" id="task-description" name="description" required rows={3} />
            <label className="field-label" htmlFor="task-assignee">Responsable</label>
            <select className="text-input select-input" defaultValue="" id="task-assignee" name="assignee_id">
              <option value="">Sin asignar</option>
              {epic.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
            </select>
            {modalError && <div className="alert alert--error" role="alert">{modalError}</div>}
            <div className="modal-actions"><button className="button button--quiet" onClick={() => setModal(null)} type="button">Cancelar</button><button className="button button--primary" disabled={saving} type="submit"><Plus size={16} /> Crear tarea</button></div>
          </form>
        </Modal>
      )}
    </>
  );
}
