import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
Kavya Patel Multi-Turn Voice Negotiation Ladder Verification
============================================================
Tests the exact 4-turn ladder requested:
1. Kavya Patel (₹52,000, 10% merchant cap)
2. Turn 1: "नहीं, मैं इसे आज सेटल नहीं कर पाऊंगा" -> 5.00% (₹49,400)
3. Turn 2: "नहीं, ये भी ज्यादा है" -> 8.00% (₹47,840)
4. Turn 3: "नहीं कर सकता" -> 10.00% (₹46,800)
5. Turn 4: "नहीं" -> Escalation speech + ESCALATED_HUMAN + trigger_auto_close=True
"""

import asyncio
import sys

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
from app.routes import voice_transcribe_and_reply
from seed import run_seed


async def test_kavya_negotiation_flow():
    print("==================================================================")
    print("TESTING KAVYA PATEL MULTI-TURN DISCOUNT NEGOTIATION LADDER")
    print("==================================================================")

    await run_migrations()
    await run_seed()

    async with AsyncSessionLocal() as session:
        # Find or create Kavya Patel
        invoices = (
            await session.execute(
                select(Invoice)
                .options(
                    selectinload(Invoice.customer),
                    selectinload(Invoice.merchant),
                    selectinload(Invoice.recovery_events),
                )
            )
        ).scalars().all()

        kavya = next((i for i in invoices if "kavya" in i.customer.name.lower()), None)
        if not kavya:
            merchant = (await session.execute(select(Merchant))).scalars().first()
            cust = Customer(
                name="Kavya Patel",
                phone="+919820011223",
                ltv_inr=95000,
                consecutive_discount_months=0,
                merchant_id=merchant.id,
            )
            session.add(cust)
            await session.flush()
            kavya = Invoice(
                customer_id=cust.id,
                merchant_id=merchant.id,
                amount_inr=52000,
                status="UNPAID",
                failure_reason="GATEWAY_ERROR",
            )
            session.add(kavya)
            await session.flush()
            evt = RecoveryEvent(invoice_id=kavya.id, current_state=State.REMINDER_SENT, discount_offered=0.0)
            session.add(evt)
            await session.commit()

        invoice_id = kavya.id
        print(f"Target Invoice: Kavya Patel | Gross: ₹{float(kavya.amount_inr):,.0f} | Cap: {float(kavya.merchant.default_discount_cap)*100:.0f}%")

    # Turn 1
    print("\n--- TURN 1: Initial Refusal ---")
    async with AsyncSessionLocal() as session:
        res1 = await voice_transcribe_and_reply(
            invoice_id=invoice_id,
            audio_file=None,
            text_fallback="नहीं, मैं इसे आज सेटल नहीं कर पाऊंगा",
            db=session,
        )
        print(f"Debtor: 'नहीं, मैं इसे आज सेटल नहीं कर पाऊंगा'")
        print(f"Agent Reply: \"{res1.agent_reply_text}\"")
        print(f"New State: {res1.new_state} | Applied Discount: {res1.applied_discount*100:.2f}%")
        assert res1.new_state == State.TIER_1_DISCOUNT, f"Expected TIER_1_DISCOUNT, got {res1.new_state}"
        assert res1.applied_discount == 0.05, f"Expected 0.05 discount, got {res1.applied_discount}"
        assert "5.00%" in res1.agent_reply_text or "5%" in res1.agent_reply_text
        assert "49,400" in res1.agent_reply_text
        print("  [PASS] Turn 1 correctly offered 5.00% (₹49,400).")

    # Turn 2
    print("\n--- TURN 2: Refusal of Tier 1 ---")
    async with AsyncSessionLocal() as session:
        res2 = await voice_transcribe_and_reply(
            invoice_id=invoice_id,
            audio_file=None,
            text_fallback="नहीं, ये भी ज्यादा है",
            db=session,
        )
        print(f"Debtor: 'नहीं, ये भी ज्यादा है'")
        print(f"Agent Reply: \"{res2.agent_reply_text}\"")
        print(f"New State: {res2.new_state} | Applied Discount: {res2.applied_discount*100:.2f}%")
        assert res2.new_state == State.TIER_2_DISCOUNT, f"Expected TIER_2_DISCOUNT, got {res2.new_state}"
        assert res2.applied_discount == 0.08, f"Expected 0.08 discount, got {res2.applied_discount}"
        assert "8.00%" in res2.agent_reply_text or "8%" in res2.agent_reply_text
        assert "47,840" in res2.agent_reply_text
        print("  [PASS] Turn 2 correctly offered 8.00% (₹47,840).")

    # Turn 3
    print("\n--- TURN 3: Refusal of Tier 2 ---")
    async with AsyncSessionLocal() as session:
        res3 = await voice_transcribe_and_reply(
            invoice_id=invoice_id,
            audio_file=None,
            text_fallback="नहीं कर सकता",
            db=session,
        )
        print(f"Debtor: 'नहीं कर सकता'")
        print(f"Agent Reply: \"{res3.agent_reply_text}\"")
        print(f"New State: {res3.new_state} | Applied Discount: {res3.applied_discount*100:.2f}%")
        assert res3.new_state == State.TIER_3_FLOOR, f"Expected TIER_3_FLOOR, got {res3.new_state}"
        assert res3.applied_discount == 0.10, f"Expected 0.10 discount, got {res3.applied_discount}"
        assert "10.00%" in res3.agent_reply_text or "10%" in res3.agent_reply_text
        assert "46,800" in res3.agent_reply_text
        print("  [PASS] Turn 3 correctly offered 10.00% final floor (₹46,800).")

    # Turn 4
    print("\n--- TURN 4: Refusal of Final Floor -> Senior Officer Escalation ---")
    async with AsyncSessionLocal() as session:
        res4 = await voice_transcribe_and_reply(
            invoice_id=invoice_id,
            audio_file=None,
            text_fallback="नहीं",
            db=session,
        )
        print(f"Debtor: 'नहीं'")
        print(f"Agent Reply: \"{res4.agent_reply_text}\"")
        print(f"New State: {res4.new_state} | Trigger Auto Close: {res4.trigger_auto_close}")
        assert res4.new_state == State.ESCALATED_HUMAN, f"Expected ESCALATED_HUMAN, got {res4.new_state}"
        assert res4.trigger_auto_close is True, f"Expected trigger_auto_close=True, got {res4.trigger_auto_close}"
        assert "senior financial officer" in res4.agent_reply_text.lower()
        print("  [PASS] Turn 4 escalated to ESCALATED_HUMAN with senior officer dialogue and auto-close trigger.")

    print("\n==================================================================")
    print(">>> KAVYA PATEL 4-TURN NEGOTIATION LADDER FULLY VERIFIED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_kavya_negotiation_flow())
