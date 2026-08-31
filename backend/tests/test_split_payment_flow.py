"""
RecoveryAI — Split Payment & 1-Hour Follow-Up Test Suite
Validates:
1. Split Payment Offer upon initial refusal (margin preservation).
2. Transition to SPLIT_FIRST_HALF_PENDING on debtor acceptance (1-hour window).
3. Operator manual 50% (HALF) payment recording updating recovered_amount_inr and transitioning to PTP_ACTIVE (3-day PTP).
4. Full payment recording resolving case and aggregating total recovered KPI.
5. 1-Hour deadline expiration putting debtor into call queue.
6. Follow-up call refusal immediately escalating to ESCALATED_HUMAN.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "d:\\RecoveryAI\\backend")

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal
from app.engine.policy_wrapper import execute_policy_turn
from app.engine.state_machine import State, StateMachine
from main import app
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.scheduler import process_expired_deadlines


@pytest.mark.asyncio
async def test_split_payment_full_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed fresh database
        seed_resp = await client.post("/api/seed")
        assert seed_resp.status_code == 200

        # Fetch invoices
        inv_resp = await client.get("/api/invoices")
        assert inv_resp.status_code == 200
        invoices = inv_resp.json()
        assert len(invoices) > 0

        target_inv = invoices[0]
        inv_id = target_inv["id"]
        orig_amount = Decimal(str(target_inv["amount_inr"]))

        # 1. Simulate Voice Negotiation: Customer Refusal at full amount
        turn1_resp = await client.post(
            f"/api/invoices/{inv_id}/voice/turn",
            data={"text_fallback": "Main abhi poora payment nahi kar sakta"},
        )
        assert turn1_resp.status_code == 200
        t1_data = turn1_resp.json()
        assert t1_data["new_state"] == State.SPLIT_OFFERED
        assert "50%" in t1_data["agent_reply_text"]

        # 2. Customer accepts Split Payment Plan
        turn2_resp = await client.post(
            f"/api/invoices/{inv_id}/voice/turn",
            data={"text_fallback": "Theek hai, main aadha payment abhi 1 ghante mein kar deta hoon aur baaki 3 din mein"},
        )
        assert turn2_resp.status_code == 200
        t2_data = turn2_resp.json()
        assert t2_data["new_state"] == State.SPLIT_FIRST_HALF_PENDING

        # Verify DB state
        async with AsyncSessionLocal() as session:
            inv = await session.get(Invoice, uuid.UUID(inv_id))
            assert inv is not None
            assert inv.next_action_due_at is not None
            # 1-hour window assigned
            diff_mins = (inv.next_action_due_at - datetime.now(timezone.utc)).total_seconds() / 60
            assert 50 <= diff_mins <= 65

        # 3. Operator clicks "Half (50%)" payment received
        pay_half_resp = await client.post(
            f"/api/invoices/{inv_id}/record-payment",
            json={"payment_type": "HALF", "notes": "Received 1st half UPI payment"},
        )
        assert pay_half_resp.status_code == 200
        p_half = pay_half_resp.json()
        assert p_half["payment_type"] == "HALF"
        expected_half = (orig_amount / Decimal("2")).quantize(Decimal("0.01"))
        assert Decimal(str(p_half["amount_paid_inr"])) == expected_half
        assert Decimal(str(p_half["remaining_balance_inr"])) == expected_half
        assert p_half["new_status"] == "UNPAID"
        assert p_half["new_state"] == State.PTP_ACTIVE

        # 4. Check Analytics Summary — Total Recovered must equal half payment
        an_resp = await client.get("/api/analytics/summary")
        assert an_resp.status_code == 200
        an_data = an_resp.json()
        assert an_data["total_recovered_inr"] >= float(expected_half)

        # 5. Operator clicks "Full (100%)" remaining payment received
        pay_full_resp = await client.post(
            f"/api/invoices/{inv_id}/record-payment",
            json={"payment_type": "FULL", "notes": "Received final 50% payment"},
        )
        assert pay_full_resp.status_code == 200
        p_full = pay_full_resp.json()
        assert p_full["payment_type"] == "FULL"
        assert p_full["new_status"] == "RESOLVED"
        assert p_full["new_state"] == State.RESOLVED
        assert Decimal(str(p_full["remaining_balance_inr"])) == Decimal("0.00")

        # Check Analytics Summary — Total Recovered must equal full original amount
        an_resp2 = await client.get("/api/analytics/summary")
        assert an_resp2.status_code == 200
        an_data2 = an_resp2.json()
        assert an_data2["total_recovered_inr"] >= float(orig_amount)


@pytest.mark.asyncio
async def test_split_1hour_timeout_and_refusal_escalation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed fresh DB
        await client.post("/api/seed")
        invoices = (await client.get("/api/invoices")).json()
        target_inv = invoices[1]
        inv_id = target_inv["id"]

        # 1. Refusal -> SPLIT_OFFERED
        await client.post(
            f"/api/invoices/{inv_id}/voice/turn",
            data={"text_fallback": "Nahi, main abhi nahi bhar sakta"},
        )

        # 2. Accept Split -> SPLIT_FIRST_HALF_PENDING
        await client.post(
            f"/api/invoices/{inv_id}/voice/turn",
            data={"text_fallback": "Haan 50% split plan theek hai"},
        )

        # 3. Simulate 1-hour expiration via skip-wait
        skip_res = await client.post(f"/api/invoices/{inv_id}/skip-wait")
        assert skip_res.status_code == 200
        assert skip_res.json()["trigger_call"] is True

        # Check invoice is queued for follow-up
        inv_after_skip = (await client.get(f"/api/invoices/{inv_id}")).json()
        assert inv_after_skip["call_pending"] is True

        # Check greeting for 1-hour follow-up
        greeting_res = await client.get(f"/api/invoices/{inv_id}/voice-call/greeting")
        assert greeting_res.status_code == 200
        assert "1 ghanta pehle" in greeting_res.json()["greeting_text"]

        # 4. Debtor refuses on follow-up call -> immediate escalation
        turn_refuse = await client.post(
            f"/api/invoices/{inv_id}/voice/turn",
            data={"text_fallback": "Main abhi 50% bhi nahi doonga, jo karna hai kar lo"},
        )
        assert turn_refuse.status_code == 200
        assert turn_refuse.json()["new_state"] == State.ESCALATED_HUMAN


if __name__ == "__main__":
    asyncio.run(test_split_payment_full_lifecycle())
    asyncio.run(test_split_1hour_timeout_and_refusal_escalation())
    print("All Split Payment Flow Tests Passed Successfully!")
