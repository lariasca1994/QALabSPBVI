import { useEffect, useState, type FormEvent } from "react";
import { Check, LoaderCircle, Mail, ShieldCheck, Smartphone } from "lucide-react";
import { api, type MfaMethods, type TotpSetup } from "./api";
import { friendlyError, Modal } from "./ui";

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

function CodeField({ id, value, onChange }: { id: string; value: string; onChange: (value: string) => void }) {
  return (
    <>
      <label className="field-label" htmlFor={id}>Código</label>
      <input
        autoComplete="one-time-code"
        className="text-input code-input"
        id={id}
        inputMode="numeric"
        maxLength={6}
        onChange={(event) => onChange(event.target.value.replace(/\D/g, "").slice(0, 6))}
        placeholder="· · · · · ·"
        required
        type="text"
        value={value}
      />
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
