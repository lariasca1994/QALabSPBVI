from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.db.models import KeyStatus, UserRole
from app.domains.keys.key_types import normalize_key_type, normalize_key_value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^[0-9]{6}$")


class UserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    role: UserRole

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre no puede quedar vacio.")
        return value


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    display_name: str
    role: UserRole
    is_active: bool
    # Solo en el alta: "sent" o "failed" según la entrega del correo de bienvenida.
    notification_status: str | None = None


class CsrfResponse(BaseModel):
    csrf_token: str


class EpicCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    member_ids: list[int] = Field(default_factory=list, max_length=500)


class EpicMembersRequest(BaseModel):
    member_ids: list[int] = Field(min_length=1, max_length=500)


class StoryCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    priority: str = Field(default="medium", pattern=r"^(lowest|low|medium|high|highest)$")
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)


class TestStep(BaseModel):
    action: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=2000)


class TestCaseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    priority: str = Field(default="medium", pattern=r"^(lowest|low|medium|high|highest)$")
    preconditions: list[str] = Field(default_factory=list, max_length=100)
    steps: list[TestStep] = Field(min_length=1, max_length=100)
    expected_result: str = Field(min_length=1, max_length=5000)
    request_method: str = Field(min_length=3, max_length=10)
    request_path: str = Field(min_length=1, max_length=500)
    request_query: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    request_body: dict[str, Any] | list[Any] | None = None
    expected_status_codes: list[int] = Field(
        default_factory=lambda: [200],
        min_length=1,
        max_length=20,
    )
    expected_response: dict[str, Any] | list[Any] | None = None


class TestCaseDefinitionUpdateRequest(BaseModel):
    """JSON ejecutable del CP: reemplaza la versión vigente y deja la anterior en historial."""

    request_method: str = Field(min_length=3, max_length=10)
    request_path: str = Field(min_length=1, max_length=500)
    request_query: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    request_body: dict[str, Any] | list[Any] | None = None
    expected_status_codes: list[int] = Field(min_length=1, max_length=20)
    expected_response: dict[str, Any] | list[Any] | None = None
    change_note: str = Field(min_length=1, max_length=500)


PRIORITY_PATTERN = r"^(lowest|low|medium|high|highest)$"


class ProgramTestCase(BaseModel):
    """CP de un programa importable: criterios tipo Jira + contrato REST/JSON ejecutable."""

    ref: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    priority: str = Field(default="medium", pattern=PRIORITY_PATTERN)
    preconditions: list[str] = Field(default_factory=list, max_length=100)
    steps: list[TestStep] = Field(min_length=1, max_length=100)
    expected_result: str = Field(min_length=1, max_length=5000)
    labels: list[str] = Field(default_factory=list, max_length=30)
    request_method: str = Field(min_length=3, max_length=10)
    request_path: str = Field(min_length=1, max_length=500)
    request_query: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    request_body: dict[str, Any] | list[Any] | None = None
    expected_status_codes: list[int] = Field(min_length=1, max_length=20)
    expected_response: dict[str, Any] | list[Any] | None = None


class ProgramStory(BaseModel):
    ref: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    priority: str = Field(default="medium", pattern=PRIORITY_PATTERN)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)
    labels: list[str] = Field(default_factory=list, max_length=30)
    story_points: int | None = Field(default=None, ge=0, le=100)
    test_cases: list[ProgramTestCase] = Field(default_factory=list, max_length=100)


class ProgramTask(BaseModel):
    ref: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    labels: list[str] = Field(default_factory=list, max_length=30)
    story_points: int | None = Field(default=None, ge=0, le=100)


class ProgramImportRequest(BaseModel):
    """Programa de pruebas (HU, CP y tareas) para cargar en una épica existente."""

    format: Literal["qalabspbvi-qa-program/v1"]
    name: str = Field(min_length=1, max_length=200)
    stories: list[ProgramStory] = Field(default_factory=list, max_length=200)
    tasks: list[ProgramTask] = Field(default_factory=list, max_length=300)


class TaskAssignRequest(BaseModel):
    assignee_id: int


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    assignee_id: int | None = None


class BugCreateRequest(BaseModel):
    case_key: str = Field(min_length=1, max_length=32)
    execution_key: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    severity: str = Field(pattern=r"^(trivial|minor|major|critical|blocker)$")


class FixCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)


class FixTransitionRequest(BaseModel):
    status: str = Field(pattern=r"^ready_for_retest$")


class BugTransitionRequest(BaseModel):
    status: str = Field(
        pattern=r"^(assigned|in_fix|ready_for_retest|closed|reopened)$"
    )
    retest_passed: bool | None = None
    assignee_id: int | None = None


class TaskTransitionRequest(BaseModel):
    status: str = Field(pattern=r"^(in_progress|done)$")


class AccountCreateRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    spbvi_id: str = Field(min_length=1, max_length=100)
    balance_cents: StrictInt = Field(default=0, ge=0)


class AccountResponse(BaseModel):
    id: str
    spbvi_id: str
    balance_cents: int


class PaymentCreateRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=100)
    source_account_id: str = Field(min_length=1, max_length=100)
    destination_key_type: str = Field(min_length=1, max_length=32)
    destination_key_value: str = Field(min_length=1, max_length=255)
    amount_cents: StrictInt = Field(gt=0)

    @field_validator(
        "operation_id",
        "source_account_id",
        "destination_key_type",
        "destination_key_value",
    )
    @classmethod
    def trim_required_payment_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El valor no puede quedar vacio.")
        return value

    @model_validator(mode="after")
    def validate_destination_key(self) -> "PaymentCreateRequest":
        self.destination_key_type = normalize_key_type(self.destination_key_type)
        self.destination_key_value = normalize_key_value(
            self.destination_key_type, self.destination_key_value
        )
        return self


class PaymentStatusResponse(BaseModel):
    """Consulta de estado de un pago (equivalente de laboratorio a pacs.028 → pacs.002)."""

    operation_id: str
    payment_type: str
    status: str
    iso_status: str
    source_account_id: str
    destination_account_id: str
    amount_cents: int
    created_at: str


class StatementEntry(BaseModel):
    entry_id: int
    entry_type: str
    amount_cents: int
    operation_id: str | None
    created_at: str


class AccountStatementResponse(BaseModel):
    """Extracto de cuenta (equivalente de laboratorio a camt.052/053), conciliado con el saldo."""

    account_id: str
    spbvi_id: str
    opening_balance_cents: int
    entries: list[StatementEntry]
    total_credits_cents: int
    total_debits_cents: int
    closing_balance_cents: int
    reconciled: bool


class PaymentResponse(BaseModel):
    id: int
    operation_id: str
    source_account_id: str
    destination_account_id: str
    amount_cents: int
    payment_type: str
    status: str
    replayed: bool


class InterSpbviPaymentResponse(PaymentResponse):
    pacs008_xml: str
    pacs002_xml: str


class InterSpbviPaymentRejectionResponse(BaseModel):
    detail: str
    operation_id: str
    status: Literal["rejected"]
    pacs002_xml: str


class KeyRegistrationRequest(BaseModel):
    key_type: str = Field(min_length=1, max_length=32)
    key_value: str = Field(min_length=1, max_length=255)
    deposit_product_id: str = Field(min_length=1, max_length=100)
    owner_email: EmailStr | None = None

    @field_validator("key_value", "deposit_product_id")
    @classmethod
    def trim_required_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El valor no puede quedar vacio.")
        return value

    @model_validator(mode="after")
    def validate_bre_b_key(self) -> "KeyRegistrationRequest":
        self.key_type = normalize_key_type(self.key_type)
        self.key_value = normalize_key_value(self.key_type, self.key_value)
        return self


class KeyLifecycleRequest(BaseModel):
    key_type: str = Field(min_length=1, max_length=32)
    key_value: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("key_value", "reason")
    @classmethod
    def trim_lifecycle_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El valor no puede quedar vacio.")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_key(self) -> "KeyLifecycleRequest":
        self.key_type = normalize_key_type(self.key_type)
        self.key_value = normalize_key_value(self.key_type, self.key_value)
        return self


class KeySuspendRequest(KeyLifecycleRequest):
    suspension_type: Literal["administrative", "personal"]


class KeyReactivateRequest(KeyLifecycleRequest):
    reactivation_type: Literal["administrative", "personal"]


class KeyOwnerUpdateRequest(KeyLifecycleRequest):
    owner_email: EmailStr


class PaymentKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key_type: str
    key_value: str
    spbvi_id: str
    deposit_product_id: str
    status: KeyStatus


class KeyDeleteResponse(BaseModel):
    deleted: bool
    key_type: str
    key_value: str
    spbvi_id: str
