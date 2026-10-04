"""Gateway ISO 20022 de laboratorio (Render, plan gratuito).

Genera y valida los mensajes pacs.008.001.08 y pacs.002.001.10 con el mismo adaptador
y los mismos XSD propios del monolito (app/domains/iso20022). Es un servicio sin
estado: no guarda pagos ni datos. Exige `Authorization: Bearer ISO_GATEWAY_TOKEN`.

SUPUESTO: perfiles y XSD de laboratorio, no oficiales; no prueban conformidad con
ISO 20022 ni con Banrep.
"""

import hmac
import os
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, StrictInt

from app.domains.iso20022.messages import (
    Iso20022ValidationError,
    Pacs002Data,
    Pacs008Data,
    build_pacs002,
    build_pacs008,
    validate_message,
)

app = FastAPI(title="QALabSPBVI · Gateway ISO 20022", version="1.0.0")


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ISO_GATEWAY_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="El gateway no tiene token configurado.")
    provided = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Token inválido.")


class Pacs008Request(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=100)
    source_spbvi_id: str = Field(min_length=1, max_length=100)
    destination_spbvi_id: str = Field(min_length=1, max_length=100)
    source_account_id: str = Field(min_length=1, max_length=100)
    destination_account_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt = Field(gt=0)
    created_at: datetime
    currency: str = "COP"


class Pacs002Request(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    original_message_id: str = Field(min_length=1, max_length=200)
    original_message_name: str = Field(min_length=1, max_length=200)
    group_status: Literal["ACCP", "RJCT", "PDNG"]
    created_at: datetime
    status_reason: str | None = Field(default=None, max_length=200)


class ValidateRequest(BaseModel):
    xml: str = Field(min_length=1, max_length=200_000)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "iso20022-gateway"}


@app.post("/pacs008", dependencies=[Depends(require_token)])
def pacs008(request: Pacs008Request) -> dict:
    try:
        xml = build_pacs008(Pacs008Data(**request.model_dump()))
    except (ValueError, Iso20022ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"message_name": "pacs.008.001.08", "xml": xml}


@app.post("/pacs002", dependencies=[Depends(require_token)])
def pacs002(request: Pacs002Request) -> dict:
    try:
        xml = build_pacs002(Pacs002Data(**request.model_dump()))
    except (ValueError, Iso20022ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"message_name": "pacs.002.001.10", "xml": xml}


@app.post("/validate", dependencies=[Depends(require_token)])
def validate(request: ValidateRequest) -> dict:
    try:
        validate_message(request.xml)
    except Iso20022ValidationError as error:
        return {"valid": False, "detail": str(error)}
    return {"valid": True}
