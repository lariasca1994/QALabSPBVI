import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeftRight, Check, LoaderCircle, Mail, RefreshCw, ShieldCheck, Smartphone } from "lucide-react";
import { api, type MfaMethods, type TotpSetup } from "./api";
import { friendlyError, Modal } from "./ui";
import { CampoCodigo } from "./efectos/CampoCodigo";

/** QR (otpauth://) y clave para configurar la app autenticadora. */
export function TotpSetupBlock({ setup }: { setup: TotpSetup }) {
  const [image, setImage] = useState("");

  useEffect(() => {
    let active = true;
    import("qrcode")
      .then(({ default: qrcode }) => qrcode.toDataURL(setup.uri, { errorCorrectionLevel: "M", margin: 2, width: 220 }))
      .then((url) => { if (active) setImage(url); })
      .catch(() => setImage(""));
    return () => { active = false; };
  }, [setup.uri]);

  return (
    <div className="totp-setup">
      <p className="field-hint">
        Escanea el código con Google Authenticator, Microsoft Authenticator u otra app y escribe el código de 6 dígitos que muestra.
      </p>
      {image && <img alt="Código QR para la app autenticadora" className="totp-qr" height={220} src={image} width={220} />}
      <p className="field-hint">¿No puedes escanear? Escribe esta clave: <code className="totp-secret">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code></p>
    </div>
  );
}

function CodeField({ id, value, onChange, label = "Código" }: { id: string; value: string; onChange: (value: string) => void; label?: string }) {
  return (
    <>
      <label className="field-label" htmlFor={id}>{label}</label>
      <CampoCodigo id={id} onChange={onChange} value={value} />
    </>
  );
}

/** Seguridad: métodos de la verificación en dos pasos, agregar el que falta y elegir el preferido. */
export function SecurityModal({ onClose }: { onClose: (message?: string) => void }) {
  const [methods, setMethods] = useState<MfaMethods | null>(null);
  const [adding, setAdding] = useState<"totp" | "email" | null>(null);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mfaMethods().then(setMethods).catch((loadError) => setError(friendlyError(loadError)));
  }, []);

  async function start(method: "totp" | "email") {
    setError("");
    setNotice("");
    setCode("");
    setAdding(method);
    try {
      if (method === "totp") setSetup(await api.startTotp());
      else setNotice((await api.startEmailMethod()).message);
    } catch (startError) {
      setError(friendlyError(startError));
    }
  }

  async function activate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const updated = adding === "totp" ? await api.activateTotp(code) : await api.activateEmailMethod(code);
      setMethods(updated);
      setNotice(adding === "totp" ? "La app autenticadora quedó activa." : "El código por correo quedó activo.");
      setAdding(null);
      setSetup(null);
      setCode("");
    } catch (activateError) {
      setError(friendlyError(activateError));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  async function prefer(method: "email" | "totp") {
    setError("");
    try {
      setMethods(await api.setPreferredMethod(method));
      setNotice(method === "totp" ? "Ahora se pide la app autenticadora por defecto." : "Ahora se pide el código por correo por defecto.");
    } catch (preferError) {
      setError(friendlyError(preferError));
    }
  }

  return (
    <Modal eyebrow="TU CUENTA" title="Seguridad" onClose={() => onClose()}>
      <div className="form-stack modal-form">
        <p className="field-hint">La verificación en dos pasos es obligatoria. Tener los dos métodos te permite entrar aunque no tengas el teléfono a mano.</p>
        {!methods && !error && <p className="field-hint"><LoaderCircle className="spin" size={15} /> Cargando…</p>}
        {methods && adding === null && (
          <>
            <ul className="mfa-methods">
              <li><Smartphone size={16} /> <strong>App autenticadora</strong> <span>{methods.totp ? "Activa" : "Sin configurar"}{methods.totp && methods.preferred === "totp" ? " · por defecto" : ""}</span></li>
              <li><Mail size={16} /> <strong>Código por correo</strong> <span>{methods.email ? "Activo" : "Sin configurar"}{methods.email && methods.preferred === "email" ? " · por defecto" : ""}</span></li>
            </ul>
            <div className="modal-actions">
              {!methods.totp && <button className="button button--primary" onClick={() => void start("totp")} type="button"><Smartphone size={16} /> Agregar app autenticadora</button>}
              {!methods.email && <button className="button button--primary" onClick={() => void start("email")} type="button"><Mail size={16} /> Agregar código por correo</button>}
              {methods.totp && methods.email && methods.preferred !== "totp" && <button className="button button--quiet" onClick={() => void prefer("totp")} type="button">Pedir la app por defecto</button>}
              {methods.totp && methods.email && methods.preferred !== "email" && <button className="button button--quiet" onClick={() => void prefer("email")} type="button">Pedir el correo por defecto</button>}
            </div>
          </>
        )}
        {adding && (
          <form className="form-stack" onSubmit={activate}>
            {adding === "totp" && setup && <TotpSetupBlock setup={setup} />}
            <CodeField id="mfa-add-code" onChange={setCode} value={code} />
            {error && <div className="alert alert--error" role="alert">{error}</div>}
            <div className="modal-actions">
              <button className="button button--quiet" onClick={() => { setAdding(null); setSetup(null); setError(""); }} type="button">Cancelar</button>
              <button className="button button--primary" disabled={busy || code.length !== 6 || (adding === "totp" && !setup)} type="submit"><ShieldCheck size={16} /> Activar</button>
            </div>
          </form>
        )}
        {notice && <div className="inline-notice"><Check size={16} />{notice}</div>}
        {error && !adding && <div className="alert alert--error" role="alert">{error}</div>}
      </div>
    </Modal>
  );
}

const RECOVERY_RESEND_SECONDS = 60;

/**
 * "Perdí mi autenticador": envía un código al correo de la cuenta (aunque el método correo no
 * esté activo), lo verifica y obliga a configurar una app autenticadora nueva antes de entrar.
 */
export function LostAuthenticator({ onDone, onCancel }: { onDone: () => Promise<void>; onCancel: () => void }) {
  const [step, setStep] = useState<"sending" | "code" | "setup">("sending");
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);

  async function send() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      setNotice((await api.startMfaRecovery()).message);
      setWait(RECOVERY_RESEND_SECONDS);
    } catch (sendError) {
      setError(friendlyError(sendError));
    } finally {
      setBusy(false);
      setStep("code");
    }
  }

  // Una sola vez aunque React monte dos veces en desarrollo (StrictMode).
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void send();
  }, []);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setTimeout(() => setWait((current) => current - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [wait]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (step === "code") {
        setSetup(await api.verifyMfaRecovery(code));
        setNotice("");
        setStep("setup");
      } else {
        await api.activateMfaRecovery(code);
        await onDone();
      }
      setCode("");
    } catch (submitError) {
      setError(friendlyError(submitError));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (step === "sending") {
    return <p className="field-hint"><LoaderCircle className="spin" size={15} /> Enviando el código a tu correo…</p>;
  }
  return (
    <>
      <form className="form-stack" onSubmit={submit}>
        {step === "setup" && setup && <TotpSetupBlock setup={setup} />}
        <CodeField
          id="recovery-code"
          label={step === "code" ? "Código de recuperación" : "Código de la app nueva"}
          onChange={setCode}
          value={code}
        />
        <p className="field-hint">
          {step === "code"
            ? "El código llegó al correo de tu cuenta, vence en 15 minutos y sirve una sola vez."
            : "La app anterior deja de funcionar cuando actives la nueva."}
        </p>
        {notice && <div className="inline-notice" role="status"><Check size={16} />{notice}</div>}
        {error && <div className="alert alert--error" role="alert">{error}</div>}
        <button className="button button--primary button--wide" disabled={busy || code.length !== 6} type="submit">
          {busy ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={16} />}
          {step === "code" ? "Verificar código" : "Activar app nueva e ingresar"}
        </button>
      </form>
      {step === "code" && (
        <div className="resend-row">
          <span>¿No te llegó el código?</span>
          <button className="text-button" disabled={wait > 0 || busy} onClick={() => void send()} type="button">
            <RefreshCw size={15} /> {wait > 0 ? `Reenviar en ${Math.floor(wait / 60)}:${String(wait % 60).padStart(2, "0")}` : "Reenviar código"}
          </button>
        </div>
      )}
      <button className="text-button back-button" onClick={onCancel} type="button"><ArrowLeftRight size={15} /> Volver al inicio de sesión</button>
    </>
  );
}
