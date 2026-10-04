from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from lxml import etree

PACS_008_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
PACS_002_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10"
SCHEMAS_DIRECTORY = Path(__file__).with_name("schemas")
SCHEMAS = {
    PACS_008_NAMESPACE: SCHEMAS_DIRECTORY / "pacs.008.001.08-lab.xsd",
    PACS_002_NAMESPACE: SCHEMAS_DIRECTORY / "pacs.002.001.10-lab.xsd",
}


class Iso20022ValidationError(ValueError):
    """El mensaje no corresponde a los perfiles XSD locales del laboratorio."""


@dataclass(frozen=True)
class Pacs008Data:
    message_id: str
    operation_id: str
    source_spbvi_id: str
    destination_spbvi_id: str
    source_account_id: str
    destination_account_id: str
    amount_cents: int
    created_at: datetime
    currency: str = "COP"

    def __post_init__(self) -> None:
        _validate_required_values(
            self.message_id,
            self.operation_id,
            self.source_spbvi_id,
            self.destination_spbvi_id,
            self.source_account_id,
            self.destination_account_id,
            self.currency,
        )
        _validate_cents(self.amount_cents)
        _validate_datetime(self.created_at)
        if re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("La moneda debe ser un codigo de tres letras mayusculas.")


@dataclass(frozen=True)
class Pacs002Data:
    message_id: str
    original_message_id: str
    original_message_name: str
    group_status: str
    created_at: datetime
    status_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_required_values(
            self.message_id,
            self.original_message_id,
            self.original_message_name,
        )
        if self.group_status not in {"ACCP", "RJCT", "PDNG"}:
            raise ValueError("Estado pacs.002 no soportado por el perfil local.")
        _validate_datetime(self.created_at)


def pacs002_group_status_for_payment(payment_status: str) -> str:
    statuses = {
        "completed": "ACCP",
        "pending": "PDNG",
        "rejected": "RJCT",
    }
    try:
        return statuses[payment_status]
    except KeyError as error:
        raise ValueError(
            f"Estado de pago sin correspondencia pacs.002: {payment_status}"
        ) from error


def _validate_required_values(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("Los identificadores del mensaje no pueden quedar vacios.")


def _validate_cents(amount_cents: int) -> None:
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("El monto debe ser un entero positivo de centavos.")


def _validate_datetime(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("La fecha del mensaje debe incluir zona horaria.")


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _append(
    parent: etree._Element,
    namespace: str,
    name: str,
    value: str,
) -> etree._Element:
    element = etree.SubElement(parent, _tag(namespace, name))
    element.text = value
    return element


def _date_time(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _amount(amount_cents: int) -> str:
    return f"{amount_cents // 100}.{amount_cents % 100:02d}"


def build_pacs008(data: Pacs008Data) -> str:
    namespace = PACS_008_NAMESPACE
    root = etree.Element(_tag(namespace, "Document"), nsmap={None: namespace})
    transfer = etree.SubElement(root, _tag(namespace, "FIToFICstmrCdtTrf"))
    header = etree.SubElement(transfer, _tag(namespace, "GrpHdr"))
    _append(header, namespace, "MsgId", data.message_id)
    _append(header, namespace, "CreDtTm", _date_time(data.created_at))
    _append(header, namespace, "NbOfTxs", "1")
    total_amount = _append(header, namespace, "TtlIntrBkSttlmAmt", _amount(data.amount_cents))
    total_amount.set("Ccy", data.currency)
    settlement = etree.SubElement(header, _tag(namespace, "SttlmInf"))
    _append(settlement, namespace, "SttlmMtd", "CLRG")
    clearing_system = etree.SubElement(settlement, _tag(namespace, "ClrSys"))
    _append(clearing_system, namespace, "Prtry", "SIMULATED_MOL")

    transaction = etree.SubElement(transfer, _tag(namespace, "CdtTrfTxInf"))
    payment_id = etree.SubElement(transaction, _tag(namespace, "PmtId"))
    _append(payment_id, namespace, "InstrId", data.operation_id)
    _append(payment_id, namespace, "EndToEndId", data.operation_id)
    _append(payment_id, namespace, "TxId", data.message_id)
    transaction_amount = _append(
        transaction,
        namespace,
        "IntrBkSttlmAmt",
        _amount(data.amount_cents),
    )
    transaction_amount.set("Ccy", data.currency)
    debtor = etree.SubElement(transaction, _tag(namespace, "Dbtr"))
    _append(debtor, namespace, "Nm", data.source_spbvi_id)
    debtor_account = etree.SubElement(transaction, _tag(namespace, "DbtrAcct"))
    debtor_account_id = etree.SubElement(debtor_account, _tag(namespace, "Id"))
    debtor_account_other = etree.SubElement(debtor_account_id, _tag(namespace, "Othr"))
    _append(debtor_account_other, namespace, "Id", data.source_account_id)
    creditor = etree.SubElement(transaction, _tag(namespace, "Cdtr"))
    _append(creditor, namespace, "Nm", data.destination_spbvi_id)
    creditor_account = etree.SubElement(transaction, _tag(namespace, "CdtrAcct"))
    creditor_account_id = etree.SubElement(creditor_account, _tag(namespace, "Id"))
    creditor_account_other = etree.SubElement(creditor_account_id, _tag(namespace, "Othr"))
    _append(creditor_account_other, namespace, "Id", data.destination_account_id)

    message = etree.tostring(root, encoding="unicode")
    validate_message(message)
    return message


def build_pacs002(data: Pacs002Data) -> str:
    namespace = PACS_002_NAMESPACE
    root = etree.Element(_tag(namespace, "Document"), nsmap={None: namespace})
    report = etree.SubElement(root, _tag(namespace, "FIToFIPmtStsRpt"))
    header = etree.SubElement(report, _tag(namespace, "GrpHdr"))
    _append(header, namespace, "MsgId", data.message_id)
    _append(header, namespace, "CreDtTm", _date_time(data.created_at))
    original_group = etree.SubElement(report, _tag(namespace, "OrgnlGrpInfAndSts"))
    _append(original_group, namespace, "OrgnlMsgId", data.original_message_id)
    _append(original_group, namespace, "OrgnlMsgNmId", data.original_message_name)
    _append(original_group, namespace, "GrpSts", data.group_status)
    if data.status_reason is not None:
        reason_info = etree.SubElement(
            original_group,
            _tag(namespace, "StsRsnInf"),
        )
        reason = etree.SubElement(reason_info, _tag(namespace, "Rsn"))
        _append(reason, namespace, "Prtry", data.status_reason)

    message = etree.tostring(root, encoding="unicode")
    validate_message(message)
    return message


def validate_message(xml_message: str) -> None:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(xml_message.encode("utf-8"), parser=parser)
        namespace = etree.QName(root).namespace
        schema_path = SCHEMAS.get(namespace)
        if schema_path is None:
            raise Iso20022ValidationError("El mensaje usa un perfil ISO 20022 desconocido.")
        schema_document = etree.parse(str(schema_path), parser=parser)
        schema = etree.XMLSchema(schema_document)
        schema.assertValid(root)
    except (etree.XMLSyntaxError, etree.DocumentInvalid, OSError) as error:
        raise Iso20022ValidationError(
            "El XML no cumple el perfil XSD local de QALabSPBVI."
        ) from error
