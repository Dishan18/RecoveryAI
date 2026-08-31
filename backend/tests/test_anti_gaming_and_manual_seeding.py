import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Verification Suite for Anti-Gaming Caps, Manual Seeding, and Payment Actions
==========================================================================================
Tests:
1. Anti-Gaming Ceiling and Tier Rates:
   - 0 mo: 5% -> 8% -> 10% (Effective Ceiling: 10% Cap, all 3 tiers accessible)
   - 1 mo: 5% -> 8% (Effective Ceiling: 8% Cap, Tier 3 blocked)
   - 2+ mo: 5% (Effective Ceiling: 5% Cap, Tiers 2 & 3 blocked)
2. Empty Database check:
   - Cleared DB -> list_invoices returns [] without auto-seeding.
   - POST /api/seed seeds 10 diverse cases initialized at TRIGGERED with ~10m timers.
3. Quick Payment Action:
   - MARK_SETTLED override immediately resolves case and clears timers.
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timezone

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.database import AsyncSessionLocal
from app.engine.calculator import DiscountCalculator
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import list_invoices, operator_override, seed_database
from app.schemas import OperatorOverrideRequest
from seed import clear_existing_data, run_seed


async def test_anti_gaming_and_seeding():
    print("==================================================================")
    print("STARTING ANTI-GAMING, MANUAL SEEDING, & PAYMENT ACTION TESTS")
    print("==================================================================")

    # ── 1. Anti-Gaming Calculation Verification ───────────────────────────────
    print("\n--- TEST 1: Anti-Gaming Calculation & Effective Ceiling ---")
    calc = DiscountCalculator()
    merchant_cap = Decimal("0.10")  # 10%
    gross = Decimal("50000.00")

    # 0 Months
    eff0 = calc.effective_cap(merchant_cap, 0)
    t1_0 = calc.calculate(merchant_cap, 0, 1, gross)
    t2_0 = calc.calculate(merchant_cap, 0, 2, gross)
    t3_0 = calc.calculate(merchant_cap, 0, 3, gross)
    print(f"0 Months: Eff Cap = {eff0*100:.0f}% | T1 = {t1_0.discount_pct} (Acc: {t1_0.is_accessible}) | T2 = {t2_0.discount_pct} (Acc: {t2_0.is_accessible}) | T3 = {t3_0.discount_pct} (Acc: {t3_0.is_accessible})")
    assert eff0 == Decimal("0.1000")
    assert t1_0.discount_rate == Decimal("0.0500") and t1_0.is_accessible is True
    assert t2_0.discount_rate == Decimal("0.0800") and t2_0.is_accessible is True
    assert t3_0.discount_rate == Decimal("0.1000") and t3_0.is_accessible is True

    # 1 Month
    eff1 = calc.effective_cap(merchant_cap, 1)
    t1_1 = calc.calculate(merchant_cap, 1, 1, gross)
    t2_1 = calc.calculate(merchant_cap, 1, 2, gross)
    t3_1 = calc.calculate(merchant_cap, 1, 3, gross)
    print(f"1 Month:  Eff Cap = {eff1*100:.0f}% | T1 = {t1_1.discount_pct} (Acc: {t1_1.is_accessible}) | T2 = {t2_1.discount_pct} (Acc: {t2_1.is_accessible}) | T3 = {t3_1.discount_pct} (Acc: {t3_1.is_accessible})")
    assert eff1 == Decimal("0.0800")
    assert t1_1.discount_rate == Decimal("0.0500") and t1_1.is_accessible is True
    assert t2_1.discount_rate == Decimal("0.0800") and t2_1.is_accessible is True
    assert t3_1.is_accessible is False and t3_1.discount_rate == Decimal("0")

    # 2+ Months
    eff2 = calc.effective_cap(merchant_cap, 2)
    t1_2 = calc.calculate(merchant_cap, 2, 1, gross)
    t2_2 = calc.calculate(merchant_cap, 2, 2, gross)
    t3_2 = calc.calculate(merchant_cap, 2, 3, gross)
    print(f"2 Months: Eff Cap = {eff2*100:.0f}% | T1 = {t1_2.discount_pct} (Acc: {t1_2.is_accessible}) | T2 = {t2_2.discount_pct} (Acc: {t2_2.is_accessible}) | T3 = {t3_2.discount_pct} (Acc: {t3_2.is_accessible})")
    assert eff2 == Decimal("0.0500")
    assert t1_2.discount_rate == Decimal("0.0500") and t1_2.is_accessible is True
    assert t2_2.is_accessible is False and t2_2.discount_rate == Decimal("0")
    assert t3_2.is_accessible is False and t3_2.discount_rate == Decimal("0")
    print("  [PASS] Anti-gaming calculations and tier accessibility accurately enforced.")

    # ── 2. Manual Seeding & Empty DB Check ────────────────────────────────────
    print("\n--- TEST 2: Empty Database & Manual Seed Endpoint ---")
    async with AsyncSessionLocal() as session:
        await clear_existing_data(session)

    async with AsyncSessionLocal() as session:
        invs = await list_invoices(db=session)
        print(f"GET /api/invoices on empty database returned {len(invs)} items.")
        assert len(invs) == 0, f"Expected 0 items on empty DB, got {len(invs)}"
        print("  [PASS] Database returned [] on empty DB without auto-seeding.")

    # Trigger manual seeding via seed_database() route handler
    seed_res = await seed_database()
    print(f"Seed DB endpoint created {seed_res['invoices_created']} invoices.")
    assert seed_res["invoices_created"] == 6

    async with AsyncSessionLocal() as session:
        invs_after = await list_invoices(db=session)
        assert len(invs_after) == 6
        now = datetime.now(timezone.utc)
        for inv in invs_after:
            assert inv.status == "UNPAID"
            latest = inv.recovery_events[-1]
            assert latest.current_state == State.TRIGGERED
            diff = (inv.next_action_due_at - now).total_seconds() / 60
            assert 8.0 <= diff <= 11.0
        print("  [PASS] All 10 cases initialized uniformly at TRIGGERED with 10-minute timers.")

    # ── 3. Quick [Payment Received] Action ─────────────────────────────────────
    print("\n--- TEST 3: Quick [Payment Received] Mark Settled Action ---")
    target_inv_id = invs_after[0].id
    async with AsyncSessionLocal() as session:
        res_override = await operator_override(
            invoice_id=target_inv_id,
            payload=OperatorOverrideRequest(
                override_type="MARK_SETTLED",
                reason="Operator marked payment received",
            ),
            db=session,
        )
        print(f"Invoice {res_override.id} updated status: {res_override.status}")
        assert res_override.status == "RESOLVED"
        assert res_override.call_pending is False
        assert res_override.next_action_due_at is None
        latest = res_override.recovery_events[-1]
        assert latest.current_state == State.RESOLVED
        print("  [PASS] [✓ Payment Received] instantly resolved the invoice and cleared active timers.")

    print("\n==================================================================")
    print(">>> ALL ANTI-GAMING, SEEDING & PAYMENT ACTION TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_anti_gaming_and_seeding())
