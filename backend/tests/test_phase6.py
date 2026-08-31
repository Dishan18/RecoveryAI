import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Phase 6 Autonomous Agent Operations Verification Script
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.database import AsyncSessionLocal
from app.migrations import run_migrations
from app.routes import (
    create_manual_invoice,
    fast_forward_simulation,
    list_invoices,
    operator_override,
)
from app.schemas import (
    FastForwardRequest,
    ManualInvoiceCreate,
    OperatorOverrideRequest,
)


async def main():
    print("==================================================================")
    print("Phase 6 Autonomous Agent Operations Verification")
    print("==================================================================")
    
    await run_migrations()
    
    async with AsyncSessionLocal() as session:
        # Test 1: Auto-seed on GET /api/invoices
        print("\n1. Testing GET /api/invoices (auto-seed)...")
        invoices = await list_invoices(session)
        print(f"  [PASS] Retrieved {len(invoices)} invoices (auto-seeded).")
        first_inv_id = invoices[0].id
        
        # Test 2: Manual Case Ingestion
        print("\n2. Testing POST /api/invoices (Manual Ingestion)...")
        manual_payload = ManualInvoiceCreate(
          customer_name="Aarav Sharma",
          phone="+919988776655",
          amount_inr=28000.0,
          failure_reason="GATEWAY_TIMEOUT",
          ltv_inr=95000.0,
          consecutive_discount_months=0,
          merchant_name="TechCorp B2B India",
          merchant_cap=0.10,
        )
        new_inv = await create_manual_invoice(manual_payload, session)
        print(f"  [PASS] Created manual invoice {new_inv.id} for {new_inv.customer.name}.")

        # Test 3: Fast Forward Engine
        print("\n3. Testing POST /api/simulation/fast-forward...")
        ff_payload = FastForwardRequest(minutes=10, all_cases=True)
        ff_res = await fast_forward_simulation(ff_payload, session)
        print(f"  [PASS] {ff_res.message}")

        # Test 4: Operator Override
        print("\n4. Testing POST /api/invoices/{id}/override...")
        override_payload = OperatorOverrideRequest(
            override_type="SIMULATE_PTP",
            reason="Customer requested 3-day hold via phone call",
        )
        ov_res = await operator_override(first_inv_id, override_payload, session)
        print(f"  [PASS] Operator override applied. New state: {ov_res.recovery_events[-1].current_state}")

    print("\n==================================================================")
    print("ALL PHASE 6 AUTONOMOUS AGENT VERIFICATIONS PASSED!")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())
