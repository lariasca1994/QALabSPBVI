from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean
from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KeyStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    SUSPENDED_ADMINISTRATIVE = "suspended_administrative"
    SUSPENDED_PERSONAL = "suspended_personal"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance_cents >= 0"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    spbvi_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    balance_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_payment_operation_id"),
        CheckConstraint("amount_cents > 0"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
    )
    destination_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
    )
    destination_key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_key_value: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="intra_spbvi",
        server_default="intra_spbvi",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (CheckConstraint("amount_cents != 0"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id"),
        nullable=True,
        index=True,
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class UserRole(str, Enum):
    ADMIN = "admin"
    ADMINISTRADOR = "administrador"
    USUARIO = "usuario"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SqlEnum(
            UserRole,
            values_callable=lambda roles: [role.value for role in roles],
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class EmailLoginChallenge(Base):
    __tablename__ = "email_login_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)


class QaAutomationCode(Base):
    """Código MFA vigente de una cuenta de automatización QA (lista cerrada en la config).

    Solo para las cuentas de QA_AUTOMATION_EMAILS: su código no se envía por correo, se
    registra aquí y qa-evidencia lo lee con el token de automatización para completar el
    login E2E. Cada emisión reemplaza la anterior y vence con el mismo plazo del código.
    """

    __tablename__ = "qa_automation_codes"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class PendingLogin(Base):
    """Inicio de sesión con contraseña válida que espera el código MFA.

    Permite reenviar el código sin volver a pedir la contraseña. El navegador solo
    guarda el token en una cookie HttpOnly; aquí se persiste su digest.
    """

    __tablename__ = "pending_logins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sent_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
