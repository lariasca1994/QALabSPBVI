import { useState, type FormEvent } from "react";
import { ArrowLeftRight, KeyRound, LoaderCircle, UserPlus } from "lucide-react";
import { api } from "./api";
import { friendlyError, Modal } from "./ui";

const MIN_PASSWORD = 12;

function passwordProblem(password: string, confirmation: string, isNew = true): string {
  if (password.length < MIN_PASSWORD) return `La contraseña${isNew ? " nueva" : ""} debe tener al menos ${MIN_PASSWORD} caracteres.`;
  if (password !== confirmation) return isNew ? "Las contraseñas nuevas no coinciden." : "Las contraseñas no coinciden.";
  return "";
}

/** "¿Olvidaste tu contraseña?": pide el código por correo y define una contraseña nueva. */
export function PasswordReset({ onDone, onCancel }: { onDone: (message: string) => void; onCancel: () => void }) {
  const [step, setStep] = useState<"request" | "confirm">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (step === "confirm") {
      const problem = passwordProblem(password, confirmation);
      if (problem) {
        setError(problem);
        return;
      }
    }
    setBusy(true);
    try {
      if (step === "request") {
        const result = await api.requestPasswordReset(email);
        setNotice(result.message);
        setStep("confirm");
      } else {
        const result = await api.confirmPasswordReset(email, code, password);
        onDone(result.message);
      }
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="form-stack" onSubmit={submit}>
        <label className="field-label" htmlFor="reset-email">Correo electrónico</label>
        <input autoComplete="username" className="text-input" disabled={step === "confirm"} id="reset-email" onChange={(event) => setEmail(event.target.value)} placeholder="nombre@organizacion.com" required type="email" value={email} />
        {step === "confirm" && (
          <>
            <label className="field-label" htmlFor="reset-code">Código de recuperación</label>
            <input autoComplete="one-time-code" className="text-input code-input" id="reset-code" inputMode="numeric" maxLength={6} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="· · · · · ·" required value={code} />
            <p className="field-hint">El código vence en 10 minutos y sirve una sola vez.</p>
            <label className="field-label" htmlFor="reset-password">Contraseña nueva</label>
            <input autoComplete="new-password" className="text-input" id="reset-password" minLength={MIN_PASSWORD} maxLength={128} onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
            <label className="field-label" htmlFor="reset-confirmation">Repite la contraseña nueva</label>
            <input autoComplete="new-password" className="text-input" id="reset-confirmation" maxLength={128} onChange={(event) => setConfirmation(event.target.value)} required type="password" value={confirmation} />
          </>
        )}
        {notice && <div className="inline-notice" role="status">{notice}</div>}
        {error && <div className="alert alert--error" role="alert">{error}</div>}
        <button className="button button--primary button--wide" disabled={busy} type="submit">
          {busy ? <LoaderCircle className="spin" size={17} /> : <KeyRound size={16} />}
          {step === "request" ? "Enviar código" : "Restablecer contraseña"}
        </button>
      </form>
      <button className="text-button back-button" onClick={onCancel} type="button"><ArrowLeftRight size={15} /> Volver al inicio de sesión</button>
    </>
  );
}

/** Cambio de la propia contraseña con sesión iniciada. */
export function ChangePasswordModal({ onClose }: { onClose: (message?: string) => void }) {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = passwordProblem(password, confirmation);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.changePassword(current, password);
      onClose(result.closed_sessions > 0
        ? `Contraseña actualizada. Se cerraron ${result.closed_sessions} sesiones abiertas en otros dispositivos.`
        : "Contraseña actualizada.");
    } catch (changeError) {
      setError(friendlyError(changeError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal eyebrow="TU CUENTA" title="Cambiar contraseña" onClose={() => onClose()}>
      <form className="form-stack modal-form" onSubmit={submit}>
        <label className="field-label" htmlFor="current-password">Contraseña actual</label>
        <input autoComplete="current-password" className="text-input" id="current-password" maxLength={128} onChange={(event) => setCurrent(event.target.value)} required type="password" value={current} />
        <label className="field-label" htmlFor="new-password">Contraseña nueva</label>
        <input autoComplete="new-password" className="text-input" id="new-password" minLength={MIN_PASSWORD} maxLength={128} onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
        <label className="field-label" htmlFor="new-password-confirmation">Repite la contraseña nueva</label>
        <input autoComplete="new-password" className="text-input" id="new-password-confirmation" maxLength={128} onChange={(event) => setConfirmation(event.target.value)} required type="password" value={confirmation} />
        <p className="field-hint">Mínimo {MIN_PASSWORD} caracteres. Las sesiones en otros dispositivos se cierran.</p>
        {error && <div className="alert alert--error" role="alert">{error}</div>}
        <div className="modal-actions"><button className="button button--quiet" onClick={() => onClose()} type="button">Cancelar</button><button className="button button--primary" disabled={busy} type="submit"><KeyRound size={16} /> Guardar contraseña</button></div>
      </form>
    </Modal>
  );
}

/** Registro público de la demo: crea una cuenta con rol usuario, pendiente de épica. */
export function SignupForm({ onDone, onCancel }: { onDone: (message: string) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = passwordProblem(password, confirmation, false);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.register(email, name, password);
      onDone(result.message);
    } catch (signupError) {
      setError(friendlyError(signupError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="form-stack" onSubmit={submit}>
        <label className="field-label" htmlFor="signup-name">Nombre</label>
        <input autoComplete="name" className="text-input" id="signup-name" maxLength={120} onChange={(event) => setName(event.target.value)} required value={name} />
        <label className="field-label" htmlFor="signup-email">Correo electrónico</label>
        <input autoComplete="email" className="text-input" id="signup-email" onChange={(event) => setEmail(event.target.value)} placeholder="nombre@organizacion.com" required type="email" value={email} />
        <label className="field-label" htmlFor="signup-password">Contraseña</label>
        <input autoComplete="new-password" className="text-input" id="signup-password" minLength={MIN_PASSWORD} maxLength={128} onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
        <label className="field-label" htmlFor="signup-confirmation">Repite la contraseña</label>
        <input autoComplete="new-password" className="text-input" id="signup-confirmation" maxLength={128} onChange={(event) => setConfirmation(event.target.value)} required type="password" value={confirmation} />
        <p className="field-hint">La cuenta se crea con rol usuario. Para empezar a trabajar, un administrador debe asignarte a una épica.</p>
        {error && <div className="alert alert--error" role="alert">{error}</div>}
        <button className="button button--primary button--wide" disabled={busy} type="submit">
          {busy ? <LoaderCircle className="spin" size={17} /> : <UserPlus size={16} />} Crear cuenta
        </button>
      </form>
      <button className="text-button back-button" onClick={onCancel} type="button"><ArrowLeftRight size={15} /> Ya tengo cuenta</button>
    </>
  );
}
