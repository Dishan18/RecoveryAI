"""
RecoveryAI — FastAPI Route Handlers (Phase 1 + 2)
==================================================

Phase 1
  GET  /api/invoices                          list all invoices
  POST /api/seed                              run migrations + seed

Phase 2
  POST /api/invoices/{id}/diagnose            TRIGGERED -> DIAGNOSED
  GET  /api/invoices/{id}/discount-preview    preview all 3 tiers for an invoice
  POST /api/invoices/{id}/action              execute a recovery action
  POST /api/invoices/{id}/simulate-timeout    fast-forward / breach simulation
  GET  /api/invoices/{id}                     fetch single invoice
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.engine.calculator import DiscountCalculator, calculator
from app.engine.gemini_service import (
    classify_debtor_intent,
    generate_dunning_copy,
    generate_grounded_speech,
    parse_debtor_message,
)
from app.engine.policy_wrapper import execute_policy_turn
from app.engine.sarvam_service import synthesize_speech, transcribe_audio
from app.engine.state_machine import (
    InvalidTransitionError,
    State,
    StateMachine,
    TerminalStateError,
)
from app.models import Customer, Invoice, RecoveryEvent
from app.schemas import (
    ActionRequest,
    DiagnoseResponse,
    DiscountPreview,
    GenerateMessageResponse,
    InterpretReplyRequest,
    InterpretReplyResponse,
    InvoiceOut,
    SeedResponse,
    SimulateTimeoutResult,
    SkipWaitResponse,
    TierPreview,
    TransitionResult,
    VoiceCallResponse,
    VoiceGreetingResponse,
    AnalyticsSummaryResponse,
    AnalyticsOverviewResponse,
    AnalyticsKPISummary,
    AnalyticsReasonBreakdown,
    AnalyticsFunnelStep,
    AnalyticsConcessionItem,
    BatchSimulationResponse,
    AuditExportResponse,
    FastForwardRequest,
    FastForwardResponse,
    ManualInvoiceCreate,
    OperatorOverrideRequest,
    AcknowledgeCallResponse,
    RecordPaymentRequest,
    RecordPaymentResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

TIER_STATE_MAP = {
    1: State.TIER_1_DISCOUNT,
    2: State.TIER_2_DISCOUNT,
    3: State.TIER_3_FLOOR,
}
STATE_TIER_MAP = {v: k for k, v in TIER_STATE_MAP.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Helper — load invoice with all relations or 404
# ─────────────────────────────────────────────────────────────────────────────
async def _get_invoice_or_404(invoice_id: uuid.UUID, db: AsyncSession) -> Invoice:
    result = await db.execute(
        select(Invoice)
        .options(
            selectinload(Invoice.customer),
            selectinload(Invoice.merchant),
            selectinload(Invoice.recovery_events),
        )
        .where(Invoice.id == invoice_id)
    )
    inv = result.scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found.")
    return inv


def _build_discount_preview(
    inv: Invoice,
    calc: DiscountCalculator = calculator,
) -> DiscountPreview:
    """Build the 3-tier preview payload for a given invoice."""
    cap = Decimal(str(inv.merchant.default_discount_cap))
    months = inv.customer.consecutive_discount_months
    gross = Decimal(str(inv.amount_inr))
    eff_cap = calc.effective_cap(cap, months)

    tiers: list[TierPreview] = []
    for tier_num in [1, 2, 3]:
        r = calc.calculate(cap, months, tier_num, gross)
        tiers.append(
            TierPreview(
                tier=tier_num,
                tier_state=TIER_STATE_MAP[tier_num],
                discount_rate=r.discount_rate,
                discount_pct=r.discount_pct,
                discount_amount_inr=r.discount_amount_inr,
                net_payable_inr=r.net_payable_inr,
                is_accessible=r.is_accessible,
                blocked_reason=None if r.is_accessible else r.audit_reason,
            )
        )

    return DiscountPreview(
        merchant_cap=cap,
        merchant_cap_pct=f"{cap*100:.2f}%",
        effective_cap=eff_cap,
        effective_cap_pct=f"{eff_cap*100:.2f}%",
        consecutive_months=months,
        gross_amount_inr=gross,
        tiers=tiers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/invoices", response_model=list[InvoiceOut], summary="List all invoices")
async def list_invoices(db: AsyncSession = Depends(get_db)) -> Any:
    """Returns all invoices with nested customer, merchant, and recovery_events without auto-seeding."""
    result = await db.execute(
        select(Invoice)
        .options(
            selectinload(Invoice.customer),
            selectinload(Invoice.merchant),
            selectinload(Invoice.recovery_events),
        )
        .order_by(Invoice.created_at.desc())
    )
    return result.scalars().all()


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, summary="Get single invoice")
async def get_invoice(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    return await _get_invoice_or_404(invoice_id, db)


@router.post("/seed", response_model=SeedResponse, summary="Seed the database")
async def seed_database() -> Any:
    """Idempotent: runs migrations + seeds 4 Indian payment-failure scenarios."""
    try:
        from seed import run_seed  # noqa: PLC0415
        return await run_seed()
    except Exception as exc:
        logger.exception("Seed failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Seed failed: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — POST /api/invoices/{id}/diagnose
# ─────────────────────────────────────────────────────────────────────────────

# Failure reason -> recommended routing
_FAILURE_ROUTING: dict[str, str] = {
    "GATEWAY_TIMEOUT":    "SEND_LINK — Technical failure, retry payment via alternate link",
    "INSUFFICIENT_FUNDS": "SET_PTP — Customer liquidity crunch, negotiate promise-to-pay",
    "MANDATE_DECLINE":    "OFFER_DISCOUNT — eMandate expired, use concession ladder to recover",
    "EXPIRED_CARD":       "SEND_LINK — Card expired, send update-card + alternate payment link",
    "DISPUTED_AMOUNT":    "FLAG_DISPUTE — TDS/GST discrepancy, freeze and route to finance",
}

@router.post(
    "/invoices/{invoice_id}/diagnose",
    response_model=DiagnoseResponse,
    summary="Diagnose invoice — TRIGGERED → DIAGNOSED",
)
async def diagnose_invoice(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Reads the invoice failure_reason and customer profile, then transitions
    TRIGGERED -> DIAGNOSED and returns a recommended next action.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    prev = sm.current_state

    reason = inv.failure_reason or "UNKNOWN"
    recommended = _FAILURE_ROUTING.get(reason, "REVIEW_MANUAL — unknown failure pattern")
    months = inv.customer.consecutive_discount_months
    ltv = Decimal(str(inv.customer.ltv_inr))

    log = (
        f"Diagnosis complete for {inv.customer.name} | "
        f"Phone: {inv.customer.phone} | "
        f"Failure: {reason} | "
        f"LTV: ₹{ltv:,.2f} | "
        f"Consecutive discount months: {months} | "
        f"Recommended: {recommended}"
    )

    try:
        event = await sm.transition(
            target_state=State.DIAGNOSED,
            log_message=log,
        )
    except (InvalidTransitionError, TerminalStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    return DiagnoseResponse(
        invoice_id=inv.id,
        previous_state=prev,
        new_state=State.DIAGNOSED,
        failure_reason=inv.failure_reason,
        recommended_action=recommended,
        log_message=log,
        event_id=event.id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — GET /api/invoices/{id}/discount-preview
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/invoices/{invoice_id}/discount-preview",
    response_model=DiscountPreview,
    summary="Preview discount ladder for an invoice",
)
async def discount_preview(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Returns all 3 discount tiers with accessibility flags and net payables."""
    inv = await _get_invoice_or_404(invoice_id, db)
    return _build_discount_preview(inv)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — POST /api/invoices/{id}/action
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/action",
    response_model=TransitionResult,
    summary="Execute a recovery action on an invoice",
)
async def execute_action(
    payload: ActionRequest,
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Accepted action_types:
    - SEND_LINK       → transition to LINK_SENT
    - SET_PTP         → transition to PTP_ACTIVE (requires ptp_deadline)
    - OFFER_DISCOUNT  → run calculator + transition to TIER_n_DISCOUNT (requires tier)
    - FLAG_DISPUTE    → freeze at FROZEN_DISPUTE
    - RESOLVE_PAYMENT → close as RESOLVED
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    prev = sm.current_state

    action = payload.action_type
    discount_offered: float = 0.0
    net_payable: Decimal | None = None
    ptp_deadline = payload.ptp_deadline
    preview: DiscountPreview | None = None
    audit: str = payload.notes or ""

    # ── Route by action type ──────────────────────────────────────────────────
    if action == "SEND_LINK":
        target = State.LINK_SENT
        audit = f"Alternate payment link dispatched. {audit}"

    elif action == "SET_PTP":
        if not ptp_deadline:
            raise HTTPException(
                status_code=422,
                detail="ptp_deadline is required for SET_PTP action.",
            )
        target = State.PTP_ACTIVE
        audit = (
            f"Promise-To-Pay negotiated. Deadline: {ptp_deadline.isoformat()}. {audit}"
        )

    elif action == "OFFER_DISCOUNT":
        tier = payload.tier
        if tier is None:
            raise HTTPException(
                status_code=422,
                detail="tier (1, 2, or 3) is required for OFFER_DISCOUNT action.",
            )
        cap = Decimal(str(inv.merchant.default_discount_cap))
        months = inv.customer.consecutive_discount_months
        gross = Decimal(str(inv.amount_inr))

        result = calculator.calculate(cap, months, tier, gross)
        if not result.is_accessible:
            raise HTTPException(
                status_code=409,
                detail=f"Tier {tier} is BLOCKED for this customer. {result.audit_reason}",
            )

        discount_offered = float(result.discount_rate)
        net_payable = result.net_payable_inr
        target = TIER_STATE_MAP[tier]
        audit = f"{result.audit_reason}\n{audit}"
        preview = _build_discount_preview(inv)

    elif action == "FLAG_DISPUTE":
        target = State.FROZEN_DISPUTE
        audit = f"Invoice flagged as disputed. Frozen for finance review. {audit}"

    elif action == "RESOLVE_PAYMENT":
        target = State.RESOLVED
        audit = f"Payment confirmed. Invoice marked RESOLVED. {audit}"

    else:
        raise HTTPException(status_code=422, detail=f"Unknown action_type: {action!r}")

    # ── Execute transition ────────────────────────────────────────────────────
    try:
        event = await sm.transition(
            target_state=target,
            discount_offered=discount_offered,
            ptp_deadline=ptp_deadline,
            log_message=audit,
        )
    except (InvalidTransitionError, TerminalStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()

    return TransitionResult(
        invoice_id=inv.id,
        action_type=action,
        previous_state=prev,
        new_state=target,
        new_invoice_status=inv.status,
        discount_offered=Decimal(str(discount_offered)),
        net_payable_inr=net_payable,
        ptp_deadline=ptp_deadline,
        event_id=event.id,
        audit_log=audit,
        discount_preview=preview,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — POST /api/invoices/{id}/simulate-timeout
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/simulate-timeout",
    response_model=SimulateTimeoutResult,
    summary="Fast-forward / simulate deadline breach",
)
async def simulate_timeout(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Simulates timeline advancement:

    - PTP_ACTIVE & past ptp_deadline  → breach → TIER_1_DISCOUNT
    - PTP_ACTIVE & no deadline set    → forced breach → TIER_1_DISCOUNT
    - TIER_1 / TIER_2 unpaid          → step to next tier
    - TIER_3_FLOOR unpaid             → ESCALATED_HUMAN
    - LINK_SENT unpaid                → TIER_1_DISCOUNT
    - All other active states         → TIER_1_DISCOUNT (fallback escalation)
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    current = sm.current_state

    if current in State.TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Invoice is in terminal state '{current}'. Cannot simulate timeout.",
        )

    cap = Decimal(str(inv.merchant.default_discount_cap))
    months = inv.customer.consecutive_discount_months
    gross = Decimal(str(inv.amount_inr))
    discount_offered: float = 0.0
    net_payable: Decimal | None = None

    # ── Determine target state ────────────────────────────────────────────────
    if current == State.PTP_ACTIVE:
        scenario = "PTP deadline breached — stepping to first eligible discount tier"
        target = State.TIER_1_DISCOUNT

    elif current in State.DISCOUNT_TIERS:
        next_tier = StateMachine.next_discount_tier(current)
        if next_tier is None:
            # At TIER_3_FLOOR → escalate
            scenario = "TIER_3_FLOOR timeout — no more tiers, escalating to human agent"
            target = State.ESCALATED_HUMAN
        else:
            current_tier_num = STATE_TIER_MAP.get(current, 0)
            scenario = f"Tier {current_tier_num} timeout — stepping to next tier"
            target = next_tier

    else:
        # LINK_SENT, DIAGNOSED, etc.
        scenario = f"{current} breach — stepping to first discount tier"
        target = State.TIER_1_DISCOUNT

    # ── Compute discount if stepping to a discount tier ───────────────────────
    if target in STATE_TIER_MAP:
        tier_num = STATE_TIER_MAP[target]
        result = calculator.calculate(cap, months, tier_num, gross)
        if result.is_accessible:
            discount_offered = float(result.discount_rate)
            net_payable = result.net_payable_inr
            audit = (
                f"[SIMULATE-TIMEOUT] {scenario}\n"
                f"{result.audit_reason}"
            )
        else:
            # Discount blocked — escalate instead
            scenario += f" (Tier {tier_num} blocked by anti-gaming policy)"
            target = State.ESCALATED_HUMAN
            audit = (
                f"[SIMULATE-TIMEOUT] {scenario}\n"
                f"Discount blocked: {result.audit_reason}"
            )
    else:
        audit = f"[SIMULATE-TIMEOUT] {scenario}"

    # ── Execute transition ────────────────────────────────────────────────────
    prev = sm.current_state
    try:
        event = await sm.transition(
            target_state=target,
            discount_offered=discount_offered,
            log_message=audit,
        )
    except (InvalidTransitionError, TerminalStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()

    return SimulateTimeoutResult(
        invoice_id=inv.id,
        simulated_scenario=scenario,
        previous_state=prev,
        new_state=target,
        new_invoice_status=inv.status,
        discount_offered=Decimal(str(discount_offered)),
        net_payable_inr=net_payable,
        event_id=event.id,
        audit_log=audit,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — POST /api/invoices/{id}/interpret-reply
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/interpret-reply",
    response_model=InterpretReplyResponse,
    summary="Interpret debtor natural language reply via Gemini AI",
)
async def interpret_reply(
    payload: InterpretReplyRequest,
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Passes debtor message to Gemini (or fallback parser) to detect intent and parse PTP dates.
    Applies automatic FSM state routing based on intent:
      - PROMISE_TO_PAY       → PTP_ACTIVE (with extracted ptp_deadline)
      - DISPUTE              → FROZEN_DISPUTE (with extracted dispute_reason)
      - REQUEST_ALTERNATE_LINK → LINK_SENT
      - REQUEST_DISCOUNT     → TIER_1_DISCOUNT
      - HARD_REFUSAL         → ESCALATED_HUMAN
    Generates empathetic agent response and appends log event to Supabase.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    prev = sm.current_state

    # 1. Gemini Intent Parsing
    intent_res = await parse_debtor_message(payload.message)

    # 2. Determine target FSM state & transition parameters
    target_state: str | None = None
    discount_offered: float = 0.0
    ptp_deadline: datetime | None = None
    notes = ""
    action_type_for_copy = "SOFT_REMINDER"

    if intent_res.intent == "PROMISE_TO_PAY":
        target_state = State.PTP_ACTIVE
        ptp_deadline = intent_res.ptp_deadline or (datetime.now(timezone.utc) + timedelta(days=3))
        latest_event = inv.recovery_events[-1] if inv.recovery_events else None
        if latest_event:
            discount_offered = float(latest_event.discount_offered)
        notes = f"Parsed PTP commitment until {ptp_deadline.isoformat()}"
        action_type_for_copy = "PTP_CONFIRMATION"

    elif intent_res.intent == "DISPUTE":
        target_state = State.FROZEN_DISPUTE
        reason = intent_res.dispute_reason or payload.message
        notes = f"Debtor dispute flagged: {reason}"
        action_type_for_copy = "DISPUTE_ACK"

    elif intent_res.intent == "REQUEST_ALTERNATE_LINK":
        target_state = State.LINK_SENT
        notes = "Debtor requested alternate payment link"
        action_type_for_copy = "ALTERNATE_LINK"

    elif intent_res.intent == "REQUEST_DISCOUNT":
        # Check if Tier 1 is valid
        if sm.can_transition(State.TIER_1_DISCOUNT):
            target_state = State.TIER_1_DISCOUNT
            cap = Decimal(str(inv.merchant.default_discount_cap))
            months = inv.customer.consecutive_discount_months
            gross = Decimal(str(inv.amount_inr))
            res = calculator.calculate(cap, months, 1, gross)
            if res.is_accessible:
                discount_offered = float(res.discount_rate)
                notes = f"Requested discount -> applied Tier 1 ({res.discount_pct})"
                action_type_for_copy = "DISCOUNT_OFFER"
            else:
                notes = f"Requested discount -> Tier 1 BLOCKED ({res.audit_reason})"
                action_type_for_copy = "SOFT_REMINDER"

    elif intent_res.intent == "HARD_REFUSAL":
        target_state = State.ESCALATED_HUMAN
        notes = "Debtor hard refusal -> escalated to human agent"
        action_type_for_copy = "SOFT_REMINDER"

    # 3. Attempt state transition if target state is defined and valid
    event_id: uuid.UUID | None = None
    state_changed = False
    audit_log = f"Debtor Reply: \"{payload.message}\"\nIntent: {intent_res.intent} (Confidence: {intent_res.confidence:.2f})\nExplanation: {intent_res.explanation}"
    if notes:
        audit_log += f"\nNote: {notes}"

    if target_state and prev != target_state and not sm.current_state in State.TERMINAL_STATES:
        try:
            event = await sm.transition(
                target_state=target_state,
                discount_offered=discount_offered,
                ptp_deadline=ptp_deadline,
                log_message=audit_log,
            )
            event_id = event.id
            state_changed = True
        except (InvalidTransitionError, TerminalStateError) as exc:
            logger.warning("FSM transition skipped during interpret-reply: %s", exc)

    # 4. Generate AI Agent Reply Copy
    inv_data = {
        "amount_inr": float(inv.amount_inr),
        "merchant_cap": float(inv.merchant.default_discount_cap),
        "failure_reason": inv.failure_reason,
        "merchant_name": inv.merchant.name,
    }
    cust_data = {
        "name": inv.customer.name,
        "consecutive_discount_months": inv.customer.consecutive_discount_months,
    }
    agent_reply = await generate_dunning_copy(
        invoice_data=inv_data,
        customer_data=cust_data,
        action_type=action_type_for_copy,
        tier=1 if discount_offered > 0 else None,
    )

    await db.commit()

    return InterpretReplyResponse(
        invoice_id=inv.id,
        raw_message=payload.message,
        intent_result=intent_res,
        state_changed=state_changed,
        previous_state=prev,
        new_state=sm.current_state,
        new_invoice_status=inv.status,
        event_id=event_id,
        agent_reply=agent_reply,
        audit_log=audit_log,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — GET /api/invoices/{id}/generate-message
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/invoices/{invoice_id}/generate-message",
    response_model=GenerateMessageResponse,
    summary="Generate contextual dunning message copy for an invoice",
)
async def generate_message_endpoint(
    action_type: str = "SOFT_REMINDER",
    tier: int | None = None,
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Generates tailored Indian English / Hinglish dunning message copy for SMS/WhatsApp.
    """
    inv = await _get_invoice_or_404(invoice_id, db)

    inv_data = {
        "amount_inr": float(inv.amount_inr),
        "merchant_cap": float(inv.merchant.default_discount_cap),
        "failure_reason": inv.failure_reason,
        "merchant_name": inv.merchant.name,
    }
    cust_data = {
        "name": inv.customer.name,
        "consecutive_discount_months": inv.customer.consecutive_discount_months,
    }

    message_result = await generate_dunning_copy(
        invoice_data=inv_data,
        customer_data=cust_data,
        action_type=action_type,
        tier=tier,
    )

    return GenerateMessageResponse(
        invoice_id=inv.id,
        action_type=action_type,
        message=message_result,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — POST /api/invoices/{id}/voice/transcribe-and-reply
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/voice/transcribe-and-reply",
    response_model=VoiceCallResponse,
    summary="In-browser Hinglish Voice Recovery Call via Sarvam AI STT/TTS & Gemini Intent Engine",
)
@router.post(
    "/invoices/{invoice_id}/voice/turn",
    response_model=VoiceCallResponse,
    summary="Alias for voice call turn pipeline",
    include_in_schema=False,
)
async def voice_transcribe_and_reply(
    invoice_id: uuid.UUID = Path(...),
    audio_file: UploadFile | None = File(None),
    text_fallback: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Structured Voice Call Turn Execution:
    1. Transcribes incoming debtor audio via Sarvam AI saaras-v3 STT (or uses text_fallback).
    2. Classifies debtor intent via Gemini 2.5 Flash structured Pydantic schema (no chain-of-thought).
    3. Deterministic Policy Wrapper evaluates financial caps, concession ladder & FSM transitions.
    4. Generates natural conversational Hinglish reply strictly grounded in authoritative numbers.
    5. Synthesizes voice audio response via Sarvam AI bulbul-v3 TTS into base64 WAV.
    6. Appends immutable audit event to PostgreSQL.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    prev_state = inv.current_state or State.TRIGGERED

    # 1. Speech-to-Text (STT) via Sarvam saaras-v3
    transcript = ""
    used_stt_fallback = False
    if audio_file and hasattr(audio_file, "filename") and audio_file.filename:
        audio_bytes = await audio_file.read()
        if audio_bytes and len(audio_bytes) > 0:
            stt_res = await transcribe_audio(
                audio_bytes=audio_bytes,
                filename=audio_file.filename,
                content_type=audio_file.content_type or "audio/webm",
            )
            transcript = stt_res.get("transcript", "")
            used_stt_fallback = stt_res.get("used_fallback", False)

    if not transcript:
        if text_fallback:
            transcript = text_fallback
        else:
            transcript = "Bhai Monday tak pakka payment clear kar dunga, tension mat lo."
            used_stt_fallback = True

    # 2. Structured Gemini 2.5 Flash Intent Classification
    invoice_ctx = {
        "customer_name": inv.customer.name,
        "amount_inr": float(inv.amount_inr),
        "current_state": inv.current_state,
        "current_tier": inv.current_discount_tier,
        "failure_reason": inv.failure_reason,
    }
    intent_data = await classify_debtor_intent(transcript, invoice_ctx)

    # 3. Deterministic Policy Wrapper Execution (Authoritative Financial Boundary)
    turn_decision = await execute_policy_turn(
        invoice=inv,
        intent_data=intent_data,
        session=db,
    )

    # Clear call_pending flag since call has now taken place
    inv.call_pending = False
    if turn_decision.resulting_state in State.TERMINAL_STATES:
        inv.next_action_due_at = None
    elif turn_decision.resulting_state in (State.PTP_ACTIVE, State.SPLIT_FIRST_HALF_PENDING) and turn_decision.ptp_date:
        inv.next_action_due_at = turn_decision.ptp_date
    else:
        inv.next_action_due_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    # 4. Generate Empathetic Grounded Response Speech
    reply_text = await generate_grounded_speech(invoice_ctx, turn_decision)

    # 5. Text-to-Speech (TTS) via Sarvam bulbul-v3
    tts_res = await synthesize_speech(
        text=reply_text,
        target_language_code="hi-IN",
        speaker="shubh",
    )

    await db.commit()

    return VoiceCallResponse(
        invoice_id=inv.id,
        transcription=transcript,
        parsed_intent=turn_decision.intent,
        confidence=turn_decision.confidence,
        ptp_deadline=turn_decision.ptp_date,
        dispute_reason=turn_decision.dispute_reason,
        agent_reply_text=reply_text,
        audio_base64=tts_res.get("audio_base64", ""),
        audio_format=tts_res.get("audio_format", "audio/wav"),
        previous_state=prev_state,
        new_state=turn_decision.resulting_state,
        new_invoice_status=inv.status,
        used_stt_fallback=used_stt_fallback,
        used_tts_fallback=tts_res.get("used_fallback", False),
        applied_discount=float(turn_decision.authorized_discount_rate),
        authorized_discount_rate=float(turn_decision.authorized_discount_rate),
        authorized_net_amount=float(turn_decision.authorized_net_amount),
        customer_stated_discount_pct=float(turn_decision.customer_stated_discount_pct) if turn_decision.customer_stated_discount_pct is not None else None,
        action_executed=turn_decision.action_executed,
        trigger_auto_close=turn_decision.trigger_auto_close,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — POST /api/invoices/{id}/voice/greeting
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/voice/greeting",
    response_model=VoiceGreetingResponse,
    summary="Outbound Voice Call Opening Greeting via Sarvam AI v3 TTS (bulbul-v3)",
)
async def voice_call_greeting(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Generates and synthesizes the outbound voice call opening greeting:
    stating customer name, merchant name, amount due in INR, failure reason,
    or follow-up discount status before opening the user microphone.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    current_state = sm.current_state

    cap = Decimal(str(inv.merchant.default_discount_cap)) if inv.merchant else Decimal("0.10")
    months = inv.customer.consecutive_discount_months if inv.customer else 0
    gross = Decimal(str(inv.amount_inr))

    has_prior_ptp_breached = any(
        "PTP commitment deadline breached" in (e.log_message or "")
        or "PTP Breach" in (e.log_message or "")
        or "pichla payment promise breach" in (e.log_message or "")
        or "PTP breached" in (e.log_message or "")
        or (e.current_state == State.PTP_ACTIVE and e.ptp_deadline and e.ptp_deadline < e.timestamp)
        for e in inv.recovery_events
    ) or (current_state == State.PTP_ACTIVE and getattr(inv, "ptp_date", None) and inv.ptp_date < datetime.now(timezone.utc))

    if has_prior_ptp_breached or current_state == State.PTP_ACTIVE:
        greeting_text = (
            f"Namaste {inv.customer.name} ji! Aapka pichla payment promise breach ho gaya hai. "
            f"Policy ke mutabik ab aur time nahi diya ja sakta. Kripya 1 ghante ke andar payment complete karein ya case escalate kiya jaye?"
        )
    elif current_state in (State.SPLIT_FIRST_HALF_PENDING, State.SPLIT_OFFERED):
        half_amt = float(inv.amount_inr) / 2.0
        greeting_text = (
            f"Namaste {inv.customer.name} ji! Main {inv.merchant.name} se bol raha hoon. "
            f"Humne 1 ghanta pehle 50% payment ke liye discuss kiya tha, par abhi tak aapka ₹{half_amt:,.0f} ka payment receive nahi hua hai. "
            "Kya koi technical issue aa rahi hai ya aap abhi complete kar rahe hain?"
        )
    elif current_state == State.TIER_1_DISCOUNT:
        res1 = calculator.calculate(cap, months, 1, gross)
        greeting_text = (
            f"Namaste {inv.customer.name} ji! Aapka {res1.discount_pct} discount wala payment abhi tak receive nahi hua hai. "
            f"Kya aap abhi payment complete kar rahe hain?"
        )
    elif current_state == State.TIER_2_DISCOUNT:
        res2 = calculator.calculate(cap, months, 2, gross)
        greeting_text = (
            f"Namaste {inv.customer.name} ji! {res2.discount_pct} discount ka payment abhi tak pending hai. "
            f"Kya hum isko final settle karein?"
        )
    elif current_state == State.TIER_3_FLOOR:
        res3 = calculator.calculate(cap, months, 3, gross)
        greeting_text = (
            f"Namaste {inv.customer.name} ji! Final {res3.discount_pct} discount ka payment abhi tak receive nahi hua hai. "
            f"Kya aap ise clear kar rahe hain ya case escalate kiya jaye?"
        )
    else:
        amount_inr = float(inv.amount_inr)
        reason_spoken_map = {
            "GATEWAY_TIMEOUT": "ek technical gateway issue",
            "INSUFFICIENT_FUNDS": "insufficient funds",
            "MANDATE_DECLINE": "bank mandate decline",
            "EXPIRED_CARD": "card expiry",
            "DISPUTED_AMOUNT": "amount mismatch issue",
        }
        raw_reason = str(inv.failure_reason or "").strip()
        spoken_reason = reason_spoken_map.get(raw_reason, raw_reason.replace("_", " ").lower() or "payment processing technical issue")
        greeting_text = (
            f"Namaste {inv.customer.name} ji! Main {inv.merchant.name} se bol raha hoon. "
            f"Aapka ₹{amount_inr:,.0f} ka payment due hai jo {spoken_reason} ki wajah se complete nahi ho paya. "
            f"Kya aap isse aaj settle kar sakte hain?"
        )

    tts_res = await synthesize_speech(
        text=greeting_text,
        target_language_code="hi-IN",
        speaker="shubh",
    )

    return VoiceGreetingResponse(
        invoice_id=inv.id,
        greeting_text=greeting_text,
        audio_base64=tts_res.get("audio_base64", ""),
        audio_format=tts_res.get("audio_format", "audio/wav"),
        used_tts_fallback=tts_res.get("used_fallback", False),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Portfolio Analytics Summary Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummaryResponse,
    summary="Compute Live Portfolio Recovery Analytics & Preserved Margin",
)
async def get_analytics_summary(
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Calculates live aggregate recovery metrics across all ingested payment failure invoices.
    """
    stmt = select(Invoice).options(
        selectinload(Invoice.customer),
        selectinload(Invoice.merchant),
        selectinload(Invoice.recovery_events),
    )
    res = await db.execute(stmt)
    invoices = res.scalars().all()

    total_at_risk = 0.0
    total_recovered = 0.0
    margin_preserved = 0.0
    active_ptp = 0.0
    frozen_dispute = 0.0
    resolved_count = 0
    ptp_count = 0
    disputed_count = 0

    for inv in invoices:
        current_remaining = float(inv.amount_inr)
        recovered_amt = float(getattr(inv, "recovered_amount_inr", 0.0) or 0.0)
        orig_amt = float(getattr(inv, "original_amount_inr", 0.0) or 0.0) or (current_remaining + recovered_amt)

        # Active unpaid amounts are at risk
        if inv.status == "UNPAID":
            total_at_risk += current_remaining

        # Total recovered includes both full and partial payments
        if inv.status == "RESOLVED" and recovered_amt == 0.0:
            total_recovered += orig_amt
        else:
            total_recovered += recovered_amt

        latest = inv.recovery_events[-1] if inv.recovery_events else None
        state = latest.current_state if latest else "TRIGGERED"
        disc_offered = float(latest.discount_offered) if latest else 0.0

        if inv.status == "RESOLVED" or state == "RESOLVED":
            resolved_count += 1
            cap = float(inv.merchant.default_discount_cap) if inv.merchant else 0.10
            margin_preserved += orig_amt * max(0.0, cap - disc_offered)
        elif state in (State.PTP_ACTIVE, State.SPLIT_FIRST_HALF_PENDING):
            ptp_count += 1
            active_ptp += current_remaining
        elif state == State.FROZEN_DISPUTE or inv.status == "DISPUTED":
            disputed_count += 1
            frozen_dispute += current_remaining

    total_pool = total_recovered + total_at_risk
    recovery_rate = (total_recovered / total_pool * 100.0) if total_pool > 0 else 0.0

    return AnalyticsSummaryResponse(
        total_at_risk_inr=round(total_at_risk, 2),
        total_recovered_inr=round(total_recovered, 2),
        margin_preserved_inr=round(max(0.0, margin_preserved), 2),
        active_ptp_inr=round(active_ptp, 2),
        frozen_dispute_inr=round(frozen_dispute, 2),
        recovery_rate_pct=round(recovery_rate, 1),
        total_invoices=len(invoices),
        resolved_count=resolved_count,
        ptp_count=ptp_count,
        disputed_count=disputed_count,
    )


# 1. Moderate Synthetic Historical Baseline (Prior 30 days history)
SYNTHETIC_BASELINE = {
    "total_cases": 120,
    "total_at_risk": 1480000.0,
    "gross_recovered": 1065600.0,      # ~72% historical recovery rate
    "net_collected": 1005200.0,        # after concessions
    "discounts_granted": 60400.0,
    "anti_gaming_margin_saved": 42500.0,
    "by_reason": {
        "GATEWAY_TIMEOUT": {"total": 45, "resolved": 39, "amount": 540000.0},
        "INSUFFICIENT_FUNDS": {"total": 35, "resolved": 22, "amount": 420000.0},
        "MANDATE_DECLINE": {"total": 20, "resolved": 14, "amount": 260000.0},
        "EXPIRED_CARD": {"total": 12, "resolved": 10, "amount": 150000.0},
        "DISPUTED_AMOUNT": {"total": 8, "resolved": 2, "amount": 110000.0},
    },
    "concessions": {
        "Full Price (0%)": {"cases": 48, "volume": 580000.0},
        "Tier 1 (5%)": {"cases": 24, "volume": 288000.0},
        "Tier 2 (8%)": {"cases": 12, "volume": 144000.0},
        "Tier 3 (10%)": {"cases": 3, "volume": 53200.0},
    },
    "funnel": {
        "ingested": 120,
        "reminder_sent": 114,
        "call_connected": 88,
        "ptp_agreed": 54,
        "resolved": 87,
    },
}


@router.get(
    "/analytics/overview",
    response_model=AnalyticsOverviewResponse,
    summary="Get Hybrid Portfolio Recovery Analytics (Baseline + Live DB)",
)
async def get_analytics_overview(
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Computes unified recovery analytics blending a realistic historical baseline
    with live database metrics.
    """
    stmt = select(Invoice).options(
        selectinload(Invoice.customer),
        selectinload(Invoice.merchant),
        selectinload(Invoice.recovery_events),
    )
    res = await db.execute(stmt)
    live_invoices = res.scalars().all()

    live_total_cases = len(live_invoices)
    live_at_risk = 0.0
    live_recovered = 0.0
    live_discounts = 0.0
    live_margin_saved = 0.0

    live_reminders = 0
    live_calls = 0
    live_ptp_or_concessions = 0
    live_resolved = 0

    live_reason_stats: dict[str, dict[str, Any]] = {}
    live_concessions: dict[str, dict[str, Any]] = {
        "Full Price (0%)": {"cases": 0, "volume": 0.0},
        "Tier 1 (5%)": {"cases": 0, "volume": 0.0},
        "Tier 2 (8%)": {"cases": 0, "volume": 0.0},
        "Tier 3 (10%)": {"cases": 0, "volume": 0.0},
    }

    for inv in live_invoices:
        current_remaining = float(inv.amount_inr)
        recovered_amt = float(getattr(inv, "recovered_amount_inr", 0.0) or 0.0)
        orig_amt = float(getattr(inv, "original_amount_inr", 0.0) or 0.0) or (current_remaining + recovered_amt)

        if inv.status == "UNPAID":
            live_at_risk += current_remaining

        if inv.status == "RESOLVED" and recovered_amt == 0.0:
            live_recovered += orig_amt
        else:
            live_recovered += recovered_amt

        raw_reason = str(inv.failure_reason or "GATEWAY_TIMEOUT").strip()
        if raw_reason not in live_reason_stats:
            live_reason_stats[raw_reason] = {"total": 0, "resolved": 0, "amount": 0.0}
        live_reason_stats[raw_reason]["total"] += 1
        live_reason_stats[raw_reason]["amount"] += orig_amt

        latest_evt = inv.recovery_events[-1] if inv.recovery_events else None
        state = latest_evt.current_state if latest_evt else "TRIGGERED"
        disc = float(latest_evt.discount_offered) if latest_evt else 0.0

        # Track funnel
        events = inv.recovery_events or []
        has_rem = any(e.current_state in ("REMINDER_SENT", "LINK_SENT", "PTP_ACTIVE", "SPLIT_FIRST_HALF_PENDING", "TIER_1_DISCOUNT", "TIER_2_DISCOUNT", "TIER_3_FLOOR", "RESOLVED") for e in events)
        has_call = any(e.current_state in ("TIER_1_DISCOUNT", "TIER_2_DISCOUNT", "TIER_3_FLOOR", "PTP_ACTIVE", "SPLIT_OFFERED", "SPLIT_FIRST_HALF_PENDING", "ESCALATED_HUMAN") or "VOICE CALL" in (e.log_message or "") for e in events) or inv.call_pending
        has_ptp_conc = any(e.current_state in ("PTP_ACTIVE", "SPLIT_FIRST_HALF_PENDING", "TIER_1_DISCOUNT", "TIER_2_DISCOUNT", "TIER_3_FLOOR", "LINK_SENT") for e in events)
        is_res = inv.status == "RESOLVED" or state == "RESOLVED"

        if has_rem:
            live_reminders += 1
        if has_call:
            live_calls += 1
        if has_ptp_conc:
            live_ptp_or_concessions += 1
        if is_res:
            live_resolved += 1
            live_discounts += orig_amt * disc
            live_reason_stats[raw_reason]["resolved"] += 1

            cap = float(inv.merchant.default_discount_cap) if inv.merchant else 0.10
            live_margin_saved += orig_amt * max(0.0, cap - disc)

            if disc == 0.0:
                live_concessions["Full Price (0%)"]["cases"] += 1
                live_concessions["Full Price (0%)"]["volume"] += orig_amt
            elif disc <= 0.05:
                live_concessions["Tier 1 (5%)"]["cases"] += 1
                live_concessions["Tier 1 (5%)"]["volume"] += orig_amt * (1.0 - disc)
            elif disc <= 0.08:
                live_concessions["Tier 2 (8%)"]["cases"] += 1
                live_concessions["Tier 2 (8%)"]["volume"] += orig_amt * (1.0 - disc)
            else:
                live_concessions["Tier 3 (10%)"]["cases"] += 1
                live_concessions["Tier 3 (10%)"]["volume"] += orig_amt * (1.0 - disc)

    # Blend baseline + live
    total_cases = SYNTHETIC_BASELINE["total_cases"] + live_total_cases
    total_at_risk = SYNTHETIC_BASELINE["total_at_risk"] + live_at_risk
    gross_recovered = SYNTHETIC_BASELINE["gross_recovered"] + live_recovered
    discounts_granted = SYNTHETIC_BASELINE["discounts_granted"] + live_discounts
    net_collected = gross_recovered - discounts_granted
    recovery_rate = round((gross_recovered / total_at_risk * 100), 1) if total_at_risk > 0 else 0.0
    margin_preserved = SYNTHETIC_BASELINE["anti_gaming_margin_saved"] + live_margin_saved

    funnel = [
        AnalyticsFunnelStep(stage="Ingested Cases", count=total_cases),
        AnalyticsFunnelStep(stage="WhatsApp Reminders", count=SYNTHETIC_BASELINE["funnel"]["reminder_sent"] + live_reminders),
        AnalyticsFunnelStep(stage="Voice Calls Triggered", count=SYNTHETIC_BASELINE["funnel"]["call_connected"] + live_calls),
        AnalyticsFunnelStep(stage="PTP / Concession Agreed", count=SYNTHETIC_BASELINE["funnel"]["ptp_agreed"] + live_ptp_or_concessions),
        AnalyticsFunnelStep(stage="Resolved", count=SYNTHETIC_BASELINE["funnel"]["resolved"] + live_resolved),
    ]

    by_reason: list[AnalyticsReasonBreakdown] = []
    for reason_key, syn in SYNTHETIC_BASELINE["by_reason"].items():
        live_entry = live_reason_stats.get(reason_key, {"total": 0, "resolved": 0, "amount": 0.0})
        t_cases = syn["total"] + int(live_entry["total"])
        r_cases = syn["resolved"] + int(live_entry["resolved"])
        amt_val = syn["amount"] + float(live_entry["amount"])
        rec_pct = round((r_cases / t_cases * 100), 1) if t_cases > 0 else 0.0
        by_reason.append(
            AnalyticsReasonBreakdown(
                reason=reason_key.replace("_", " "),
                total_cases=t_cases,
                resolved_cases=r_cases,
                amount_at_risk=round(amt_val, 2),
                recovery_rate=rec_pct,
            )
        )

    concessions: list[AnalyticsConcessionItem] = []
    for tier_name, syn_tier in SYNTHETIC_BASELINE["concessions"].items():
        live_tier = live_concessions.get(tier_name, {"cases": 0, "volume": 0.0})
        concessions.append(
            AnalyticsConcessionItem(
                tier=tier_name,
                resolved_cases=syn_tier["cases"] + live_tier["cases"],
                volume_inr=round(syn_tier["volume"] + live_tier["volume"], 2),
            )
        )

    return AnalyticsOverviewResponse(
        summary=AnalyticsKPISummary(
            total_cases=total_cases,
            total_at_risk=round(total_at_risk, 2),
            gross_recovered=round(gross_recovered, 2),
            net_collected=round(net_collected, 2),
            discounts_granted=round(discounts_granted, 2),
            recovery_rate=recovery_rate,
            margin_preserved=round(margin_preserved, 2),
        ),
        funnel=funnel,
        by_reason=by_reason,
        concessions=concessions,
    )



# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Multi-Account Batch Simulation Runner
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/simulation/batch-run",
    response_model=BatchSimulationResponse,
    summary="Run Automated Multi-Account Recovery Batch Simulation",
)
async def run_batch_simulation(
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Generates and processes a batch of diverse Indian payment failure scenarios through the FSM.
    """
    # 1. Run seed script to generate sample accounts
    from seed import run_seed
    await run_seed()

    # 2. Query all invoices and simulate FSM progression
    stmt = select(Invoice).options(
        selectinload(Invoice.customer),
        selectinload(Invoice.merchant),
        selectinload(Invoice.recovery_events),
    )
    res = await db.execute(stmt)
    invoices = res.scalars().all()

    batch_recovered = 0.0
    resolved_cnt = 0
    ptp_cnt = 0
    disputed_cnt = 0

    for inv in invoices:
        sm = StateMachine(inv, db)
        state = sm.current_state
        amt = float(inv.amount_inr)

        if state == "TRIGGERED":
            # Auto diagnose
            await sm.transition(State.DIAGNOSED, log_message="Batch runner: Diagnosed failure root cause")
            state = sm.current_state

            if inv.failure_reason in ("GATEWAY_TIMEOUT", "EXPIRED_CARD"):
                await sm.transition(State.LINK_SENT, log_message="Batch runner: Dispatched UPI payment link")
                await sm.transition(State.RESOLVED, log_message="Batch runner: UPI payment received")
            elif inv.failure_reason == "MANDATE_DECLINE":
                deadline = datetime.now(timezone.utc) + timedelta(days=2)
                await sm.transition(State.PTP_ACTIVE, ptp_deadline=deadline, log_message="Batch runner: Debtor promised payment by Friday")
                await sm.transition(State.RESOLVED, log_message="Batch runner: Paid before PTP deadline")
            elif inv.failure_reason == "INSUFFICIENT_FUNDS":
                cap = Decimal(str(inv.merchant.default_discount_cap))
                months = inv.customer.consecutive_discount_months
                calc_res = calculator.calculate(cap, months, 1, Decimal(str(amt)))
                disc = float(calc_res.discount_rate) if calc_res.is_accessible else 0.0
                await sm.transition(State.TIER_1_DISCOUNT, discount_offered=disc, log_message=f"Batch runner: Applied Tier 1 discount {calc_res.discount_pct}")
                await sm.transition(State.RESOLVED, discount_offered=disc, log_message="Batch runner: Settled at Tier 1 discount")
            elif inv.failure_reason == "DISPUTED_AMOUNT":
                await sm.transition(State.FROZEN_DISPUTE, log_message="Batch runner: Invoice frozen due to billing dispute")

        # Recalculate stats
        latest = inv.recovery_events[-1] if inv.recovery_events else None
        curr = latest.current_state if latest else "TRIGGERED"
        if curr == "RESOLVED":
            resolved_cnt += 1
            batch_recovered += amt * (1.0 - float(latest.discount_offered if latest else 0.0))
        elif curr == "PTP_ACTIVE":
            ptp_cnt += 1
        elif curr == "FROZEN_DISPUTE":
            disputed_cnt += 1

    await db.commit()

    return BatchSimulationResponse(
        total_created=len(invoices),
        resolved_count=resolved_cnt,
        ptp_count=ptp_cnt,
        disputed_count=disputed_cnt,
        total_recovered_inr=round(batch_recovered, 2),
        summary_message=f"Successfully executed batch simulation for {len(invoices)} Indian payment failure accounts.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Compliance Audit Dossier Export Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/invoices/{invoice_id}/audit-export",
    response_model=AuditExportResponse,
    summary="Export Structured Regulatory Compliance Audit Dossier (JSON)",
)
async def export_audit_dossier(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Exports complete compliance dossier JSON for RBI / auditor verification.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    amt = float(inv.amount_inr)
    cap = float(inv.merchant.default_discount_cap)
    latest = inv.recovery_events[-1] if inv.recovery_events else None
    disc_offered = float(latest.discount_offered) if latest else 0.0
    net_payable = amt * (1.0 - disc_offered)

    timeline = []
    for i, evt in enumerate(inv.recovery_events):
        prev_s = inv.recovery_events[i - 1].current_state if i > 0 else "TRIGGERED"
        timeline.append({
            "event_id": str(evt.id),
            "timestamp": evt.timestamp.isoformat(),
            "previous_state": prev_s,
            "current_state": evt.current_state,
            "discount_offered_pct": f"{float(evt.discount_offered) * 100:.2f}%",
            "ptp_deadline": evt.ptp_deadline.isoformat() if evt.ptp_deadline else None,
            "audit_log_entry": evt.log_message,
        })

    return AuditExportResponse(
        dossier_id=uuid.uuid4(),
        exported_at=datetime.now(timezone.utc),
        merchant={
            "id": str(inv.merchant.id),
            "name": inv.merchant.name,
            "default_discount_cap": f"{cap * 100:.1f}%",
        },
        customer={
            "id": str(inv.customer.id),
            "name": inv.customer.name,
            "phone": inv.customer.phone,
            "email": inv.customer.email,
            "ltv_inr": float(inv.customer.ltv_inr),
            "consecutive_discount_months": inv.customer.consecutive_discount_months,
        },
        invoice={
            "id": str(inv.id),
            "amount_inr": amt,
            "due_date": inv.due_date.isoformat(),
            "status": inv.status,
            "failure_reason": inv.failure_reason,
            "created_at": inv.created_at.isoformat(),
        },
        diagnostic={
            "failure_reason": inv.failure_reason or "UNKNOWN",
            "category": "TECHNICAL_GATEWAY" if inv.failure_reason in ("GATEWAY_TIMEOUT", "MANDATE_DECLINE") else "FINANCIAL_DISPUTE",
            "recommended_action": "AUTOMATED_UPI_RETRY" if inv.failure_reason == "GATEWAY_TIMEOUT" else "CONCESSION_LADDER",
        },
        event_timeline=timeline,
        settlement={
            "final_status": inv.status,
            "gross_amount_inr": amt,
            "applied_discount_pct": f"{disc_offered * 100:.2f}%",
            "net_payable_inr": round(net_payable, 2),
            "margin_preserved_inr": round(amt * (cap - disc_offered), 2),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Autonomous Operations Endpoints
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — POST /api/invoices/{id}/skip-wait
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — POST /api/invoices/{id}/skip-wait
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/invoices/{invoice_id}/skip-wait",
    response_model=SkipWaitResponse,
    summary="Skip wait timer — advance this invoice by expiring its current deadline",
)
async def skip_wait(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Advances a single invoice by immediately expiring its current deadline and
    running the authoritative autonomous scheduler. Gracefully handles terminal
    and PTP states without 409 errors.
    """
    from app.scheduler import process_expired_deadlines
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    current = sm.current_state

    latest_evt = inv.recovery_events[-1] if inv.recovery_events else None
    disc_offered = Decimal(str(latest_evt.discount_offered)) if latest_evt else Decimal("0")

    if current in State.TERMINAL_STATES:
        return SkipWaitResponse(
            invoice_id=inv.id,
            previous_state=current,
            new_state=current,
            action_taken=f"No wait timer active for state {current}",
            trigger_call=False,
            discount_offered=disc_offered,
            net_payable_inr=None,
        )

    if current == State.PTP_ACTIVE:
        return SkipWaitResponse(
            invoice_id=inv.id,
            previous_state=current,
            new_state=current,
            action_taken="PTP active. Use Simulate Breach to resume recovery.",
            trigger_call=False,
            discount_offered=disc_offered,
            net_payable_inr=None,
        )

    prev = current
    now = datetime.now(timezone.utc)
    inv.next_action_due_at = now - timedelta(seconds=1)
    await db.commit()

    # Process expired deadline via the shared autonomous scheduler logic
    sched_res = await process_expired_deadlines()

    # Expire session cache so updated columns and events from scheduler are loaded
    db.expire_all()

    # Reload invoice
    fresh_inv = await _get_invoice_or_404(invoice_id, db)
    fresh_sm = StateMachine(fresh_inv, db)

    is_call_queued = fresh_inv.call_pending or str(fresh_inv.id) in sched_res.get("calls_queued", [])
    if is_call_queued:
        action_taken = "Voice call queued"
    else:
        action_taken = f"Advanced from {prev} to {fresh_sm.current_state}"

    fresh_evt = fresh_inv.recovery_events[-1] if fresh_inv.recovery_events else None
    fresh_disc = Decimal(str(fresh_evt.discount_offered)) if fresh_evt else Decimal("0")

    return SkipWaitResponse(
        invoice_id=fresh_inv.id,
        previous_state=prev,
        new_state=fresh_sm.current_state,
        action_taken=action_taken,
        trigger_call=is_call_queued,
        discount_offered=fresh_disc,
        net_payable_inr=None,
    )


@router.post(
    "/invoices/{invoice_id}/simulate-timeout",
    response_model=SimulateTimeoutResult,
    summary="Simulate PTP deadline breach / timeout",
)
async def simulate_timeout(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Simulates a PTP deadline breach or timeout, advancing the invoice
    according to autonomous policy.
    """
    from app.scheduler import process_expired_deadlines
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    current = sm.current_state
    prev = current

    now = datetime.now(timezone.utc)
    inv.next_action_due_at = now - timedelta(seconds=1)
    if current == State.PTP_ACTIVE:
        if inv.recovery_events and inv.recovery_events[-1].ptp_deadline:
            inv.recovery_events[-1].ptp_deadline = now - timedelta(seconds=1)
    await db.commit()

    sched_res = await process_expired_deadlines()
    db.expire_all()

    fresh_inv = await _get_invoice_or_404(invoice_id, db)
    fresh_sm = StateMachine(fresh_inv, db)

    latest_evt = fresh_inv.recovery_events[-1] if fresh_inv.recovery_events else None
    disc_val = Decimal(str(latest_evt.discount_offered)) if latest_evt else Decimal("0")
    gross_val = Decimal(str(fresh_inv.amount_inr))
    net_val = gross_val * (Decimal("1") - disc_val)

    return SimulateTimeoutResult(
        invoice_id=fresh_inv.id,
        simulated_scenario="PTP_BREACH" if prev == State.PTP_ACTIVE else "STAGE_TIMEOUT",
        previous_state=prev,
        new_state=fresh_sm.current_state,
        new_invoice_status=fresh_inv.status,
        discount_offered=disc_val,
        net_payable_inr=net_val,
        event_id=latest_evt.id if latest_evt else uuid.uuid4(),
        audit_log=latest_evt.log_message or f"Simulated breach: advanced {prev} -> {fresh_sm.current_state}",
    )


@router.post(
    "/invoices/{invoice_id}/acknowledge-call",
    response_model=AcknowledgeCallResponse,
    summary="Acknowledge call queue pickup",
)
async def acknowledge_call(
    invoice_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    inv = await _get_invoice_or_404(invoice_id, db)
    inv.call_pending = False
    await db.commit()
    return AcknowledgeCallResponse(
        invoice_id=inv.id,
        call_pending=False,
        message=f"Call acknowledged and dequeued from pending for {inv.customer.name}",
    )


@router.post("/invoices", response_model=InvoiceOut, summary="Manual case ingestion")
async def create_manual_invoice(
    payload: ManualInvoiceCreate,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Manually ingest a new recovery case directly into the autonomous pipeline."""
    from app.models import Merchant  # noqa: PLC0415
    res = await db.execute(select(Merchant).where(Merchant.name == payload.merchant_name))
    merchant = res.scalars().first()
    if not merchant:
        merchant = Merchant(
            id=uuid.uuid4(),
            name=payload.merchant_name,
            default_discount_cap=payload.merchant_cap,
        )
        db.add(merchant)
        await db.flush()

    customer = Customer(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        name=payload.customer_name,
        phone=payload.phone,
        ltv_inr=payload.ltv_inr,
        consecutive_discount_months=payload.consecutive_discount_months,
    )
    db.add(customer)
    await db.flush()

    now = datetime.now(timezone.utc)
    inv = Invoice(
        id=uuid.uuid4(),
        customer_id=customer.id,
        merchant_id=merchant.id,
        amount_inr=payload.amount_inr,
        status="UNPAID",
        failure_reason=payload.failure_reason,
        created_at=now,
        due_date=now + timedelta(days=7),
        next_action_due_at=now + timedelta(minutes=10),
        call_pending=False,
    )
    db.add(inv)
    await db.flush()

    evt = RecoveryEvent(
        id=uuid.uuid4(),
        invoice_id=inv.id,
        current_state=State.TRIGGERED,
        discount_offered=0.0,
        log_message=f"[MANUAL INGESTION] Case created for {payload.customer_name} (₹{payload.amount_inr:,.2f} - {payload.failure_reason}). Enqueued into autonomous pipeline.",
        timestamp=now,
    )
    db.add(evt)
    await db.commit()

    return await _get_invoice_or_404(inv.id, db)


@router.post("/invoices/{invoice_id}/override", response_model=InvoiceOut, summary="Operator exception override")
async def operator_override(
    invoice_id: uuid.UUID = Path(...),
    payload: OperatorOverrideRequest = ...,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Manually override a recovery case lifecycle with operator rationale logging."""
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)

    override_state_map = {
        "MANUAL_LINK": State.LINK_SENT,
        "SIMULATE_PTP": State.PTP_ACTIVE,
        "FORCE_DISCOUNT": State.TIER_1_DISCOUNT,
        "FLAG_DISPUTE": State.FROZEN_DISPUTE,
        "MARK_SETTLED": State.RESOLVED,
        "MARK_HALF_SETTLED": State.PTP_ACTIVE,
        "ESCALATE_HUMAN": State.ESCALATED_HUMAN,
    }

    target_state = override_state_map.get(payload.override_type, State.ESCALATED_HUMAN)
    ptp_deadline = (datetime.now(timezone.utc) + timedelta(days=3)) if payload.override_type in ("SIMULATE_PTP", "MARK_HALF_SETTLED") else None

    # If operator marked half settled, deduct 50% from amount_inr
    if payload.override_type == "MARK_HALF_SETTLED":
        inv.amount_inr = (Decimal(str(inv.amount_inr)) / Decimal("2")).quantize(Decimal("0.01"))
        inv.ptp_date = ptp_deadline

    discount_offered = 0.0
    if payload.override_type == "FORCE_DISCOUNT":
        cap = Decimal(str(inv.merchant.default_discount_cap))
        months = inv.customer.consecutive_discount_months
        gross = Decimal(str(inv.amount_inr))
        res = calculator.calculate(cap, months, 1, gross)
        discount_offered = float(res.discount_rate)

    log_msg = f"[OPERATOR OVERRIDE] Type: {payload.override_type} | Rationale: \"{payload.reason}\""

    try:
        await sm.transition(
            target_state=target_state,
            discount_offered=discount_offered,
            ptp_deadline=ptp_deadline,
            log_message=log_msg,
        )
    except Exception as exc:
        evt = RecoveryEvent(
            id=uuid.uuid4(),
            invoice_id=inv.id,
            current_state=target_state,
            discount_offered=discount_offered,
            ptp_deadline=ptp_deadline,
            log_message=f"{log_msg} (Override transition: {exc})",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(evt)
        sm._sync_invoice_status(target_state)

    inv.call_pending = False
    if target_state in State.TERMINAL_STATES:
        inv.next_action_due_at = None
    elif target_state == State.PTP_ACTIVE:
        inv.next_action_due_at = ptp_deadline
    else:
        inv.next_action_due_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    await db.commit()
    return await _get_invoice_or_404(inv.id, db)


@router.post("/invoices/{invoice_id}/record-payment", response_model=RecordPaymentResponse, summary="Record partial (50%) or full (100%) payment settlement")
async def record_payment_endpoint(
    invoice_id: uuid.UUID = Path(...),
    payload: RecordPaymentRequest = ...,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Record payment settlement:
    - FULL: Marks invoice fully resolved (amount_inr = 0), status = 'RESOLVED', state = RESOLVED.
    - HALF: Deducts 50% from amount_inr, schedules remaining 50% for PTP in 3 days, state = PTP_ACTIVE.
    """
    inv = await _get_invoice_or_404(invoice_id, db)
    sm = StateMachine(inv, db)
    prev_state = sm.current_state

    current_bal = Decimal(str(inv.amount_inr))
    current_recovered = Decimal(str(getattr(inv, "recovered_amount_inr", 0) or 0))
    if not inv.original_amount_inr:
        inv.original_amount_inr = current_bal + current_recovered

    now = datetime.now(timezone.utc)
    notes = payload.notes or ""

    if payload.payment_type == "FULL":
        amount_paid = current_bal
        remaining = Decimal("0.00")
        target_state = State.RESOLVED
        ptp_deadline = None
        inv.recovered_amount_inr = current_recovered + amount_paid
        inv.amount_inr = remaining
        inv.status = "RESOLVED"
        inv.call_pending = False
        inv.next_action_due_at = None

        log_msg = f"[PAYMENT RECEIVED] Full settlement confirmed (100% - ₹{amount_paid:,.2f}). Invoice marked RESOLVED. {notes}".strip()
        try:
            await sm.transition(target_state=State.RESOLVED, log_message=log_msg)
        except Exception:
            evt = RecoveryEvent(
                id=uuid.uuid4(),
                invoice_id=inv.id,
                current_state=State.RESOLVED,
                discount_offered=0.0,
                ptp_deadline=None,
                log_message=log_msg,
                timestamp=now,
            )
            db.add(evt)
            sm._sync_invoice_status(State.RESOLVED)

        msg = f"Full payment of ₹{amount_paid:,.2f} recorded. Case resolved."

    else:
        # HALF (50%) Payment
        amount_paid = (current_bal / Decimal("2")).quantize(Decimal("0.01"))
        remaining = (current_bal - amount_paid).quantize(Decimal("0.01"))
        target_state = State.PTP_ACTIVE
        ptp_deadline = now + timedelta(days=3)
        inv.recovered_amount_inr = current_recovered + amount_paid
        inv.amount_inr = remaining
        inv.status = "UNPAID"
        inv.ptp_date = ptp_deadline
        inv.call_pending = False
        inv.next_action_due_at = ptp_deadline

        log_msg = f"[PAYMENT RECEIVED] 50% Partial Payment Received (₹{amount_paid:,.2f}); 50% remaining (₹{remaining:,.2f}) due in 3 days ({ptp_deadline.strftime('%d %b %Y')}). {notes}".strip()
        try:
            await sm.transition(target_state=State.PTP_ACTIVE, ptp_deadline=ptp_deadline, log_message=log_msg)
        except Exception:
            evt = RecoveryEvent(
                id=uuid.uuid4(),
                invoice_id=inv.id,
                current_state=State.PTP_ACTIVE,
                discount_offered=0.0,
                ptp_deadline=ptp_deadline,
                log_message=log_msg,
                timestamp=now,
            )
            db.add(evt)
            sm._sync_invoice_status(State.PTP_ACTIVE)

        msg = f"50% partial payment of ₹{amount_paid:,.2f} recorded. Remaining ₹{remaining:,.2f} scheduled for {ptp_deadline.strftime('%d %b %Y')}."

    await db.commit()
    updated_inv = await _get_invoice_or_404(inv.id, db)

    return RecordPaymentResponse(
        invoice_id=inv.id,
        payment_type=payload.payment_type,
        amount_paid_inr=amount_paid,
        remaining_balance_inr=remaining,
        previous_state=prev_state,
        new_state=sm.current_state,
        new_status=updated_inv.status,
        ptp_deadline=ptp_deadline,
        message=msg,
        invoice=updated_inv,
    )


@router.post("/simulation/fast-forward", response_model=FastForwardResponse, summary="Fast forward autonomous agent simulation")
async def fast_forward_simulation(
    payload: FastForwardRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Advances simulation time by modifying stored deadlines and invoking the
    exact same autonomous background scheduler that handles real-time expiry.
    """
    from app.scheduler import process_expired_deadlines
    now = datetime.now(timezone.utc)

    query = select(Invoice).options(
        selectinload(Invoice.customer),
        selectinload(Invoice.merchant),
        selectinload(Invoice.recovery_events),
    ).where(Invoice.status == "UNPAID")

    if payload.invoice_id:
        query = query.where(Invoice.id == payload.invoice_id)

    result = await db.execute(query)
    invoices = result.scalars().all()

    is_all = payload.all_cases or payload.fast_forward_all
    for inv in invoices:
        if is_all:
            inv.next_action_due_at = now - timedelta(seconds=1)
            if inv.recovery_events and inv.recovery_events[-1].ptp_deadline:
                inv.recovery_events[-1].ptp_deadline = now - timedelta(seconds=1)
        else:
            if inv.next_action_due_at:
                inv.next_action_due_at = inv.next_action_due_at - timedelta(minutes=payload.minutes)
            else:
                inv.next_action_due_at = now - timedelta(seconds=1)
            if inv.recovery_events and inv.recovery_events[-1].ptp_deadline:
                inv.recovery_events[-1].ptp_deadline = inv.recovery_events[-1].ptp_deadline - timedelta(minutes=payload.minutes)

    await db.commit()

    # Process all now-expired deadlines through the single authoritative scheduler
    sched_res = await process_expired_deadlines()

    # Expire session cache to reflect newly committed changes from process_expired_deadlines()
    db.expire_all()

    # Fetch updated invoices to identify all queued calls
    res_fresh = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.customer), selectinload(Invoice.recovery_events))
        .where(Invoice.status == "UNPAID")
    )
    fresh_invoices = res_fresh.scalars().all()
    call_triggered_ids = [str(i.id) for i in fresh_invoices if i.call_pending]
    for cid in sched_res.get("calls_queued", []):
        if cid not in call_triggered_ids:
            call_triggered_ids.append(cid)

    return FastForwardResponse(
        advanced_minutes=payload.minutes,
        actions_triggered=len(sched_res.get("fired", [])) + len(sched_res.get("calls_queued", [])),
        events_updated=[{"action": item} for item in sched_res.get("fired", [])],
        call_triggered_ids=call_triggered_ids,
        message=f"Fast-forwarded {payload.minutes}m: {len(sched_res.get('fired', []))} autonomous actions fired. Calls queued: {len(call_triggered_ids)}.",
    )

