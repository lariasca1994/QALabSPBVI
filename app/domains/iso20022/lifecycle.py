"""Mensajes ISO 20022 de laboratorio posteriores al pago.

- pacs.004: devolución (total o parcial) de un pago liquidado.
- camt.056: solicitud de cancelación de un pago, enviada por el SPBVI de origen.
- camt.029: resolución de la investigación que abre la camt.056.
- camt.054: notificación de créditos y débitos de una cuenta.
- pain.002: reporte de estado del pago para el cliente que lo ordenó.

SUPUESTO: los nombres de elementos y los códigos de motivo siguen la documentación pública de
ISO 20022 (listas de códigos externos), pero los XSD son perfiles propios limitados a los
campos implementados. No son los esquemas oficiales ni evidencia de conformidad con ISO 20022
o con el anexo 6 de la Circular DSP-465; se reemplazan al tener los requisitos de Banrep.
"""

from dataclasses import dataclass
from datetime import date, datetime

from lxml import etree

from app.domains.iso20022.messages import (
    _amount,
    _append,
    _date_time,
    _tag,
    _validate_cents,
    _validate_datetime,
    _validate_required_values,
    validate_message,
)

PACS_004_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pacs.004.001.09"
CAMT_056_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.056.001.08"
CAMT_029_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.029.001.09"
CAMT_054_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.054.001.08"
PAIN_002_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pain.002.001.10"
ORIGINAL_MESSAGE_NAME = "pacs.008.001.08"
CLEARING_SYSTEM = "SIMULATED_MOL"

# Códigos externos ISO 20022 (ExternalReturnReason1Code y ExternalCancellationReason1Code)
# que admite el laboratorio, con su significado para mostrarlo en la API.
RETURN_REASONS = {
    "AC01": "Número de cuenta incorrecto",
    "AC04": "Cuenta cerrada",
    "AC06": "Cuenta bloqueada",
    "AM05": "Pago duplicado",
    "FOCR": "Devolución tras solicitud de cancelación",
    "MD06": "Reembolso solicitado por el cliente",
    "MS02": "Motivo no especificado, generado por el cliente",
    "MS03": "Motivo no especificado, generado por la entidad",
}
CANCELLATION_REASONS = {
    "AC03": "Cuenta del beneficiario inválida",
    "AM09": "Monto errado",
    "CUST": "Solicitada por el cliente",
    "DUPL": "Pago duplicado",
    "FRAD": "Origen fraudulento",
    "TECH": "Problema técnico",
    "UPAY": "Pago indebido",
}
# Motivos de rechazo de una cancelación (camt.029, CxlStsRsnInf).
CANCELLATION_REJECTION_REASONS = {
    "AC04": "Cuenta cerrada",
    "AM04": "Fondos insuficientes para devolver",
    "ARDT": "El pago ya fue devuelto",
    "CUST": "Decisión del beneficiario",
    "LEGL": "Decisión legal",
    "NOAS": "Sin respuesta del beneficiario",
    "NOOR": "No se recibió el pago original",
}
# Motivos de rechazo para el cliente (pain.002, StsRsnInf).
PAYMENT_REJECTION_REASONS = {
    "AC03": "Llave o cuenta del beneficiario inválida",
    "AM02": "Monto no permitido (límite por operación)",
    "AM04": "Fondos insuficientes",
    "NARR": "Rechazo con descripción",
}


def _require_code(code: str, allowed: dict[str, str], label: str) -> None:
    if code not in allowed:
        raise ValueError(f"Código de {label} no soportado: {code}.")


def _document(namespace: str, root_name: str) -> tuple[etree._Element, etree._Element]:
    root = etree.Element(_tag(namespace, "Document"), nsmap={None: namespace})
    return root, etree.SubElement(root, _tag(namespace, root_name))


def _amount_element(parent: etree._Element, namespace: str, name: str, cents: int, currency: str) -> None:
    _append(parent, namespace, name, _amount(cents)).set("Ccy", currency)


def _original_group(parent: etree._Element, namespace: str, name: str, operation_id: str) -> None:
    group = etree.SubElement(parent, _tag(namespace, name))
    _append(group, namespace, "OrgnlMsgId", f"pacs008-{operation_id}")
    _append(group, namespace, "OrgnlMsgNmId", ORIGINAL_MESSAGE_NAME)


def _agent(parent: etree._Element, namespace: str, name: str, spbvi_id: str) -> None:
    agent = etree.SubElement(parent, _tag(namespace, name))
    institution = etree.SubElement(etree.SubElement(agent, _tag(namespace, "Agt")), _tag(namespace, "FinInstnId"))
    _append(etree.SubElement(institution, _tag(namespace, "Othr")), namespace, "Id", spbvi_id)


def _reason(parent: etree._Element, namespace: str, name: str, code: str) -> None:
    reason = etree.SubElement(etree.SubElement(parent, _tag(namespace, name)), _tag(namespace, "Rsn"))
    _append(reason, namespace, "Cd", code)


def _finish(root: etree._Element) -> str:
    message = etree.tostring(root, encoding="unicode")
    validate_message(message)
    return message


@dataclass(frozen=True)
class Pacs004Data:
    return_id: str
    operation_id: str
    original_amount_cents: int
    returned_amount_cents: int
    reason_code: str
    created_at: datetime
    currency: str = "COP"

    def __post_init__(self) -> None:
        _validate_required_values(self.return_id, self.operation_id)
        _validate_cents(self.original_amount_cents)
        _validate_cents(self.returned_amount_cents)
        if self.returned_amount_cents > self.original_amount_cents:
            raise ValueError("La devolución no puede superar el monto original.")
        _require_code(self.reason_code, RETURN_REASONS, "devolución")
        _validate_datetime(self.created_at)


def build_pacs004(data: Pacs004Data) -> str:
    namespace = PACS_004_NAMESPACE
    root, payment_return = _document(namespace, "PmtRtr")
    header = etree.SubElement(payment_return, _tag(namespace, "GrpHdr"))
    _append(header, namespace, "MsgId", f"pacs004-{data.return_id}")
    _append(header, namespace, "CreDtTm", _date_time(data.created_at))
    _append(header, namespace, "NbOfTxs", "1")
    settlement = etree.SubElement(header, _tag(namespace, "SttlmInf"))
    _append(settlement, namespace, "SttlmMtd", "CLRG")
    _append(etree.SubElement(settlement, _tag(namespace, "ClrSys")), namespace, "Prtry", CLEARING_SYSTEM)
    transaction = etree.SubElement(payment_return, _tag(namespace, "TxInf"))
    _append(transaction, namespace, "RtrId", data.return_id)
    _original_group(transaction, namespace, "OrgnlGrpInf", data.operation_id)
    _append(transaction, namespace, "OrgnlEndToEndId", data.operation_id)
    _amount_element(transaction, namespace, "OrgnlIntrBkSttlmAmt", data.original_amount_cents, data.currency)
    _amount_element(transaction, namespace, "RtrdIntrBkSttlmAmt", data.returned_amount_cents, data.currency)
    _reason(transaction, namespace, "RtrRsnInf", data.reason_code)
    return _finish(root)


@dataclass(frozen=True)
class Camt056Data:
    cancellation_id: str
    operation_id: str
    requester_spbvi_id: str
    responder_spbvi_id: str
    original_amount_cents: int
    reason_code: str
    created_at: datetime
    currency: str = "COP"

    def __post_init__(self) -> None:
        _validate_required_values(
            self.cancellation_id, self.operation_id, self.requester_spbvi_id, self.responder_spbvi_id
        )
        _validate_cents(self.original_amount_cents)
        _require_code(self.reason_code, CANCELLATION_REASONS, "cancelación")
        _validate_datetime(self.created_at)


def build_camt056(data: Camt056Data) -> str:
    namespace = CAMT_056_NAMESPACE
    root, request = _document(namespace, "FIToFIPmtCxlReq")
    assignment = etree.SubElement(request, _tag(namespace, "Assgnmt"))
    _append(assignment, namespace, "Id", f"camt056-{data.cancellation_id}")
    _agent(assignment, namespace, "Assgnr", data.requester_spbvi_id)
    _agent(assignment, namespace, "Assgne", data.responder_spbvi_id)
    _append(assignment, namespace, "CreDtTm", _date_time(data.created_at))
    transaction = etree.SubElement(etree.SubElement(request, _tag(namespace, "Undrlyg")), _tag(namespace, "TxInf"))
    _append(transaction, namespace, "CxlId", data.cancellation_id)
    _original_group(transaction, namespace, "OrgnlGrpInf", data.operation_id)
    _append(transaction, namespace, "OrgnlEndToEndId", data.operation_id)
    _amount_element(transaction, namespace, "OrgnlIntrBkSttlmAmt", data.original_amount_cents, data.currency)
    _reason(transaction, namespace, "CxlRsnInf", data.reason_code)
    return _finish(root)


# Estado de la investigación → (confirmación del grupo, estado de la transacción).
CAMT_029_STATUS = {
    "pending": ("PECR", "PDCR"),
    "accepted": ("CNCL", "ACCR"),
    "rejected": ("RJCR", "RJCR"),
}


@dataclass(frozen=True)
class Camt029Data:
    cancellation_id: str
    operation_id: str
    responder_spbvi_id: str
    requester_spbvi_id: str
    status: str
    created_at: datetime
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_required_values(
            self.cancellation_id, self.operation_id, self.requester_spbvi_id, self.responder_spbvi_id
        )
        if self.status not in CAMT_029_STATUS:
            raise ValueError("Estado de investigación no soportado por el perfil local.")
        if (self.status == "rejected") != (self.rejection_reason is not None):
            raise ValueError("Solo el rechazo lleva motivo, y es obligatorio.")
        if self.rejection_reason is not None:
            _require_code(self.rejection_reason, CANCELLATION_REJECTION_REASONS, "rechazo de cancelación")
        _validate_datetime(self.created_at)


def build_camt029(data: Camt029Data) -> str:
    namespace = CAMT_029_NAMESPACE
    confirmation, transaction_status = CAMT_029_STATUS[data.status]
    root, resolution = _document(namespace, "RsltnOfInvstgtn")
    assignment = etree.SubElement(resolution, _tag(namespace, "Assgnmt"))
    _append(assignment, namespace, "Id", f"camt029-{data.cancellation_id}")
    _agent(assignment, namespace, "Assgnr", data.responder_spbvi_id)
    _agent(assignment, namespace, "Assgne", data.requester_spbvi_id)
    _append(assignment, namespace, "CreDtTm", _date_time(data.created_at))
    _append(etree.SubElement(resolution, _tag(namespace, "Sts")), namespace, "Conf", confirmation)
    details = etree.SubElement(etree.SubElement(resolution, _tag(namespace, "CxlDtls")), _tag(namespace, "TxInfAndSts"))
    _append(details, namespace, "CxlStsId", data.cancellation_id)
    _original_group(details, namespace, "OrgnlGrpInf", data.operation_id)
    _append(details, namespace, "OrgnlEndToEndId", data.operation_id)
    _append(details, namespace, "TxCxlSts", transaction_status)
    if data.rejection_reason is not None:
        _reason(details, namespace, "CxlStsRsnInf", data.rejection_reason)
    return _finish(root)


@dataclass(frozen=True)
class NotificationEntry:
    entry_id: int
    amount_cents: int
    credit: bool
    booked_at: datetime
    entry_type: str
    operation_id: str | None


@dataclass(frozen=True)
class Camt054Data:
    notification_id: str
    account_id: str
    entries: tuple[NotificationEntry, ...]
    created_at: datetime
    currency: str = "COP"

    def __post_init__(self) -> None:
        _validate_required_values(self.notification_id, self.account_id)
        _validate_datetime(self.created_at)
        for entry in self.entries:
            _validate_cents(entry.amount_cents)
            _validate_datetime(entry.booked_at)


def build_camt054(data: Camt054Data) -> str:
    namespace = CAMT_054_NAMESPACE
    root, report = _document(namespace, "BkToCstmrDbtCdtNtfctn")
    header = etree.SubElement(report, _tag(namespace, "GrpHdr"))
    _append(header, namespace, "MsgId", f"camt054-{data.notification_id}")
    _append(header, namespace, "CreDtTm", _date_time(data.created_at))
    notification = etree.SubElement(report, _tag(namespace, "Ntfctn"))
    _append(notification, namespace, "Id", data.notification_id)
    _append(notification, namespace, "CreDtTm", _date_time(data.created_at))
    account = etree.SubElement(notification, _tag(namespace, "Acct"))
    account_id = etree.SubElement(etree.SubElement(account, _tag(namespace, "Id")), _tag(namespace, "Othr"))
    _append(account_id, namespace, "Id", data.account_id)
    _append(account, namespace, "Ccy", data.currency)
    for entry in data.entries:
        node = etree.SubElement(notification, _tag(namespace, "Ntry"))
        _append(node, namespace, "NtryRef", str(entry.entry_id))
        _amount_element(node, namespace, "Amt", entry.amount_cents, data.currency)
        _append(node, namespace, "CdtDbtInd", "CRDT" if entry.credit else "DBIT")
        _append(etree.SubElement(node, _tag(namespace, "Sts")), namespace, "Cd", "BOOK")
        _append(etree.SubElement(node, _tag(namespace, "BookgDt")), namespace, "DtTm", _date_time(entry.booked_at))
        value_date: date = entry.booked_at.date()
        _append(etree.SubElement(node, _tag(namespace, "ValDt")), namespace, "Dt", value_date.isoformat())
        code = etree.SubElement(etree.SubElement(node, _tag(namespace, "BkTxCd")), _tag(namespace, "Prtry"))
        _append(code, namespace, "Cd", entry.entry_type.upper())
        if entry.operation_id:
            refs = etree.SubElement(
                etree.SubElement(etree.SubElement(node, _tag(namespace, "NtryDtls")), _tag(namespace, "TxDtls")),
                _tag(namespace, "Refs"),
            )
            _append(refs, namespace, "EndToEndId", entry.operation_id)
    return _finish(root)


@dataclass(frozen=True)
class Pain002Data:
    operation_id: str
    transaction_status: str
    created_at: datetime
    amount_cents: int | None = None
    reason_code: str | None = None
    currency: str = "COP"

    def __post_init__(self) -> None:
        _validate_required_values(self.operation_id)
        if self.transaction_status not in {"ACSC", "PDNG", "RJCT"}:
            raise ValueError("Estado pain.002 no soportado por el perfil local.")
        if (self.transaction_status == "RJCT") != (self.reason_code is not None):
            raise ValueError("Solo el rechazo lleva motivo, y es obligatorio.")
        if self.reason_code is not None:
            _require_code(self.reason_code, PAYMENT_REJECTION_REASONS, "rechazo de pago")
        if self.amount_cents is not None:
            _validate_cents(self.amount_cents)
        _validate_datetime(self.created_at)


def pain002_status_for_payment(payment_status: str) -> str:
    """ACSC: liquidado en la cuenta del beneficiario; el pago intra o inter ya liquidó."""
    statuses = {"completed": "ACSC", "pending": "PDNG", "rejected": "RJCT"}
    try:
        return statuses[payment_status]
    except KeyError as error:
        raise ValueError(f"Estado de pago sin correspondencia pain.002: {payment_status}") from error


def build_pain002(data: Pain002Data) -> str:
    namespace = PAIN_002_NAMESPACE
    root, report = _document(namespace, "CstmrPmtStsRpt")
    header = etree.SubElement(report, _tag(namespace, "GrpHdr"))
    _append(header, namespace, "MsgId", f"pain002-{data.operation_id}")
    _append(header, namespace, "CreDtTm", _date_time(data.created_at))
    group = etree.SubElement(report, _tag(namespace, "OrgnlGrpInfAndSts"))
    # SUPUESTO: el tramo app → SPBVI no exige pain.001; el mensaje original es la orden del cliente.
    _append(group, namespace, "OrgnlMsgId", f"pain001-{data.operation_id}")
    _append(group, namespace, "OrgnlMsgNmId", "pain.001.001.09")
    payment_info = etree.SubElement(report, _tag(namespace, "OrgnlPmtInfAndSts"))
    _append(payment_info, namespace, "OrgnlPmtInfId", data.operation_id)
    transaction = etree.SubElement(payment_info, _tag(namespace, "TxInfAndSts"))
    _append(transaction, namespace, "OrgnlEndToEndId", data.operation_id)
    _append(transaction, namespace, "TxSts", data.transaction_status)
    if data.reason_code is not None:
        _reason(transaction, namespace, "StsRsnInf", data.reason_code)
    if data.amount_cents is not None:
        amount = etree.SubElement(etree.SubElement(transaction, _tag(namespace, "OrgnlTxRef")), _tag(namespace, "Amt"))
        _amount_element(amount, namespace, "InstdAmt", data.amount_cents, data.currency)
    return _finish(root)
