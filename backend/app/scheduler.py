"""
RecoveryAI — Autonomous Background Scheduler
============================================

Polls the database every 20 seconds and fires any invoice whose
`next_action_due_at` has expired.  This is the ONLY place where
autonomous state transitions originate — Fast Forward shares this
same code path by moving deadlines backward before calling it.

Autonomous progression:
  TRIGGERED     (deadline expired) → REMINDER_SENT  (+10 min deadline)
  REMINDER_SENT (deadline expired) → call_pending=True (frontend picks up)
  PTP_ACTIVE    (ptp_deadline expired) → TIER_1_DISCOUNT (+10 min deadline)

The scheduler is registered as an asyncio background task in main.py via
the FastAPI lifespan context manager.  It runs for the lifetime of the
server process and is robust to DB connection failures (it logs and retries).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.engine.gemini_service import generate_dunning_copy
from app.engine.state_machine import State, StateMachine
from app.models import Invoice

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 20
ACTION_WINDOW_SECONDS = 10  # 10 minute wait between stages


async def process_expired_deadlines() -> dict:
    """
    Core worker — processes all invoices with expired next_action_due_at.
    Returns a summary of what fired.
    """
    now = datetime.now(timezone.utc)
    fired: list[str] = []
    calls_queued: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            # Load all active invoices with expired deadlines
            result = await session.execute(
                select(Invoice)
                .options(
                    selectinload(Invoice.customer),
                    selectinload(Invoice.merchant),
                    selectinload(Invoice.recovery_events),
                )
                .where(
                    Invoice.next_action_due_at <= now,
                    Invoice.status == "UNPAID",
                    Invoice.call_pending.is_(False),
                )
            )
            invoices = result.scalars().all()

            for inv in invoices:
                sm = StateMachine(inv, session)
                current = sm.current_state

                try:
                    if current == State.TRIGGERED:
                        # ── Diagnose + send reminder in one step ───────────────
                        cap = float(inv.merchant.default_discount_cap) if inv.merchant else 0.10
                        months = inv.customer.consecutive_discount_months if inv.customer else 0
                        amount = float(inv.amount_inr)
                        cust_name = inv.customer.name if inv.customer else "Customer"
                        failure_reason = inv.failure_reason or "payment failure"

                        # Generate reminder copy via Gemini
                        try:
                            dunning_res = await generate_dunning_copy(
                                invoice_data={
                                    "amount_inr": amount,
                                    "merchant_cap": cap,
                                    "merchant_name": inv.merchant.name if inv.merchant else "DemoMerchant",
                                    "failure_reason": failure_reason,
                                },
                                customer_data={
                                    "name": cust_name,
                                    "consecutive_discount_months": months,
                                },
                                action_type="SOFT_REMINDER",
                            )
                            reminder_text = dunning_res.body
                        except Exception as e:
                            logger.warning("Gemini dunning copy failed for %s: %s", inv.id, e)
                            reminder_text = (
                                f"Dear {cust_name}, your payment of ₹{amount:,.0f} is pending. "
                                f"Please complete your payment at your earliest convenience."
                            )

                        log = (
                            f"[AUTO-SCHEDULER] Diagnosis complete. Failure: {failure_reason}. "
                            f"Reminder sent via WhatsApp/SMS.\nReminder: {reminder_text}"
                        )
                        await sm.transition(
                            target_state=State.REMINDER_SENT,
                            log_message=log,
                        )
                        # Set 10-minute deadline for call initiation
                        inv.next_action_due_at = now + timedelta(minutes=10)
                        fired.append(f"{inv.id}:TRIGGERED→REMINDER_SENT")
                        logger.info("🔔 Reminder sent for invoice %s (%s)", inv.id, cust_name)

                    elif current in (State.REMINDER_SENT, State.LINK_SENT):
                        # Check if this invoice had a prior PTP breach
                        has_prior_ptp_breached = any(
                            "PTP commitment deadline breached" in (e.log_message or "")
                            or "PTP Breach" in (e.log_message or "")
                            or "pichla payment promise breach" in (e.log_message or "")
                            or "PTP breached" in (e.log_message or "")
                            or "Post-PTP breach" in (e.log_message or "")
                            for e in inv.recovery_events
                        )
                        if current == State.LINK_SENT and has_prior_ptp_breached:
                            # ── Post-PTP breach 1-hour link window expired — escalate immediately ──
                            last_disc = float(inv.recovery_events[-1].discount_offered) if inv.recovery_events else 0.0
                            await sm.transition(
                                target_state=State.ESCALATED_HUMAN,
                                discount_offered=last_disc,
                                log_message=(
                                    "[AUTO-SCHEDULER] Post-PTP breach 1-hour payment deadline expired without payment. "
                                    "Escalated immediately to senior financial officer."
                                ),
                            )
                            inv.next_action_due_at = None
                            inv.call_pending = False
                            fired.append(f"{inv.id}:POST_PTP_BREACH_1HR_EXPIRED→ESCALATED_HUMAN")
                            logger.info("🚨 Post-PTP breach 1-hour expired: Escalated to human for invoice %s", inv.id)
                        else:
                            # ── Standard Reminder / Link window expired — queue voice call ──────────
                            inv.call_pending = True
                            inv.next_action_due_at = None
                            from app.models import RecoveryEvent
                            import uuid as _uuid
                            last_disc = float(inv.recovery_events[-1].discount_offered) if inv.recovery_events else 0.0
                            evt = RecoveryEvent(
                                id=_uuid.uuid4(),
                                invoice_id=inv.id,
                                current_state=current,
                                discount_offered=last_disc,
                                log_message=(
                                    f"[AUTO-SCHEDULER] {current} payment window expired. "
                                    "No payment received. Voice call queued."
                                ),
                                timestamp=now,
                            )
                            session.add(evt)
                            inv.recovery_events.append(evt)
                            calls_queued.append(str(inv.id))
                            logger.info("📞 Call queued for invoice %s (%s)", inv.id, current)

                    elif current in (State.TIER_1_DISCOUNT, State.TIER_2_DISCOUNT):
                        # ── Discount payment window expired — queue follow-up call ─────
                        inv.call_pending = True
                        inv.next_action_due_at = None
                        from app.models import RecoveryEvent
                        import uuid as _uuid
                        last_disc = float(inv.recovery_events[-1].discount_offered) if inv.recovery_events else 0.0
                        evt = RecoveryEvent(
                            id=_uuid.uuid4(),
                            invoice_id=inv.id,
                            current_state=current,
                            discount_offered=last_disc,
                            log_message=(
                                f"[AUTO-SCHEDULER] {current} concession payment window expired. "
                                "Follow-up voice negotiation call queued."
                            ),
                            timestamp=now,
                        )
                        session.add(evt)
                        inv.recovery_events.append(evt)
                        calls_queued.append(str(inv.id))
                        logger.info("📞 Follow-up call queued for invoice %s (%s)", inv.id, current)

                    elif current in (State.SPLIT_FIRST_HALF_PENDING, State.SPLIT_OFFERED):
                        # ── 1-hour split payment window expired without 1st half payment — queue follow-up call ──
                        inv.call_pending = True
                        inv.next_action_due_at = None
                        from app.models import RecoveryEvent
                        import uuid as _uuid
                        evt = RecoveryEvent(
                            id=_uuid.uuid4(),
                            invoice_id=inv.id,
                            current_state=current,
                            discount_offered=0.0,
                            log_message=(
                                "[AUTO-SCHEDULER] 1-hour Split Payment (1st 50%) window expired without payment. "
                                "Follow-up voice negotiation call queued."
                            ),
                            timestamp=now,
                        )
                        session.add(evt)
                        inv.recovery_events.append(evt)
                        calls_queued.append(str(inv.id))
                        logger.info("📞 Follow-up call queued for 1-hour split payment breach on invoice %s", inv.id)

                    elif current == State.TIER_3_FLOOR:
                        # ── Final floor payment window expired — escalate to human ──────
                        last_disc = float(inv.recovery_events[-1].discount_offered) if inv.recovery_events else 0.0
                        await sm.transition(
                            target_state=State.ESCALATED_HUMAN,
                            discount_offered=last_disc,
                            log_message=(
                                "[AUTO-SCHEDULER] Final floor discount window expired. "
                                "No settlement received. Escalated to senior financial officer."
                            ),
                        )
                        inv.next_action_due_at = None
                        inv.call_pending = False
                        fired.append(f"{inv.id}:TIER_3_FLOOR→ESCALATED_HUMAN")

                    elif current == State.PTP_ACTIVE:
                        # ── PTP deadline breached — directly queue outbound voice call to re-negotiate ─────────
                        inv.call_pending = True
                        inv.next_action_due_at = None
                        from app.models import RecoveryEvent
                        import uuid as _uuid
                        last_disc = float(inv.recovery_events[-1].discount_offered) if inv.recovery_events else 0.0
                        evt = RecoveryEvent(
                            id=_uuid.uuid4(),
                            invoice_id=inv.id,
                            current_state=current,
                            discount_offered=last_disc,
                            log_message=(
                                "[AUTO-SCHEDULER] PTP commitment deadline breached without payment. "
                                "Direct outbound recovery voice call queued to re-negotiate or escalate."
                            ),
                            timestamp=now,
                        )
                        session.add(evt)
                        inv.recovery_events.append(evt)
                        calls_queued.append(str(inv.id))
                        logger.info("📞 PTP Breach: Direct voice call queued for invoice %s (%s)", inv.id, inv.customer.name if inv.customer else "")

                    else:
                        # Unknown expired state — just clear deadline
                        inv.next_action_due_at = None

                except Exception as e:
                    logger.error("Scheduler error processing invoice %s: %s", inv.id, e)

            await session.commit()

    except Exception as e:
        logger.error("Scheduler DB error: %s", e)

    return {"fired": fired, "calls_queued": calls_queued}


async def run_scheduler() -> None:
    """
    Infinite async loop — the main scheduler task registered in main.py lifespan.
    Polls every POLL_INTERVAL_SECONDS seconds.
    """
    logger.info("🤖 RecoveryAI autonomous scheduler started (poll=%ds)", POLL_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            result = await process_expired_deadlines()
            if result["fired"] or result["calls_queued"]:
                logger.info(
                    "⚙️  Scheduler cycle: fired=%s calls_queued=%s",
                    result["fired"],
                    result["calls_queued"],
                )
        except Exception as e:
            logger.error("Scheduler cycle error: %s", e)
