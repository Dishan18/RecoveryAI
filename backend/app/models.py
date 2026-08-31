"""
RecoveryAI — SQLAlchemy ORM Models
All monetary amounts are in INR (₹), stored as NUMERIC(12, 2).
Discount/cap ratios are NUMERIC(5, 4), e.g. 0.1000 = 10%.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Merchant ──────────────────────────────────────────────────────────────────
class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Maximum discount ratio the merchant allows, e.g. 0.1000 = 10%
    default_discount_cap: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0.1
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    customers: Mapped[list["Customer"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Merchant id={self.id} name={self.name!r}>"


# ── Customer ──────────────────────────────────────────────────────────────────
class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Indian mobile format: +919876543210
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Customer lifetime value in ₹
    ltv_inr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0.0)
    # How many consecutive months this customer has received a discount
    consecutive_discount_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Relationships
    merchant: Mapped["Merchant"] = relationship(back_populates="customers")
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} name={self.name!r}>"


# ── Invoice ───────────────────────────────────────────────────────────────────
class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Amount due in ₹ (remaining balance)
    amount_inr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Original invoice gross amount in ₹
    original_amount_inr: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Total collected/settled amount in ₹
    recovered_amount_inr: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0.0, server_default="0.0"
    )
    # UNPAID | RESOLVED | DISPUTED | ESCALATED
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="UNPAID"
    )
    # Root-cause bucket for the payment failure
    # GATEWAY_TIMEOUT | INSUFFICIENT_FUNDS | MANDATE_DECLINE | EXPIRED_CARD | DISPUTED_AMOUNT
    failure_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── Autonomous scheduler fields ───────────────────────────────────────────
    # When the next autonomous action fires (e.g. send reminder, initiate call).
    # NULL means either the case is terminal or a call is pending (see call_pending).
    next_action_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True when the invoice's next action is a voice call — frontend auto-enqueues.
    # Set by the scheduler when REMINDER_SENT timer expires.
    call_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="invoices")
    merchant: Mapped["Merchant"] = relationship(back_populates="invoices")
    recovery_events: Mapped[list["RecoveryEvent"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="RecoveryEvent.timestamp",
    )

    @property
    def current_state(self) -> str:
        if self.recovery_events:
            return self.recovery_events[-1].current_state
        return "TRIGGERED"

    @property
    def current_discount_tier(self) -> int:
        st = self.current_state
        if st == "TIER_1_DISCOUNT":
            return 1
        elif st == "TIER_2_DISCOUNT":
            return 2
        elif st == "TIER_3_FLOOR":
            return 3
        return 0

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} amount_inr={self.amount_inr}"
            f" status={self.status!r}>"
        )


# ── RecoveryEvent ─────────────────────────────────────────────────────────────
class RecoveryEvent(Base):
    __tablename__ = "recovery_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    # State machine node
    # TRIGGERED | DIAGNOSED | LINK_SENT | PTP_ACTIVE | TIER_1_DISCOUNT |
    # TIER_2_DISCOUNT | TIER_3_FLOOR | RESOLVED | FROZEN_DISPUTE | ESCALATED_HUMAN
    current_state: Mapped[str] = mapped_column(String(50), nullable=False)
    # Ratio offered, e.g. 0.0500 = 5%
    discount_offered: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0.0
    )
    # Deadline for Promise-To-Pay (nullable — only set when in PTP state)
    ptp_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="recovery_events")

    def __repr__(self) -> str:
        return (
            f"<RecoveryEvent id={self.id} state={self.current_state!r}"
            f" invoice={self.invoice_id}>"
        )
