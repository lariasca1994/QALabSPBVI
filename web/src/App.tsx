import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowRightLeft,
  ArrowUpRight,
  Ban,
  Bell,
  Bug,
  Check,
  ChevronDown,
  CircleHelp,
  CircleDollarSign,
  ClipboardCheck,
  Command,
  CreditCard,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Moon,
  Plus,
  RefreshCw,
  ScrollText,
  Search,
  ShieldCheck,
  Sun,
  Trash2,
  Users,
  WalletCards,
  X,
} from "lucide-react";
import { friendlyError, formatDate, Modal, PageHeading } from "./ui";
import { QualityWorkspace } from "./QualityWorkspace";
import { BugsWorkspace, type BugDraft } from "./BugsWorkspace";
import { PaymentOperations } from "./PaymentOperations";
import { AuditPage } from "./AuditPage";
import { ChangePasswordModal, PasswordReset } from "./PasswordForms";
import { api, ApiError, type Epic, type KeyTypeInfo, type Payment, type User, type WorkItem } from "./api";

type Theme = "light" | "dark";
type View = "overview" | "keys" | "payments" | "quality" | "bugs" | "audit" | "users";
type AuthState = "checking" | "login" | "mfa" | "app";

interface ActionResult {
  method: string;
  path: string;
  status: number;
  data: unknown;
}

function readTheme(): Theme {
  const saved = localStorage.getItem("qalab-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand--compact" : ""}`}>
      <div className="brand-mark" aria-hidden="true">
        <span />
        <span />
      </div>
      {!compact && (
        <div className="brand-copy">
          <strong>qalab<span>spbvi</span></strong>
          <small>LABORATORIO DE PAGOS</small>
        </div>
      )}
    </div>
  );
}

function ThemeButton({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const Icon = theme === "dark" ? Sun : Moon;
  return (
    <button
      className="icon-button"
      onClick={onToggle}
      type="button"
      aria-label={theme === "dark" ? "Activar tema claro" : "Activar tema oscuro"}
      title={theme === "dark" ? "Tema claro" : "Tema oscuro"}
    >
      <Icon size={18} strokeWidth={1.8} />
    </button>
  );
}

function AuthScreen({
  mode,
  onLogin,
  onVerify,
  onResend,
  onBack,
  error,
  busy,
  theme,
  onToggleTheme,
}: {
  mode: "login" | "mfa";
  onLogin: (email: string, password: string) => Promise<void>;
  onVerify: (code: string) => Promise<void>;
  onResend: () => Promise<void>;
  onBack: () => void;
  error: string;
  busy: boolean;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState("");
  // Espera entre reenvíos: coincide con RESEND_COOLDOWN_SECONDS del backend.
  const [resendWait, setResendWait] = useState(0);
  const [resending, setResending] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    if (mode === "mfa") {
      setResendWait(RESEND_COOLDOWN_SECONDS);
      setCode("");
    }
  }, [mode]);

  useEffect(() => {
    if (resendWait <= 0) return;
    const timer = window.setTimeout(() => setResendWait((current) => current - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendWait]);

  async function resend() {
    setNotice("");
    setResending(true);
    try {
      await onResend();
      setCode("");
      setNotice("Te enviamos un código nuevo. El anterior ya no sirve.");
    } catch {
      // El mensaje de error lo muestra el contenedor.
    } finally {
      setResending(false);
      setResendWait(RESEND_COOLDOWN_SECONDS);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("");
    try {
      if (mode === "login") {
        await onLogin(email, password);
        setNotice("Te enviamos un código de acceso al correo.");
      } else {
        await onVerify(code);
      }
    } catch {
      return;
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-visual">
        <div className="auth-visual-top">
          <Brand />
          <span className="environment-pill"><span /> {environmentLabel()}</span>
        </div>
        <div className="visual-content">
          <div className="eyebrow"><span /> PAGOS QUE CONECTAN</div>
          <h1>Calidad y confianza, <em>en cada transacción.</em></h1>
          <p>
            Un laboratorio interoperable para probar pagos inmediatos,
            directorios de llaves y flujos de aseguramiento de calidad.
          </p>
          <div className="visual-art" aria-hidden="true">
            <div className="orbit orbit--one" />
            <div className="orbit orbit--two" />
            <div className="orbit orbit--three" />
            <div className="orbit-core"><ArrowLeftRight size={28} /></div>
            <div className="orbit-node orbit-node--left"><ArrowUpRight size={16} /></div>
            <div className="orbit-node orbit-node--right"><ArrowDownLeft size={16} /></div>
            <div className="orbit-caption orbit-caption--left">ORIGEN</div>
            <div className="orbit-caption orbit-caption--right">DESTINO</div>
          </div>
        </div>
        <div className="auth-visual-footer">
          <span>INSPIRADO EN EL ECOSISTEMA DE PAGOS DE BAJO VALOR</span>
          <span>QALABSPBVI · 2026</span>
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-panel-tools">
          <span className="auth-panel-label">PLATAFORMA DE ASEGURAMIENTO</span>
          <ThemeButton theme={theme} onToggle={onToggleTheme} />
        </div>
        <div className="auth-form-wrap">
          <div className="auth-icon"><ShieldCheck size={23} /></div>
          <span className="eyebrow eyebrow--muted">{resetting ? "RECUPERAR ACCESO" : mode === "login" ? "ACCESO SEGURO" : "SEGUNDO FACTOR"}</span>
          <h2>{resetting ? "Restablece tu contraseña." : mode === "login" ? "Qué bueno verte." : "Revisa tu correo."}</h2>
          <p className="auth-description">
            {resetting
              ? "Te enviamos un código al correo de tu cuenta para definir una contraseña nueva."
              : mode === "login"
              ? "Ingresa a tu espacio de pruebas y operaciones."
              : "Te enviamos un código de un solo uso para confirmar tu identidad."}
          </p>
          {resetting ? (
            <PasswordReset onCancel={() => setResetting(false)} onDone={(message) => { setResetting(false); setNotice(message); }} />
          ) : (
          <>
          <form className="form-stack" onSubmit={submit}>
            {mode === "login" ? (
              <>
                <label className="field-label" htmlFor="login-email">Correo electrónico</label>
                <input
                  autoComplete="username"
                  className="text-input"
                  id="login-email"
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="nombre@organizacion.com"
                  required
                  type="email"
                  value={email}
                />
                <label className="field-label" htmlFor="login-password">Contraseña</label>
                <input
                  autoComplete="current-password"
                  className="text-input"
                  id="login-password"
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Ingresa tu contraseña"
                  required
                  type="password"
                  value={password}
                />
              </>
            ) : (
              <>
                <label className="field-label" htmlFor="mfa-code">Código de acceso</label>
                <input
                  autoComplete="one-time-code"
                  className="text-input code-input"
                  id="mfa-code"
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="· · · · · ·"
                  required
                  type="text"
                  value={code}
                />
                <p className="field-hint">El código es de un solo uso y vence en 5 minutos.</p>
              </>
            )}
            {notice && <div className="inline-notice"><Check size={16} />{notice}</div>}
            {error && <div className="alert alert--error" role="alert">{error}</div>}
            <button className="button button--primary button--wide" disabled={busy} type="submit">
              {busy ? <LoaderCircle className="spin" size={17} /> : null}
              {busy ? "Un momento…" : mode === "login" ? "Continuar" : "Verificar e ingresar"}
            </button>
          </form>
          {mode === "mfa" && (
            <div className="resend-row">
              <span>¿No te llegó el código?</span>
              <button
                className="text-button"
                disabled={resendWait > 0 || resending || busy}
                onClick={resend}
                type="button"
              >
                {resending ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
                {resendWait > 0 ? `Reenviar en ${formatWait(resendWait)}` : "Reenviar código"}
              </button>
            </div>
          )}
          {mode === "mfa" && (
            <button className="text-button back-button" onClick={onBack} type="button">
              <ArrowLeftRight size={15} /> Volver al inicio de sesión
            </button>
          )}
          {mode === "login" && (
            <button className="text-button back-button" onClick={() => { setNotice(""); setResetting(true); }} type="button">
              <KeyRound size={15} /> ¿Olvidaste tu contraseña?
            </button>
          )}
          </>
          )}
          <div className="auth-security-note">
            <ShieldCheck size={16} />
            <span>Conexión de laboratorio · Tus credenciales no se guardan en el navegador.</span>
          </div>
        </div>
        <footer className="auth-panel-footer">
          <span>¿Necesitas ayuda?</span><a href="mailto:soporte@qalabspbvi.local">Contacta al equipo</a>
        </footer>
      </section>
    </main>
  );
}

const RESEND_COOLDOWN_SECONDS = 60;

function formatWait(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function environmentLabel(): string {
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return "ENTORNO LOCAL";
  return host.includes("-prod") ? "PRODUCCIÓN" : "ENTORNO DE LABORATORIO";
}

// Respaldo si /keys/types no responde; la fuente de verdad es app/domains/keys/key_types.py.
const FALLBACK_KEY_TYPES: KeyTypeInfo[] = [
  { code: "document", label: "Documento de identidad", example: "1023456789", hint: "Solo números, de 5 a 15 dígitos." },
  { code: "phone", label: "Celular", example: "3001234567", hint: "Celular colombiano de 10 dígitos que inicia en 3." },
  { code: "email", label: "Correo electrónico", example: "nombre@dominio.com", hint: "Correo registrado en la entidad financiera." },
  { code: "alias", label: "Llave alfanumérica", example: "@ana2026", hint: "Inicia con @ seguida de 3 a 20 letras o números." },
  { code: "merchant_code", label: "Código de comercio", example: "0012345", hint: "Código del comercio, de 4 a 10 dígitos." },
];
let keyTypesRequest: Promise<KeyTypeInfo[]> | null = null;

function useKeyTypes(): KeyTypeInfo[] {
  const [keyTypes, setKeyTypes] = useState<KeyTypeInfo[]>(FALLBACK_KEY_TYPES);
  useEffect(() => {
    keyTypesRequest ??= api.keyTypes().catch(() => FALLBACK_KEY_TYPES);
    let active = true;
    void keyTypesRequest.then((loaded) => { if (active && loaded.length) setKeyTypes(loaded); });
    return () => { active = false; };
  }, []);
  return keyTypes;
}

function KeyTypeFields({ idPrefix, typeName, valueName, typeLabel, valueLabel, valueId }: {
  idPrefix: string;
  typeName: string;
  valueName: string;
  typeLabel: string;
  valueLabel: string;
  valueId?: string;
}) {
  const keyTypes = useKeyTypes();
  const [selected, setSelected] = useState("email");
  const current = keyTypes.find((item) => item.code === selected) ?? keyTypes[0];
  const inputId = valueId ?? `${idPrefix}-key-value`;
  return (
    <>
      <label className="field-label" htmlFor={`${idPrefix}-key-type`}>{typeLabel}</label>
      <select className="text-input select-input" id={`${idPrefix}-key-type`} name={typeName} value={selected} onChange={(event) => setSelected(event.target.value)}>
        {keyTypes.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
      </select>
      <label className="field-label" htmlFor={inputId}>{valueLabel}</label>
      <input className="text-input" id={inputId} name={valueName} placeholder={current?.example} required maxLength={255} aria-describedby={`${inputId}-hint`} />
      <p className="field-hint" id={`${inputId}-hint`}>{current?.hint}</p>
    </>
  );
}

function App() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [auth, setAuth] = useState<AuthState>("checking");
  const [user, setUser] = useState<User | null>(null);
  const [email, setEmail] = useState("");
  const [authError, setAuthError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("overview");
  const [epics, setEpics] = useState<Epic[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [apiHealthy, setApiHealthy] = useState<boolean | null>(null);
  // null: sin consultar; false: alguna base se está reanudando (DIFE en Australia tarda ~1 min).
  const [databasesReady, setDatabasesReady] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedEpic, setSelectedEpic] = useState("");
  const [bugDraft, setBugDraft] = useState<BugDraft | null>(null);
  const [changingPassword, setChangingPassword] = useState(false);
  const [accountNotice, setAccountNotice] = useState("");
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [userNotice, setUserNotice] = useState("");
  const [userError, setUserError] = useState("");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("qalab-theme", theme);
  }, [theme]);

  useEffect(() => {
    let active = true;
    api.me()
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
          setAuth("app");
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setAuth("login");
          if (!(error instanceof ApiError && error.status === 401)) {
            setAuthError(friendlyError(error));
          }
        }
      });
    return () => { active = false; };
  }, []);

  async function loadDashboard() {
    setLoading(true);
    setLoadError("");
    const healthPromise = api.health()
      .then(() => setApiHealthy(true))
      .catch(() => setApiHealthy(false));
    try {
      const nextEpics = await api.epics();
      setEpics(nextEpics);
      await healthPromise;
    } catch (error) {
      setLoadError(friendlyError(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (auth === "app") void loadDashboard();
  }, [auth]);

  // Al entrar, activa las bases y consulta cada 5 s hasta que estén listas (máximo 3 min).
  useEffect(() => {
    if (auth !== "app") return;
    let cancelled = false;
    const deadline = Date.now() + 180_000;
    async function poll() {
      while (!cancelled && Date.now() < deadline) {
        try {
          const ready = await api.databasesReady();
          if (cancelled) return;
          setDatabasesReady(ready);
          if (ready) return;
        } catch {
          if (!cancelled) setDatabasesReady(null);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 5_000));
      }
      if (!cancelled) setDatabasesReady(null);
    }
    void poll();
    return () => { cancelled = true; };
  }, [auth]);

  useEffect(() => {
    if (auth !== "app" || view !== "users" || !user || user.role === "usuario") return;
    api.users()
      .then(setUsers)
      .catch((error: unknown) => setUserError(friendlyError(error)));
  }, [auth, view, user]);

  async function toggleUserActive(member: User) {
    setUserError("");
    setUserNotice("");
    try {
      const updated = await api.setUserActive(member.id, !member.is_active);
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item));
      setUserNotice(updated.is_active ? `${updated.email} quedó activa.` : `${updated.email} quedó inactiva y sus sesiones se cerraron.`);
    } catch (toggleError) {
      setUserError(friendlyError(toggleError));
    }
  }

  async function handleLogin(loginEmail: string, password: string) {
    setBusy(true);
    setAuthError("");
    setEmail(loginEmail);
    try {
      await api.login(loginEmail, password);
      setAuth("mfa");
    } catch (error) {
      setAuthError(friendlyError(error));
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    setAuthError("");
    try {
      await api.resendCode();
    } catch (error) {
      setAuthError(friendlyError(error));
      throw error;
    }
  }

  async function handleVerify(code: string) {
    setBusy(true);
    setAuthError("");
    try {
      await api.verifyCode(email, code);
      const currentUser = await api.me();
      setUser(currentUser);
      setAuth("app");
    } catch (error) {
      setAuthError(friendlyError(error));
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    try {
      await api.logout();
      setUser(null);
      setAuth("login");
      setAuthError("");
    } catch (error) {
      setLoadError(friendlyError(error));
    }
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUserError("");
    setUserNotice("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const role = String(form.get("role")) as "administrador" | "usuario";
    try {
      const created = await api.createUser({
        email: String(form.get("email")),
        display_name: String(form.get("display_name")),
        password: String(form.get("password")),
        role,
      });
      setUsers((current) => [...current, created].sort((a, b) => a.email.localeCompare(b.email)));
      setUserNotice(
        `Cuenta ${created.email} creada con rol ${created.role}. ` +
        (created.notification_status === "failed"
          ? "No se pudo enviar el correo de bienvenida; compártele el acceso por otro canal."
          : "Le enviamos un correo de bienvenida (sin la contraseña)."),
      );
      formElement.reset();
      setUserModalOpen(false);
    } catch (error) {
      setUserError(friendlyError(error));
    }
  }

  const visibleNav = useMemo(() => [
    { id: "overview" as const, label: "Resumen", mobileLabel: "Inicio", icon: LayoutDashboard },
    { id: "keys" as const, label: "Llaves", mobileLabel: "Llaves", icon: KeyRound },
    { id: "payments" as const, label: "Pagos", mobileLabel: "Pagos", icon: CreditCard },
    { id: "quality" as const, label: "Calidad y pruebas", mobileLabel: "QA", icon: ClipboardCheck },
    { id: "bugs" as const, label: "Bugs y fixes", mobileLabel: "Bugs", icon: Bug },
    ...(user && user.role !== "usuario"
      ? [
          { id: "audit" as const, label: "Auditoría", mobileLabel: "Logs", icon: ScrollText },
          { id: "users" as const, label: "Usuarios y roles", mobileLabel: "Usuarios", icon: Users },
        ]
      : []),
  ], [user]);

  if (auth === "checking") {
    return <main className="loading-screen"><Brand /><LoaderCircle className="spin" size={22} /><span>Conectando con tu espacio…</span></main>;
  }
  if (auth === "login" || auth === "mfa") {
    return (
      <AuthScreen
        busy={busy}
        error={authError}
        mode={auth}
        onBack={() => { setAuth("login"); setAuthError(""); }}
        onLogin={handleLogin}
        onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")}
        onResend={handleResend}
        onVerify={handleVerify}
        theme={theme}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><Brand /></div>
        <div className="workspace-select">
          <div className="workspace-avatar">QA</div>
          <div className="workspace-copy"><strong>Laboratorio de pagos</strong><small>QALabSPBVI</small></div>
          <ChevronDown size={15} />
        </div>
        <span className="nav-section-label">ESPACIO DE TRABAJO</span>
        <nav className="primary-nav" aria-label="Navegación principal">
          {visibleNav.map(({ id, label, mobileLabel, icon: Icon }) => (
            <button
              className={`nav-link ${view === id ? "nav-link--active" : ""}`}
              key={id}
              onClick={() => { setView(id); setLoadError(""); }}
              type="button"
            >
              <Icon size={18} strokeWidth={1.8} />
              <span className="nav-label-desktop">{label}</span>
              <span className="nav-label-mobile">{mobileLabel}</span>
              {id === "quality" && <span className="nav-count">{epics.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status">
            <span className={`status-dot ${apiHealthy ? "status-dot--ok" : apiHealthy === false ? "status-dot--error" : ""}`} />
            <span>API</span>
            <small>{apiHealthy ? "Operativa" : apiHealthy === false ? "Sin conexión" : "Revisando"}</small>
          </div>
          <div className="sidebar-user">
            <div className="user-avatar">{user?.display_name.slice(0, 1).toUpperCase()}</div>
            <div className="sidebar-user-copy"><strong>{user?.display_name}</strong><small>{user?.role}</small></div>
            <button className="icon-button icon-button--subtle" onClick={() => setChangingPassword(true)} title="Cambiar contraseña" aria-label="Cambiar contraseña" type="button"><KeyRound size={17} /></button>
            <button className="icon-button icon-button--subtle" onClick={() => void handleLogout()} title="Cerrar sesión" aria-label="Cerrar sesión" type="button"><LogOut size={17} /></button>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb"><span>QALabSPBVI</span><span>/</span><strong>{visibleNav.find((item) => item.id === view)?.label}</strong></div>
          <div className="topbar-actions">
            <div className="local-badge"><span /> {environmentLabel().replace(/^ENTORNO (DE )?/, "")}</div>
            <button className="icon-button" type="button" aria-label="Buscar" title="Buscar" onClick={() => setView("quality")}><Search size={18} /></button>
            <button className="icon-button" type="button" aria-label="Notificaciones" title="Notificaciones"><Bell size={18} /></button>
            <ThemeButton theme={theme} onToggle={() => setTheme((current) => current === "dark" ? "light" : "dark")} />
            {/* En móvil la barra lateral oculta su pie: la cuenta se gestiona desde aquí. */}
            <button className="icon-button topbar-account" onClick={() => setChangingPassword(true)} title="Cambiar contraseña" aria-label="Cambiar contraseña" type="button"><KeyRound size={18} /></button>
            <button className="icon-button topbar-account" onClick={() => void handleLogout()} title="Cerrar sesión" aria-label="Cerrar sesión" type="button"><LogOut size={18} /></button>
            <div className="topbar-divider" />
            <button className="help-button" type="button" title="Ayuda"><CircleHelp size={17} /> Ayuda</button>
          </div>
        </header>

        <main className="content">
          {accountNotice && <div className="alert alert--success page-alert" role="status"><Check size={16} /><span>{accountNotice}</span><button className="icon-button" onClick={() => setAccountNotice("")} aria-label="Cerrar aviso" type="button"><X size={16} /></button></div>}
          {changingPassword && <ChangePasswordModal onClose={(message) => { setChangingPassword(false); if (message) setAccountNotice(message); }} />}
          {databasesReady === false && <div className="alert alert--info page-alert" role="status"><LoaderCircle className="spin" size={16} /><span>Activando las bases de datos. La de llaves está en otra región y tarda cerca de un minuto en reanudarse; las ejecuciones se habilitan cuando esté lista.</span></div>}
          {loadError && <div className="alert alert--error page-alert" role="alert"><span>{loadError}</span><button className="icon-button" onClick={() => setLoadError("")} aria-label="Cerrar aviso"><X size={16} /></button></div>}
          {view === "overview" && (
            <Overview
              apiHealthy={apiHealthy}
              epics={epics}
              loading={loading}
              onOpenEpic={(key) => { setSelectedEpic(key); setView("quality"); }}
              onRefresh={() => void loadDashboard()}
              user={user}
            />
          )}
          {view === "quality" && user && (
            <QualityWorkspace
              epics={epics}
              onEpicsChanged={loadDashboard}
              databasesReady={databasesReady !== false}
              onReportBug={user.role === "usuario" ? (draft) => { setBugDraft(draft); setView("bugs"); } : undefined}
              onSelectEpic={setSelectedEpic}
              selectedEpic={selectedEpic}
              user={user}
            />
          )}
          {view === "bugs" && user && (
            <BugsWorkspace
              draft={bugDraft}
              epics={epics}
              onDraftUsed={() => setBugDraft(null)}
              onSelectEpic={setSelectedEpic}
              selectedEpic={selectedEpic}
              user={user}
            />
          )}
          {view === "audit" && user && user.role !== "usuario" && <AuditPage />}
          {view === "keys" && user && <KeysPage role={user.role} />}
          {view === "payments" && user && <PaymentsPage role={user.role} />}
          {view === "users" && user && user.role !== "usuario" && (
            <UsersPage
              currentRole={user.role}
              currentUserId={user.id}
              error={userError}
              notice={userNotice}
              onCreate={() => setUserModalOpen(true)}
              onToggleActive={(member) => void toggleUserActive(member)}
              users={users}
            />
          )}
        </main>
        <footer className="app-footer">
          <span>QALabSPBVI <b>·</b> Laboratorio de aseguramiento de calidad</span>
          <span><ShieldCheck size={14} /> Entorno aislado · No procesa pagos reales</span>
        </footer>
      </div>
      {userModalOpen && user && (
        <Modal title="Crear cuenta del equipo" onClose={() => setUserModalOpen(false)}>
          <form className="form-stack modal-form" onSubmit={(event) => void createUser(event)}>
            <label className="field-label" htmlFor="new-user-name">Nombre visible</label>
            <input className="text-input" id="new-user-name" name="display_name" required maxLength={120} />
            <label className="field-label" htmlFor="new-user-email">Correo electrónico</label>
            <input className="text-input" id="new-user-email" name="email" type="email" required />
            <label className="field-label" htmlFor="new-user-password">Contraseña inicial</label>
            <input className="text-input" id="new-user-password" name="password" type="password" minLength={12} maxLength={128} required autoComplete="new-password" />
            <p className="field-hint">Mínimo 12 caracteres. La contraseña no se conserva en esta interfaz.</p>
            <label className="field-label" htmlFor="new-user-role">Rol</label>
            <select className="text-input select-input" id="new-user-role" name="role">
              {user.role === "admin" && <option value="administrador">Administrador</option>}
              <option value="usuario">Usuario</option>
            </select>
            {userError && <div className="alert alert--error" role="alert">{userError}</div>}
            <div className="modal-actions">
              <button className="button button--quiet" onClick={() => setUserModalOpen(false)} type="button">Cancelar</button>
              <button className="button button--primary" type="submit"><Plus size={16} /> Crear cuenta</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

type EpicProgress = { stories: number; cases: number; openTasks: number };

function Overview({
  apiHealthy,
  epics,
  loading,
  onOpenEpic,
  onRefresh,
  user,
}: {
  apiHealthy: boolean | null;
  epics: Epic[];
  loading: boolean;
  onOpenEpic: (key: string) => void;
  onRefresh: () => void;
  user: User | null;
}) {
  const [progress, setProgress] = useState<Record<string, EpicProgress>>({});
  const [myTasks, setMyTasks] = useState<Array<WorkItem & { epic_key?: string }>>([]);
  const visibleEpics = epics.slice(0, 6);

  useEffect(() => {
    let active = true;
    // Resumen de la jerarquía QA: HU, CP y tareas por épica, y las tareas propias.
    void Promise.all(visibleEpics.map(async (epic) => [epic.key, await api.workItems(epic.key).catch(() => [])] as const))
      .then((results) => {
        if (!active) return;
        const nextProgress: Record<string, EpicProgress> = {};
        const nextTasks: Array<WorkItem & { epic_key?: string }> = [];
        for (const [epicKey, items] of results) {
          nextProgress[epicKey] = {
            stories: items.filter((item) => item.kind === "story").length,
            cases: items.filter((item) => item.kind === "test_case").length,
            openTasks: items.filter((item) => item.kind === "task" && item.status !== "done").length,
          };
          nextTasks.push(...items.filter((item) => item.kind === "task" && item.status !== "done" && item.assignee?.user_id === user?.id).map((item) => ({ ...item, epic_key: epicKey })));
        }
        setProgress(nextProgress);
        setMyTasks(nextTasks);
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epics, user?.id]);

  return (
    <>
      <PageHeading
        eyebrow="PANEL DE CONTROL"
        title={`Buen día, ${user?.display_name.split(" ")[0] ?? "equipo"}.`}
        description="Tu trabajo de calidad primero: épicas, historias, tareas y casos de prueba."
        actions={<button className="button button--quiet" onClick={onRefresh} type="button"><RefreshCw className={loading ? "spin" : ""} size={16} /> Actualizar</button>}
      />

      <section className="lower-grid">
        <div className="surface-card epic-card">
          <div className="card-heading"><div><h2>Épicas</h2><p>Avance de cada espacio de trabajo QA.</p></div><Command size={18} className="muted-icon" /></div>
          {epics.length === 0 ? (
            <div className="empty-state"><div className="empty-icon"><ClipboardCheck size={19} /></div><strong>No hay épicas asignadas</strong><p>{user?.role === "admin" ? "Crea la primera épica desde Calidad y pruebas." : "Cuando te asocien a una épica, aparecerá en este espacio."}</p></div>
          ) : (
            <div className="epic-list">
              {visibleEpics.map((epic) => {
                const counts = progress[epic.key];
                return (
                  <button className="epic-row" key={epic.key} onClick={() => onOpenEpic(epic.key)} type="button">
                    <div className="epic-icon"><ClipboardCheck size={17} /></div>
                    <div className="epic-row-copy"><strong>{epic.title}</strong><small>{epic.key} · {counts ? `${counts.stories} HU · ${counts.cases} CP · ${counts.openTasks} tareas abiertas` : "Cargando avance…"} · {epic.members?.length ?? 0} integrantes</small></div>
                    <span className="epic-date">{formatDate(epic.created_at_epoch)}</span>
                    <ArrowUpRight size={16} className="epic-arrow" />
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div className="surface-card health-card">
          <div className="card-heading"><div><h2>Mis tareas</h2><p>Pendientes asignadas a ti.</p></div><span className="updated-label">{myTasks.length} ABIERTAS</span></div>
          {myTasks.length === 0 ? (
            <div className="empty-state"><div className="empty-icon"><Check size={19} /></div><strong>Sin pendientes</strong><p>No tienes tareas abiertas asignadas.</p></div>
          ) : (
            <div className="task-list">
              {myTasks.slice(0, 6).map((task) => (
                <button className="epic-row" key={task.key} onClick={() => onOpenEpic(task.epic_key ?? "")} type="button">
                  <div className="epic-row-copy"><strong>{task.title}</strong><small>{task.key} · {task.epic_key} · {task.status === "in_progress" ? "En curso" : "Abierta"}</small></div>
                  <ArrowUpRight size={16} className="epic-arrow" />
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      <div className="section-heading">
        <div><h2>Estado del entorno</h2><p>Servicios conectados a la plataforma.</p></div>
        <span className="updated-label">ACTUALIZADO AHORA</span>
      </div>
      <section className="metric-grid">
        <MetricCard icon={<Activity size={18} />} label="API" value={apiHealthy ? "Operativa" : apiHealthy === false ? "Sin conexión" : "Verificando"} detail="FastAPI · REST/JSON" tone={apiHealthy ? "green" : "neutral"} />
        <MetricCard icon={<ClipboardCheck size={18} />} label="Épicas asignadas" value={String(epics.length).padStart(2, "0")} detail="Espacios de trabajo QA" tone="blue" />
        <MetricCard icon={<KeyRound size={18} />} label="Directorios" value="DIFE + DICE" detail="Llaves interoperables" tone="gold" />
        <MetricCard icon={<CreditCard size={18} />} label="Liquidación" value="MOL simulado" detail="Entorno sin dinero real" tone="violet" />
      </section>

      <section className="welcome-banner">
        <div className="welcome-copy">
          <span className="banner-overline">LABORATORIO INTEROPERABLE</span>
          <h2>Prueba con confianza.<br /><span>Mejora con evidencia.</span></h2>
          <p>Orquesta tus escenarios de prueba en un entorno dedicado y controlado.</p>
          <button className="button button--banner" onClick={() => onOpenEpic(epics[0]?.key ?? "")} disabled={epics.length === 0} type="button">
            Explorar calidad <ArrowUpRight size={16} />
          </button>
        </div>
        <div className="banner-illustration" aria-hidden="true">
          <div className="banner-ring banner-ring--outer" />
          <div className="banner-ring banner-ring--middle" />
          <div className="banner-ring banner-ring--inner" />
          <div className="banner-center"><ArrowLeftRight size={26} /></div>
          <div className="banner-chip banner-chip--top"><ShieldCheck size={14} /> ATÓMICO</div>
          <div className="banner-chip banner-chip--bottom"><Activity size={14} /> TRAZABLE</div>
        </div>
      </section>
    </>
  );
}

function MetricCard({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return <article className="metric-card"><div className={`metric-icon metric-icon--${tone}`}>{icon}</div><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

/** Busca los campos *_xml (también anidados, como payment_return.pacs004_xml) de una respuesta. */
function isoMessages(data: unknown): Array<[string, string]> {
  if (typeof data !== "object" || data === null) return [];
  return Object.entries(data as Record<string, unknown>).flatMap(([key, value]): Array<[string, string]> => {
    if (key.endsWith("_xml") && typeof value === "string") {
      const name = key.replace(/_xml$/, "").replace(/^([a-z]+)(\d{3})$/, "$1.$2");
      return [[name, value]];
    }
    return typeof value === "object" ? isoMessages(value) : [];
  });
}

function ActionResultCard({ result }: { result: ActionResult }) {
  return (
    <section className="surface-card execution-card execution-card--passed" aria-live="polite">
      <div className="card-heading">
        <div><h2>Respuesta del backend</h2><p>Resultado de la última operación.</p></div>
        <span className="result-pill result-pill--passed">HTTP {result.status}</span>
      </div>
      <div className="request-summary">
        <span className="method-pill">{result.method}</span>
        <code title={result.path}>{result.path}</code>
        <strong>{result.status}</strong>
      </div>
      <details className="response-details">
        <summary>Ver JSON de respuesta</summary>
        <pre>{JSON.stringify(result.data, null, 2)}</pre>
      </details>
      {isoMessages(result.data).map(([name, xml]) => (
        <details className="response-details" key={name}>
          <summary>Mensaje {name}</summary>
          <pre>{xml}</pre>
        </details>
      ))}
    </section>
  );
}

function KeysPage({ role }: { role: User["role"] }) {
  const [action, setAction] = useState("lookup");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  const canAdminister = role === "admin" || role === "administrador";

  async function registerKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const formElement = event.currentTarget;
    const values = new FormData(formElement);
    const spbviId = String(values.get("spbvi_id"));
    const payload = {
      key_type: String(values.get("key_type")),
      key_value: String(values.get("key_value")),
      deposit_product_id: String(values.get("deposit_product_id")),
      ...(String(values.get("owner_email")).trim()
        ? { owner_email: String(values.get("owner_email")).trim() }
        : {}),
    };
    try {
      const response = await api.registerKey(spbviId, payload);
      setResult({
        method: "POST",
        path: `/difes/${encodeURIComponent(spbviId)}/keys`,
        status: response.status,
        data: response.data,
      });
      setNotice("La llave quedó registrada y confirmada por el flujo DIFE/DICE.");
      formElement.reset();
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function manageKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const values = new FormData(event.currentTarget);
    const spbviId = String(values.get("spbvi_id"));
    const keyType = String(values.get("key_type"));
    const keyValue = String(values.get("key_value"));
    const reason = String(values.get("reason") ?? "").trim();
    const pathBase = `/difes/${encodeURIComponent(spbviId)}/keys`;
    try {
      if (action === "lookup") {
        const response = await api.resolveKey(spbviId, keyType, keyValue);
        setResult({
          method: "GET",
          path: `${pathBase}/resolve?key_type=${encodeURIComponent(keyType)}&key_value=${encodeURIComponent(keyValue)}`,
          status: response.status,
          data: response.data,
        });
        setNotice("Se encontró la llave activa en el DIFE indicado.");
      } else {
        if (action === "delete" && !window.confirm(`¿Quieres eliminar la llave ${keyType}:${keyValue}? Esta acción libera la llave.`)) {
          return;
        }
        let endpointAction: "suspend" | "reactivate" | "owner" | "delete";
        let method: string;
        let endpoint: string;
        let payload: Record<string, string>;
        if (action.startsWith("suspend-")) {
          endpointAction = "suspend";
          method = "POST";
          endpoint = `${pathBase}/suspend`;
          payload = {
            key_type: keyType,
            key_value: keyValue,
            reason,
            suspension_type: action.endsWith("personal") ? "personal" : "administrative",
          };
        } else if (action.startsWith("reactivate-")) {
          endpointAction = "reactivate";
          method = "POST";
          endpoint = `${pathBase}/reactivate`;
          payload = {
            key_type: keyType,
            key_value: keyValue,
            reason,
            reactivation_type: action.endsWith("personal") ? "personal" : "administrative",
          };
        } else if (action === "owner") {
          endpointAction = "owner";
          method = "PATCH";
          endpoint = `${pathBase}/owner`;
          payload = {
            key_type: keyType,
            key_value: keyValue,
            reason,
            owner_email: String(values.get("owner_email")),
          };
        } else {
          endpointAction = "delete";
          method = "DELETE";
          endpoint = pathBase;
          payload = { key_type: keyType, key_value: keyValue, reason };
        }
        const response = await api.updateKeyLifecycle(spbviId, endpointAction, payload);
        setResult({ method, path: endpoint, status: response.status, data: response.data });
        setNotice(action === "delete" ? "La llave se eliminó de los directorios." : "La operación sobre la llave finalizó correctamente.");
      }
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="DIRECTORIOS DIFE / DICE"
        title="Gestión de llaves"
        description="Registra llaves en ambos directorios y ejecuta el ciclo de vida con sus permisos correspondientes."
      />
      <div className="role-policy">
        <KeyRound size={17} />
        <span><strong>La autorización la valida FastAPI.</strong> Las acciones personales requieren que el correo de la sesión sea el titular. No hay todavía un endpoint para listar llaves: para consultar, ingresa el SPBVI y la llave; la consulta resuelve solo llaves activas.</span>
      </div>
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
      <div className="operation-grid">
        <section className="surface-card operation-card">
          <div className="card-heading"><div><h2>Registrar llave</h2><p>Alta coordinada DIFE → DICE.</p></div><Plus size={19} className="muted-icon" /></div>
          {canAdminister ? (
            <form className="form-stack operation-form" onSubmit={(event) => void registerKey(event)}>
              <label className="field-label" htmlFor="register-spbvi">SPBVI de origen</label>
              <input className="text-input" id="register-spbvi" name="spbvi_id" placeholder="spbvi-a" required maxLength={100} />
              <KeyTypeFields idPrefix="register" typeName="key_type" valueName="key_value" typeLabel="Tipo de llave" valueLabel="Valor de la llave" />
              <label className="field-label" htmlFor="register-product">Producto de depósito</label>
              <input className="text-input" id="register-product" name="deposit_product_id" placeholder="cuenta-123" required maxLength={100} />
              <label className="field-label" htmlFor="register-owner">Correo del titular <span className="optional-label">OPCIONAL</span></label>
              <input className="text-input" id="register-owner" name="owner_email" type="email" />
              <button className="button button--primary" disabled={busy} type="submit"><Plus size={15} />{busy ? "Procesando…" : "Registrar llave"}</button>
            </form>
          ) : (
            <div className="empty-state operation-denied"><ShieldCheck size={20} /><strong>Acción administrativa</strong><p>El alta de llaves requiere rol admin o administrador.</p></div>
          )}
        </section>

        <section className="surface-card operation-card">
          <div className="card-heading"><div><h2>Consultar o administrar</h2><p>Indica la llave objetivo y la acción.</p></div><ArrowRightLeft size={19} className="muted-icon" /></div>
          <form className="form-stack operation-form" onSubmit={(event) => void manageKey(event)}>
            <label className="field-label" htmlFor="key-action">Operación</label>
            <select className="text-input select-input" id="key-action" value={action} onChange={(event) => setAction(event.target.value)}>
              <option value="lookup">Consultar activa (GET)</option>
              <option value="suspend-personal">Suspender personalmente</option>
              {canAdminister && <option value="suspend-administrative">Suspender administrativamente</option>}
              <option value="reactivate-personal">Reactivar personalmente</option>
              {canAdminister && <option value="reactivate-administrative">Reactivar administrativamente</option>}
              {canAdminister && <option value="owner">Asignar o cambiar titular</option>}
              {canAdminister && <option value="delete">Eliminar llave</option>}
            </select>
            <label className="field-label" htmlFor="manage-spbvi">SPBVI</label>
            <input className="text-input" id="manage-spbvi" name="spbvi_id" placeholder="spbvi-a" required maxLength={100} />
            <KeyTypeFields idPrefix="manage" typeName="key_type" valueName="key_value" typeLabel="Tipo de llave" valueLabel="Valor de la llave" />
            {action !== "lookup" && <>
              <label className="field-label" htmlFor="manage-reason">Motivo</label>
              <input className="text-input" id="manage-reason" name="reason" required maxLength={500} />
            </>}
            {action === "owner" && <>
              <label className="field-label" htmlFor="manage-owner">Correo del nuevo titular</label>
              <input className="text-input" id="manage-owner" name="owner_email" type="email" required />
            </>}
            <button className={`button ${action === "delete" ? "button--danger" : "button--primary"}`} disabled={busy} type="submit">
              {action === "lookup" ? <Search size={15} /> : action === "delete" ? <Trash2 size={15} /> : action.startsWith("suspend") ? <Ban size={15} /> : <RefreshCw size={15} />}
              {busy ? "Procesando…" : action === "lookup" ? "Consultar llave" : action === "delete" ? "Eliminar llave" : action.startsWith("suspend") ? "Suspender llave" : action.startsWith("reactivate") ? "Reactivar llave" : "Guardar titular"}
            </button>
          </form>
        </section>
      </div>
      {result && <ActionResultCard result={result} />}
    </>
  );
}

function PaymentsPage({ role }: { role: User["role"] }) {
  const [paymentType, setPaymentType] = useState<"intra" | "inter">("intra");
  const [operationId, setOperationId] = useState<string>(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  const canCreateAccount = role === "admin" || role === "administrador";

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const formElement = event.currentTarget;
    const values = new FormData(formElement);
    const balance = Number(values.get("balance_cents"));
    if (!Number.isSafeInteger(balance) || balance < 0) {
      setError("El saldo inicial debe ser un entero de centavos igual o mayor que cero.");
      setBusy(false);
      return;
    }
    const payload = {
      account_id: String(values.get("account_id")),
      spbvi_id: String(values.get("spbvi_id")),
      balance_cents: balance,
    };
    try {
      const response = await api.createAccount(payload);
      setResult({ method: "POST", path: "/accounts", status: response.status, data: response.data });
      setNotice("La cuenta de laboratorio quedó creada.");
      formElement.reset();
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const values = new FormData(event.currentTarget);
    const amount = Number(values.get("amount_cents"));
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      setError("El monto debe ser un entero positivo expresado en centavos.");
      setBusy(false);
      return;
    }
    try {
      const response = await api.createPayment(paymentType, {
        operation_id: String(values.get("operation_id")),
        source_account_id: String(values.get("source_account_id")),
        destination_key_type: String(values.get("destination_key_type")),
        destination_key_value: String(values.get("destination_key_value")),
        amount_cents: amount,
      });
      setResult({
        method: "POST",
        path: paymentType === "inter" ? "/payments/inter-spbvi" : "/payments",
        status: response.status,
        data: response.data,
      });
      const payment = response.data as Payment;
      setNotice(payment.replayed
        ? "Orden idempotente reconocida: se devolvió el pago existente, sin duplicar el abono."
        : "El pago se procesó correctamente.");
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="ORQUESTACIÓN Y LEDGER"
        title="Pagos de laboratorio"
        description="Crea cuentas de prueba y ejecuta pagos intra o inter-SPBVI con saldos enteros en centavos."
      />
      <div className="role-policy"><ShieldCheck size={17} /><span><strong>Entorno simulado: no mueve dinero real.</strong> Las operaciones son transaccionales e idempotentes. Inter-SPBVI usa DICE y el MOL simulado.</span></div>
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
      <div className="operation-grid">
        {canCreateAccount && (
          <section className="surface-card operation-card">
            <div className="card-heading"><div><h2>Cuenta de laboratorio</h2><p>Prepara una cuenta origen o destino.</p></div><WalletCards size={19} className="muted-icon" /></div>
            <form className="form-stack operation-form" onSubmit={(event) => void createAccount(event)}>
              <label className="field-label" htmlFor="account-id">Identificador de cuenta</label>
              <input className="text-input" id="account-id" name="account_id" required maxLength={100} />
              <label className="field-label" htmlFor="account-spbvi">SPBVI</label>
              <input className="text-input" id="account-spbvi" name="spbvi_id" placeholder="spbvi-a" required maxLength={100} />
              <label className="field-label" htmlFor="account-balance">Saldo inicial (centavos)</label>
              <input className="text-input" id="account-balance" name="balance_cents" type="number" min="0" step="1" inputMode="numeric" defaultValue="0" required />
              <p className="field-hint">Ingresa un entero. Ejemplo: 125000 equivale a COP 1.250,00.</p>
              <button className="button button--quiet" disabled={busy} type="submit"><Plus size={15} />Crear cuenta</button>
            </form>
          </section>
        )}
        <section className="surface-card operation-card payment-operation-card">
          <div className="card-heading"><div><h2>Enviar pago</h2><p>El servidor aplica el flujo correspondiente.</p></div><CircleDollarSign size={19} className="muted-icon" /></div>
          <form className="form-stack operation-form" onSubmit={(event) => void submitPayment(event)}>
            <label className="field-label" htmlFor="payment-type">Tipo de flujo</label>
            <select className="text-input select-input" id="payment-type" value={paymentType} onChange={(event) => setPaymentType(event.target.value as "intra" | "inter")}>
              <option value="intra">Intra-SPBVI · DIFE</option>
              <option value="inter">Inter-SPBVI · DICE + MOL</option>
            </select>
            <label className="field-label" htmlFor="payment-operation-id">Identificador idempotente</label>
            <div className="inline-input-action">
              <input className="text-input" id="payment-operation-id" name="operation_id" value={operationId} onChange={(event) => setOperationId(event.target.value)} required maxLength={100} />
              <button className="icon-button" type="button" aria-label="Generar otro identificador" title="Generar otro identificador" onClick={() => setOperationId(crypto.randomUUID())}><RefreshCw size={16} /></button>
            </div>
            <label className="field-label" htmlFor="payment-source">Cuenta de origen</label>
            <input className="text-input" id="payment-source" name="source_account_id" required maxLength={100} />
            <KeyTypeFields idPrefix="payment" typeName="destination_key_type" valueName="destination_key_value" typeLabel="Tipo de llave destino" valueLabel="Llave destino" />
            <label className="field-label" htmlFor="payment-amount">Monto en centavos</label>
            <input className="text-input" id="payment-amount" name="amount_cents" type="number" min="1" step="1" inputMode="numeric" required />
            <button className="button button--primary" disabled={busy} type="submit"><ArrowUpRight size={15} />{busy ? "Procesando…" : "Ejecutar pago"}</button>
          </form>
        </section>
        <PaymentOperations onError={setError} onNotice={setNotice} onResult={setResult} />
      </div>
      {result && <ActionResultCard result={result} />}
    </>
  );
}

function UsersPage({
  currentRole,
  currentUserId,
  error,
  notice,
  onCreate,
  onToggleActive,
  users,
}: {
  currentRole: User["role"];
  currentUserId: number;
  error: string;
  notice: string;
  onCreate: () => void;
  onToggleActive: (member: User) => void;
  users: User[];
}) {
  const active = users.filter((member) => member.is_active).length;
  // Mismas reglas que el backend: nadie se gestiona a sí mismo, el admin no se desactiva y el
  // administrador solo gestiona usuarios.
  const canManage = (member: User) =>
    member.id !== currentUserId && member.role !== "admin" && (currentRole === "admin" || member.role === "usuario");
  return (
    <>
      <PageHeading
        eyebrow="ACCESO Y SEGURIDAD"
        title="Usuarios y roles"
        description="Cuentas visibles del equipo y permisos asignados desde el backend."
        actions={<button className="button button--primary" onClick={onCreate} type="button"><Plus size={16} /> Crear cuenta</button>}
      />
      <div className="role-policy"><ShieldCheck size={17} /><span><strong>Privilegios verificados por el servidor.</strong> {currentRole === "admin" ? "Como admin puedes crear administradores y usuarios; el rol admin no se asigna por API." : "Como administrador puedes crear cuentas con rol usuario; solo el admin crea administradores."} La interfaz no crea cuentas ocultas ni define contraseñas predeterminadas.</span></div>
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
      <section className="surface-card users-card">
        <div className="card-heading"><div><h2>Equipo</h2><p>{users.length} cuentas registradas, {active} activas. Al desactivar una cuenta se cierran sus sesiones y ya no puede ingresar.</p></div><Users size={18} className="muted-icon" /></div>
        {users.length === 0 ? <div className="empty-state"><LoaderCircle className="spin" size={20} /><strong>Cargando cuentas…</strong></div> : (
          <div className="users-table-wrap">
            <table className="users-table"><thead><tr><th>PERSONA</th><th>CORREO</th><th>ROL</th><th>ESTADO</th><th>ACCIÓN</th></tr></thead><tbody>{users.map((member) => <tr key={member.id}><td><div className="table-person"><div className="user-avatar">{member.display_name.slice(0, 1).toUpperCase()}</div><strong>{member.display_name}</strong></div></td><td>{member.email}</td><td><span className={`role-tag role-tag--${member.role}`}>{member.role}</span></td><td>{member.is_active ? <span className="active-status"><span />Activo</span> : <span className="active-status active-status--off"><span />Inactivo</span>}</td><td>{canManage(member) ? <button className={`button button--small ${member.is_active ? "button--quiet" : "button--primary"}`} onClick={() => onToggleActive(member)} type="button">{member.is_active ? "Desactivar" : "Activar"}</button> : <span className="muted-text">—</span>}</td></tr>)}</tbody></table>
          </div>
        )}
      </section>
      <div className="security-footnote"><ShieldCheck size={15} /> El primer admin se crea únicamente mediante el bootstrap CLI seguro; no se crean cuentas de acceso ocultas.</div>
    </>
  );
}

export default App;
