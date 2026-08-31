import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Anti-Gaming 0% Discount for >2 Months & Seed Diversity Verification
================================================================================
"""

import asyncio
import sys
from decimal import Decimal

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.engine.calculator import calculator
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import voice_transcribe_and_reply
from seed import run_seed


async def test_excessive_discount_and_seed_diversity():
    print("==================================================================")
    print("STARTING 3+ MO 0% DISCOUNT CAP & SEED DIVERSITY TESTS")
    print("==================================================================")

    # ── TEST 1: Anti-Gaming Tier Multipliers & Accessibility ──────────────────
    print("\n--- TEST 1: Anti-Gaming Tier Multipliers & Accessibility ---")
    cap = Decimal("0.10")
    gross = Decimal("10000.00")

    # 0 Months (Clean History)
    r0_1 = calculator.calculate(cap, 0, 1, gross)
    r0_2 = calculator.calculate(cap, 0, 2, gross)
    r0_3 = calculator.calculate(cap, 0, 3, gross)
    print(f"0 Months: Eff Cap = {r0_1.effective_cap_pct} | T1 = {r0_1.discount_pct} (Acc: {r0_1.is_accessible}) | T2 = {r0_2.discount_pct} (Acc: {r0_2.is_accessible}) | T3 = {r0_3.discount_pct} (Acc: {r0_3.is_accessible})")
    assert r0_1.effective_cap == Decimal("0.10")
    assert r0_1.is_accessible and r0_2.is_accessible and r0_3.is_accessible

    # 1 Month (80% Ceiling)
    r1_1 = calculator.calculate(cap, 1, 1, gross)
    r1_2 = calculator.calculate(cap, 1, 2, gross)
    r1_3 = calculator.calculate(cap, 1, 3, gross)
    print(f"1 Month:  Eff Cap = {r1_1.effective_cap_pct} | T1 = {r1_1.discount_pct} (Acc: {r1_1.is_accessible}) | T2 = {r1_2.discount_pct} (Acc: {r1_2.is_accessible}) | T3 = {r1_3.discount_pct} (Acc: {r1_3.is_accessible})")
    assert r1_1.effective_cap == Decimal("0.08")
    assert r1_1.is_accessible and r1_2.is_accessible and not r1_3.is_accessible

    # 2 Months (50% Ceiling)
    r2_1 = calculator.calculate(cap, 2, 1, gross)
    r2_2 = calculator.calculate(cap, 2, 2, gross)
    r2_3 = calculator.calculate(cap, 2, 3, gross)
    print(f"2 Months: Eff Cap = {r2_1.effective_cap_pct} | T1 = {r2_1.discount_pct} (Acc: {r2_1.is_accessible}) | T2 = {r2_2.discount_pct} (Acc: {r2_2.is_accessible}) | T3 = {r2_3.discount_pct} (Acc: {r2_3.is_accessible})")
    assert r2_1.effective_cap == Decimal("0.05")
    assert r2_1.is_accessible and not r2_2.is_accessible and not r2_3.is_accessible

    # 3+ Months (NO Discount / 0% Cap)
    r3_1 = calculator.calculate(cap, 3, 1, gross)
    r3_2 = calculator.calculate(cap, 3, 2, gross)
    r3_3 = calculator.calculate(cap, 3, 3, gross)
    print(f"3 Months: Eff Cap = {r3_1.effective_cap_pct} | T1 = {r3_1.discount_pct} (Acc: {r3_1.is_accessible}) | T2 = {r3_2.discount_pct} (Acc: {r3_2.is_accessible}) | T3 = {r3_3.discount_pct} (Acc: {r3_3.is_accessible})")
    assert r3_1.effective_cap == Decimal("0.00")
    assert not r3_1.is_accessible and not r3_2.is_accessible and not r3_3.is_accessible
    assert r3_1.discount_rate == Decimal("0.00")

    # 4 Months (NO Discount / 0% Cap)
    r4_1 = calculator.calculate(cap, 4, 1, gross)
    print(f"4 Months: Eff Cap = {r4_1.effective_cap_pct} | T1 = {r4_1.discount_pct} (Acc: {r4_1.is_accessible})")
    assert r4_1.effective_cap == Decimal("0.00")
    assert not r4_1.is_accessible
    print("  [PASS] Anti-gaming calculations accurately enforce 0% cap for 3+ months history.")

    # ── TEST 2: Voice Negotiation on 3+ Months History ────────────────────────
    print("\n--- TEST 2: Voice Negotiation on 3+ Months History ---")
    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust = Customer(name="Rohan Mehta", phone="+919855566677", ltv_inr=95000, consecutive_discount_months=3, merchant_id=merchant.id)
        session.add(cust)
        await session.flush()

        inv = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=28000,
            status="UNPAID",
            call_pending=False,
        )
        session.add(inv)
        await session.flush()

        evt = RecoveryEvent(
            invoice_id=inv.id,
            current_state="TRIGGERED",
            discount_offered=0.0,
            log_message="Initial trigger for 3-month consecutive exploiter.",
        )
        session.add(evt)
        await session.commit()
        inv_id = inv.id

    async with AsyncSessionLocal() as session:
        reply = await voice_transcribe_and_reply(
            invoice_id=inv_id,
            audio_file=None,
            text_fallback="Paise kam kar do, thoda discount chahiye.",
            db=session,
        )
        print(f"Debtor: 'Paise kam kar do, thoda discount chahiye.'")
        print(f"Agent Reply: \"{reply.agent_reply_text}\"")
        print(f"New State: {reply.new_state} | Applied Discount: {reply.applied_discount}")

        # When discount cap is 0%, debtor cannot be granted discount -> escalates
        assert reply.new_state == State.ESCALATED_HUMAN
        assert reply.applied_discount == 0.0
        print("  [PASS] Customer with 3+ months discount history blocked from concessions and escalated.")

    # ── TEST 3: Seed Database Diversity ───────────────────────────────────────
    print("\n--- TEST 3: Seed Database Diversity ---")
    await run_seed()

    async with AsyncSessionLocal() as session:
        invoices = (await session.execute(select(Invoice))).scalars().all()
        customers = (await session.execute(select(Customer))).scalars().all()

        months_list = [c.consecutive_discount_months for c in customers]
        print(f"Seeded Customer Discount Months Distribution: {months_list}")

        has_0 = any(m == 0 for m in months_list)
        has_1 = any(m == 1 for m in months_list)
        has_2 = any(m == 2 for m in months_list)
        has_3plus = any(m >= 3 for m in months_list)

        assert has_0 and has_1 and has_2 and has_3plus, "Seed dataset missing one of the 4 anti-gaming tiers!"
        print("  [PASS] Database seeded with rich distribution across 0 mo, 1 mo, 2 mo, and 3+ mo discount histories.")

    print("\n==================================================================")
    print(">>> ALL 3+ MO 0% CAP & SEED DIVERSITY TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_excessive_discount_and_seed_diversity())
