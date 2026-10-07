import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Ban, Camera, Check, ClipboardPaste, Copy, ImageUp, LoaderCircle, QrCode, ScanLine, Send } from "lucide-react";
import { api, type KeyTypeInfo, type PaymentKey, type QrCharge, type QrCode as QrCodeData, type QrPreview, type SpbviSummary } from "./api";
import { formatCents, useAccounts } from "./LabDirectory";
import { friendlyError } from "./ui";

// Pagos con QR (perfil EMVCo de laboratorio): "Cobrar" genera el QR y "Pagar" lo lee con la
// cámara, desde una imagen o pegando su contenido. El backend valida CRC y firma.

export interface QrResult {
  method: string;
  path: string;
  status: number;
  data: unknown;
}

type ChargeKind = "dynamic" | "static" | "static_hybrid";
type ReadMode = "camera" | "image" | "text";

const STATUS_LABELS: Record<QrCharge["status"], string> = {
  pending: "Pendiente de pago",
  reserved: "Pago en proceso",
  paid: "Pagado",
  expired: "Vencido",
  cancelled: "Anulado",
};
const QR_TYPE_LABELS: Record<QrPreview["qr_type"], string> = {
  static: "QR estático · eliges el monto",
  static_hybrid: "QR estático con monto fijo",
  dynamic: "Cobro dinámico de un solo uso",
};

function parseCents(value: FormDataEntryValue | null): number | null {
  const amount = Number(value);
  return Number.isSafeInteger(amount) && amount > 0 ? amount : null;
}

function useQrImage(payload: string | null): string {
  const [image, setImage] = useState("");
  useEffect(() => {
    if (!payload) {
      setImage("");
      return;
    }
    let active = true;
    // Corrección de errores media: el QR sigue leyéndose con brillo o reflejos de pantalla.
    // Las librerías de QR se cargan solo al usar esta pantalla (la app inicia más liviana).
    import("qrcode").then(({ default: qrcode }) => qrcode.toDataURL(payload, { errorCorrectionLevel: "M", margin: 2, width: 280 })).then(
      (url) => { if (active) setImage(url); },
      () => { if (active) setImage(""); },
    );
    return () => { active = false; };
  }, [payload]);
  return image;
}

type QrReader = (data: ImageData) => string | null;

/** Lector de QR (jsQR) cargado bajo demanda: devuelve el contenido o null. */
async function loadQrReader(): Promise<QrReader> {
  const { default: jsQR } = await import("jsqr");
  return (data) => jsQR(data.data, data.width, data.height, { inversionAttempts: "attemptBoth" })?.data ?? null;
}

function ChargeCard({ onCancelled, charge }: { onCancelled: (charge: QrCharge) => void; charge: QrCharge }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = charge.status === "pending" || charge.status === "reserved";
  async function cancel() {
    setBusy(true);
    setError("");
    try {
      onCancelled((await api.qrCancelCharge(charge.charge_id)).data);
    } catch (cancelError) {
      setError(friendlyError(cancelError));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="qr-charge-status">
      <span className={`qr-status qr-status--${charge.status}`} role="status">{STATUS_LABELS[charge.status]}</span>
      {open && <span className="qr-countdown">Vence en {Math.floor(charge.seconds_left / 60)}:{String(charge.seconds_left % 60).padStart(2, "0")}</span>}
      {charge.status === "paid" && <span className="field-hint">Pagado desde {charge.payer_account_id}.</span>}
      {charge.status === "pending" && (
        <button className="button button--quiet" disabled={busy} onClick={() => void cancel()} type="button"><Ban size={15} /> Anular cobro</button>
      )}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
    </div>
  );
}

export function QrCollect({ keyTypes, spbvis, onResult }: {
  keyTypes: KeyTypeInfo[];
  spbvis: SpbviSummary[] | null | "error";
  onResult: (result: QrResult) => void;
}) {
  const [kind, setKind] = useState<ChargeKind>("dynamic");
  const [spbviId, setSpbviId] = useState("");
  const [keyType, setKeyType] = useState("alias");
  const [keys, setKeys] = useState<PaymentKey[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [code, setCode] = useState<QrCodeData | null>(null);
  const [charge, setCharge] = useState<QrCharge | null>(null);
  const [copied, setCopied] = useState(false);
  const known = Array.isArray(spbvis) ? spbvis.map((item) => item.spbvi_id) : [];
  const selectedSpbvi = known.includes(spbviId) ? spbviId : (known[0] ?? "");
  const payload = charge?.payload ?? code?.payload ?? null;
  const image = useQrImage(payload);

  useEffect(() => {
    if (!selectedSpbvi) {
      setKeys([]);
      return;
    }
    let active = true;
    setKeys(null);
    api.confirmedKeys(selectedSpbvi, keyType).then(
      (loaded) => { if (active) setKeys(loaded); },
      () => { if (active) setKeys([]); },
    );
    return () => { active = false; };
  }, [selectedSpbvi, keyType]);

  // El cobro abierto se consulta cada 3 s SOLO con la pantalla visible y hasta su vencimiento:
  // con la app oculta o el cobro cerrado no hay solicitudes, así la API y las bases (capas
  // gratuitas) pueden dormir. Al volver a la pantalla se consulta una vez y se retoma.
  useEffect(() => {
    if (!charge || !["pending", "reserved"].includes(charge.status)) return;
    const chargeId = charge.charge_id;
    const closesAt = Date.now() + (charge.seconds_left + 5) * 1000;
    let timer = 0;
    const refresh = () => { api.qrCharge(chargeId).then(setCharge, () => undefined); };
    const start = () => {
      window.clearInterval(timer);
      if (document.visibilityState !== "visible" || Date.now() > closesAt) return;
      timer = window.setInterval(() => {
        if (Date.now() > closesAt) window.clearInterval(timer);
        refresh();
      }, 3000);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
      start();
    };
    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [charge?.charge_id, charge?.status]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setCopied(false);
    const values = new FormData(event.currentTarget);
    const receiver = {
      spbvi_id: selectedSpbvi,
      key_type: keyType,
      key_value: String(values.get("key_value") ?? ""),
      merchant_name: String(values.get("merchant_name") ?? "").trim(),
      merchant_city: String(values.get("merchant_city") ?? "").trim() || "Bogota",
    };
    const amount = parseCents(values.get("amount_cents"));
    if (kind !== "static" && amount === null) {
      setError("El monto debe ser un entero positivo en centavos.");
      return;
    }
    setBusy(true);
    try {
      if (kind === "dynamic") {
        const reference = String(values.get("reference") ?? "").trim();
        const response = await api.qrCreateCharge({
          ...receiver,
          amount_cents: amount ?? 0,
          expires_in_seconds: Number(values.get("expires_in_seconds")),
          ...(reference ? { reference } : {}),
        });
        setCode(null);
        setCharge(response.data);
        onResult({ method: "POST", path: "/qr/charges", status: response.status, data: response.data });
      } else {
        const response = await api.qrStatic({ ...receiver, ...(kind === "static_hybrid" && amount ? { amount_cents: amount } : {}) });
        setCharge(null);
        setCode(response.data);
        onResult({ method: "POST", path: "/qr/static", status: response.status, data: response.data });
      }
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function copyPayload() {
    if (!payload) return;
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const currentType = keyTypes.find((item) => item.code === keyType);
  return (
    <section className="surface-card operation-card">
      <div className="card-heading"><div><h2>Cobrar con QR</h2><p>Genera el QR que la otra persona escanea.</p></div><QrCode size={19} className="muted-icon" /></div>
      <form className="form-stack operation-form" onSubmit={(event) => void submit(event)}>
        <label className="field-label" htmlFor="qr-kind">Tipo de QR</label>
        <select className="text-input select-input" id="qr-kind" value={kind} onChange={(event) => setKind(event.target.value as ChargeKind)}>
          <option value="dynamic">Cobro dinámico · un solo uso, con vencimiento</option>
          <option value="static">Estático · quien paga elige el monto</option>
          <option value="static_hybrid">Estático con monto fijo</option>
        </select>
        <label className="field-label" htmlFor="qr-spbvi">SPBVI de la llave</label>
        <select className="text-input select-input" id="qr-spbvi" value={selectedSpbvi} onChange={(event) => setSpbviId(event.target.value)}>
          {known.length === 0 && <option value="">{spbvis === null ? "Cargando…" : "No hay SPBVI con llaves"}</option>}
          {known.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <label className="field-label" htmlFor="qr-key-type">Tipo de llave</label>
        <select className="text-input select-input" id="qr-key-type" value={keyType} onChange={(event) => setKeyType(event.target.value)}>
          {keyTypes.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
        </select>
        <label className="field-label" htmlFor="qr-key">Llave que recibe el pago</label>
        <select className="text-input select-input" id="qr-key" name="key_value" required disabled={!keys?.length}>
          {keys === null && <option value="">Cargando llaves…</option>}
          {keys?.length === 0 && <option value="">Sin llaves confirmadas de {currentType?.label.toLowerCase() ?? "este tipo"}</option>}
          {keys?.map((key) => <option key={key.id} value={key.key_value}>{key.key_value} · {key.deposit_product_id}</option>)}
        </select>
        <label className="field-label" htmlFor="qr-merchant">Nombre que verá quien paga</label>
        <input className="text-input" id="qr-merchant" name="merchant_name" maxLength={25} required placeholder="Tienda de la esquina" />
        <label className="field-label" htmlFor="qr-city">Ciudad</label>
        <input className="text-input" id="qr-city" name="merchant_city" maxLength={15} placeholder="Bogota" />
        {kind !== "static" && <>
          <label className="field-label" htmlFor="qr-amount">Monto en centavos</label>
          <input className="text-input" id="qr-amount" name="amount_cents" type="number" min="1" step="1" inputMode="numeric" required />
        </>}
        {kind === "dynamic" && <>
          <label className="field-label" htmlFor="qr-reference">Referencia <span className="optional-label">OPCIONAL</span></label>
          <input className="text-input" id="qr-reference" name="reference" maxLength={25} placeholder="Factura 123" />
          <label className="field-label" htmlFor="qr-expiry">Vence en</label>
          <select className="text-input select-input" id="qr-expiry" name="expires_in_seconds" defaultValue="600">
            <option value="300">5 minutos</option>
            <option value="600">10 minutos</option>
            <option value="1800">30 minutos</option>
            <option value="3600">1 hora</option>
          </select>
        </>}
        {error && <div className="alert alert--error" role="alert">{error}</div>}
        <button className="button button--primary" disabled={busy || !keys?.length} type="submit">
          {busy ? <LoaderCircle className="spin" size={15} /> : <QrCode size={15} />} Generar QR
        </button>
      </form>
      {payload && (
        <div className="qr-output">
          {image ? <img alt="Código QR para pagar" className="qr-image" src={image} /> : <LoaderCircle className="spin" size={22} />}
          <strong>{charge ? `${formatCents(charge.amount_cents)} · ${charge.merchant_name}` : code?.amount_cents ? `${formatCents(code.amount_cents)} · ${code.merchant_name}` : code?.merchant_name}</strong>
          {charge && <ChargeCard charge={charge} onCancelled={setCharge} />}
          <details className="qr-payload">
            <summary>Contenido EMVCo del QR</summary>
            <code id="qr-payload-text">{payload}</code>
          </details>
          <button className="button button--quiet" onClick={() => void copyPayload()} type="button">{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "Copiado" : "Copiar contenido"}</button>
        </div>
      )}
    </section>
  );
}

function CameraReader({ onRead }: { onRead: (payload: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Este navegador no permite usar la cámara aquí. Sube una imagen del QR o pega su contenido.");
      return;
    }
    let stream: MediaStream | null = null;
    let frame = 0;
    let stopped = false;
    let readQr: QrReader | null = null;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const scan = () => {
      const element = video.current;
      if (stopped || !element || !context) return;
      if (readQr && element.readyState >= element.HAVE_ENOUGH_DATA && element.videoWidth) {
        canvas.width = element.videoWidth;
        canvas.height = element.videoHeight;
        context.drawImage(element, 0, 0, canvas.width, canvas.height);
        const found = readQr(context.getImageData(0, 0, canvas.width, canvas.height));
        if (found) {
          onRead(found);
          return;
        }
      }
      frame = requestAnimationFrame(scan);
    };
    void loadQrReader().then((reader) => { readQr = reader; });
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }).then(
      (media) => {
        if (stopped) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = media;
        if (video.current) {
          video.current.srcObject = media;
          void video.current.play();
          frame = requestAnimationFrame(scan);
        }
      },
      () => setError("No se pudo abrir la cámara (permiso denegado o sin cámara). Sube una imagen del QR o pega su contenido."),
    );
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [onRead]);
  if (error) return <div className="alert alert--error" role="alert">{error}</div>;
  return (
    <div className="qr-camera">
      <video aria-label="Vista de la cámara" muted playsInline ref={video} />
      <span className="field-hint"><ScanLine size={14} /> Apunta la cámara al QR; se lee solo.</span>
    </div>
  );
}

export function QrPay({ onResult }: { onResult: (result: QrResult) => void }) {
  const [mode, setMode] = useState<ReadMode>("camera");
  const [cameraOn, setCameraOn] = useState(false);
  const [preview, setPreview] = useState<QrPreview | null>(null);
  const [payload, setPayload] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [operationId, setOperationId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const { items: accounts, reload: reloadAccounts } = useAccounts();

  const decode = useCallback(async (content: string) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const response = await api.qrDecode(content.trim());
      setPayload(content.trim());
      setPreview(response.data);
      setOperationId(crypto.randomUUID());
      onResult({ method: "POST", path: "/qr/decode", status: response.status, data: response.data });
    } catch (decodeError) {
      setPreview(null);
      setError(friendlyError(decodeError));
    } finally {
      setBusy(false);
    }
  }, [onResult]);

  const onCameraRead = useCallback((content: string) => {
    setCameraOn(false);
    void decode(content);
  }, [decode]);

  async function readImage(file: File | undefined) {
    if (!file) return;
    setError("");
    try {
      const bitmap = await createImageBitmap(file);
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("canvas");
      context.drawImage(bitmap, 0, 0);
      const readQr = await loadQrReader();
      const found = readQr(context.getImageData(0, 0, canvas.width, canvas.height));
      if (!found) {
        setError("No se encontró un QR en la imagen. Prueba con una foto más nítida.");
        return;
      }
      await decode(found);
    } catch {
      setError("No se pudo leer la imagen.");
    }
  }

  async function pay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview) return;
    setError("");
    setNotice("");
    const values = new FormData(event.currentTarget);
    const amount = preview.amount_editable ? parseCents(values.get("amount_cents")) : null;
    if (preview.amount_editable && amount === null) {
      setError("El monto debe ser un entero positivo en centavos.");
      return;
    }
    setBusy(true);
    try {
      const response = await api.qrPay({
        payload,
        source_account_id: sourceId,
        ...(amount !== null ? { amount_cents: amount } : {}),
        ...(preview.qr_type !== "dynamic" ? { operation_id: operationId } : {}),
      });
      onResult({ method: "POST", path: "/qr/pay", status: response.status, data: response.data });
      const result = response.data;
      setNotice(result.payment.replayed
        ? "Pago ya registrado: se devolvió el existente, sin cobrar dos veces."
        : `Pago con QR aprobado (${result.flow === "intra" ? "intra-SPBVI" : "inter-SPBVI"}) por ${formatCents(result.payment.amount_cents)}.`);
      reloadAccounts();
      if (preview.qr_type === "dynamic") setPreview({ ...preview, charge_status: result.charge_status });
    } catch (payError) {
      setError(friendlyError(payError));
    } finally {
      setBusy(false);
    }
  }

  const accountList = Array.isArray(accounts) ? accounts : [];
  const closed = preview?.qr_type === "dynamic" && preview.charge_status !== "pending";
  return (
    <section className="surface-card operation-card">
      <div className="card-heading"><div><h2>Pagar con QR</h2><p>Escanea, sube una imagen o pega el contenido.</p></div><ScanLine size={19} className="muted-icon" /></div>
      <div className="segmented" role="tablist" aria-label="Cómo leer el QR">
        <button aria-selected={mode === "camera"} className={mode === "camera" ? "segmented--active" : ""} onClick={() => { setMode("camera"); setCameraOn(false); }} role="tab" type="button"><Camera size={15} /> Cámara</button>
        <button aria-selected={mode === "image"} className={mode === "image" ? "segmented--active" : ""} onClick={() => { setMode("image"); setCameraOn(false); }} role="tab" type="button"><ImageUp size={15} /> Imagen</button>
        <button aria-selected={mode === "text"} className={mode === "text" ? "segmented--active" : ""} onClick={() => { setMode("text"); setCameraOn(false); }} role="tab" type="button"><ClipboardPaste size={15} /> Texto</button>
      </div>
      {mode === "camera" && !preview && (cameraOn
        ? <CameraReader onRead={onCameraRead} />
        : <button className="button button--quiet qr-camera-start" onClick={() => setCameraOn(true)} type="button"><Camera size={15} /> Activar cámara</button>)}
      {mode === "image" && (
        <div className="form-stack">
          <label className="field-label" htmlFor="qr-image-file">Imagen del QR</label>
          <input accept="image/*" className="text-input" id="qr-image-file" onChange={(event) => void readImage(event.target.files?.[0])} type="file" />
        </div>
      )}
      {mode === "text" && (
        <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void decode(String(new FormData(event.currentTarget).get("payload") ?? "")); }}>
          <label className="field-label" htmlFor="qr-text">Contenido del QR</label>
          <textarea className="text-input" id="qr-text" name="payload" required rows={3} spellCheck={false} />
          <button className="button button--quiet" disabled={busy} type="submit"><ScanLine size={15} /> Leer QR</button>
        </form>
      )}
      {notice && <div className="alert alert--success" role="status"><Check size={16} />{notice}</div>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
      {preview && (
        <form className="form-stack operation-form qr-preview" onSubmit={(event) => void pay(event)}>
          <div className="qr-preview-summary">
            <span className="qr-kind-label">{QR_TYPE_LABELS[preview.qr_type]}</span>
            <strong>{preview.merchant_name}</strong>
            <span>{preview.merchant_city} · llave {preview.key_value}</span>
            {preview.reference && <span>Referencia: {preview.reference}</span>}
            {preview.amount_cents !== null && <strong className="qr-amount">{formatCents(preview.amount_cents)}</strong>}
            {preview.charge_status && <span className={`qr-status qr-status--${preview.charge_status}`}>{STATUS_LABELS[preview.charge_status as QrCharge["status"]] ?? preview.charge_status}</span>}
          </div>
          <label className="field-label" htmlFor="qr-source">Cuenta de origen</label>
          <select className="text-input select-input" id="qr-source" required value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
            <option value="">Selecciona la cuenta de origen</option>
            {accountList.map((account) => <option key={account.id} value={account.id}>{account.id} · {account.spbvi_id} · {formatCents(account.balance_cents)}</option>)}
          </select>
          {preview.amount_editable && <>
            <label className="field-label" htmlFor="qr-pay-amount">Monto en centavos</label>
            <input className="text-input" id="qr-pay-amount" name="amount_cents" type="number" min="1" step="1" inputMode="numeric" required />
          </>}
          <div className="modal-actions">
            <button className="button button--quiet" onClick={() => { setPreview(null); setNotice(""); setError(""); }} type="button">Leer otro QR</button>
            <button className="button button--primary" disabled={busy || closed} type="submit">{busy ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />} Pagar</button>
          </div>
        </form>
      )}
    </section>
  );
}
