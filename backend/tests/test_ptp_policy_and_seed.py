import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Verification Suite for Multi-Day PTP, 3-Day Policy, & Seed Uniformity
===================================================================================
Tests:
1. Seed database -> all 10 scenarios initialize uniformly at TRIGGERED with 10-min live countdown timers.
2. 3-day PTP ("मैं तीन दिन में पेमेंट कर दूंगा") -> PROMISE_TO_PAY with 3-day deadline + auto-close.
3. Extended PTP ("मैं अगले हफ्ते पेमेंट करूँगा") -> PTP_EXCEEDS_POLICY + 3-day policy counter-offer.
4. Accept 3-day policy ("Haan theek hai") -> Transition to PTP_ACTIVE with 3-day deadline + auto-close.
5. Reject 3-day policy ("Nahi 3 din mein nahi ho payega") -> Transition to ESCALATED_HUMAN + auto-close.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.engine.state_machine import State, StateMachine
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import voice_transcribe_and_reply
from seed import run_seed


async def test_ptp_policy_and_seed():
    print("==================================================================")
    print("STARTING PTP MULTI-DAY, 3-DAY POLICY, & SEED UNIFORMITY TEST")
    print("==================================================================")

    # ── 1. Seed Realignment Verification ──────────────────────────────────────
    print("\n--- TEST 1: Database Seed Realignment to Initial Breach ---")
    seed_res = await run_seed()
    print(f"Seed complete: {seed_res['invoices_created']} invoices created.")

    async with AsyncSessionLocal() as session:
        invoices = (await session.execute(
            select(Invoice).options(selectinload(Invoice.recovery_events))
        )).scalars().all()

        assert len(invoices) == 10, f"Expected 10 invoices, got {len(invoices)}"
        now = datetime.now(timezone.utc)

        for inv in invoices:
            assert inv.status == "UNPAID", f"Invoice {inv.id} status was {inv.status}, expected UNPAID"
            assert inv.call_pending is False, f"Invoice {inv.id} call_pending was True, expected False"
            assert inv.next_action_due_at is not None, f"Invoice {inv.id} next_action_due_at is None"
            diff_mins = (inv.next_action_due_at - now).total_seconds() / 60
            assert 8.0 <= diff_mins <= 11.0, f"Invoice {inv.id} timer was {diff_mins:.1f}m, expected ~10m"

            latest_evt = inv.recovery_events[-1] if inv.recovery_events else None
            assert latest_evt is not None
            assert latest_evt.current_state == State.TRIGGERED, f"Invoice {inv.id} state was {latest_evt.current_state}, expected TRIGGERED"
            assert float(latest_evt.discount_offered) == 0.0, f"Invoice {inv.id} discount was {latest_evt.discount_offered}, expected 0.0"

        print("  [PASS] All 10 seeded invoices uniformly initialized at TRIGGERED with active ~10-minute timers.")

    # ── 2. Direct 3-Day PTP Commitment ("मैं तीन दिन में पेमेंट कर दूंगा") ────
    print("\n--- TEST 2: Direct 3-Day PTP Commitment ---")
    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust1 = Customer(name="Aman Gupta", phone="+919812345678", ltv_inr=90000, merchant_id=merchant.id)
        session.add(cust1)
        await session.flush()
        inv1 = Invoice(customer_id=cust1.id, merchant_id=merchant.id, amount_inr=42000, status="UNPAID")
        session.add(inv1)
        await session.flush()
        evt1 = RecoveryEvent(invoice_id=inv1.id, current_state=State.TRIGGERED, discount_offered=0.0)
        session.add(evt1)
        await session.commit()
        inv1_id = inv1.id

    async with AsyncSessionLocal() as session:
        res_ptp3 = await voice_transcribe_and_reply(
            invoice_id=inv1_id,
            audio_file=None,
            text_fallback="मैं तीन दिन में पेमेंट कर दूंगा",
            db=session,
        )
        print(f"Debtor: 'मैं तीन दिन में पेमेंट कर दूंगा'")
        print(f"Parsed Intent: {res_ptp3.parsed_intent}")
        print(f"Agent Reply: \"{res_ptp3.agent_reply_text}\"")
        print(f"New State: {res_ptp3.new_state} | Auto-Close: {res_ptp3.trigger_auto_close}")

        assert res_ptp3.parsed_intent == "PROMISE_TO_PAY"
        assert res_ptp3.new_state == State.PTP_ACTIVE
        assert res_ptp3.trigger_auto_close is True
        assert "3 din" in res_ptp3.agent_reply_text or "3 दिन" in res_ptp3.agent_reply_text
        print("  [PASS] Direct 3-day PTP parsed, transitioned to PTP_ACTIVE with auto-close.")

    # ── 3. Extended PTP Request + Accept 3-Day Counter-Offer ───────────────────
    print("\n--- TEST 3: Extended PTP (>3 Days) Counter-Offer & Acceptance ---")
    async with AsyncSessionLocal() as session:
        cust2 = Customer(name="Pooja Mehta", phone="+919823456789", ltv_inr=120000, merchant_id=merchant.id)
        session.add(cust2)
        await session.flush()
        inv2 = Invoice(customer_id=cust2.id, merchant_id=merchant.id, amount_inr=65000, status="UNPAID")
        session.add(inv2)
        await session.flush()
        evt2 = RecoveryEvent(invoice_id=inv2.id, current_state=State.TRIGGERED, discount_offered=0.0)
        session.add(evt2)
        await session.commit()
        inv2_id = inv2.id

    # Turn 1: Debtor requests next week / 5 days
    async with AsyncSessionLocal() as session:
        res_ext = await voice_transcribe_and_reply(
            invoice_id=inv2_id,
            audio_file=None,
            text_fallback="मैं अगले हफ्ते / 5 दिन बाद करूँगा",
            db=session,
        )
        print(f"Debtor: 'मैं अगले हफ्ते / 5 दिन बाद करूँगा'")
        print(f"Parsed Intent: {res_ext.parsed_intent}")
        print(f"Agent Reply: \"{res_ext.agent_reply_text}\"")
        print(f"Auto-Close: {res_ext.trigger_auto_close}")

        assert "maximum 3 din" in res_ext.agent_reply_text
        assert res_ext.trigger_auto_close is False
        print("  [PASS] Extended PTP countered with 3-day maximum policy, modal stayed open.")

    # Turn 2: Debtor accepts 3-day policy ("Haan theek hai")
    async with AsyncSessionLocal() as session:
        res_accept = await voice_transcribe_and_reply(
            invoice_id=inv2_id,
            audio_file=None,
            text_fallback="Haan 3 din mein theek hai",
            db=session,
        )
        print(f"Debtor: 'Haan 3 din mein theek hai'")
        print(f"Agent Reply: \"{res_accept.agent_reply_text}\"")
        print(f"New State: {res_accept.new_state} | Auto-Close: {res_accept.trigger_auto_close}")

        assert res_accept.new_state == State.PTP_ACTIVE
        assert res_accept.trigger_auto_close is True
        assert "3 din" in res_accept.agent_reply_text
        print("  [PASS] Debtor accepted 3-day policy -> transitioned to PTP_ACTIVE with auto-close.")

    # ── 4. Extended PTP Request + Reject 3-Day Counter-Offer ───────────────────
    print("\n--- TEST 4: Extended PTP (>3 Days) Counter-Offer & Rejection ---")
    async with AsyncSessionLocal() as session:
        cust3 = Customer(name="Deepak Rao", phone="+919834567890", ltv_inr=85000, merchant_id=merchant.id)
        session.add(cust3)
        await session.flush()
        inv3 = Invoice(customer_id=cust3.id, merchant_id=merchant.id, amount_inr=29000, status="UNPAID")
        session.add(inv3)
        await session.flush()
        evt3 = RecoveryEvent(invoice_id=inv3.id, current_state=State.TRIGGERED, discount_offered=0.0)
        session.add(evt3)
        await session.commit()
        inv3_id = inv3.id

    # Turn 1: Debtor requests 10 days
    async with AsyncSessionLocal() as session:
        res_ext2 = await voice_transcribe_and_reply(
            invoice_id=inv3_id,
            audio_file=None,
            text_fallback="10 din baad payment karunga",
            db=session,
        )
        print(f"Debtor: '10 din baad payment karunga'")
        print(f"Agent Reply: \"{res_ext2.agent_reply_text}\"")
        assert "maximum 3 din" in res_ext2.agent_reply_text

    # Turn 2: Debtor refuses 3 days ("Nahi 3 din mein nahi ho payega")
    async with AsyncSessionLocal() as session:
        res_reject = await voice_transcribe_and_reply(
            invoice_id=inv3_id,
            audio_file=None,
            text_fallback="Nahi 3 din mein nahi ho payega",
            db=session,
        )
        print(f"Debtor: 'Nahi 3 din mein nahi ho payega'")
        print(f"Agent Reply: \"{res_reject.agent_reply_text}\"")
        print(f"New State: {res_reject.new_state} | Auto-Close: {res_reject.trigger_auto_close}")

        assert res_reject.new_state == State.ESCALATED_HUMAN
        assert res_reject.trigger_auto_close is True
        assert "senior officer" in res_reject.agent_reply_text.lower()
        print("  [PASS] Debtor rejected 3-day policy -> escalated to ESCALATED_HUMAN with auto-close.")

    print("\n==================================================================")
    print(">>> ALL PTP MULTI-DAY & SEED UNIFORMITY TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_ptp_policy_and_seed())
