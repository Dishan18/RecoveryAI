"""
RecoveryAI — Pydantic Schemas (API Request & Response Models)
Phase 2 additions: action payloads, diagnose response, discount preview, transition result.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Shared base ───────────────────────────────────────────────────────────────
class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Merchant ──────────────────────────────────────────────────────────────────
class MerchantOut(OrmBase):
    id: uuid.UUID
    name: str
    default_discount_cap: Decimal
    created_at: datetime


# ── Customer ──────────────────────────────────────────────────────────────────
class CustomerOut(OrmBase):
    id: uuid.UUID
    merchant_id: uuid.UUID
    name: str
    phone: str
    email: str | None
    ltv_inr: Decimal
    consecutive_discount_months: int


# ── Recovery Event ────────────────────────────────────────────────────────────
class RecoveryEventOut(OrmBase):
    id: uuid.UUID
    invoice_id: uuid.UUID
    current_state: str
    discount_offered: Decimal
    ptp_deadline: datetime | None
    log_message: str | None
    timestamp: datetime


# ── Invoice (with nested relations) ──────────────────────────────────────────
class InvoiceOut(OrmBase):
    id: uuid.UUID
    amount_inr: Decimal
    status: str
    failure_reason: str | None
    created_at: datetime
    due_date: datetime | None
    next_action_due_at: datetime | None = None
    call_pending: bool = False
    customer: CustomerOut
    merchant: MerchantOut
    recovery_events: list[RecoveryEventOut]


class AcknowledgeCallResponse(BaseModel):
    invoice_id: uuid.UUID
    call_pending: bool
    message: str


# ── Seed response ─────────────────────────────────────────────────────────────
class SeedResponse(BaseModel):
    message: str
    invoices_created: int
    scenarios: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Request / Response Schemas
# ─────────────────────────────────────────────────────────────────────────────

# ── Diagnose response ─────────────────────────────────────────────────────────
class DiagnoseResponse(BaseModel):
    invoice_id: uuid.UUID
    previous_state: str
    new_state: str
    failure_reason: str | None
    recommended_action: str
    log_message: str
    event_id: uuid.UUID


# ── Discount tier preview ─────────────────────────────────────────────────────
class TierPreview(BaseModel):
    tier: int
    tier_state: str
    discount_rate: Decimal          # e.g. 0.0500 for 5%
    discount_pct: str               # "5.00%"
    discount_amount_inr: Decimal    # ₹ deducted
    net_payable_inr: Decimal        # ₹ customer pays
    is_accessible: bool
    blocked_reason: str | None


class DiscountPreview(BaseModel):
    merchant_cap: Decimal
    merchant_cap_pct: str
    effective_cap: Decimal
    effective_cap_pct: str
    consecutive_months: int
    gross_amount_inr: Decimal
    tiers: list[TierPreview]


# ── Action request payload ────────────────────────────────────────────────────
ActionType = Literal[
    "SEND_LINK",
    "OFFER_DISCOUNT",
    "SET_PTP",
    "FLAG_DISPUTE",
    "RESOLVE_PAYMENT",
]

class ActionRequest(BaseModel):
    action_type: ActionType
    # Required when action_type == "SET_PTP"
    ptp_deadline: datetime | None = Field(
        default=None,
        description="ISO 8601 timestamp for promise-to-pay deadline.",
    )
    # Required when action_type == "OFFER_DISCOUNT"
    tier: int | None = Field(
        default=None,
        ge=1,
        le=3,
        description="Discount tier to offer (1, 2, or 3).",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional freeform notes for audit log.",
    )


# ── Action / transition result ────────────────────────────────────────────────
class TransitionResult(BaseModel):
    invoice_id: uuid.UUID
    action_type: str
    previous_state: str
    new_state: str
    new_invoice_status: str
    discount_offered: Decimal
    net_payable_inr: Decimal | None
    ptp_deadline: datetime | None
    event_id: uuid.UUID
    audit_log: str
    discount_preview: DiscountPreview | None = None


# ── Simulate-timeout result ───────────────────────────────────────────────────
class SimulateTimeoutResult(BaseModel):
    invoice_id: uuid.UUID
    simulated_scenario: str
    previous_state: str
    new_state: str
    new_invoice_status: str
    discount_offered: Decimal
    net_payable_inr: Decimal | None
    event_id: uuid.UUID
    audit_log: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Gemini AI Schemas
# ─────────────────────────────────────────────────────────────────────────────

DebtorIntent = Literal[
    "PROMISE_TO_PAY",
    "PTP_EXCEEDS_POLICY",
    "AGREED_TO_PAY",
    "REQUEST_NEGOTIATION",
    "DISPUTE",
    "REQUEST_ALTERNATE_LINK",
    "REQUEST_DISCOUNT",
    "HARD_REFUSAL",
    "GENERAL_INQUIRY",
]


class DebtorIntentResult(BaseModel):
    """Structured output from Gemini intent + PTP parser."""
    intent: str                    # One of DebtorIntent values
    ptp_deadline: datetime | None  # Parsed ISO timestamp, if intent == PROMISE_TO_PAY
    dispute_reason: str | None     # Extracted dispute text, if intent == DISPUTE
    confidence: float              # 0.0 – 1.0
    explanation: str               # Short reasoning string
    used_fallback: bool = False    # True when Gemini was unavailable


class DunningMessageResult(BaseModel):
    """Generated dunning copy from Gemini."""
    subject: str          # SMS/WhatsApp subject / opening line
    body: str             # Full message body (Indian English / Hinglish)
    channel: str          # SMS | WHATSAPP | EMAIL
    action_type: str      # The action that triggered this message
    used_fallback: bool = False


# ── Interpret-reply endpoint ──────────────────────────────────────────────────
class InterpretReplyRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Debtor's text reply in English or Hinglish.",
    )


class InterpretReplyResponse(BaseModel):
    invoice_id: uuid.UUID
    raw_message: str
    intent_result: DebtorIntentResult
    state_changed: bool
    previous_state: str
    new_state: str
    new_invoice_status: str
    event_id: uuid.UUID | None
    agent_reply: DunningMessageResult
    audit_log: str


# ── Generate-message endpoint ─────────────────────────────────────────────────
class GenerateMessageResponse(BaseModel):
    invoice_id: uuid.UUID
    action_type: str
    message: DunningMessageResult


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Voice Recovery Schemas
# ─────────────────────────────────────────────────────────────────────────────

class VoiceCallResponse(BaseModel):
    invoice_id: uuid.UUID
    transcription: str
    parsed_intent: str
    ptp_deadline: datetime | None
    dispute_reason: str | None
    agent_reply_text: str
    audio_base64: str
    audio_format: str = "audio/wav"
    previous_state: str
    new_state: str
    new_invoice_status: str
    used_stt_fallback: bool = False
    used_tts_fallback: bool = False
    applied_discount: float = 0.0
    action_executed: str = ""
    trigger_auto_close: bool = False


class VoiceGreetingResponse(BaseModel):
    invoice_id: uuid.UUID
    greeting_text: str
    audio_base64: str
    audio_format: str = "audio/wav"
    used_tts_fallback: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Analytics & Compliance Audit Schemas
# ─────────────────────────────────────────────────────────────────────────────

class AnalyticsSummaryResponse(BaseModel):
    total_at_risk_inr: float
    total_recovered_inr: float
    margin_preserved_inr: float
    active_ptp_inr: float
    frozen_dispute_inr: float
    recovery_rate_pct: float
    total_invoices: int
    resolved_count: int
    ptp_count: int
    disputed_count: int


class AnalyticsKPISummary(BaseModel):
    total_cases: int
    total_at_risk: float
    gross_recovered: float
    net_collected: float
    discounts_granted: float
    recovery_rate: float
    margin_preserved: float


class AnalyticsReasonBreakdown(BaseModel):
    reason: str
    total_cases: int
    resolved_cases: int
    amount_at_risk: float
    recovery_rate: float


class AnalyticsFunnelStep(BaseModel):
    stage: str
    count: int


class AnalyticsConcessionItem(BaseModel):
    tier: str
    resolved_cases: int
    volume_inr: float


class AnalyticsOverviewResponse(BaseModel):
    summary: AnalyticsKPISummary
    funnel: list[AnalyticsFunnelStep]
    by_reason: list[AnalyticsReasonBreakdown]
    concessions: list[AnalyticsConcessionItem]


class BatchSimulationResponse(BaseModel):
    total_created: int
    resolved_count: int
    ptp_count: int
    disputed_count: int
    total_recovered_inr: float
    summary_message: str


class AuditExportResponse(BaseModel):
    dossier_id: uuid.UUID
    exported_at: datetime
    merchant: dict[str, Any]
    customer: dict[str, Any]
    invoice: dict[str, Any]
    diagnostic: dict[str, Any]
    event_timeline: list[dict[str, Any]]
    settlement: dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Autonomous Agent Operations Schemas
# ─────────────────────────────────────────────────────────────────────────────

class FastForwardRequest(BaseModel):
    minutes: int = Field(10, ge=1, le=1440)
    invoice_id: uuid.UUID | None = None
    all_cases: bool = False
    fast_forward_all: bool = False


class FastForwardResponse(BaseModel):
    advanced_minutes: int
    actions_triggered: int
    events_updated: list[dict[str, Any]]
    message: str
    # Invoice IDs whose immediate next step is a voice call (frontend should enqueue these)
    call_triggered_ids: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Skip-Wait (per-invoice single-step advance)
# ─────────────────────────────────────────────────────────────────────────────

class SkipWaitResponse(BaseModel):
    invoice_id: uuid.UUID
    previous_state: str
    new_state: str
    # Human-readable description of the action taken
    action_taken: str
    # True when the immediate next step is a voice call — frontend must open VoiceCallModal
    trigger_call: bool
    discount_offered: Decimal
    net_payable_inr: Decimal | None


class ManualInvoiceCreate(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15)
    amount_inr: float = Field(..., gt=0)
    failure_reason: str = Field(..., min_length=2)
    ltv_inr: float = Field(50000.0, ge=0)
    consecutive_discount_months: int = Field(0, ge=0)
    merchant_name: str = "TechCorp B2B India"
    merchant_cap: float = 0.10


class OperatorOverrideRequest(BaseModel):
    override_type: str = Field(..., description="MANUAL_LINK | SIMULATE_PTP | FORCE_DISCOUNT | FLAG_DISPUTE | MARK_SETTLED | ESCALATE_HUMAN")
    reason: str = Field(..., min_length=3, max_length=500)


class SeedResponse(BaseModel):
    message: str
    invoices_created: int
    scenarios: list[str]






