"""
RecoveryAI — Voice Conversation State & Intent Pipeline Regression Tests
========================================================================

Covers the 12 explicit regression test requirements:
Test 1:  "I need a 50% discount" -> REQUEST_DISCOUNT -> TIER_1_DISCOUNT -> 5% on 10% cap
Test 2:  "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए" -> REQUEST_DISCOUNT (NOT GENERAL_INQUIRY)
Test 3:  "discount chahiye" -> REQUEST_DISCOUNT
Test 4:  Sequential refusals: Tier 0 -> Tier 1 -> Tier 2 -> Tier 3 -> ESCALATED_HUMAN
Test 5:  Merchant cap 20%: Tier 1 = 10%, Tier 2 = 16%, Tier 3 = 20%
Test 6:  Merchant cap 15%: Tier 1 = 7.5%, Tier 2 = 12%, Tier 3 = 15%
Test 7:  Anti-gaming: consecutive_discount_months >= 3 -> authorized discount = 0%
Test 8:  Dispute: "Invoice amount is wrong" -> FROZEN_DISPUTE, status=DISPUTED, call_pending=False
Test 9:  PTP: "I'll pay in 3 days" -> PROMISE_TO_PAY, PTP_ACTIVE, future ptp_date
Test 10: PAY_NOW: "I'll pay now" -> PAY_NOW, link sent, invoice UNPAID (not resolved)
Test 11: Technical issue: "UPI isn't working" -> TECHNICAL_PROBLEM, discount = 0%
Test 12: Final refusal at Tier 3 -> ESCALATED_HUMAN, spoken reply mentions senior financial officer
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

from app.engine.calculator import calculator
from app.engine.gemini_service import classify_debtor_intent, generate_grounded_speech
from app.engine.policy_wrapper import execute_policy_turn, parse_relative_ptp_date
from app.engine.state_machine import State
from app.models import Customer, Invoice, Merchant, RecoveryEvent
from app.schemas import DebtorIntentClassification


class MockAsyncSession:
    """Mock async DB session for unit tests."""
    def add(self, obj):
        pass
    async def flush(self):
        pass


def make_test_invoice(
    merchant_cap: Decimal = Decimal("0.1000"),
    consecutive_months: int = 0,
    amount_inr: Decimal = Decimal("18500.00"),
    initial_state: str = State.REMINDER_SENT,
    failure_reason: str = "GATEWAY_TIMEOUT",
) -> Invoice:
    m_id = uuid.uuid4()
    c_id = uuid.uuid4()
    inv_id = uuid.uuid4()
    merchant = Merchant(
        id=m_id,
        name="DemoMerchant India Pvt. Ltd.",
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


# ── Test 1: 50% discount request on 10% cap ──────────────────────────────────
@pytest.mark.asyncio
async def test_1_discount_request_50_pct():
    transcript = "I need a 50% discount"
    invoice = make_test_invoice(merchant_cap=Decimal("0.1000"), amount_inr=Decimal("18500.00"))
    
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "REQUEST_DISCOUNT"
    assert intent_data.customer_stated_discount_pct == Decimal("50")
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.resulting_state == State.TIER_1_DISCOUNT
    assert decision.authorized_discount_rate == Decimal("0.0500")  # 10% cap * 50% = 5%
    assert decision.authorized_net_amount == Decimal("17575.00")   # ₹18,500 - 5% = ₹17,575


# ── Test 2: Hindi 50% discount request ────────────────────────────────────────
@pytest.mark.asyncio
async def test_2_hindi_fifty_percent_discount():
    transcript = "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए"
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "REQUEST_DISCOUNT"
    assert intent_data.intent != "GENERAL_INQUIRY"
    assert intent_data.customer_stated_discount_pct == Decimal("50")


# ── Test 3: "discount chahiye" ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_3_discount_chahiye():
    transcript = "discount chahiye"
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "REQUEST_DISCOUNT"


# ── Test 4: Sequential refusals progression ──────────────────────────────────
@pytest.mark.asyncio
async def test_4_sequential_refusals_climb_ladder():
    invoice = make_test_invoice(merchant_cap=Decimal("0.1000"), amount_inr=Decimal("18500.00"))
    refusal = DebtorIntentClassification(intent="REFUSAL", confidence=0.90)
    
    # Refusal #1 from initial state -> SPLIT_OFFERED (0% discount, 50/50 split terms)
    d1 = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert d1.resulting_state == State.SPLIT_OFFERED
    assert d1.authorized_discount_rate == Decimal("0.0")
    
    # Refusal #2 from SPLIT_OFFERED -> Tier 1 (5%)
    d2 = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert d2.resulting_state == State.TIER_1_DISCOUNT
    assert d2.authorized_discount_rate == Decimal("0.0500")
    
    # Refusal #3 from Tier 1 -> Tier 2 (8%)
    d3 = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert d3.resulting_state == State.TIER_2_DISCOUNT
    assert d3.authorized_discount_rate == Decimal("0.0800")
    
    # Refusal #4 from Tier 2 -> Tier 3 Floor (10%)
    d4 = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert d4.resulting_state == State.TIER_3_FLOOR
    assert d4.authorized_discount_rate == Decimal("0.1000")
    
    # Refusal #5 from Tier 3 Floor -> ESCALATED_HUMAN
    d5 = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert d5.resulting_state == State.ESCALATED_HUMAN
    assert d5.trigger_auto_close is True


# ── Test 5: Merchant cap 20% ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_5_merchant_cap_20_percent():
    invoice = make_test_invoice(merchant_cap=Decimal("0.2000"), amount_inr=Decimal("10000.00"))
    discount_req = DebtorIntentClassification(intent="REQUEST_DISCOUNT", customer_stated_discount_pct=50.0, confidence=0.90)
    
    # Tier 1 = 10%
    d1 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d1.authorized_discount_rate == Decimal("0.1000")
    assert d1.authorized_net_amount == Decimal("9000.00")
    
    # Tier 2 = 16%
    d2 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d2.authorized_discount_rate == Decimal("0.1600")
    assert d2.authorized_net_amount == Decimal("8400.00")
    
    # Tier 3 = 20%
    d3 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d3.authorized_discount_rate == Decimal("0.2000")
    assert d3.authorized_net_amount == Decimal("8000.00")


# ── Test 6: Merchant cap 15% ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_6_merchant_cap_15_percent():
    invoice = make_test_invoice(merchant_cap=Decimal("0.1500"), amount_inr=Decimal("10000.00"))
    discount_req = DebtorIntentClassification(intent="REQUEST_DISCOUNT", customer_stated_discount_pct=50.0, confidence=0.90)
    
    # Tier 1 = 7.5%
    d1 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d1.authorized_discount_rate == Decimal("0.0750")
    
    # Tier 2 = 12.0%
    d2 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d2.authorized_discount_rate == Decimal("0.1200")
    
    # Tier 3 = 15.0%
    d3 = await execute_policy_turn(invoice, discount_req, MockAsyncSession())
    assert d3.authorized_discount_rate == Decimal("0.1500")


# ── Test 7: Anti-gaming blocks concession (3+ months) ─────────────────────────
@pytest.mark.asyncio
async def test_7_anti_gaming_blocks_concession():
    invoice = make_test_invoice(merchant_cap=Decimal("0.1000"), consecutive_months=3)
    intent_data = DebtorIntentClassification(intent="REQUEST_DISCOUNT", confidence=0.95)
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.authorized_discount_rate == Decimal("0.0000")
    assert decision.authorized_net_amount == Decimal("18500.00")


# ── Test 8: Dispute immediately freezes dunning ───────────────────────────────
@pytest.mark.asyncio
async def test_8_dispute_halts_dunning():
    transcript = "Invoice amount is wrong"
    invoice = make_test_invoice(failure_reason="DISPUTED_AMOUNT")
    
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "DISPUTE"
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.resulting_state == State.FROZEN_DISPUTE
    assert decision.new_invoice_status == "DISPUTED"
    assert invoice.call_pending is False
    assert decision.trigger_auto_close is True


# ── Test 9: Promise to Pay 3 days ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_9_promise_to_pay_3_days():
    transcript = "I'll pay in 3 days"
    invoice = make_test_invoice()
    
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "PROMISE_TO_PAY"
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.resulting_state == State.PTP_ACTIVE
    assert decision.ptp_date is not None
    assert decision.ptp_date > datetime.now(timezone.utc)
    assert decision.new_invoice_status == "UNPAID"  # NOT marked resolved


# ── Test 10: PAY_NOW sends link without resolving invoice ─────────────────────
@pytest.mark.asyncio
async def test_10_pay_now_sends_link():
    transcript = "I'll pay now"
    invoice = make_test_invoice()
    
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "PAY_NOW"
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.resulting_state == State.LINK_SENT
    assert decision.new_invoice_status == "UNPAID"  # Requires actual payment confirmation
    assert decision.trigger_auto_close is True


# ── Test 11: Technical issue gives zero discount ──────────────────────────────
@pytest.mark.asyncio
async def test_11_technical_problem_zero_discount():
    transcript = "UPI isn't working"
    invoice = make_test_invoice()
    
    intent_data = await classify_debtor_intent(transcript)
    assert intent_data.intent == "TECHNICAL_PROBLEM"
    
    decision = await execute_policy_turn(invoice, intent_data, MockAsyncSession())
    assert decision.resulting_state == State.LINK_SENT
    assert decision.authorized_discount_rate == Decimal("0.0000")


# ── Test 12: Final refusal at Tier 3 escalates with speech ────────────────────
@pytest.mark.asyncio
async def test_12_final_refusal_speech_escalation():
    invoice = make_test_invoice(merchant_cap=Decimal("0.1000"), initial_state=State.TIER_3_FLOOR)
    refusal = DebtorIntentClassification(intent="REFUSAL", confidence=0.95)
    
    decision = await execute_policy_turn(invoice, refusal, MockAsyncSession())
    assert decision.resulting_state == State.ESCALATED_HUMAN
    
    spoken = await generate_grounded_speech({"customer_name": "Aarav"}, decision)
    assert "senior financial officer" in spoken.lower()
