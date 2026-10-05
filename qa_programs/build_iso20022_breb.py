"""Genera el programa importable ISO 20022 / Bre-B (API REST + JSON) para QALabSPBVI.

Fuente: "Programa de Pruebas ISO 20022 - Ecosistema BREB BanRep (Enfoque API REST / JSON)".
Se adapta a los endpoints que el laboratorio implementa. Las HU sin endpoint se cargan
sin CP ejecutables y lo indican en su descripción.

Uso (desde la raíz del repositorio):
    python qa_programs/build_iso20022_breb.py
Escribe qa_programs/iso20022-breb-rest-json.json, que se importa desde
Calidad y pruebas → Importar programa, o con POST /qa/epics/{epic_key}/import.
"""

import json
from pathlib import Path

# Datos fijos del laboratorio: los CP de preparación los crean y el resto los usa.
SPBVI_A = "spbvi-a"
SPBVI_B = "spbvi-b"
ORIGIN = "qa-breb-a-origen"
DEST_A = "qa-breb-a-destino"
NO_FUNDS = "qa-breb-a-sinfondos"
EMPTY = "qa-breb-a-vacia"
DEST_B = "qa-breb-b-destino"
# Las llaves no son fijas: los CP de preparación las generan con {{key:new:TIPO}} según el
# catálogo Bre-B y las transacciones toman una de la lista de la épica con {{key:TIPO:SPBVI}}.
# Los identificadores de operación se generan con {{op:NOMBRE:new}} y se reutilizan con
# {{op:NOMBRE}} (reenvío idempotente y consulta de estado).
SETUP_KEYS = [
    # (ref, tipo, SPBVI, cuenta destino)
    ("LAB-TC-006", "phone", SPBVI_A, DEST_A),
    ("LAB-TC-024", "email", SPBVI_A, DEST_A),
    ("LAB-TC-025", "document", SPBVI_A, DEST_A),
    ("LAB-TC-026", "alias", SPBVI_A, DEST_A),
    ("LAB-TC-027", "merchant_code", SPBVI_A, DEST_A),
    ("LAB-TC-007", "alias", SPBVI_B, DEST_B),
    ("LAB-TC-028", "phone", SPBVI_B, DEST_B),
]
KEY_LABELS = {
    "phone": "celular",
    "email": "correo electrónico",
    "document": "documento de identidad",
    "alias": "alfanumérica (@)",
    "merchant_code": "código de comercio",
}
# 1.000 UVB con el valor por defecto de UVB_VALUE_CENTS (1.155.200) = 1.155.200.000 centavos.
OVER_LIMIT_CENTS = 1_155_200_001

NO_ENDPOINT = (
    "ADAPTACIÓN AL LABORATORIO: QALabSPBVI todavía no expone un endpoint para este mensaje, "
    "por lo que la HU se carga sin CP ejecutables. Los casos del documento original ({cases}) "
    "quedan como referencia hasta implementar el servicio."
)


def step(action: str, expected: str) -> dict[str, str]:
    return {"action": action, "expected": expected}


def case(
    ref: str,
    title: str,
    description: str,
    *,
    method: str,
    path: str,
    expected_status: list[int],
    expected_response: object = None,
    body: object = None,
    query: dict | None = None,
    priority: str = "high",
    preconditions: list[str] | None = None,
    steps: list[dict[str, str]] | None = None,
    expected_result: str,
    labels: list[str] | None = None,
) -> dict:
    return {
        "ref": ref,
        "title": title,
        "description": description,
        "priority": priority,
        "preconditions": preconditions or [],
        "steps": steps
        or [
            step(f"Enviar {method} {path} con el JSON del caso.", "La API responde con JSON."),
            step(
                f"Validar el código HTTP ({', '.join(map(str, expected_status))}).",
                "El código coincide con lo esperado.",
            ),
            step("Comparar la respuesta con el JSON esperado.", "Los campos esperados coinciden."),
        ],
        "expected_result": expected_result,
        "labels": labels or [],
        "request_method": method,
        "request_path": path,
        "request_query": query or {},
        "request_headers": {},
        "request_body": body,
        "expected_status_codes": expected_status,
        "expected_response": expected_response,
    }


def payment(
    operation: str,
    source: str,
    key_type: str,
    spbvi: str,
    amount_cents: int,
    *,
    key_value: str | None = None,
) -> dict:
    """Cuerpo de pago: la llave sale de la lista de la épica según el tipo indicado."""
    return {
        "operation_id": operation,
        "source_account_id": source,
        "destination_key_type": key_type,
        "destination_key_value": key_value or f"{{{{key:{key_type}:{spbvi}}}}}",
        "amount_cents": amount_cents,
    }


def new_op(name: str) -> str:
    return f"{{{{op:{name}:new}}}}"


def op(name: str) -> str:
    return f"{{{{op:{name}}}}}"


PREP = ["Ejecutar antes los CP de LAB-HU-000 (preparación de datos)."]
ADMIN_PREP = ["Ejecutar con rol administrador: crear cuentas y llaves es una acción administrativa."]

stories = [
    {
        "ref": "LAB-HU-000",
        "title": "Preparar datos de prueba del laboratorio",
        "description": (
            "Como analista de pruebas, quiero crear las cuentas y las llaves que usan los demás "
            "casos, para que el programa sea repetible. HU agregada por el laboratorio. Las "
            "cuentas son fijas (201 la primera vez, 409 si ya existían). Las llaves las genera el "
            "sistema en cada ejecución con un valor válido para su tipo Bre-B (celular, correo, "
            "documento, alfanumérica y código de comercio) y quedan en la lista de llaves de la "
            "épica, de donde las toman los CP de transacciones según el tipo que indican."
        ),
        "priority": "highest",
        "acceptance_criteria": [
            "Existen las cuentas qa-breb-* en spbvi-a y spbvi-b.",
            "Hay al menos una llave confirmada de cada tipo Bre-B en spbvi-a y llaves alfanumérica y celular en spbvi-b.",
            "Cada llave generada cumple el formato de su tipo y queda en la lista de llaves de la épica.",
            "Reejecutar la preparación no duplica cuentas y agrega llaves nuevas sin repetir valores.",
        ],
        "labels": ["datos-prueba", "laboratorio"],
        "story_points": 2,
        "test_cases": [
            *[
                case(
                    f"LAB-TC-00{index}",
                    f"Crear cuenta {account}",
                    f"Crea la cuenta {account} en {spbvi} con saldo inicial de {balance} centavos.",
                    method="POST",
                    path="/accounts",
                    body={"account_id": account, "spbvi_id": spbvi, "balance_cents": balance},
                    expected_status=[201, 409],
                    preconditions=ADMIN_PREP,
                    expected_result="La cuenta existe (201 la primera vez, 409 si ya estaba creada).",
                    priority="highest",
                    labels=["datos-prueba"],
                )
                for index, (account, spbvi, balance) in enumerate(
                    [
                        (ORIGIN, SPBVI_A, 500_000_000),
                        (DEST_A, SPBVI_A, 0),
                        (NO_FUNDS, SPBVI_A, 100),
                        (EMPTY, SPBVI_A, 0),
                        (DEST_B, SPBVI_B, 0),
                    ],
                    start=1,
                )
            ],
            *[
                case(
                    ref,
                    f"Registrar llave {KEY_LABELS[key_type]} generada en {spbvi}",
                    (
                        f"El sistema genera una llave de tipo {key_type} válida según el catálogo "
                        f"Bre-B y la registra en el DIFE de {spbvi} (DIFE → DICE) apuntando a {account}."
                    ),
                    method="POST",
                    path=f"/difes/{spbvi}/keys",
                    body={
                        "key_type": key_type,
                        "key_value": f"{{{{key:new:{key_type}}}}}",
                        "deposit_product_id": account,
                    },
                    expected_status=[201],
                    expected_response={
                        "key_type": key_type,
                        "key_value": f"{{{{key:new:{key_type}}}}}",
                        "spbvi_id": spbvi,
                        "status": "confirmed",
                    },
                    preconditions=ADMIN_PREP,
                    expected_result="La llave queda confirmada en DIFE y DICE y se agrega a la lista de llaves de la épica.",
                    priority="highest",
                    labels=["datos-prueba", "llaves", key_type],
                )
                for ref, key_type, spbvi, account in SETUP_KEYS
            ],
            case(
                "LAB-TC-008",
                "Resolver llave destino en el DIFE",
                "Consulta la llave en el directorio federado del SPBVI de origen.",
                method="GET",
                path=f"/difes/{SPBVI_A}/keys/resolve",
                query={"key_type": "phone", "key_value": "{{key:phone:spbvi-a}}"},
                expected_status=[200],
                expected_response={"key_value": "{{key:phone:spbvi-a}}", "spbvi_id": SPBVI_A, "deposit_product_id": DEST_A},
                preconditions=["LAB-TC-006 ejecutado (llave celular en spbvi-a)."],
                expected_result="El DIFE devuelve la llave activa y su producto de depósito.",
                labels=["llaves", "dife"],
            ),
        ],
    },
    {
        "ref": "HU-001",
        "title": "Validar mensajes PAIN.001 para pago inmediato",
        "description": (
            "Como analista de pruebas, quiero enviar solicitudes de pago inmediato en JSON y "
            "validar su respuesta, para cumplir las especificaciones Bre-B antes de producción. "
            "ADAPTACIÓN: en el laboratorio la solicitud pain.001 se mapea a POST /payments "
            "(pago intra-SPBVI); el mapeo exacto depende del anexo 6 de la Circular DSP-465 (supuesto)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Payload JSON con origen, llave destino, monto en centavos e identificador de operación (endToEndId del laboratorio).",
            "Campos obligatorios validados: una solicitud incompleta se rechaza con 422.",
            "Regla de negocio: monto ≤ 1.000 UVB por operación; tiempo de respuesta ≤ 20 segundos.",
            "Respuesta positiva con estado completed (aceptación).",
            "Respuesta negativa con código de rechazo y razón clara.",
            "Reenviar el mismo identificador de operación no duplica el abono.",
        ],
        "labels": ["pain001", "iso20022", "breb", "pruebas-integracion", "json"],
        "story_points": 8,
        "test_cases": [
            case(
                "TC-001",
                "Validar envío de pago inmediato con JSON válido",
                "Envía un pago intra-SPBVI válido a una llave celular de la lista de la épica.",
                method="POST",
                path="/payments",
                body=payment(new_op("tc001"), ORIGIN, "phone", SPBVI_A, 125_000),
                expected_status=[201],
                expected_response={"operation_id": op("tc001"), "status": "completed", "payment_type": "intra_spbvi", "amount_cents": 125_000},
                preconditions=PREP,
                expected_result="Pago aceptado con un identificador de operación nuevo en ≤ 20 segundos.",
                labels=["pain001", "api-rest", "json"],
            ),
            case(
                "TC-002",
                "Validar rechazo de pago con monto > 1.000 UVB",
                "Envía un pago que supera el límite por operación.",
                method="POST",
                path="/payments",
                body=payment(new_op("tc002"), ORIGIN, "email", SPBVI_A, OVER_LIMIT_CENTS),
                expected_status=[422],
                expected_response={"detail": "El monto supera el limite de 1000 UVB por operacion."},
                preconditions=[*PREP, "UVB_VALUE_CENTS con su valor por defecto (1.155.200)."],
                expected_result="Rechazo 422 con la razón «monto excede límite» y sin movimientos.",
                labels=["pain001", "validacion-reglas", "json"],
            ),
            case(
                "LAB-TC-009",
                "Validar rechazo por campos obligatorios faltantes",
                "Envía un pago sin llave destino.",
                method="POST",
                path="/payments",
                body={"operation_id": new_op("lab009"), "source_account_id": ORIGIN, "amount_cents": 1000},
                expected_status=[422],
                expected_result="La API rechaza el JSON incompleto con 422 y el detalle de validación.",
                labels=["pain001", "validacion", "json"],
            ),
            case(
                "LAB-TC-010",
                "Validar idempotencia del pago inmediato",
                "Reenvía la misma orden de TC-001 (mismo identificador de operación y misma llave).",
                method="POST",
                path="/payments",
                body=payment(op("tc001"), ORIGIN, "phone", SPBVI_A, 125_000),
                expected_status=[200],
                expected_response={"operation_id": op("tc001"), "replayed": True},
                preconditions=[
                    "TC-001 ejecutado justo antes.",
                    "Usar la misma llave celular que TC-001 (por defecto, la más reciente de la lista).",
                ],
                expected_result="La API devuelve el pago original (replayed) sin duplicar el abono.",
                labels=["pain001", "idempotencia"],
            ),
        ],
    },
    {
        "ref": "HU-002",
        "title": "Validar mensajes PACS.008 para transferencias interbancarias",
        "description": (
            "Como analista de pruebas, quiero validar transferencias inter-SPBVI por API REST "
            "JSON, para asegurar la interoperabilidad en Bre-B. ADAPTACIÓN: POST /payments/inter-spbvi "
            "resuelve la llave en el DICE, liquida en el MOL simulado y devuelve pacs.008 y pacs.002 "
            "de laboratorio. Las cuentas usan el formato local del laboratorio (no CLABE)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Petición POST con payload JSON válido según el contrato de la API.",
            "Origen, llave destino y SPBVI de cada parte presentes (instructingAgent / instructedAgent en el pacs.008).",
            "Enrutamiento correcto hacia el SPBVI destino simulado vía DICE.",
            "Confirmación de liquidación recibida (pacs.002 ACCP).",
            "Escenarios: éxito, cuenta inválida, llave o institución destino no disponible.",
        ],
        "labels": ["pacs008", "iso20022", "breb", "liquidacion", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "TC-003",
                "Validar enrutamiento de PACS.008 vía API REST JSON",
                "Envía una transferencia de spbvi-a a spbvi-b.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("tc003"), ORIGIN, "alias", SPBVI_B, 75_000),
                expected_status=[201],
                expected_response={"operation_id": op("tc003"), "status": "completed", "payment_type": "inter_spbvi", "destination_account_id": DEST_B},
                preconditions=PREP,
                expected_result="Transferencia enrutada al SPBVI destino y liquidada; incluye pacs008_xml y pacs002_xml.",
                labels=["pacs008", "enrutamiento", "json"],
            ),
            case(
                "LAB-TC-011",
                "Validar rechazo por cuenta de origen inexistente",
                "Envía una transferencia desde una cuenta que no existe.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("lab011"), "qa-breb-no-existe", "phone", SPBVI_B, 1_000),
                expected_status=[404],
                expected_response={"detail": "No se encontro la cuenta de origen."},
                expected_result="Rechazo 404 sin movimientos.",
                labels=["pacs008", "cuenta-invalida"],
            ),
            case(
                "LAB-TC-012",
                "Validar rechazo por institución o llave destino no disponible",
                "Envía una transferencia a una llave no registrada en el DICE.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("lab012"), ORIGIN, "alias", SPBVI_B, 1_000, key_value="@qabrebnoexiste"),
                expected_status=[404],
                expected_response={"detail": "DICE no encontro una llave confirmada o su cuenta receptora."},
                preconditions=PREP,
                expected_result="Rechazo 404: el DICE no puede enrutar el pago.",
                labels=["pacs008", "dice"],
            ),
            case(
                "LAB-TC-013",
                "Validar rechazo de flujo inter con llave del mismo SPBVI",
                "Envía por el flujo inter una llave que pertenece al SPBVI de origen.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("lab013"), ORIGIN, "document", SPBVI_A, 1_000),
                expected_status=[422],
                preconditions=PREP,
                expected_result="Rechazo 422: el pago debe ir por el flujo intra-SPBVI.",
                labels=["pacs008", "validacion-reglas"],
            ),
        ],
    },
    {
        "ref": "HU-003",
        "title": "Automatizar pruebas de regresión ISO 20022",
        "description": (
            "Como analista de pruebas, quiero ejecutar regresiones automatizadas de los endpoints "
            "REST antes de cada despliegue. ADAPTACIÓN: la regresión corre en GitHub Actions "
            "(pytest + Playwright) en cada push y el programa de CP se ejecuta desde la plataforma; "
            "no hay integración con Jira/Xray (los resultados quedan en las ejecuciones de cada CP)."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "Suite automatizada con ≥ 20 casos (este programa incluye más de 20 CP ejecutables).",
            "Integración con el pipeline CI/CD (GitHub Actions).",
            "Resultados registrados por ejecución con solicitud, respuesta y veredicto.",
            "Alertas por correo a la épica en cada ejecución.",
        ],
        "labels": ["automatizacion", "iso20022", "breb", "ci-cd", "api-testing"],
        "story_points": 13,
    },
    {
        "ref": "HU-004",
        "title": "Validar manejo de errores y rechazos ISO 20022",
        "description": (
            "Como analista de soporte, quiero validar escenarios de error y códigos HTTP/JSON, "
            "para asegurar que el sistema maneje excepciones antes de la certificación Bre-B. "
            "ADAPTACIÓN: el timeout no se simula en el laboratorio (no hay mock server)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Casos para JSON inválido, cuenta no existente y fondos insuficientes.",
            "Errores devueltos en JSON con detalle claro; los rechazos inter incluyen pacs.002 RJCT.",
            "Ningún rechazo modifica saldos.",
        ],
        "labels": ["manejo-errores", "iso20022", "breb", "soporte", "json"],
        "story_points": 5,
        "test_cases": [
            case(
                "LAB-TC-014",
                "Validar rechazo por tipo de dato inválido en el JSON",
                "Envía el monto como texto.",
                method="POST",
                path="/payments",
                body={**payment(new_op("lab014"), ORIGIN, "merchant_code", SPBVI_A, 1), "amount_cents": "mil"},
                expected_status=[422],
                expected_result="Rechazo 422 con el detalle de validación del campo amount_cents.",
                labels=["manejo-errores", "json"],
            ),
            case(
                "LAB-TC-015",
                "Validar rechazo intra por fondos insuficientes",
                "Envía un pago desde la cuenta sin fondos.",
                method="POST",
                path="/payments",
                body=payment(new_op("lab015"), NO_FUNDS, "email", SPBVI_A, 5_000),
                expected_status=[409],
                expected_response={"detail": "La cuenta de origen no tiene saldo suficiente."},
                preconditions=PREP,
                expected_result="Rechazo 409 sin débito ni crédito.",
                labels=["manejo-errores", "fondos-insuficientes"],
            ),
            case(
                "LAB-TC-016",
                "Validar rechazo inter por fondos insuficientes",
                "Envía una transferencia inter desde la cuenta sin fondos.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("lab016"), NO_FUNDS, "phone", SPBVI_B, 5_000),
                expected_status=[409],
                preconditions=PREP,
                expected_result="Rechazo 409; el MOL no liquida y no hay movimientos.",
                labels=["manejo-errores", "fondos-insuficientes", "pacs002"],
            ),
            case(
                "LAB-TC-017",
                "Validar rechazo intra por cuenta no existente",
                "Envía un pago desde una cuenta inexistente.",
                method="POST",
                path="/payments",
                body=payment(new_op("lab017"), "qa-breb-no-existe", "document", SPBVI_A, 1_000),
                expected_status=[404],
                expected_response={"detail": "No se encontro la cuenta de origen."},
                expected_result="Rechazo 404 con detalle claro.",
                labels=["manejo-errores", "cuenta-invalida"],
            ),
        ],
    },
    {
        "ref": "HU-005",
        "title": "Generar datos de prueba realistas ISO 20022",
        "description": (
            "Como analista de datos, quiero generar payloads JSON que reflejen escenarios Bre-B. "
            "ADAPTACIÓN: este programa se genera con qa_programs/build_iso20022_breb.py y sus datos "
            "cumplen el límite de 1.000 UVB y usan cuentas y llaves válidas del laboratorio."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "Script Python que genera los payloads JSON del programa.",
            "Datos con montos ≤ 1.000 UVB (salvo los casos de rechazo), cuentas válidas y SPBVI participantes.",
            "Datos exportados en JSON reutilizable (archivo del programa).",
            "Estructura y reglas documentadas en el README.",
        ],
        "labels": ["datos-prueba", "python", "breb", "json"],
        "story_points": 5,
    },
    {
        "ref": "HU-006",
        "title": "Validar endpoints REST de extractos y conciliación (CAMT / equivalente JSON)",
        "description": (
            "Como analista de pruebas, quiero validar extractos y conciliación en JSON, para "
            "asegurar la consistencia de saldos. ADAPTACIÓN: GET /accounts/{id}/statement devuelve "
            "saldo inicial, movimientos (con operation_id como endToEndId del laboratorio) y saldo "
            "final, e indica si concilia con el saldo de la cuenta."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Estructura JSON del extracto validada contra el contrato de la API.",
            "Saldo inicial + movimientos = saldo final, igual al saldo de la cuenta (reconciled = true).",
            "Cada movimiento de pago referencia su operation_id.",
        ],
        "labels": ["reconciliation", "iso20022", "breb", "api", "json"],
        "story_points": 8,
        "test_cases": [
            case(
                "LAB-TC-018",
                "Validar conciliación del extracto de la cuenta origen",
                "Consulta el extracto de la cuenta que originó los pagos.",
                method="GET",
                path=f"/accounts/{ORIGIN}/statement",
                expected_status=[200],
                expected_response={"account_id": ORIGIN, "reconciled": True},
                preconditions=[*PREP, "TC-001 y TC-003 ejecutados."],
                expected_result="El extracto concilia: saldo final igual al saldo de la cuenta.",
                labels=["reconciliation", "json"],
            ),
        ],
    },
    {
        "ref": "HU-007",
        "title": "Validar mensajes PACS.004 para devoluciones",
        "description": (
            "Como analista de pruebas, quiero validar devoluciones de transferencias. "
            "POST /payments/{operation_id}/returns devuelve total (sin amount_cents) o parcialmente "
            "un pago liquidado, mueve el dinero de la cuenta receptora a la de origen y responde el "
            "pacs004_xml. Los motivos son códigos ISO 20022 (AC01, AC04, AC06, AM05, FOCR, MD06, MS02, MS03)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Campos obligatorios: return_id, reason_code; amount_cents es opcional (sin él se devuelve el saldo pendiente).",
            "El pago original existe y está liquidado.",
            "Escenarios: devolución total, parcial y motivo inválido.",
            "La suma de las devoluciones nunca supera el monto original.",
        ],
        "labels": ["pacs004", "iso20022", "breb", "devoluciones", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "LAB-TC-029",
                "Crear el pago que se va a devolver",
                "Envía un pago intra-SPBVI de 50.000 centavos que usan los CP de devolución.",
                method="POST",
                path="/payments",
                body=payment(new_op("dev"), ORIGIN, "phone", SPBVI_A, 50_000),
                expected_status=[201],
                expected_response={"operation_id": op("dev"), "status": "completed"},
                preconditions=PREP,
                expected_result="Pago liquidado con un identificador de operación nuevo.",
                labels=["pacs004", "datos-prueba"],
            ),
            case(
                "TC-005",
                "Validar devolución parcial de una transferencia",
                "Devuelve 20.000 de los 50.000 centavos del pago de LAB-TC-029.",
                method="POST",
                path=f"/payments/{op('dev')}/returns",
                body={"return_id": new_op("ret-parcial"), "amount_cents": 20_000, "reason_code": "MD06"},
                expected_status=[201],
                expected_response={
                    "operation_id": op("dev"),
                    "amount_cents": 20_000,
                    "returned_total_cents": 20_000,
                    "reason_code": "MD06",
                    "status": "completed",
                },
                preconditions=["LAB-TC-029 ejecutado justo antes."],
                expected_result="Devolución parcial con pacs004_xml y el acumulado devuelto en 20.000.",
                labels=["pacs004", "devolucion-parcial"],
            ),
            case(
                "TC-004",
                "Validar devolución total del saldo pendiente",
                "Sin amount_cents, devuelve lo que falta del pago (30.000 centavos).",
                method="POST",
                path=f"/payments/{op('dev')}/returns",
                body={"return_id": new_op("ret-total"), "reason_code": "AM05"},
                expected_status=[201],
                expected_response={"amount_cents": 30_000, "returned_total_cents": 50_000, "reason_code": "AM05"},
                preconditions=["TC-005 ejecutado justo antes."],
                expected_result="El pago queda devuelto por completo y el ordenante recupera los 50.000 centavos.",
                labels=["pacs004", "devolucion-total"],
            ),
            case(
                "LAB-TC-030",
                "Validar rechazo de devolución con motivo inválido",
                "Envía un código de motivo que no pertenece a la lista ISO admitida.",
                method="POST",
                path=f"/payments/{op('dev')}/returns",
                body={"return_id": new_op("ret-invalida"), "reason_code": "ZZ99"},
                expected_status=[422],
                preconditions=["LAB-TC-029 ejecutado."],
                expected_result="Rechazo 422 que indica los motivos válidos, sin movimientos.",
                labels=["pacs004", "validacion"],
            ),
            case(
                "LAB-TC-031",
                "Validar rechazo de devolución sobre un pago ya devuelto",
                "Intenta devolver otra vez el pago que TC-004 dejó devuelto por completo.",
                method="POST",
                path=f"/payments/{op('dev')}/returns",
                body={"return_id": new_op("ret-extra"), "reason_code": "MD06"},
                expected_status=[409],
                expected_response={"detail": "El pago ya fue devuelto por completo."},
                preconditions=["TC-004 ejecutado."],
                expected_result="Conflicto 409: no se devuelve más de lo pagado.",
                labels=["pacs004", "reglas"],
            ),
        ],
    },
    {
        "ref": "HU-008",
        "title": "Validar mensajes CAMT.056 para solicitudes de cancelación",
        "description": (
            "Como analista de pruebas, quiero validar solicitudes de cancelación. "
            "POST /payments/{operation_id}/cancellation-requests responde 202 con el camt056_xml y "
            "abre una investigación pendiente (camt029_xml con PECR/PDCR). El MOL simulado liquida "
            "de forma síncrona: la cancelación no anula el pago, el SPBVI receptor la resuelve en HU-009."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Campos obligatorios: cancellation_id y reason_code (AC03, AM09, CUST, DUPL, FRAD, TECH, UPAY).",
            "El pago original existe y no está devuelto por completo.",
            "Escenarios: solicitud exitosa, investigación ya pendiente y motivo inválido.",
        ],
        "labels": ["camt056", "iso20022", "breb", "cancelacion", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "LAB-TC-032",
                "Crear el pago que se va a cancelar",
                "Envía un pago intra-SPBVI de 30.000 centavos para solicitar su cancelación.",
                method="POST",
                path="/payments",
                body=payment(new_op("cxl"), ORIGIN, "email", SPBVI_A, 30_000),
                expected_status=[201],
                expected_response={"operation_id": op("cxl"), "status": "completed"},
                preconditions=PREP,
                expected_result="Pago liquidado.",
                labels=["camt056", "datos-prueba"],
            ),
            case(
                "TC-006",
                "Validar solicitud de cancelación exitosa",
                "El SPBVI de origen pide cancelar el pago de LAB-TC-032 por pago duplicado.",
                method="POST",
                path=f"/payments/{op('cxl')}/cancellation-requests",
                body={"cancellation_id": new_op("cxl-acepta"), "reason_code": "DUPL"},
                expected_status=[202],
                expected_response={
                    "cancellation_id": op("cxl-acepta"),
                    "operation_id": op("cxl"),
                    "status": "pending",
                    "reason_code": "DUPL",
                },
                preconditions=["LAB-TC-032 ejecutado justo antes."],
                expected_result="Investigación pendiente con camt056_xml y camt029_xml PECR.",
                labels=["camt056", "cancelacion"],
            ),
            case(
                "LAB-TC-033",
                "Validar rechazo de una segunda cancelación con investigación pendiente",
                "Pide otra cancelación del mismo pago mientras la de TC-006 sigue pendiente.",
                method="POST",
                path=f"/payments/{op('cxl')}/cancellation-requests",
                body={"cancellation_id": new_op("cxl-doble"), "reason_code": "CUST"},
                expected_status=[409],
                preconditions=["TC-006 ejecutado y todavía sin resolver."],
                expected_result="Conflicto 409 que indica la investigación pendiente.",
                labels=["camt056", "reglas"],
            ),
            case(
                "TC-007",
                "Validar rechazo de cancelación con motivo inválido",
                "Envía un código de motivo de cancelación que no existe.",
                method="POST",
                path=f"/payments/{op('cxl')}/cancellation-requests",
                body={"cancellation_id": new_op("cxl-invalida"), "reason_code": "ABCD"},
                expected_status=[422],
                preconditions=["LAB-TC-032 ejecutado."],
                expected_result="Rechazo 422 con la lista de motivos válidos.",
                labels=["camt056", "validacion"],
            ),
        ],
    },
    {
        "ref": "HU-009",
        "title": "Validar mensajes CAMT.029 para resolución de investigaciones",
        "description": (
            "Como analista de pruebas, quiero validar resoluciones de investigaciones. "
            "POST /investigations/{cancellation_id}/resolution: si el SPBVI receptor acepta, la "
            "cancelación se ejecuta como devolución pacs.004 con motivo FOCR (CNCL/ACCR); si la "
            "rechaza, indica el motivo (RJCR). GET /investigations/{cancellation_id} consulta el estado."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "Campos obligatorios: accepted y, al rechazar, rejection_reason (AC04, AM04, ARDT, CUST, LEGL, NOAS, NOOR).",
            "La investigación referenciada existe.",
            "Escenarios: resolución aceptada, rechazada e investigación no encontrada.",
        ],
        "labels": ["camt029", "iso20022", "breb", "investigaciones", "json", "api"],
        "story_points": 5,
        "test_cases": [
            case(
                "TC-008",
                "Validar resolución aceptada de una cancelación",
                "El SPBVI receptor acepta la cancelación de TC-006 y devuelve el pago con FOCR.",
                method="POST",
                path=f"/investigations/{op('cxl-acepta')}/resolution",
                body={"accepted": True},
                expected_status=[200],
                expected_response={
                    "status": "accepted",
                    "payment_return": {"amount_cents": 30_000, "reason_code": "FOCR"},
                },
                preconditions=["TC-006 ejecutado (investigación pendiente)."],
                expected_result="camt029_xml con CNCL/ACCR y una devolución pacs.004 FOCR por el total.",
                labels=["camt029", "aceptada"],
            ),
            case(
                "LAB-TC-034",
                "Crear un pago y su solicitud de cancelación para rechazarla",
                "Paga 15.000 centavos para abrir una investigación que se rechaza en TC-009.",
                method="POST",
                path="/payments",
                body=payment(new_op("inv"), ORIGIN, "document", SPBVI_A, 15_000),
                expected_status=[201],
                expected_response={"operation_id": op("inv"), "status": "completed"},
                preconditions=PREP,
                expected_result="Pago liquidado.",
                labels=["camt029", "datos-prueba"],
            ),
            case(
                "LAB-TC-035",
                "Solicitar la cancelación que se va a rechazar",
                "Pide cancelar el pago de LAB-TC-034 a solicitud del cliente.",
                method="POST",
                path=f"/payments/{op('inv')}/cancellation-requests",
                body={"cancellation_id": new_op("cxl-rechaza"), "reason_code": "CUST"},
                expected_status=[202],
                expected_response={"status": "pending"},
                preconditions=["LAB-TC-034 ejecutado justo antes."],
                expected_result="Investigación pendiente.",
                labels=["camt029", "datos-prueba"],
            ),
            case(
                "TC-009",
                "Validar resolución rechazada de una cancelación",
                "El beneficiario no responde: el SPBVI receptor rechaza con NOAS y el dinero no se mueve.",
                method="POST",
                path=f"/investigations/{op('cxl-rechaza')}/resolution",
                body={"accepted": False, "rejection_reason": "NOAS"},
                expected_status=[200],
                expected_response={"status": "rejected", "rejection_reason": "NOAS", "payment_return": None},
                preconditions=["LAB-TC-035 ejecutado."],
                expected_result="camt029_xml con RJCR y motivo NOAS; el pago sigue liquidado.",
                labels=["camt029", "rechazada"],
            ),
            case(
                "LAB-TC-036",
                "Validar investigación no encontrada",
                "Consulta una investigación que no existe.",
                method="GET",
                path="/investigations/qa-investigacion-inexistente",
                expected_status=[404],
                expected_response={"detail": "No se encontro la investigacion."},
                expected_result="404 sin efectos.",
                labels=["camt029", "no-encontrado"],
            ),
        ],
    },
    {
        "ref": "HU-010",
        "title": "Validar mensajes PACS.028 para solicitudes de estado de pago",
        "description": (
            "Como analista de pruebas, quiero consultar el estado de un pago. ADAPTACIÓN: "
            "GET /payments/{operation_id} responde el estado y su equivalente pacs.002 (iso_status)."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "El mensaje original (operation_id) existe.",
            "Respuesta JSON con el estado actual del pago (ACCP / PDNG / RJCT).",
            "Escenarios: pago liquidado y mensaje no encontrado.",
        ],
        "labels": ["pacs028", "iso20022", "breb", "consulta-estado", "json", "api"],
        "story_points": 5,
        "test_cases": [
            case(
                "TC-010",
                "Validar consulta de estado de un pago liquidado",
                "Consulta el estado del pago de TC-001.",
                method="GET",
                path=f"/payments/{op('tc001')}",
                expected_status=[200],
                expected_response={"operation_id": op("tc001"), "status": "completed", "iso_status": "ACCP"},
                priority="medium",
                preconditions=["TC-001 ejecutado."],
                expected_result="Estado del pago recibido en JSON en ≤ 20 segundos.",
                labels=["pacs028", "api-rest", "json"],
            ),
            case(
                "TC-011",
                "Validar consulta con mensaje original no encontrado",
                "Consulta un identificador de operación inexistente.",
                method="GET",
                path="/payments/qa-breb-no-existe",
                expected_status=[404],
                expected_response={"detail": "No se encontro el pago con ese identificador de operacion."},
                priority="medium",
                expected_result="Rechazo 404 con la razón «mensaje no encontrado».",
                labels=["pacs028", "validacion-reglas", "json"],
            ),
        ],
    },
    {
        "ref": "HU-011",
        "title": "Validar mensajes CAMT.052 para reportes de movimientos intradía",
        "description": (
            "Como analista de pruebas, quiero obtener movimientos intradía en JSON. ADAPTACIÓN: "
            "GET /accounts/{id}/statement devuelve todos los movimientos de la cuenta (el laboratorio "
            "no filtra todavía por fromDateTime / toDateTime)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Respuesta JSON con estructura de movimientos validada.",
            "Saldo inicial, movimientos y saldo final consistentes.",
            "Escenarios: movimientos existentes, sin movimientos, cuenta inválida.",
        ],
        "labels": ["camt052", "iso20022", "breb", "intradia", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "TC-012",
                "Validar movimientos de una cuenta con abonos",
                "Consulta el extracto de la cuenta destino de TC-001.",
                method="GET",
                path=f"/accounts/{DEST_A}/statement",
                expected_status=[200],
                expected_response={"account_id": DEST_A, "reconciled": True},
                preconditions=[*PREP, "TC-001 ejecutado."],
                expected_result="Movimientos recibidos en JSON y conciliados.",
                labels=["camt052", "api-rest", "json"],
            ),
            case(
                "LAB-TC-019",
                "Validar cuenta sin movimientos",
                "Consulta el extracto de una cuenta sin movimientos.",
                method="GET",
                path=f"/accounts/{EMPTY}/statement",
                expected_status=[200],
                expected_response={
                    "account_id": EMPTY,
                    "total_credits_cents": 0,
                    "total_debits_cents": 0,
                    "closing_balance_cents": 0,
                    "reconciled": True,
                },
                preconditions=PREP,
                expected_result="Extracto vacío con saldos en cero.",
                labels=["camt052", "json"],
            ),
            case(
                "LAB-TC-020",
                "Validar extracto de cuenta inválida",
                "Consulta el extracto de una cuenta inexistente.",
                method="GET",
                path="/accounts/qa-breb-no-existe/statement",
                expected_status=[404],
                expected_response={"detail": "No se encontro la cuenta."},
                expected_result="Rechazo 404.",
                labels=["camt052", "cuenta-invalida"],
            ),
        ],
    },
    {
        "ref": "HU-012",
        "title": "Validar mensajes CAMT.053 para extractos de cuenta",
        "description": (
            "Como analista de pruebas, quiero obtener extractos de cuenta en JSON. ADAPTACIÓN: "
            "usa el mismo endpoint de extracto (sin filtro fromDate / toDate en el laboratorio)."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Estructura de extracto validada contra el contrato de la API.",
            "Saldos inicial, movimientos y final consistentes.",
            "Escenarios: con movimientos, sin movimientos, cuenta inválida.",
        ],
        "labels": ["camt053", "iso20022", "breb", "extractos", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "LAB-TC-021",
                "Validar extracto de la cuenta destino inter-SPBVI",
                "Consulta el extracto de la cuenta que recibió TC-003 en spbvi-b.",
                method="GET",
                path=f"/accounts/{DEST_B}/statement",
                expected_status=[200],
                expected_response={"account_id": DEST_B, "spbvi_id": SPBVI_B, "reconciled": True},
                preconditions=[*PREP, "TC-003 ejecutado."],
                expected_result="Extracto conciliado con el abono liquidado por el MOL.",
                labels=["camt053", "json"],
            ),
        ],
    },
    {
        "ref": "HU-013",
        "title": "Validar mensajes CAMT.054 para notificaciones de crédito/débito",
        "description": (
            "Como analista de pruebas, quiero validar notificaciones de crédito y débito. "
            "GET /accounts/{account_id}/notifications devuelve los movimientos de pagos y devoluciones "
            "de la cuenta (opcionalmente de una operación) con su camt054_xml."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "Cada notificación indica tipo (crédito o débito), monto, moneda y fecha valor.",
            "La notificación está asociada a una transacción válida.",
            "Escenarios: crédito, débito y cuenta inválida.",
        ],
        "labels": ["camt054", "iso20022", "breb", "notificaciones", "json", "api"],
        "story_points": 5,
        "test_cases": [
            case(
                "LAB-TC-037",
                "Validar notificación de crédito al beneficiario",
                "Consulta los movimientos de la cuenta destino por el pago de LAB-TC-029.",
                method="GET",
                path=f"/accounts/{DEST_A}/notifications",
                query={"operation_id": op("dev")},
                expected_status=[200],
                expected_response={
                    "account_id": DEST_A,
                    "entries": [{"notification_type": "credit", "amount_cents": 50_000, "currency": "COP"}],
                    "total_credits_cents": 50_000,
                },
                preconditions=["HU-007 ejecutada (pago y devoluciones de LAB-TC-029)."],
                expected_result="Crédito por el pago y débitos por las devoluciones, con camt054_xml.",
                labels=["camt054", "credito"],
            ),
            case(
                "LAB-TC-038",
                "Validar notificación de débito al ordenante",
                "Consulta los movimientos de la cuenta de origen por el mismo pago.",
                method="GET",
                path=f"/accounts/{ORIGIN}/notifications",
                query={"operation_id": op("dev")},
                expected_status=[200],
                expected_response={
                    "account_id": ORIGIN,
                    "entries": [{"notification_type": "debit", "amount_cents": 50_000}],
                    "total_debits_cents": 50_000,
                },
                preconditions=["HU-007 ejecutada."],
                expected_result="Débito por el pago y créditos por las devoluciones.",
                labels=["camt054", "debito"],
            ),
            case(
                "LAB-TC-039",
                "Validar notificación de una cuenta inválida",
                "Consulta las notificaciones de una cuenta que no existe.",
                method="GET",
                path="/accounts/qa-cuenta-inexistente/notifications",
                expected_status=[404],
                expected_response={"detail": "No se encontro la cuenta."},
                expected_result="404 sin efectos.",
                labels=["camt054", "no-encontrado"],
            ),
        ],
    },
    {
        "ref": "HU-014",
        "title": "Validar mensajes PACS.002 para confirmaciones de liquidación",
        "description": (
            "Como analista de pruebas, quiero validar las confirmaciones de liquidación. "
            "ADAPTACIÓN: el pacs.002 se devuelve en la respuesta de la transferencia y su estado "
            "se consulta con GET /payments/{operation_id}."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Liquidación exitosa confirmada con ACCP.",
            "Liquidación rechazada con RJCT y razón.",
            "Mensaje no encontrado responde 404.",
        ],
        "labels": ["pacs002", "iso20022", "breb", "liquidacion", "json", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "LAB-TC-022",
                "Validar confirmación ACCP de una transferencia liquidada",
                "Consulta el estado de la transferencia de TC-003.",
                method="GET",
                path=f"/payments/{op('tc003')}",
                expected_status=[200],
                expected_response={"payment_type": "inter_spbvi", "iso_status": "ACCP"},
                preconditions=["TC-003 ejecutado."],
                expected_result="Estado ACCP de la liquidación.",
                labels=["pacs002", "liquidacion"],
            ),
            case(
                "LAB-TC-023",
                "Validar pacs.002 RJCT por monto sobre el límite",
                "Envía una transferencia que supera 1.000 UVB.",
                method="POST",
                path="/payments/inter-spbvi",
                body=payment(new_op("lab023"), ORIGIN, "alias", SPBVI_B, OVER_LIMIT_CENTS),
                expected_status=[422],
                expected_response={"operation_id": op("lab023"), "status": "rejected"},
                preconditions=PREP,
                expected_result="Rechazo con pacs002_xml RJCT y razón AMOUNT_LIMIT_EXCEEDED.",
                labels=["pacs002", "rechazo"],
            ),
        ],
    },
    {
        "ref": "HU-015",
        "title": "Validar mensajes PAIN.002 para respuestas de pago inmediato",
        "description": (
            "Como analista de pruebas, quiero recibir respuestas pain.002 como mensaje independiente. "
            "El pago intra responde pain002_xml (ACSC al liquidar; RJCT con motivo ISO al rechazar) y "
            "GET /payments/{operation_id}/status-report devuelve el reporte de cualquier pago."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Campos: operation_id, transaction_status (ACSC, PDNG, RJCT) y, en el rechazo, reason_code (AC03, AM02, AM04).",
            "Escenarios: pago aceptado, rechazado y mensaje no encontrado.",
        ],
        "labels": ["pain002", "iso20022", "breb", "pago-inmediato", "json", "api"],
        "story_points": 5,
        "test_cases": [
            case(
                "LAB-TC-040",
                "Validar pain.002 de un pago aceptado",
                "Consulta el reporte de estado del pago de TC-001.",
                method="GET",
                path=f"/payments/{op('tc001')}/status-report",
                expected_status=[200],
                expected_response={"operation_id": op("tc001"), "transaction_status": "ACSC", "payment_type": "intra_spbvi"},
                preconditions=["TC-001 ejecutado."],
                expected_result="pain002_xml con TxSts ACSC.",
                labels=["pain002", "aceptado"],
            ),
            case(
                "LAB-TC-041",
                "Validar pain.002 de un pago rechazado por fondos",
                "Paga desde la cuenta sin fondos: el cliente recibe RJCT con motivo AM04.",
                method="POST",
                path="/payments",
                body=payment(new_op("pain-rjct"), NO_FUNDS, "alias", SPBVI_A, 50_000),
                expected_status=[409],
                expected_response={"operation_id": op("pain-rjct"), "status": "rejected", "reason_code": "AM04"},
                preconditions=PREP,
                expected_result="pain002_xml con TxSts RJCT y motivo AM04, sin movimientos.",
                labels=["pain002", "rechazo"],
            ),
            case(
                "LAB-TC-042",
                "Validar pain.002 de un pago no encontrado",
                "Consulta el reporte de un identificador de operación que no existe.",
                method="GET",
                path="/payments/qa-operacion-inexistente/status-report",
                expected_status=[404],
                expected_response={"detail": "No se encontro el pago con ese identificador de operacion."},
                expected_result="404 sin efectos.",
                labels=["pain002", "no-encontrado"],
            ),
        ],
    },
    {
        "ref": "HU-016",
        "title": "Validar autenticación y autorización en la API REST",
        "description": (
            "Como analista de seguridad, quiero validar que solo clientes autorizados procesen "
            "mensajes. ADAPTACIÓN: el laboratorio usa sesión con cookie HttpOnly + MFA por correo y "
            "roles validados en el backend, no JWT/OAuth2. El rechazo de credenciales inválidas (TC-023) "
            "se cubre en las pruebas automatizadas del backend, porque el ejecutor de CP siempre reenvía "
            "la sesión del usuario que ejecuta."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Una sesión válida accede a los endpoints protegidos.",
            "Sin sesión o con sesión inválida la API responde 401 (cubierto por pytest).",
            "Los permisos por rol se validan en el servidor.",
        ],
        "labels": ["seguridad", "autenticacion", "iso20022", "breb", "api"],
        "story_points": 8,
        "test_cases": [
            case(
                "TC-022",
                "Validar acceso con sesión válida",
                "Consulta el usuario autenticado con la sesión de quien ejecuta.",
                method="GET",
                path="/auth/me",
                expected_status=[200],
                expected_response={"is_active": True},
                expected_result="Petición aceptada y procesada con la sesión vigente.",
                labels=["seguridad", "sesion"],
            ),
        ],
    },
    {
        "ref": "HU-017",
        "title": "Validar trazabilidad y auditoría de mensajes ISO 20022",
        "description": (
            "Como analista de cumplimiento, quiero trazabilidad completa en JSON. ADAPTACIÓN: cada "
            "ejecución de CP guarda método, URL, solicitud y respuesta redactadas, duración y autor; "
            "los pagos se correlacionan por operation_id. No hay exportación de logs ni uetr todavía."
        ),
        "priority": "medium",
        "acceptance_criteria": [
            "Correlación entre mensajes por operation_id (endToEndId del laboratorio).",
            "Registro de tiempos de ejecución y respuesta.",
            "Exportación de logs para análisis externo (pendiente).",
        ],
        "labels": ["auditoria", "trazabilidad", "iso20022", "breb", "json"],
        "story_points": 5,
    },
    {
        "ref": "HU-018",
        "title": "Validar resiliencia ante fallos de red en la API REST",
        "description": (
            "Como analista de pruebas, quiero validar el comportamiento ante timeouts y "
            "desconexiones. ADAPTACIÓN: el laboratorio no tiene mock server; el ejecutor aplica un "
            "timeout de 15 segundos y registra la falla. Pendiente de TASK-004 / TASK-010."
        ),
        "priority": "high",
        "acceptance_criteria": [
            "Simulación de fallos de red con un mock server.",
            "Reintentos automáticos validados.",
            "Fallos registrados en la auditoría.",
        ],
        "labels": ["resiliencia", "red", "latencia", "iso20022", "breb", "pendiente-endpoint"],
        "story_points": 8,
    },
]

TASKS = [
    ("TASK-001", "Configurar validador de contratos JSON Schema", "Validar los payloads contra el contrato de la API (JSON Schema). Implementado: el ejecutor valida cada respuesta contra el esquema OpenAPI del endpoint y código HTTP, y el CP falla si no lo cumple; los mensajes XML se validan con XSD propios de laboratorio.", ["configuracion", "json-schema"], 3),
    ("TASK-002", "Organizar el repositorio de casos de prueba", "Estructurar HU y CP por tipo de mensaje. En QALabSPBVI cumple este rol la épica importada, con HU, CP enlazados y ejecuciones (sin Jira/Xray).", ["organizacion"], 3),
    ("TASK-003", "Documentar especificaciones técnicas Bre-B para QA", "Recopilar contratos OpenAPI (disponible en /docs de la API), reglas de negocio y códigos de error; enlazar las fuentes oficiales de Banrep.", ["documentacion", "swagger"], 2),
    ("TASK-004", "Configurar simulador API REST (mock server)", "Simular el nodo central con respuestas JSON configurables para éxito, latencia y errores HTTP.", ["simulador", "api"], 5),
    ("TASK-005", "Implementar validador JSON Schema para mensajes ISO 20022", "Cargar esquemas JSON Schema por tipo de mensaje y validar payloads de ejemplo.", ["json-schema", "validacion"], 5),
    ("TASK-006", "Configurar monitoreo de latencia y rendimiento", "Medir latencia de los endpoints críticos y alertar si supera 20 segundos.", ["monitoreo", "rendimiento"], 8),
    ("TASK-007", "Implementar pruebas de seguridad para la API REST", "Ejecutar pruebas OWASP (p. ej. ZAP) sobre los endpoints críticos y documentar hallazgos.", ["seguridad", "owasp"], 8),
    ("TASK-008", "Integrar las pruebas automatizadas al CI/CD", "Ejecutar las pruebas en cada despliegue. Cubierto con GitHub Actions; pendiente reportar resultados a una herramienta externa.", ["ci-cd", "automatizacion"], 5),
    ("TASK-009", "Implementar generador de datos de prueba en JSON", "Mantener qa_programs/build_iso20022_breb.py y ampliar la generación (≥ 100 payloads) respetando el límite de 1.000 UVB.", ["datos-prueba", "python"], 5),
    ("TASK-010", "Configurar simulador de errores y latencia", "Simular HTTP 400, 500 y timeout en el mock server.", ["simulador", "errores", "latencia"], 5),
    ("TASK-011", "Configurar pruebas de autenticación", "Automatizar pruebas de sesión válida, inválida y expirada (el laboratorio usa cookie de sesión + MFA, no JWT/OAuth2).", ["seguridad", "autenticacion"], 5),
    ("TASK-012", "Implementar trazabilidad avanzada en pruebas automatizadas", "Correlacionar logs JSON por operation_id y exportarlos para análisis.", ["auditoria", "trazabilidad"], 3),
    ("TASK-013", "Simular fallos de red en pruebas automatizadas", "Configurar timeout, desconexión y latencia; validar reintentos.", ["resiliencia", "simulacion"], 5),
    ("SUB-041", "Diseñar sesiones válidas e inválidas para pruebas (HU-016)", "Subtarea de HU-016.", ["seguridad"], None),
    ("SUB-042", "Implementar pruebas automatizadas de autenticación (HU-016)", "Subtarea de HU-016.", ["seguridad"], None),
    ("SUB-043", "Documentar resultados de autenticación (HU-016)", "Subtarea de HU-016.", ["seguridad"], None),
    ("SUB-044", "Configurar logs JSON avanzados (HU-017)", "Subtarea de HU-017.", ["auditoria"], None),
    ("SUB-045", "Integrar trazabilidad en scripts automatizados (HU-017)", "Subtarea de HU-017.", ["auditoria"], None),
    ("SUB-046", "Documentar auditoría (HU-017)", "Subtarea de HU-017.", ["auditoria"], None),
    ("SUB-047", "Configurar escenarios de fallos de red (HU-018)", "Subtarea de HU-018.", ["resiliencia"], None),
    ("SUB-048", "Ejecutar pruebas de resiliencia (HU-018)", "Subtarea de HU-018.", ["resiliencia"], None),
    ("SUB-049", "Documentar resultados de resiliencia (HU-018)", "Subtarea de HU-018.", ["resiliencia"], None),
]

program = {
    "format": "qalabspbvi-qa-program/v1",
    "name": "Programa de pruebas ISO 20022 Bre-B (API REST / JSON)",
    "epic": {
        "ref": "EPIC-001",
        "title": "Implementar y validar programa de pruebas ISO 20022 para integración con Bre-B",
        "description": (
            "Programa integral de pruebas para validar la mensajería ISO 20022 adaptada a payloads "
            "JSON en el contexto Bre-B, asegurando interoperabilidad, cumplimiento de especificaciones "
            "y estabilidad antes de conectar con módulos centralizados. Fecha objetivo: 2026-12-15."
        ),
        "note": "La épica la crea el admin desde la plataforma; la importación agrega HU, CP y tareas.",
    },
    "stories": stories,
    "tasks": [
        {"ref": ref, "title": title, "description": description, "labels": labels, "story_points": points}
        for ref, title, description, labels, points in TASKS
    ],
}

if __name__ == "__main__":
    output = Path(__file__).with_name("iso20022-breb-rest-json.json")
    output.write_text(json.dumps(program, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cases = sum(len(story.get("test_cases", [])) for story in stories)
    print(f"{output.name}: {len(stories)} HU, {cases} CP, {len(TASKS)} tareas")
