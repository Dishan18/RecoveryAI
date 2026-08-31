"""
RecoveryAI — Gemini Intent & Deterministic Policy Boundary Verification Tests
=============================================================================

Tests:
1. Intent Classification Schema & Fallbacks (no chain-of-thought).
2. Financial Authority: Gemini-extracted 25% / 50% discounts are strictly informational;
   Policy Engine independently calculates authorized tiers.
3. Concession Ladder: Dynamic scaling based on merchant cap (10% vs 20%).
4. Sequential Refusals: Tier 0 -> Tier 1 -> Tier 2 -> Tier 3 -> ESCALATED_HUMAN.
5. Anti-Gaming: 3+ months abuse history blocks concessions (0% cap).
6. Payment Safety: PAY_NOW intent does not mark invoice paid without verification.
7. Dispute Safety: DISPUTE immediately halts dunning and freezes invoice.
8. Technical Failure: TECHNICAL_PROBLEM generates link with ZERO discount.
9. PTP Resolution: Relative date expressions resolved to valid future datetimes.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

try:
    import pytest
except ImportError:
    class _MockPytest:
        class mark:
            @staticmethod
            def asyncio(f):
                return f
    pytest = _MockPytest()

from app.engine.calculator import DiscountCalculator
from app.engine.gemini_service import classify_debtor_intent, generate_grounded_speech
from app.engine.policy_wrapper import execute_policy_turn, parse_relative_ptp_date
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.schemas import DebtorIntentClassification


class MockAsyncSession:
    """Lightweight mock session for policy wrapper tests."""
    def add(self, obj):
        pass
    async def flush(self):
        pass


def make_test_invoice(
    merchant_cap: Decimal = Decimal("0.1000"),
    consecutive_months: int = 0,
    amount_inr: Decimal = Decimal("10000.00"),
    initial_state: str = State.REMINDER_SENT,
    failure_reason: str = "GATEWAY_TIMEOUT",
) -> Invoice:
    m_id = uuid.uuid4()
    c_id = uuid.uuid4()
    inv_id = uuid.uuid4()
    merchant = Merchant(
        id=m_id,
        name="Test Merchant",
        default_discount_cap=merchant_cap,
        created_at=datetime.now(timezone.utc),
    )
    customer = Customer(
        id=c_id,
        merchant_id=m_id,
        name="Aarav Sharma",
        phone="+919811122233",
        email="aarav@test.com",
        ltv_inr=Decimal("150000.00"),
        consecutive_discount_months=consecutive_months,
    )
    invoice = Invoice(
        id=inv_id,
        customer_id=c_id,
        merchant_id=m_id,
        amount_inr=amount_inr,
        status="UNPAID",
        failure_reason=failure_reason,
        call_pending=True,
    )
    invoice.merchant = merchant
    invoice.customer = customer
    initial_evt = RecoveryEvent(
        id=uuid.uuid4(),
        invoice_id=inv_id,
        current_state=initial_state,
        discount_offered=0.0,
        log_message="Initial state",
        timestamp=datetime.now(timezone.utc),
    )
    invoice.recovery_events = [initial_evt]
    return invoice


# ─────────────────────────────────────────────────────────────────────────────
# 1. Intent Classification Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_classification_discount_request():
    transcript = "25 percent discount de do, tabhi payment karunga."
    res = await classify_debtor_intent(transcript)
    assert res.intent == "REQUEST_DISCOUNT"
    assert res.customer_stated_discount_pct == Decimal("25")
    assert not hasattr(res, "reasoning")  # No chain-of-thought


@pytest.mark.asyncio
async def test_intent_classification_promise_to_pay():
    transcript = "haan Friday ko pakka payment clear kar dunga"
    res = await classify_debtor_intent(transcript)
    assert res.intent == "PROMISE_TO_PAY"
    assert res.ptp_date_extracted is not None
    assert not hasattr(res, "reasoning")


@pytest.mark.asyncio
async def test_intent_classification_dispute():
    transcript = "invoice amount galat hai, hamara order sirf 40000 ka tha"
    res = await classify_debtor_intent(transcript)
    assert res.intent == "DISPUTE"
    assert res.dispute_reason is not None


@pytest.mark.asyncio
async def test_intent_classification_technical_problem():
    transcript = "HDFC gateway timeout ho gaya aur UPI fail ho gaya"
    res = await classify_debtor_intent(transcript)
    assert res.intent == "TECHNICAL_PROBLEM"


@pytest.mark.asyncio
async def test_intent_classification_request_link():
    transcript = "mujhe direct payment link bhejo WhatsApp par"
    res = await classify_debtor_intent(transcript)
    assert res.intent == "REQUEST_PAYMENT_LINK"


@pytest.mark.asyncio
async def test_intent_classification_refusal():
    transcript = "nahi dunga, jo karna hai kar lo"
    res = await classify_debtor_intent(transcript)
    assert res.intent == "REFUSAL"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Financial Authority & Policy Boundary Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_50_percent_request_on_10_percent_cap():
    """
    Customer requests 50% discount on a 10% merchant cap invoice.
    Policy Engine MUST independently offer Tier 1 (5.0%), NOT 50%.
    """
    invoice = make_test_invoice(
        merchant_cap=Decimal("0.1000"),
        amount_inr=Decimal("10000.00"),
        consecutive_months=0,
    )

    intent_data = DebtorIntentClassification(
        intent="REQUEST_DISCOUNT",
        confidence=0.95,
        customer_stated_discount_pct=Decimal("50.0"),  # Stated 50%
    )

    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    # Verify: 50% was completely ignored for calculation
    # Tier 1 = 50% of 10% cap = 5.0%
    assert decision.authorized_discount_rate == Decimal("0.0500")
    assert decision.authorized_net_amount == Decimal("9500.00")
    assert decision.resulting_state == State.TIER_1_DISCOUNT
    assert invoice.current_discount_tier == 1


@pytest.mark.asyncio
async def test_customer_25_percent_request_on_20_percent_cap():
    """
    Customer requests 25% discount on a 20% merchant cap invoice.
    Policy Engine MUST offer Tier 1 (10.0%), NOT 25%.
    """
    invoice = make_test_invoice(
        merchant_cap=Decimal("0.2000"),
        amount_inr=Decimal("50000.00"),
        consecutive_months=0,
    )

    intent_data = DebtorIntentClassification(
        intent="REQUEST_DISCOUNT",
        confidence=0.95,
        customer_stated_discount_pct=Decimal("25.0"),
    )

    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    # Tier 1 = 50% of 20% cap = 10.0%
    assert decision.authorized_discount_rate == Decimal("0.1000")
    assert decision.authorized_net_amount == Decimal("45000.00")
    assert decision.resulting_state == State.TIER_1_DISCOUNT


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tier Refusal Ladder Progression
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sequential_refusals_climb_ladder_to_escalation():
    """
    Tests sequential refusals:
    Tier 0 -> Tier 1 (5%)
    Tier 1 -> Tier 2 (8%)
    Tier 2 -> Tier 3 (10%)
    Tier 3 -> ESCALATED_HUMAN
    """
    invoice = make_test_invoice(
        merchant_cap=Decimal("0.1000"),
        amount_inr=Decimal("12000.00"),
        consecutive_months=0,
    )

    refusal_intent = DebtorIntentClassification(intent="REFUSAL", confidence=0.90)

    # Turn 1: Refuse at Tier 0 -> Offered Tier 1 (5%)
    d1 = await execute_policy_turn(invoice, refusal_intent, MockAsyncSession())
    assert d1.resulting_state == State.TIER_1_DISCOUNT
    assert d1.authorized_discount_rate == Decimal("0.0500")
    assert invoice.current_discount_tier == 1

    # Turn 2: Refuse at Tier 1 -> Offered Tier 2 (8%)
    d2 = await execute_policy_turn(invoice, refusal_intent, MockAsyncSession())
    assert d2.resulting_state == State.TIER_2_DISCOUNT
    assert d2.authorized_discount_rate == Decimal("0.0800")
    assert invoice.current_discount_tier == 2

    # Turn 3: Refuse at Tier 2 -> Offered Tier 3 Floor (10%)
    d3 = await execute_policy_turn(invoice, refusal_intent, MockAsyncSession())
    assert d3.resulting_state == State.TIER_3_FLOOR
    assert d3.authorized_discount_rate == Decimal("0.1000")
    assert invoice.current_discount_tier == 3

    # Turn 4: Refuse at Tier 3 Floor -> ESCALATED_HUMAN
    d4 = await execute_policy_turn(invoice, refusal_intent, MockAsyncSession())
    assert d4.resulting_state == State.ESCALATED_HUMAN
    assert d4.trigger_auto_close is True
    assert invoice.call_pending is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Anti-Gaming Abuse History Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chronic_discount_exploiter_blocked():
    """
    Customer with 3+ consecutive discount months is blocked from concessions (0% cap).
    """
    invoice = make_test_invoice(
        merchant_cap=Decimal("0.1000"),
        amount_inr=Decimal("28000.00"),
        consecutive_months=3,  # 3 consecutive months
    )

    intent_data = DebtorIntentClassification(
        intent="REQUEST_DISCOUNT",
        confidence=0.95,
        customer_stated_discount_pct=Decimal("10.0"),
    )

    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    # Concession blocked: 0% discount, net amount unchanged
    assert decision.authorized_discount_rate == Decimal("0.0000")
    assert decision.authorized_net_amount == Decimal("28000.00")
    assert "Anti-Gaming" in decision.action_executed


# ─────────────────────────────────────────────────────────────────────────────
# 5. Safety Invariants: Payment, Dispute & Technical Failure
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pay_now_does_not_mark_paid_without_verification():
    """
    PAY_NOW intent sends payment link, does NOT mark invoice status as PAID/RESOLVED.
    """
    invoice = make_test_invoice(amount_inr=Decimal("5000.00"))

    intent_data = DebtorIntentClassification(intent="PAY_NOW", confidence=0.95)
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    # Must NOT be marked RESOLVED without webhook/gateway confirmation
    assert decision.new_invoice_status == "UNPAID"
    assert decision.resulting_state == State.LINK_SENT
    assert decision.trigger_auto_close is True


@pytest.mark.asyncio
async def test_dispute_freezes_collection_immediately():
    """
    DISPUTE intent transitions to FROZEN_DISPUTE, sets status to DISPUTED, and pauses outreach.
    """
    invoice = make_test_invoice(amount_inr=Decimal("95000.00"), failure_reason="DISPUTED_AMOUNT")

    intent_data = DebtorIntentClassification(
        intent="DISPUTE",
        confidence=0.92,
        dispute_reason="GST calculation error on invoice",
    )
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    assert decision.resulting_state == State.FROZEN_DISPUTE
    assert decision.new_invoice_status == "DISPUTED"
    assert invoice.call_pending is False
    assert decision.trigger_auto_close is True


@pytest.mark.asyncio
async def test_technical_problem_offers_zero_discount():
    """
    TECHNICAL_PROBLEM intent generates fresh link with zero concession.
    """
    invoice = make_test_invoice(amount_inr=Decimal("15000.00"), failure_reason="GATEWAY_TIMEOUT")

    intent_data = DebtorIntentClassification(intent="TECHNICAL_PROBLEM", confidence=0.90)
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())

    assert decision.resulting_state == State.LINK_SENT
    assert decision.authorized_discount_rate == Decimal("0.0000")
    assert decision.authorized_net_amount == Decimal("15000.00")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Relative PTP Date Resolution Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_relative_ptp_date_resolution():
    fixed_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)  # Monday

    # "Friday" -> Friday of current week (4 days ahead)
    fri = parse_relative_ptp_date("Friday", fixed_now)
    assert fri.weekday() == 4

    # "kal" / "tomorrow" -> 1 day ahead
    kal = parse_relative_ptp_date("kal", fixed_now)
    assert (kal - fixed_now).days == 1

    # "3 days" / "3 din" -> 3 days ahead
    d3 = parse_relative_ptp_date("3 din", fixed_now)
    assert (d3 - fixed_now).days == 3
