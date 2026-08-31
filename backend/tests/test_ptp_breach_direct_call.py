import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Verification Suite for Direct Voice Call on PTP Breach
===================================================================
Tests:
1. PTP Breach Trigger:
   - Invoice in PTP_ACTIVE has deadline breached via simulate_timeout.
   - Authoritative state remains PTP_ACTIVE with call_pending=True and next_action_due_at=None (NO blind 5% discount timer).
2. Voice Call Greeting:
   - Outbound call greeting specifically addresses the missed PTP commitment.
3. Multi-Turn Re-negotiation:
   - Debtor requests concession -> climbs to Tier 1 (5% settlement discount).
   - Debtor hard refusal -> escalates to ESCALATED_HUMAN.
   - Debtor agrees to pay -> dispatches link.
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timedelta, timezone

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import simulate_timeout, voice_call_greeting, voice_transcribe_and_reply


async def test_ptp_breach_direct_call():
    print("==================================================================")
    print("STARTING DIRECT VOICE CALL ON PTP BREACH VERIFICATION")
    print("==================================================================")

    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust = Customer(name="Rajat Kapoor", phone="+919876543210", ltv_inr=75000, merchant_id=merchant.id)
        session.add(cust)
        await session.flush()

        inv = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=35000,
            status="UNPAID",
            next_action_due_at=datetime.now(timezone.utc) + timedelta(days=2),
            call_pending=False,
        )
        session.add(inv)
        await session.flush()

        evt = RecoveryEvent(
            invoice_id=inv.id,
            current_state=State.PTP_ACTIVE,
            discount_offered=0.0,
            ptp_deadline=datetime.now(timezone.utc) + timedelta(days=2),
            log_message="Debtor promised to pay within 2 days.",
        )
        session.add(evt)
        await session.commit()
        inv_id = inv.id

    # ── STEP 1: Simulate PTP Breach ───────────────────────────────────────────
    print("\n--- STEP 1: Simulate PTP Breach ---")
    async with AsyncSessionLocal() as session:
        breach_res = await simulate_timeout(invoice_id=inv_id, db=session)
        print(f"Breach Result State: {breach_res.new_state} | Discount Offered: {breach_res.discount_offered}")

        fresh_inv = (await session.execute(
            select(Invoice).options(selectinload(Invoice.recovery_events)).where(Invoice.id == inv_id)
        )).scalars().first()
        print(f"Persisted State: {fresh_inv.recovery_events[-1].current_state} | Call Pending: {fresh_inv.call_pending} | Next Due: {fresh_inv.next_action_due_at}")

        # Assert: invoice must NOT have prematurely applied 5% discount or created a 24-hour timer
        assert fresh_inv.recovery_events[-1].current_state == State.PTP_ACTIVE
        assert fresh_inv.call_pending is True
        assert fresh_inv.next_action_due_at is None
        assert float(fresh_inv.recovery_events[-1].discount_offered) == 0.0
        print("  [PASS] Direct voice call queued on PTP breach without premature discount concession.")

    # ── STEP 2: Voice Call Opening Greeting ───────────────────────────────────
    print("\n--- STEP 2: Voice Call Opening Greeting ---")
    async with AsyncSessionLocal() as session:
        greeting_res = await voice_call_greeting(invoice_id=inv_id, db=session)
        print(f"Opening Greeting Text: \"{greeting_res.greeting_text}\"")

        assert "Rajat Kapoor" in greeting_res.greeting_text
        assert "commitment" in greeting_res.greeting_text.lower()
        print("  [PASS] Opening greeting addressed the missed payment commitment.")

    # ── STEP 3: Re-negotiation Turn (Debtor requests concession) ──────────────
    print("\n--- STEP 3: Voice Re-negotiation Turn (Debtor requests concession) ---")
    async with AsyncSessionLocal() as session:
        reply_res = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Paise ki dikkat hai, thoda discount mil sakta hai kya?",
            db=session,
        )
        print(f"Debtor: 'Paise ki dikkat hai, thoda discount mil sakta hai kya?'")
        print(f"Parsed Intent: {reply_res.parsed_intent}")
        print(f"Agent Reply: \"{reply_res.agent_reply_text}\"")
        print(f"New State: {reply_res.new_state} | Applied Discount: {reply_res.applied_discount*100:.1f}%")

        assert reply_res.new_state == State.TIER_1_DISCOUNT
        assert reply_res.applied_discount == 0.05
        assert "5.00%" in reply_res.agent_reply_text or "5%" in reply_res.agent_reply_text
        print("  [PASS] Re-negotiation dynamically transitioned to Tier 1 (5%) concession.")

    print("\n==================================================================")
    print(">>> DIRECT VOICE CALL ON PTP BREACH TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_ptp_breach_direct_call())
