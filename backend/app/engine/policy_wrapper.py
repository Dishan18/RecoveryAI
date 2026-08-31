"""
RecoveryAI — Deterministic Policy Wrapper & Financial Authority Boundary
==========================================================================

This module is the SOLE authority for translating natural language intent into
financial actions, discount tiers, PTP commitments, dispute locks, and FSM transitions.

CORE ARCHITECTURAL RULE:
  Gemini is NOT financially authoritative.
  Gemini only interprets natural language intent.
  All numbers (discounts, net payables, caps, state transitions) originate here.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from app.engine.calculator import calculator, TIER_STATE_NAMES
from app.engine.state_machine import State, StateMachine
from app.schemas import AgentTurnDecision, DebtorIntentClassification

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models import Invoice

logger = logging.getLogger(__name__)


def parse_relative_ptp_date(date_str: str | None, base_date: datetime | None = None) -> datetime:
    """
    Deterministically resolve relative date strings (English / Hindi / Hinglish)
    into a validated future UTC datetime. STRICTLY CLAMPED TO A MAXIMUM 3-DAY WINDOW.
    """
    now = base_date or datetime.now(timezone.utc)
    max_ptp = now + timedelta(days=3)
    if not date_str:
        return max_ptp

    raw = date_str.lower().strip()

    # Direct day offsets
    if any(k in raw for k in ["kal", "कल", "tomorrow", "1 din", "1 day", "ek din", "एक दिन", "1 डे", "वन डे"]):
        return min(now + timedelta(days=1), max_ptp)
    if any(k in raw for k in ["parson", "parso", "परसों", "day after", "2 din", "2 days", "do din", "दो दिन", "2 डेज़", "2 डेज", "टू डेज़", "टू डेज", "टु डेज़"]):
        return min(now + timedelta(days=2), max_ptp)
    if any(k in raw for k in ["3 din", "3 days", "teen din", "तीन दिन", "3 डेज़", "3 डेज", "थ्री डेज़", "थ्री डेज"]):
        return max_ptp

    # Greater than 3 days (e.g. 5 days, 10 days, next week) -> STRICTLY CAPPED AT 3 DAYS
    if any(k in raw for k in ["5 din", "5 days", "paanch din", "पांच दिन", "पाँच दिन", "पाँच", "पांच", "5 डेज़", "next week", "agle hafte", "अगले हफ्ते", "1 week", "one week", "month"]):
        return max_ptp

    # Weekdays mapping
    days_of_week = {
        "monday": 0, "मंडे": 0, "somwar": 0, "सोमवार": 0,
        "tuesday": 1, "ट्यूजडे": 1, "ट्यूसडे": 1, "mangalwar": 1, "मंगलवार": 1,
        "wednesday": 2, "वेडनसडे": 2, "budhwar": 2, "बुधवार": 2,
        "thursday": 3, "थर्सडे": 3, "guruwar": 3, "गुरुवार": 3,
        "friday": 4, "फ्राइडे": 4, "shukrawar": 4, "शुक्रवार": 4,
        "saturday": 5, "सैटरडे": 5, "shaniwar": 5, "शनिवार": 5,
        "sunday": 6, "संडे": 6, "raviwar": 6, "रविवार": 6,
    }
    for name, target_weekday in days_of_week.items():
        if name in raw:
            current_weekday = now.weekday()
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            calculated = now + timedelta(days=days_ahead)
            return min(calculated, max_ptp)

    # Regex for N days (e.g. "4 days", "10 din", "पाँच दिन")
    match = re.search(r"(\d+)\s*(days?|din|दिन|डेज़|डेज)", raw)
    if match:
        n = int(match.group(1))
        return min(now + timedelta(days=max(n, 1)), max_ptp)

    # Default fallback: 3 days policy cap
    return max_ptp


async def execute_policy_turn(
    invoice: Invoice,
    intent_data: DebtorIntentClassification,
    db: AsyncSession,
) -> AgentTurnDecision:
    """
    Authoritative evaluation of debtor intent.
    Translates intent into strict deterministic financial and FSM actions.
    Enforces 3-day PTP cap and post-PTP breach escalation/1-hour payment rules.
    """
    sm = StateMachine(invoice, db)
    previous_state = sm.current_state or invoice.current_state or State.TRIGGERED
    merchant_cap = Decimal(str(invoice.merchant.default_discount_cap))
    consecutive_months = invoice.customer.consecutive_discount_months
    gross_amount = Decimal(str(invoice.amount_inr))
    current_tier = getattr(invoice, "current_discount_tier", 0) or 0

    # Retain or compute current authorized discount
    existing_discount_rate = Decimal(str(getattr(invoice, "applied_discount_rate", 0) or 0))
    authorized_discount_rate = existing_discount_rate
    authorized_net_amount = (gross_amount * (Decimal("1.0000") - authorized_discount_rate)).quantize(Decimal("0.01"))

    resulting_state = previous_state
    new_invoice_status = invoice.status
    action_executed = "No state transition"
    trigger_auto_close = False
    resolved_ptp_date: datetime | None = None
    dispute_reason_text: str | None = None

    intent = intent_data.intent

    # Check for prior PTP breach condition
    has_prior_ptp_breached = any(
        "PTP commitment deadline breached" in (e.log_message or "")
        or "PTP Breach" in (e.log_message or "")
        or "pichla payment promise breach" in (e.log_message or "")
        or "PTP breached" in (e.log_message or "")
        or "PTP breach" in (e.log_message or "")
        or "prior_ptp_breach" in (e.log_message or "")
        or (e.current_state == State.PTP_ACTIVE and e.ptp_deadline and e.ptp_deadline < e.timestamp)
        for e in invoice.recovery_events
    ) or (invoice.current_state == State.PTP_ACTIVE and getattr(invoice, "ptp_date", None) and invoice.ptp_date < datetime.now(timezone.utc))

    # ─────────────────────────────────────────────────────────────────────────
    # POST-PTP BREACH POLICY: No further PTP allowed! Pay within 1 hr or escalate.
    # ─────────────────────────────────────────────────────────────────────────
    if has_prior_ptp_breached:
        if intent in ("PAY_NOW", "REQUEST_PAYMENT_LINK"):
            if sm.can_transition(State.LINK_SENT):
                resulting_state = State.LINK_SENT
                await sm.transition(
                    target_state=State.LINK_SENT,
                    discount_offered=float(authorized_discount_rate),
                    log_message="Debtor agreed to pay post-PTP breach. 1-hour urgent payment link dispatched.",
                )
            invoice.next_action_due_at = datetime.now(timezone.utc) + timedelta(hours=1)
            action_executed = "Post-PTP breach payment link dispatched. Debtor has 1 hour to complete payment before escalation."
            trigger_auto_close = True
        else:
            # Any PTP request, refusal, discount request, or hesitation after PTP breach -> ESCALATE IMMEDIATELY
            resulting_state = State.ESCALATED_HUMAN
            invoice.call_pending = False
            invoice.next_action_due_at = None
            if sm.can_transition(State.ESCALATED_HUMAN):
                await sm.transition(
                    target_state=State.ESCALATED_HUMAN,
                    log_message="Debtor requested PTP or refused payment after prior PTP breach. Policy prohibits further extensions. Escalated to senior recovery officer.",
                )
            action_executed = "Debtor requested PTP or refused payment after prior PTP breach. Policy prohibits further extensions. Escalated to senior recovery officer."
            trigger_auto_close = True

        return AgentTurnDecision(
            intent=intent,
            confidence=intent_data.confidence,
            customer_stated_discount_pct=intent_data.customer_stated_discount_pct,
            authorized_discount_rate=authorized_discount_rate,
            authorized_net_amount=authorized_net_amount,
            previous_state=previous_state,
            resulting_state=resulting_state,
            new_invoice_status=invoice.status,
            action_executed=action_executed,
            trigger_auto_close=trigger_auto_close,
            ptp_date=resolved_ptp_date,
            dispute_reason=dispute_reason_text,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 1. PAY_NOW / Split Plan Acceptance — Customer indicates immediate payment
    # ─────────────────────────────────────────────────────────────────────────
    if intent == "PAY_NOW":
        if previous_state == State.SPLIT_OFFERED:
            target_ptp = datetime.now(timezone.utc) + timedelta(days=3)
            resolved_ptp_date = target_ptp
            invoice.ptp_date = target_ptp
            half_amt = (gross_amount / Decimal("2")).quantize(Decimal("0.01"))
            if sm.can_transition(State.PTP_ACTIVE):
                resulting_state = State.PTP_ACTIVE
                await sm.transition(
                    target_state=State.PTP_ACTIVE,
                    discount_offered=0.0,
                    ptp_deadline=target_ptp,
                    log_message=f"Debtor accepted Split Payment Plan. First 50% (₹{half_amt:,.0f}) due now, remaining 50% (₹{half_amt:,.0f}) committed by {target_ptp.strftime('%d %b %Y')}.",
                )
            action_executed = f"Debtor accepted Split Payment Plan (50% ₹{half_amt:,.0f} now, 50% in 3 days). Payment link dispatched."
            trigger_auto_close = True
        else:
            if sm.can_transition(State.LINK_SENT):
                resulting_state = State.LINK_SENT
                await sm.transition(
                    target_state=State.LINK_SENT,
                    discount_offered=float(authorized_discount_rate),
                    log_message="Debtor indicated immediate payment. Direct multi-rail payment link dispatched.",
                )
            action_executed = "Debtor indicated immediate payment. Multi-rail payment link dispatched."
            trigger_auto_close = True

    # ─────────────────────────────────────────────────────────────────────────
    # 2. PROMISE_TO_PAY — Negotiate commitment date (MAX 3 DAYS ALLOWED)
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "PROMISE_TO_PAY":
        if previous_state == State.SPLIT_OFFERED:
            target_ptp = datetime.now(timezone.utc) + timedelta(days=3)
            resolved_ptp_date = target_ptp
            invoice.ptp_date = target_ptp
            half_amt = (gross_amount / Decimal("2")).quantize(Decimal("0.01"))
            if sm.can_transition(State.PTP_ACTIVE):
                resulting_state = State.PTP_ACTIVE
                await sm.transition(
                    target_state=State.PTP_ACTIVE,
                    discount_offered=0.0,
                    ptp_deadline=target_ptp,
                    log_message=f"Debtor accepted Split Payment Plan. First 50% (₹{half_amt:,.0f}) due now, remaining 50% (₹{half_amt:,.0f}) committed by {target_ptp.strftime('%d %b %Y')}.",
                )
            action_executed = f"Debtor accepted Split Payment Plan (50% ₹{half_amt:,.0f} now, 50% in 3 days). Payment link dispatched."
            trigger_auto_close = True
        else:
            target_ptp = parse_relative_ptp_date(intent_data.ptp_date_extracted)
            # Strict clamp to maximum 3 days
            max_allowed = datetime.now(timezone.utc) + timedelta(days=3)
            target_ptp = min(target_ptp, max_allowed)
            resolved_ptp_date = target_ptp
            invoice.ptp_date = target_ptp

            if sm.can_transition(State.PTP_ACTIVE):
                resulting_state = State.PTP_ACTIVE
                await sm.transition(
                    target_state=State.PTP_ACTIVE,
                    discount_offered=float(authorized_discount_rate),
                    ptp_deadline=target_ptp,
                    log_message=f"Debtor committed to settle on {target_ptp.strftime('%d %b %Y')} (3-day policy cap applied).",
                )
            action_executed = f"Promise to pay recorded until {target_ptp.strftime('%d %b %Y')} (3-day policy cap applied). Automated dunning paused."
            trigger_auto_close = True

    # ─────────────────────────────────────────────────────────────────────────
    # 3. REQUEST_DISCOUNT — Customer asks for concession
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "REQUEST_DISCOUNT":
        # Next tier to evaluate
        next_tier = min(current_tier + 1, 3)
        if next_tier == 0:
            next_tier = 1

        calc_res = calculator.calculate(
            merchant_cap=merchant_cap,
            consecutive_discount_months=consecutive_months,
            tier=next_tier,
            gross_amount_inr=gross_amount,
        )

        if calc_res.is_accessible:
            target_state = TIER_STATE_NAMES.get(next_tier, State.TIER_1_DISCOUNT)
            authorized_discount_rate = calc_res.discount_rate
            authorized_net_amount = calc_res.net_payable_inr

            if sm.can_transition(target_state):
                resulting_state = target_state
                await sm.transition(
                    target_state=target_state,
                    discount_offered=float(authorized_discount_rate),
                    log_message=f"Customer requested concession (stated {intent_data.customer_stated_discount_pct or 'custom'}%) -> Policy Engine authorized Tier {next_tier} ({calc_res.discount_pct})",
                )
            action_executed = f"Policy Engine authorized Tier {next_tier} concession ({calc_res.discount_pct}). Net payable: ₹{authorized_net_amount:,.0f}."
        else:
            action_executed = f"Concession request blocked by Anti-Gaming Policy ({calc_res.audit_reason}). Net payable: ₹{gross_amount:,.0f}."

    # ─────────────────────────────────────────────────────────────────────────
    # 4. REFUSAL — Negotiation refusal ladder with Margin-Preserving Split Offer
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "REFUSAL":
        has_offered_split = (previous_state == State.SPLIT_OFFERED) or any(
            e.current_state == State.SPLIT_OFFERED for e in invoice.recovery_events
        )

        if current_tier == 0 and not has_offered_split:
            # ── 1st Refusal: Offer Split Payment Plan (50% now, 50% in 3 days) ───
            half_amt = (gross_amount / Decimal("2")).quantize(Decimal("0.01"))
            resulting_state = State.SPLIT_OFFERED
            if sm.can_transition(State.SPLIT_OFFERED):
                await sm.transition(
                    target_state=State.SPLIT_OFFERED,
                    discount_offered=0.0,
                    log_message=f"Refusal at full amount -> Offered Split Payment Plan (50% ₹{half_amt:,.0f} immediate, 50% ₹{half_amt:,.0f} in 3 days) to preserve margin.",
                )
            action_executed = f"Refused full payment -> Offered Split Payment Plan (50% ₹{half_amt:,.0f} immediate, 50% in 3 days) to preserve margin."

        elif current_tier == 0 and has_offered_split:
            # ── 2nd Refusal (Rejected Split): Proceed to Tier 1 Discount ─────────
            calc_res = calculator.calculate(merchant_cap, consecutive_months, 1, gross_amount)
            if calc_res.is_accessible and sm.can_transition(State.TIER_1_DISCOUNT):
                authorized_discount_rate = calc_res.discount_rate
                authorized_net_amount = calc_res.net_payable_inr
                resulting_state = State.TIER_1_DISCOUNT
                await sm.transition(
                    target_state=State.TIER_1_DISCOUNT,
                    discount_offered=float(authorized_discount_rate),
                    log_message=f"Refusal of Split Payment Plan -> Offered Tier 1 ({calc_res.discount_pct})",
                )
                action_executed = f"Refused split payment plan -> Offered Tier 1 concession ({calc_res.discount_pct})."
            else:
                resulting_state = State.ESCALATED_HUMAN
                invoice.call_pending = False
                if sm.can_transition(State.ESCALATED_HUMAN):
                    await sm.transition(
                        target_state=State.ESCALATED_HUMAN,
                        log_message=f"Refusal & concessions blocked by policy -> Escalated to human",
                    )
                action_executed = "Concessions blocked by policy -> Escalated to senior financial officer."
                trigger_auto_close = True

        elif current_tier == 1:
            # Refused Tier 1 -> Try Tier 2
            calc_res = calculator.calculate(merchant_cap, consecutive_months, 2, gross_amount)
            if calc_res.is_accessible and sm.can_transition(State.TIER_2_DISCOUNT):
                authorized_discount_rate = calc_res.discount_rate
                authorized_net_amount = calc_res.net_payable_inr
                resulting_state = State.TIER_2_DISCOUNT
                await sm.transition(
                    target_state=State.TIER_2_DISCOUNT,
                    discount_offered=float(authorized_discount_rate),
                    log_message=f"Refusal at Tier 1 -> Offered Tier 2 ({calc_res.discount_pct})",
                )
                action_executed = f"Refused Tier 1 -> Offered Tier 2 concession ({calc_res.discount_pct})."
            else:
                resulting_state = State.ESCALATED_HUMAN
                invoice.call_pending = False
                if sm.can_transition(State.ESCALATED_HUMAN):
                    await sm.transition(
                        target_state=State.ESCALATED_HUMAN,
                        log_message="Refusal at Tier 1 and Tier 2 blocked by anti-gaming penalty -> Escalated",
                    )
                action_executed = "Tier 2 blocked by abuse history -> Escalated to senior financial officer."
                trigger_auto_close = True

        elif current_tier == 2:
            # Refused Tier 2 -> Try Tier 3 Floor
            calc_res = calculator.calculate(merchant_cap, consecutive_months, 3, gross_amount)
            if calc_res.is_accessible and sm.can_transition(State.TIER_3_FLOOR):
                authorized_discount_rate = calc_res.discount_rate
                authorized_net_amount = calc_res.net_payable_inr
                resulting_state = State.TIER_3_FLOOR
                await sm.transition(
                    target_state=State.TIER_3_FLOOR,
                    discount_offered=float(authorized_discount_rate),
                    log_message=f"Refusal at Tier 2 -> Offered Tier 3 Final Floor ({calc_res.discount_pct})",
                )
                action_executed = f"Refused Tier 2 -> Offered Tier 3 Final Floor ({calc_res.discount_pct})."
            else:
                resulting_state = State.ESCALATED_HUMAN
                invoice.call_pending = False
                if sm.can_transition(State.ESCALATED_HUMAN):
                    await sm.transition(
                        target_state=State.ESCALATED_HUMAN,
                        log_message="Refusal at Tier 2 and Tier 3 blocked -> Escalated",
                    )
                action_executed = "Tier 3 blocked by abuse history -> Escalated to senior financial officer."
                trigger_auto_close = True

        elif current_tier >= 3:
            # Final Floor Refusal -> Hard Escalation
            resulting_state = State.ESCALATED_HUMAN
            invoice.call_pending = False
            if sm.can_transition(State.ESCALATED_HUMAN):
                await sm.transition(
                    target_state=State.ESCALATED_HUMAN,
                    log_message="Debtor refused Tier 3 Final Floor concession -> Escalated to senior recovery team",
                )
            action_executed = "Refused final concession floor -> Escalated to senior financial officer."
            trigger_auto_close = True

    # ─────────────────────────────────────────────────────────────────────────
    # 5. DISPUTE — Immediate collection freeze
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "DISPUTE":
        dispute_text = intent_data.dispute_reason or "Debtor disputed invoice amount or service deliverables"
        dispute_reason_text = dispute_text
        invoice.status = "DISPUTED"
        invoice.call_pending = False
        resulting_state = State.FROZEN_DISPUTE
        new_invoice_status = "DISPUTED"

        if sm.can_transition(State.FROZEN_DISPUTE):
            await sm.transition(
                target_state=State.FROZEN_DISPUTE,
                log_message=f"Debtor dispute flagged: {dispute_text}",
            )
        action_executed = "Debtor raised formal billing dispute. Collection frozen and routed to audit."
        trigger_auto_close = True

    # ─────────────────────────────────────────────────────────────────────────
    # 6. TECHNICAL_PROBLEM — Gateway bounce, retry link (ZERO discount)
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "TECHNICAL_PROBLEM":
        if sm.can_transition(State.LINK_SENT):
            resulting_state = State.LINK_SENT
            await sm.transition(
                target_state=State.LINK_SENT,
                discount_offered=float(authorized_discount_rate),
                log_message="Technical gateway failure reported. Generated alternate multi-rail link (zero concession).",
            )
        action_executed = "Technical gateway issue reported. Alternate multi-rail payment link generated (zero concession)."
        trigger_auto_close = False

    # ─────────────────────────────────────────────────────────────────────────
    # 7. REQUEST_PAYMENT_LINK — Send link (ZERO discount)
    # ─────────────────────────────────────────────────────────────────────────
    elif intent == "REQUEST_PAYMENT_LINK":
        if sm.can_transition(State.LINK_SENT):
            resulting_state = State.LINK_SENT
            await sm.transition(
                target_state=State.LINK_SENT,
                discount_offered=float(authorized_discount_rate),
                log_message="Debtor requested direct payment link.",
            )
        action_executed = "Payment link requested. Direct payment URL dispatched via SMS and WhatsApp."
        trigger_auto_close = False

    # ─────────────────────────────────────────────────────────────────────────
    # 8. UNKNOWN — Safe clarification
    # ─────────────────────────────────────────────────────────────────────────
    else:
        action_executed = "Debtor statement unclear. Clarification requested."
        trigger_auto_close = False

    return AgentTurnDecision(
        intent=intent,
        confidence=intent_data.confidence,
        customer_stated_discount_pct=intent_data.customer_stated_discount_pct,
        authorized_discount_rate=authorized_discount_rate,
        authorized_net_amount=authorized_net_amount,
        previous_state=previous_state,
        resulting_state=resulting_state,
        new_invoice_status=new_invoice_status,
        action_executed=action_executed,
        trigger_auto_close=trigger_auto_close,
        ptp_date=resolved_ptp_date,
        dispute_reason=dispute_reason_text,
    )

