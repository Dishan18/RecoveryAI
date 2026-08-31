import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Verification of Spoken Failure Enums & TTS Sanitization
====================================================================
"""

import asyncio
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.engine.sarvam_service import synthesize_speech
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.routes import voice_call_greeting


async def test_spoken_enums_and_resilience():
    print("==================================================================")
    print("STARTING SPOKEN FAILURE ENUMS & TTS SANITIZATION VERIFICATION")
    print("==================================================================")

    async with AsyncSessionLocal() as session:
        merchant = (await session.execute(select(Merchant))).scalars().first()
        cust = Customer(name="Anil Kumar", phone="+919111122222", ltv_inr=60000, merchant_id=merchant.id)
        session.add(cust)
        await session.flush()

        # Test case with GATEWAY_TIMEOUT failure reason
        inv = Invoice(
            customer_id=cust.id,
            merchant_id=merchant.id,
            amount_inr=52000,
            status="UNPAID",
            failure_reason="GATEWAY_TIMEOUT",
            call_pending=False,
        )
        session.add(inv)
        await session.flush()

        evt = RecoveryEvent(
            invoice_id=inv.id,
            current_state="TRIGGERED",
            discount_offered=0.0,
            log_message="Initial trigger with GATEWAY_TIMEOUT",
        )
        session.add(evt)
        await session.commit()
        inv_id = inv.id

    async with AsyncSessionLocal() as session:
        greeting_res = await voice_call_greeting(invoice_id=inv_id, db=session)
        print(f"Generated Greeting Text: \"{greeting_res.greeting_text}\"")

        # Assertions
        assert "_" not in greeting_res.greeting_text, "Underscore leaked into greeting text!"
        assert "GATEWAY_TIMEOUT" not in greeting_res.greeting_text, "Raw enum GATEWAY_TIMEOUT leaked into greeting text!"
        assert "technical gateway" in greeting_res.greeting_text or "gateway" in greeting_res.greeting_text
        print("  [PASS] Spoken failure reason converted to natural conversational phrasing without raw enum or underscores.")

    print("\n==================================================================")
    print(">>> SPOKEN ENUMS & RESILIENCE TESTS PASSED! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_spoken_enums_and_resilience())
