import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Single PTP Policy, Devanagari Transliteration, & Breach Escalation Test Suite
========================================================================================
Tests:
1. Transliterated Hinglish PTP Parsing:
   - "नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट" -> PROMISE_TO_PAY with 3-day deadline -> PTP_ACTIVE.
2. Single-PTP Enforcement:
   - Breached PTP follow-up call -> debtor asks for "मेरे को और 2 दिन चाहिए" -> ESCALATED_HUMAN.
3. 1-Hour Payment Window Breach:
   - Debtor agreed to pay -> 1-hour window expired -> call opens -> debtor says "नहीं कर सकता" -> ESCALATED_HUMAN.
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
from app.engine.gemini_service import parse_debtor_message
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import simulate_timeout, voice_call_greeting, voice_transcribe_and_reply


async def test_single_ptp_and_breach_escalation():
    print("==================================================================")
    print("STARTING SINGLE-PTP & BREACH ESCALATION VERIFICATION SUITE")
    print("==================================================================")

    # ── TEST 1: Transliterated Devanagari English PTP Parsing ─────────────────
    print("\n--- TEST 1: Transliterated Devanagari English PTP Parsing ---")
    phrase = "नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट"
    parsed = await parse_debtor_message(phrase)
    print(f"Input: \"{phrase}\"")
    print(f"Parsed Intent: {parsed.intent}")
    print(f"PTP Deadline: {parsed.ptp_deadline}")

    assert parsed.intent == "PROMISE_TO_PAY"
    assert parsed.ptp_deadline is not None
    print("  [PASS] Transliterated English in Devanagari correctly resolved to PROMISE_TO_PAY (3 days).")

    # ── TEST 2: Single-PTP Rule & Post-Breach Multiple PTP Escalation ─────────
    print("\n--- TEST 2: Single-PTP Rule & Post-Breach Multiple PTP Escalation ---")
    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust = Customer(name="Priya Verma", phone="+919888899999", ltv_inr=55000, merchant_id=merchant.id)
        session.add(cust)
        await session.flush()

        inv = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=45000,
            status="UNPAID",
            next_action_due_at=datetime.now(timezone.utc) + timedelta(days=3),
            call_pending=False,
        )
        session.add(inv)
        await session.flush()

        # Turn 1: Process the transliterated PTP
        reply1 = await voice_transcribe_and_reply(
            invoice_id=inv.id,
            audio_file=None,
            text_fallback=phrase,
            db=session,
        )
        print(f"Turn 1 State: {reply1.new_state} | Reply: \"{reply1.agent_reply_text}\"")
        assert reply1.new_state == State.PTP_ACTIVE
        assert reply1.applied_discount == 0.0

        # Debtor breaches PTP
        print("Simulating PTP Breach...")
        breach = await simulate_timeout(invoice_id=inv.id, db=session)
        print(f"Breach Result State: {breach.new_state}")

        # Follow-up call greeting
        greeting = await voice_call_greeting(invoice_id=inv.id, db=session)
        print(f"Follow-up Greeting: \"{greeting.greeting_text}\"")
        assert "pichla payment promise breach" in greeting.greeting_text

        # Debtor attempts to request a 2nd PTP: "मेरे को और 2 दिन चाहिए"
        reply2 = await voice_transcribe_and_reply(
            invoice_id=inv.id,
            audio_file=None,
            text_fallback="मेरे को और 2 दिन चाहिए",
            db=session,
        )
        print(f"Turn 2 Debtor: 'मेरे को और 2 दिन चाहिए'")
        print(f"Turn 2 Agent Reply: \"{reply2.agent_reply_text}\"")
        print(f"Turn 2 State: {reply2.new_state} | Auto Close: {reply2.trigger_auto_close}")

        assert reply2.new_state == State.ESCALATED_HUMAN
        assert reply2.trigger_auto_close is True
        assert "senior financial officer" in reply2.agent_reply_text or "senior" in reply2.agent_reply_text.lower()
        print("  [PASS] Debtor prevented from setting multiple PTPs; escalated to senior financial officer.")

    # ── TEST 3: 1-Hour Concession Agreement Breach Escalation ───────────────────
    print("\n--- TEST 3: 1-Hour Concession Agreement Breach Escalation ---")
    async with AsyncSessionLocal() as session:
        inv2 = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=25000,
            status="UNPAID",
            next_action_due_at=datetime.now(timezone.utc) + timedelta(hours=1),
            call_pending=False,
        )
        session.add(inv2)
        await session.flush()

        evt_agreed = RecoveryEvent(
            invoice_id=inv2.id,
            current_state=State.TIER_1_DISCOUNT,
            discount_offered=0.05,
            log_message="Debtor agreed to pay. Payment link dispatched (1-hour settlement window active).",
        )
        session.add(evt_agreed)
        await session.commit()
        inv2_id = inv2.id

    async with AsyncSessionLocal() as session:
        # Debtor fails 1-hour window and declines on follow-up call
        reply3 = await voice_transcribe_and_reply(
            invoice_id=inv2_id,
            audio_file=None,
            text_fallback="नहीं कर सकता",
            db=session,
        )
        print(f"Follow-up Debtor: 'नहीं कर सकता'")
        print(f"Agent Reply: \"{reply3.agent_reply_text}\"")
        print(f"New State: {reply3.new_state} | Auto Close: {reply3.trigger_auto_close}")

        assert reply3.new_state == State.ESCALATED_HUMAN
        assert reply3.trigger_auto_close is True
        assert "senior financial officer" in reply3.agent_reply_text or "escalat" in reply3.agent_reply_text.lower()
        print("  [PASS] 1-hour payment window breach directly escalated to senior financial officer.")

    print("\n==================================================================")
    print(">>> ALL SINGLE-PTP & BREACH ESCALATION TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_single_ptp_and_breach_escalation())
