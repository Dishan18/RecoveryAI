"""
RecoveryAI — Seed Script
Seeds 6 realistic Indian B2B/consumer payment-failure scenarios.
Called by POST /api/seed.  Idempotent: clears existing data before re-seeding.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, engine
from app.migrations import run_migrations
from app.models import Customer, Invoice, Merchant, RecoveryEvent

logger = logging.getLogger(__name__)

# ── Static merchant (shared across all scenarios) ─────────────────────────────
MERCHANT_ID = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

MERCHANT_DATA = {
    "id": MERCHANT_ID,
    "name": "DemoMerchant India Pvt. Ltd.",
    "default_discount_cap": 0.10,  # 10% maximum
}

# ── Scenario definitions ───────────────────────────────────────────────────────
SCENARIOS = [
    # ── Scenario 1: Clean History & High LTV ─────────────────────────────────
    {
        "label": "Scenario 1 — GATEWAY_TIMEOUT (Aarav Sharma, LTV ₹1,50,000)",
        "customer": {
            "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "name": "Aarav Sharma",
            "phone": "+919811122233",
            "email": "aarav.sharma@example.in",
            "ltv_inr": 150000.00,
            "consecutive_discount_months": 0,
        },
        "invoice": {
            "id": uuid.UUID("aaaa1111-aaaa-1111-aaaa-111111111111"),
            "amount_inr": 18500.00,
            "status": "UNPAID",
            "failure_reason": "GATEWAY_TIMEOUT",
            "due_date": datetime.now(timezone.utc) + timedelta(days=7),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "HDFC/ICICI card gateway timeout detected during autopay debit. High-value customer with clean history.",
        },
    },

    # ── Scenario 2: Liquidity Crunch ─────────────────────────────────────────
    {
        "label": "Scenario 2 — INSUFFICIENT_FUNDS (Priya Verma, LTV ₹65,000, 1 mo discount history)",
        "customer": {
            "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "name": "Priya Verma",
            "phone": "+919822233344",
            "email": "priya.verma@example.in",
            "ltv_inr": 65000.00,
            "consecutive_discount_months": 1,
        },
        "invoice": {
            "id": uuid.UUID("bbbb2222-bbbb-2222-bbbb-222222222222"),
            "amount_inr": 45000.00,
            "status": "UNPAID",
            "failure_reason": "INSUFFICIENT_FUNDS",
            "due_date": datetime.now(timezone.utc) + timedelta(days=5),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "UPI Autopay debit bounced due to insufficient funds. Customer has 1 month prior discount history (8% cap ceiling).",
        },
    },

    # ── Scenario 3: Chronic Discount Exploiter ───────────────────────────────
    {
        "label": "Scenario 3 — MANDATE_DECLINE (Vikram Malhotra, LTV ₹40,000, 2 consecutive discounts)",
        "customer": {
            "id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
            "name": "Vikram Malhotra",
            "phone": "+919833344455",
            "email": "vikram.malhotra@example.in",
            "ltv_inr": 40000.00,
            "consecutive_discount_months": 2,
        },
        "invoice": {
            "id": uuid.UUID("cccc3333-cccc-3333-cccc-333333333333"),
            "amount_inr": 12000.00,
            "status": "UNPAID",
            "failure_reason": "MANDATE_DECLINE",
            "due_date": datetime.now(timezone.utc) + timedelta(days=4),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "NACH mandate decline. Customer has received discounts for 2 consecutive months (5% cap ceiling).",
        },
    },

    # ── Scenario 4: Billing Dispute ───────────────────────────────────────────
    {
        "label": "Scenario 4 — DISPUTED_AMOUNT (Ananya Iyer, LTV ₹2,10,000)",
        "customer": {
            "id": uuid.UUID("44444444-4444-4444-4444-444444444444"),
            "name": "Ananya Iyer",
            "phone": "+919844455566",
            "email": "ananya.iyer@example.in",
            "ltv_inr": 210000.00,
            "consecutive_discount_months": 0,
        },
        "invoice": {
            "id": uuid.UUID("dddd4444-dddd-4444-dddd-444444444444"),
            "amount_inr": 95000.00,
            "status": "UNPAID",
            "failure_reason": "DISPUTED_AMOUNT",
            "due_date": datetime.now(timezone.utc) + timedelta(days=14),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "TDS/GST discrepancy dispute signaled on invoice ₹95,000. Highest-value portfolio account.",
        },
    },

    # ── Scenario 5: Expired Card Autopay Failure ──────────────────────────────
    {
        "label": "Scenario 5 — EXPIRED_CARD (Rohan Mehta, LTV ₹95,000, 3 mo excessive history)",
        "customer": {
            "id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
            "name": "Rohan Mehta",
            "phone": "+919855566677",
            "email": "rohan.mehta@example.in",
            "ltv_inr": 95000.00,
            "consecutive_discount_months": 3,
        },
        "invoice": {
            "id": uuid.UUID("eeee5555-eeee-5555-eeee-555555555555"),
            "amount_inr": 28000.00,
            "status": "UNPAID",
            "failure_reason": "EXPIRED_CARD",
            "due_date": datetime.now(timezone.utc) + timedelta(days=6),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "Visa corporate credit card expired. Customer has 3 consecutive discount months (0% discount permitted).",
        },
    },

    # ── Scenario 6: High LTV Gateway Timeout ──────────────────────────────────
    {
        "label": "Scenario 6 — GATEWAY_TIMEOUT (Kavya Patel, LTV ₹1,80,000, 1 mo discount history)",
        "customer": {
            "id": uuid.UUID("66666666-6666-6666-6666-666666666666"),
            "name": "Kavya Patel",
            "phone": "+919866677788",
            "email": "kavya.patel@example.in",
            "ltv_inr": 180000.00,
            "consecutive_discount_months": 1,
        },
        "invoice": {
            "id": uuid.UUID("ffff6666-ffff-6666-ffff-666666666666"),
            "amount_inr": 52000.00,
            "status": "UNPAID",
            "failure_reason": "GATEWAY_TIMEOUT",
            "due_date": datetime.now(timezone.utc) + timedelta(days=8),
        },
        "recovery_event": {
            "current_state": "TRIGGERED",
            "discount_offered": 0.0,
            "log_message": "Payment breach following HDFC gateway timeout on corporate subscription. 1 month concession history.",
        },
    },
]


async def clear_existing_data(session: AsyncSession) -> None:
    """Remove all existing seed data to ensure idempotent re-seeding."""
    await session.execute(delete(RecoveryEvent))
    await session.execute(delete(Invoice))
    await session.execute(delete(Customer))
    await session.execute(delete(Merchant))
    await session.commit()
    logger.info("🗑️   Cleared existing seed data.")


async def seed_database(session: AsyncSession) -> list[str]:
    """Insert merchant + all scenario records uniformly at initial breach. Returns list of scenario labels."""
    # ── Merchant ──────────────────────────────────────────────────────────────
    merchant = Merchant(**MERCHANT_DATA)
    session.add(merchant)
    await session.flush()

    labels: list[str] = []
    now = datetime.now(timezone.utc)

    for scenario in SCENARIOS:
        # ── Customer ──────────────────────────────────────────────────────────
        customer = Customer(
            **scenario["customer"],
            merchant_id=MERCHANT_ID,
        )
        session.add(customer)
        await session.flush()

        # ── Uniform Initial Breach: TRIGGERED with 10-minute live timer ────────
        next_due = now + timedelta(minutes=10)
        call_pend = False

        # ── Invoice ───────────────────────────────────────────────────────────
        invoice = Invoice(
            **scenario["invoice"],
            customer_id=scenario["customer"]["id"],
            merchant_id=MERCHANT_ID,
            next_action_due_at=next_due,
            call_pending=call_pend,
        )
        session.add(invoice)
        await session.flush()

        # ── Initial Breach Recovery Event ─────────────────────────────────────
        event = RecoveryEvent(
            invoice_id=scenario["invoice"]["id"],
            current_state="TRIGGERED",
            discount_offered=0.0,
            ptp_deadline=None,
            log_message=scenario["recovery_event"]["log_message"],
            timestamp=now,
        )
        session.add(event)
        labels.append(scenario["label"])
        logger.info("  ✅  Seeded: %s", scenario["label"])

    await session.commit()
    return labels


async def run_seed() -> dict:
    """Entry point called by /api/seed. Runs migrations then seeds data."""
    logger.info("🚀  Starting RecoveryAI seed process …")
    await run_migrations()

    async with AsyncSessionLocal() as session:
        await clear_existing_data(session)
        labels = await seed_database(session)

    logger.info("🎉  Seed complete — %d invoices created.", len(labels))
    return {
        "message": "Database seeded successfully",
        "invoices_created": len(labels),
        "scenarios": labels,
    }


# ── Standalone execution ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = asyncio.run(run_seed())
    print(result)
