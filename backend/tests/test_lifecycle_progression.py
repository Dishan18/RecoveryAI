import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Comprehensive Lifecycle & Negotiation Progression Verification
=============================================================================
Tests the complete refined workflow:
1. Debtor says "Main yeh payment kar dunga" (no date) -> AGREED_TO_PAY -> sets 1-hour wait timer.
2. Skip Wait on TIER_1_DISCOUNT -> Triggers Call #2 (Opening greeting asks about 5% discount).
3. Debtor refuses -> Agent offers Tier 2 (8% discount).
4. Debtor refuses Tier 2 -> Agent offers Tier 3 Floor (10%).
5. Debtor refuses Tier 3 -> Agent escalates to Senior Financial Officer (ESCALATED_HUMAN).
6. Fast Forward All across multiple cases -> queues all calls in call_triggered_ids.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.engine.state_machine import State, StateMachine
from app.migrations import run_migrations
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import (
    fast_forward_simulation,
    skip_wait,
    voice_call_greeting,
    voice_transcribe_and_reply,
)
from app.schemas import FastForwardRequest
from seed import run_seed


async def test_full_lifecycle_progression():
    print("==================================================================")
    print("STARTING FULL REFINED LIFECYCLE & NEGOTIATION PROGRESSION TEST")
    print("==================================================================")

    await run_migrations()
    await run_seed()

    # Step 1: Create a test invoice in TIER_1_DISCOUNT
    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust = Customer(
            name="Vikram Sethi",
            phone="+919877665544",
            ltv_inr=80000,
            consecutive_discount_months=0,
            merchant_id=merchant.id,
        )
        session.add(cust)
        await session.flush()
        inv = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=50000,
            status="UNPAID",
            failure_reason="GATEWAY_ERROR",
        )
        session.add(inv)
        await session.flush()
        evt = RecoveryEvent(invoice_id=inv.id, current_state=State.TIER_1_DISCOUNT, discount_offered=0.05)
        session.add(evt)
        await session.commit()
        inv_id = inv.id
        print(f"Target Invoice Created: Vikram Sethi (₹50,000) in TIER_1_DISCOUNT.")

    # Step 2: Debtor says "Main yeh payment kar dunga" (No date -> AGREED_TO_PAY -> 1h wait timer)
    print("\n--- STEP 1: Debtor Accepts Concession Without Date ---")
    async with AsyncSessionLocal() as session:
        res1 = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Main yeh payment kar dunga",
            db=session,
        )
        print(f"Debtor: 'Main yeh payment kar dunga'")
        print(f"Parsed Intent: {res1.parsed_intent}")
        print(f"Agent Reply: \"{res1.agent_reply_text}\"")
        print(f"Trigger Auto Close: {res1.trigger_auto_close}")
        assert res1.parsed_intent == "AGREED_TO_PAY", f"Expected AGREED_TO_PAY, got {res1.parsed_intent}"
        assert res1.trigger_auto_close is True, "Expected trigger_auto_close=True"
        assert "1 ghante" in res1.agent_reply_text or "payment link" in res1.agent_reply_text.lower()
        print("  [PASS] Intent classified as AGREED_TO_PAY and 1-hour wait timer reply generated.")

    # Verify 1-hour deadline in DB
    async with AsyncSessionLocal() as session:
        inv_check = (await session.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv_check.next_action_due_at is not None, "Expected next_action_due_at to be set"
        diff_mins = (inv_check.next_action_due_at - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"Persisted next_action_due_at: {inv_check.next_action_due_at.isoformat()} (~{diff_mins:.1f} mins)")
        assert 50 <= diff_mins <= 65, f"Expected ~60 min deadline, got {diff_mins} mins"
        print("  [PASS] 1-hour payment window deadline persisted in DB.")

    # Step 3: Skip Wait on TIER_1_DISCOUNT -> Triggers Call #2
    print("\n--- STEP 2: Skip Wait on 1-hour Timer -> Enqueues Follow-up Call ---")
    async with AsyncSessionLocal() as session:
        skip_res = await skip_wait(invoice_id=inv_id, db=session)
        print(f"Skip Wait Action: {skip_res.action_taken} | Trigger Call: {skip_res.trigger_call}")
        assert skip_res.trigger_call is True, "Expected trigger_call=True on discount timer expiration"
        print("  [PASS] Skip Wait successfully queued follow-up voice call.")

    # Step 4: Call #2 Opening Greeting asks about 5% discount
    print("\n--- STEP 3: Call #2 Opening Greeting Verification ---")
    async with AsyncSessionLocal() as session:
        greet_res = await voice_call_greeting(invoice_id=inv_id, db=session)
        print(f"Call #2 Greeting Text: \"{greet_res.greeting_text}\"")
        assert "5.00%" in greet_res.greeting_text or "5%" in greet_res.greeting_text
        assert "receive nahi hua" in greet_res.greeting_text
        print("  [PASS] Call #2 Opening Greeting correctly referenced the 5.00% pending discount.")

    # Step 5: Debtor Refuses ("Nahi abhi nahi ho payega") -> Agent offers Tier 2 (8%)
    print("\n--- STEP 4: Debtor Refuses -> Climbs to Tier 2 (8%) ---")
    async with AsyncSessionLocal() as session:
        res2 = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Nahi abhi nahi ho payega, thoda aur concession do",
            db=session,
        )
        print(f"Debtor: 'Nahi abhi nahi ho payega, thoda aur concession do'")
        print(f"Agent Reply: \"{res2.agent_reply_text}\"")
        print(f"New State: {res2.new_state} | Applied Discount: {res2.applied_discount*100:.2f}%")
        assert res2.new_state == State.TIER_2_DISCOUNT, f"Expected TIER_2_DISCOUNT, got {res2.new_state}"
        assert res2.applied_discount == 0.08, f"Expected 0.08 discount, got {res2.applied_discount}"
        assert "8.00%" in res2.agent_reply_text or "8%" in res2.agent_reply_text
        assert "46,000" in res2.agent_reply_text  # 50,000 * 0.92 = 46,000
        print("  [PASS] Concession climbed to Tier 2 (8.00% / ₹46,000).")

    # Step 6: Debtor Refuses Tier 2 -> Agent offers Tier 3 Floor (10%)
    print("\n--- STEP 5: Debtor Refuses Tier 2 -> Climbs to Tier 3 Floor (10%) ---")
    async with AsyncSessionLocal() as session:
        res3 = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Nahi kar sakta",
            db=session,
        )
        print(f"Debtor: 'Nahi kar sakta'")
        print(f"Agent Reply: \"{res3.agent_reply_text}\"")
        print(f"New State: {res3.new_state} | Applied Discount: {res3.applied_discount*100:.2f}%")
        assert res3.new_state == State.TIER_3_FLOOR, f"Expected TIER_3_FLOOR, got {res3.new_state}"
        assert res3.applied_discount == 0.10, f"Expected 0.10 discount, got {res3.applied_discount}"
        assert "10.00%" in res3.agent_reply_text or "10%" in res3.agent_reply_text
        assert "45,000" in res3.agent_reply_text  # 50,000 * 0.90 = 45,000
        print("  [PASS] Concession climbed to Tier 3 Floor (10.00% / ₹45,000).")

    # Step 7: Debtor Refuses Tier 3 -> Senior Officer Escalation
    print("\n--- STEP 6: Debtor Refuses Final Floor -> Senior Officer Escalation ---")
    async with AsyncSessionLocal() as session:
        res4 = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Nahi",
            db=session,
        )
        print(f"Debtor: 'Nahi'")
        print(f"Agent Reply: \"{res4.agent_reply_text}\"")
        print(f"New State: {res4.new_state} | Trigger Auto Close: {res4.trigger_auto_close}")
        assert res4.new_state == State.ESCALATED_HUMAN, f"Expected ESCALATED_HUMAN, got {res4.new_state}"
        assert res4.trigger_auto_close is True
        assert "senior financial officer" in res4.agent_reply_text.lower()
        print("  [PASS] Escalated to Senior Financial Officer with auto-close.")

    # Step 8: Test Fast Forward All with Multiple Cases
    print("\n--- STEP 7: Fast Forward All Multi-Case Synchronization ---")
    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        c1 = Customer(name="Priya Sharma", phone="+919111222333", ltv_inr=50000, merchant_id=merchant.id)
        c2 = Customer(name="Rahul Verma", phone="+919444555666", ltv_inr=70000, merchant_id=merchant.id)
        session.add_all([c1, c2])
        await session.flush()

        inv1 = Invoice(customer_id=c1.id, merchant_id=merchant.id, amount_inr=25000, status="UNPAID")
        inv2 = Invoice(customer_id=c2.id, merchant_id=merchant.id, amount_inr=35000, status="UNPAID")
        session.add_all([inv1, inv2])
        await session.flush()

        evt1 = RecoveryEvent(invoice_id=inv1.id, current_state=State.REMINDER_SENT, discount_offered=0.0)
        evt2 = RecoveryEvent(invoice_id=inv2.id, current_state=State.REMINDER_SENT, discount_offered=0.0)
        session.add_all([evt1, evt2])
        await session.commit()

    async with AsyncSessionLocal() as session:
        ff_res = await fast_forward_simulation(FastForwardRequest(minutes=60, all_cases=True), session)
        print(f"Fast Forward Message: {ff_res.message}")
        print(f"Calls Triggered IDs: {ff_res.call_triggered_ids}")
        assert len(ff_res.call_triggered_ids) >= 2, f"Expected at least 2 call_triggered_ids, got {len(ff_res.call_triggered_ids)}"
        print(f"  [PASS] Fast Forward All triggered {len(ff_res.call_triggered_ids)} sequential voice calls.")

    print("\n==================================================================")
    print(">>> ALL REFINED LIFECYCLE & NEGOTIATION TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_full_lifecycle_progression())
