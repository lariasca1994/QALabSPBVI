import { useCallback, useEffect, useState } from "react";
import { api, type Account, type KeyTypeInfo, type PaymentKey, type SpbviSummary } from "./api";

// Listas de SPBVI, cuentas y llaves confirmadas para que los formularios no dependan de
// escribir identificadores a mano. Si una lista no se puede cargar, el campo vuelve a ser
// de texto libre para no bloquear la operación.

const NEW_SPBVI = "__nuevo__";
const MANUAL_KEY = "__manual__";

/** Centavos enteros a texto COP (sin pasar por float). */
export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(cents);
  return `${sign}$ ${Math.trunc(absolute / 100).toLocaleString("es-CO")},${String(absolute % 100).padStart(2, "0")}`;
}

/** Carga una lista y la recarga a pedido; null mientras carga, "error" si falló. */
function useList<T>(load: () => Promise<T[]>): { items: T[] | null | "error"; reload: () => void } {
  const [items, setItems] = useState<T[] | null | "error">(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    load().then(
      (loaded) => { if (active) setItems(loaded); },
      () => { if (active) setItems("error"); },
    );
    return () => { active = false; };
  }, [load, version]);
  return { items, reload: useCallback(() => setVersion((value) => value + 1), []) };
}

export function useSpbvis() {
  return useList<SpbviSummary>(useCallback(() => api.spbvis(), []));
}

export function useAccounts() {
  return useList<Account>(useCallback(() => api.accounts(), []));
}

/**
 * SPBVI de un formulario: lista de los existentes y, si allowNew, la opción de crear uno.
 * Envía el valor elegido en el campo "spbvi_id".
 */
export function SpbviField({ idPrefix, label, spbvis, allowNew }: {
  idPrefix: string;
  label: string;
  spbvis: SpbviSummary[] | null | "error";
  allowNew: boolean;
}) {
  const [choice, setChoice] = useState("");
  const id = `${idPrefix}-spbvi`;
  const known = Array.isArray(spbvis) ? spbvis : [];
  const selected = choice || (known[0]?.spbvi_id ?? (allowNew ? NEW_SPBVI : ""));

  if (!Array.isArray(spbvis) || (known.length === 0 && !allowNew)) {
    return (
      <>
        <label className="field-label" htmlFor={id}>{label}</label>
        <input className="text-input" id={id} name="spbvi_id" placeholder="spbvi-a" required maxLength={100} />
      </>
    );
  }
  return (
    <>
      <label className="field-label" htmlFor={id}>{label}</label>
      <select className="text-input select-input" id={id} value={selected} onChange={(event) => setChoice(event.target.value)}>
        {known.map((item) => (
          <option key={item.spbvi_id} value={item.spbvi_id}>
            {item.spbvi_id} · {item.accounts} {item.accounts === 1 ? "cuenta" : "cuentas"}, {item.confirmed_keys} {item.confirmed_keys === 1 ? "llave" : "llaves"}
          </option>
        ))}
        {allowNew && <option value={NEW_SPBVI}>Nuevo SPBVI…</option>}
      </select>
      {selected === NEW_SPBVI ? (
        <>
          <label className="field-label" htmlFor={`${id}-new`}>Código del nuevo SPBVI</label>
          <input className="text-input" id={`${id}-new`} name="spbvi_id" placeholder="spbvi-c" required maxLength={100} />
          <p className="field-hint">Queda creado al guardar. Revisa el código: cada valor distinto es otro SPBVI.</p>
        </>
      ) : (
        <input name="spbvi_id" type="hidden" value={selected} />
      )}
    </>
  );
}

/**
 * Origen y destino de un pago: cuenta origen, SPBVI destino (fijo en intra, los demás en
 * inter), tipo de llave y una de sus llaves confirmadas, o una escrita a mano para probar
 * rechazos. Envía source_account_id, destination_key_type y destination_key_value.
 */
export function PaymentParties({ paymentType, accounts, spbvis, keyTypes }: {
  paymentType: "intra" | "inter";
  accounts: Account[] | null | "error";
  spbvis: SpbviSummary[] | null | "error";
  keyTypes: KeyTypeInfo[];
}) {
  const [sourceId, setSourceId] = useState("");
  const [destinationChoice, setDestinationChoice] = useState("");
  const [keyType, setKeyType] = useState("email");
  const [keys, setKeys] = useState<PaymentKey[] | null>(null);
  const [keyChoice, setKeyChoice] = useState(MANUAL_KEY);

  const accountList = Array.isArray(accounts) ? accounts : null;
  const sourceSpbvi = accountList?.find((account) => account.id === sourceId)?.spbvi_id ?? "";
  const known = Array.isArray(spbvis) ? spbvis.map((item) => item.spbvi_id) : [];
  const destinationOptions = paymentType === "intra"
    ? (sourceSpbvi ? [sourceSpbvi] : [])
    : known.filter((spbviId) => spbviId !== sourceSpbvi);
  const destinationSpbvi = destinationOptions.includes(destinationChoice) ? destinationChoice : (destinationOptions[0] ?? "");
  const currentType = keyTypes.find((item) => item.code === keyType) ?? keyTypes[0];

  useEffect(() => {
    if (!destinationSpbvi) {
      setKeys([]);
      setKeyChoice(MANUAL_KEY);
      return;
    }
    let active = true;
    setKeys(null);
    api.confirmedKeys(destinationSpbvi, keyType).then(
      (loaded) => {
        if (!active) return;
        setKeys(loaded);
        setKeyChoice(loaded[0]?.key_value ?? MANUAL_KEY);
      },
      () => {
        if (!active) return;
        setKeys([]);
        setKeyChoice(MANUAL_KEY);
      },
    );
    return () => { active = false; };
  }, [destinationSpbvi, keyType]);

  const groups = new Map<string, Account[]>();
  for (const account of accountList ?? []) {
    groups.set(account.spbvi_id, [...(groups.get(account.spbvi_id) ?? []), account]);
  }

  return (
    <>
      <label className="field-label" htmlFor="payment-source">Cuenta de origen</label>
      {accountList ? (
        <select className="text-input select-input" id="payment-source" name="source_account_id" required value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
          <option value="">{accountList.length ? "Selecciona la cuenta de origen" : "Todavía no hay cuentas"}</option>
          {[...groups].map(([spbviId, items]) => (
            <optgroup key={spbviId} label={spbviId}>
              {items.map((account) => (
                <option key={account.id} value={account.id}>{account.id} · {formatCents(account.balance_cents)}</option>
              ))}
            </optgroup>
          ))}
        </select>
      ) : (
        <input className="text-input" id="payment-source" name="source_account_id" required maxLength={100} disabled={accounts === null} placeholder={accounts === null ? "Cargando cuentas…" : ""} />
      )}

      <label className="field-label" htmlFor="payment-destination-spbvi">SPBVI destino</label>
      <select className="text-input select-input" id="payment-destination-spbvi" disabled={paymentType === "intra" || destinationOptions.length === 0} value={destinationSpbvi} onChange={(event) => setDestinationChoice(event.target.value)}>
        {destinationOptions.length === 0 && (
          <option value="">{paymentType === "intra" || !sourceSpbvi ? "Elige primero la cuenta de origen" : "No hay otros SPBVI"}</option>
        )}
        {destinationOptions.map((spbviId) => <option key={spbviId} value={spbviId}>{spbviId}</option>)}
      </select>
      <p className="field-hint">
        {paymentType === "intra"
          ? "Intra-SPBVI: el destino es el mismo SPBVI de la cuenta origen y la llave se resuelve en su DIFE."
          : "Inter-SPBVI: elige otro SPBVI; la llave se resuelve en el DICE y liquida el MOL."}
      </p>

      <label className="field-label" htmlFor="payment-key-type">Tipo de llave destino</label>
      <select className="text-input select-input" id="payment-key-type" name="destination_key_type" value={keyType} onChange={(event) => setKeyType(event.target.value)}>
        {keyTypes.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
      </select>

      <label className="field-label" htmlFor="payment-key-value">Llave destino</label>
      <select className="text-input select-input" id="payment-key-value" disabled={keys === null} value={keys === null ? "" : keyChoice} onChange={(event) => setKeyChoice(event.target.value)}>
        {keys === null && <option value="">Cargando llaves…</option>}
        {keys?.map((key) => <option key={key.id} value={key.key_value}>{key.key_value} · {key.deposit_product_id}</option>)}
        <option value={MANUAL_KEY}>Escribir otra llave…</option>
      </select>
      {keys === null ? null : keyChoice === MANUAL_KEY ? (
        <>
          <label className="field-label" htmlFor="payment-key-manual">Llave destino escrita</label>
          <input className="text-input" id="payment-key-manual" name="destination_key_value" placeholder={currentType?.example} required maxLength={255} />
          <p className="field-hint">
            {keys && keys.length === 0 && destinationSpbvi ? `${destinationSpbvi} no tiene llaves confirmadas de este tipo. ` : ""}
            Útil para probar rechazos, como una llave inexistente.
          </p>
        </>
      ) : (
        <input name="destination_key_value" type="hidden" value={keyChoice} />
      )}
    </>
  );
}
