"""
RecoveryAI — Comprehensive Conversational Edge Case Tests
===========================================================

Tests the priority-ordered intent classifier against all known failure modes:
1. Sub-string / regex priority inversion (पेमेंट in discount context)
2. Hindi/Hinglish/Devanagari linguistic variations
3. Negation-aware PAY_NOW suppression
4. Compound conditional clauses
5. Dispute masking
6. Speech output sanitization
"""

import re
import sys
import pytest

sys.path.insert(0, "d:\\RecoveryAI\\backend")

from app.engine.gemini_service import (
    _rule_based_fallback_classification,
    _sanitize_speech_output,
)


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 1: DISPUTE must override ALL other keywords
# ─────────────────────────────────────────────────────────────────────────────

class TestDisputePriority:
    """DISPUTE takes highest priority — even when discount/payment keywords are present."""

    def test_dispute_gst_with_payment_keyword(self):
        """GST dispute containing 'payment' should classify as DISPUTE, not PAY_NOW."""
        result = _rule_based_fallback_classification("GST galat hai, payment nahi karunga")
        assert result["intent"] == "DISPUTE"

    def test_dispute_wrong_amount(self):
        result = _rule_based_fallback_classification("wrong amount bhai, ye galat hai")
        assert result["intent"] == "DISPUTE"

    def test_dispute_devanagari_galat(self):
        result = _rule_based_fallback_classification("ये amount गलत है")
        assert result["intent"] == "DISPUTE"

    def test_dispute_billing_error(self):
        result = _rule_based_fallback_classification("billing error hai invoice mein")
        assert result["intent"] == "DISPUTE"

    def test_dispute_already_paid(self):
        result = _rule_based_fallback_classification("already paid hai ye")
        assert result["intent"] == "DISPUTE"

    def test_dispute_not_ordered(self):
        result = _rule_based_fallback_classification("Maine ye order nahi kiya tha")
        assert result["intent"] == "DISPUTE"

    def test_dispute_defective(self):
        result = _rule_based_fallback_classification("defective product mila hai")
        assert result["intent"] == "DISPUTE"

    def test_dispute_overcharged(self):
        result = _rule_based_fallback_classification("overcharged kiya hai aapne")
        assert result["intent"] == "DISPUTE"


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 2: HARD REFUSAL
# ─────────────────────────────────────────────────────────────────────────────

class TestHardRefusal:
    """Hard refusals must be classified before softer categories."""

    def test_refusal_never_pay(self):
        result = _rule_based_fallback_classification("I will never pay this amount")
        assert result["intent"] == "REFUSAL"

    def test_refusal_kabhi_nahi(self):
        result = _rule_based_fallback_classification("kabhi nahi dunga")
        assert result["intent"] == "REFUSAL"

    def test_refusal_court_jao(self):
        result = _rule_based_fallback_classification("court jao, mujhe farak nahi padta")
        assert result["intent"] == "REFUSAL"

    def test_refusal_cancel_karo(self):
        result = _rule_based_fallback_classification("cancel karo ye sab")
        assert result["intent"] == "REFUSAL"


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 3: REQUEST_DISCOUNT — The Core Bug Fix
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscountPriority:
    """
    Discount intent MUST win when discount keywords coexist with
    payment/pay/dunga keywords in the same sentence.
    
    This is the exact bug observed in production:
    "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए नहीं तो मैं यह पेमेंट नहीं कर पाऊंगा"
    was incorrectly classified as AGREED_TO_PAY.
    """

    def test_discount_with_payment_nahi_kar_paaunga(self):
        """THE CRITICAL BUG: Discount request containing 'पेमेंट नहीं कर पाऊंगा'."""
        result = _rule_based_fallback_classification(
            "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए नहीं तो मैं यह पेमेंट नहीं कर पाऊंगा"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"
        assert result["customer_stated_discount_pct"] == 50.0

    def test_discount_fifty_percent_english(self):
        result = _rule_based_fallback_classification("I need a discount of fifty percent")
        assert result["intent"] == "REQUEST_DISCOUNT"
        assert result["customer_stated_discount_pct"] == 50.0

    def test_discount_50_percent_numeric(self):
        result = _rule_based_fallback_classification("50% discount de do")
        assert result["intent"] == "REQUEST_DISCOUNT"
        assert result["customer_stated_discount_pct"] == 50.0

    def test_discount_devanagari_chahiye(self):
        result = _rule_based_fallback_classification("डिस्काउंट चाहिए")
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_thoda_kam(self):
        result = _rule_based_fallback_classification("thoda kam karo bhai")
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_with_conditional_pay(self):
        """'If you give discount then I'll pay' → DISCOUNT, not PAY_NOW."""
        result = _rule_based_fallback_classification(
            "agar aap discount doge toh payment kar dunga"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_chahiye_nahi_to_nahi_dunga(self):
        result = _rule_based_fallback_classification("discount chahiye, nahi to nahi dunga")
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_25_percent_devanagari(self):
        result = _rule_based_fallback_classification(
            "मुझे पच्चीस प्रतिशत डिस्काउंट चाहिए"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"
        assert result["customer_stated_discount_pct"] == 25.0

    def test_discount_kuch_discount(self):
        result = _rule_based_fallback_classification("kuch discount milega kya?")
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_ten_percent_off(self):
        result = _rule_based_fallback_classification("agar 10% off milega toh aaj pay karunga")
        assert result["intent"] == "REQUEST_DISCOUNT"
        assert result["customer_stated_discount_pct"] == 10.0

    def test_discount_concession_request(self):
        result = _rule_based_fallback_classification("concession please, I cannot pay full")
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_discount_kam_kardo(self):
        result = _rule_based_fallback_classification("paisa kam kardo")
        assert result["intent"] == "REQUEST_DISCOUNT"


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 4: PROMISE_TO_PAY
# ─────────────────────────────────────────────────────────────────────────────

class TestPromiseToPay:
    """PTP should match on time-related keywords and Hindi/Hinglish speech."""

    def test_ptp_3_din(self):
        result = _rule_based_fallback_classification("3 din mein payment kar dunga")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_hindi_five_days_user_transcript(self):
        """User test case: 'मैं इसको पाँच दिन में सेटल कर दूँगा।'"""
        result = _rule_based_fallback_classification("मैं इसको पाँच दिन में सेटल कर दूँगा")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_transliterated_three_days_user_transcript(self):
        """User test case: 'आई विल मेक द पेमेंट विदिन थ्री डेज़।'"""
        result = _rule_based_fallback_classification("आई विल मेक द पेमेंट विदिन थ्री डेज़")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_kal(self):
        result = _rule_based_fallback_classification("kal kar dunga bhai")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_monday(self):
        result = _rule_based_fallback_classification("Monday tak pakka")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_tomorrow(self):
        result = _rule_based_fallback_classification("tomorrow I will settle")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_next_week(self):
        result = _rule_based_fallback_classification("next week kar deta hoon")
        assert result["intent"] == "PROMISE_TO_PAY"

    def test_ptp_salary_aane_par(self):
        result = _rule_based_fallback_classification("salary aane par dunga bhai")
        assert result["intent"] == "PROMISE_TO_PAY"


class TestPTPPolicyAndBreachRules:
    """Strict enforcement of 3-day PTP cap and post-PTP breach rules."""

    def test_ptp_date_clamped_to_3_days_max(self):
        from datetime import datetime, timezone, timedelta
        from app.engine.policy_wrapper import parse_relative_ptp_date

        base = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        
        # 5 days requested -> clamped to max 3 days
        d5 = parse_relative_ptp_date("5 days", base_date=base)
        assert d5 == base + timedelta(days=3)

        # "पाँच दिन" requested -> clamped to max 3 days
        d_hindi = parse_relative_ptp_date("पाँच दिन", base_date=base)
        assert d_hindi == base + timedelta(days=3)

        # "next week" requested -> clamped to max 3 days
        d_week = parse_relative_ptp_date("next week", base_date=base)
        assert d_week == base + timedelta(days=3)

        # 1 day requested -> 1 day (within 3 days)
        d1 = parse_relative_ptp_date("kal", base_date=base)
        assert d1 == base + timedelta(days=1)

        # 2 days requested -> 2 days (within 3 days)
        d2 = parse_relative_ptp_date("parso", base_date=base)
        assert d2 == base + timedelta(days=2)


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 5: TECHNICAL_PROBLEM
# ─────────────────────────────────────────────────────────────────────────────

class TestTechnicalProblem:
    """Gateway/UPI/bank failures → TECHNICAL_PROBLEM."""

    def test_upi_failure(self):
        result = _rule_based_fallback_classification("UPI reject ho raha hai bar bar")
        assert result["intent"] == "TECHNICAL_PROBLEM"

    def test_gateway_timeout(self):
        result = _rule_based_fallback_classification("gateway timeout aa raha hai")
        assert result["intent"] == "TECHNICAL_PROBLEM"

    def test_otp_not_received(self):
        result = _rule_based_fallback_classification("otp nahi aa raha hai")
        assert result["intent"] == "TECHNICAL_PROBLEM"


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY 6: PAY_NOW — Negation-aware
# ─────────────────────────────────────────────────────────────────────────────

class TestPayNowNegationAware:
    """PAY_NOW should only match on clear affirmative statements with no negation."""

    def test_pay_now_affirmative(self):
        result = _rule_based_fallback_classification("abhi payment kar deta hoon")
        assert result["intent"] == "PAY_NOW"

    def test_pay_now_blocked_by_negation(self):
        """'पेमेंट नहीं कर पाऊंगा' has negation → should NOT be PAY_NOW."""
        result = _rule_based_fallback_classification(
            "मैं यह पेमेंट नहीं कर पाऊंगा"
        )
        assert result["intent"] != "PAY_NOW"

    def test_pay_now_blocked_by_nahi(self):
        result = _rule_based_fallback_classification("nahi, abhi payment nahi kar sakta")
        assert result["intent"] != "PAY_NOW"

    def test_pay_now_blocked_by_cancel(self):
        result = _rule_based_fallback_classification("cancel karo, pay nahi karunga")
        assert result["intent"] != "PAY_NOW"

    def test_pay_now_clearing(self):
        result = _rule_based_fallback_classification("clearing now, done")
        assert result["intent"] == "PAY_NOW"


# ─────────────────────────────────────────────────────────────────────────────
# COMPOUND CLAUSES AND MIXED-SCRIPT UTTERANCES
# ─────────────────────────────────────────────────────────────────────────────

class TestCompoundClauses:
    """Compound sentences with multiple intents should resolve to the highest priority."""

    def test_discount_with_payment_conditional(self):
        """'If discount, then payment' → DISCOUNT, not PAY_NOW."""
        result = _rule_based_fallback_classification(
            "agar 50% discount milega toh main payment kar dunga"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_dispute_with_payment_refusal(self):
        """Dispute + refusal → DISPUTE (higher priority)."""
        result = _rule_based_fallback_classification(
            "amount galat hai, nahi dunga main"
        )
        assert result["intent"] == "DISPUTE"

    def test_discount_with_nahi_to_nahi(self):
        """Discount with conditional 'nahi to nahi' → DISCOUNT."""
        result = _rule_based_fallback_classification(
            "I need a discount of fifty percent, nahi to nahi dunga"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"

    def test_mixed_devanagari_latin_discount(self):
        """Mixed script: Devanagari discount + Latin payment."""
        result = _rule_based_fallback_classification(
            "मुझे discount चाहिए otherwise I won't pay"
        )
        assert result["intent"] == "REQUEST_DISCOUNT"


# ─────────────────────────────────────────────────────────────────────────────
# SPEECH OUTPUT SANITIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestSpeechSanitization:
    """_sanitize_speech_output must strip all non-speakable artifacts."""

    def test_removes_payment_link_placeholder(self):
        text = "Please pay via: [PAYMENT_LINK] now"
        result = _sanitize_speech_output(text)
        assert "[PAYMENT_LINK]" not in result
        assert "PAYMENT_LINK" not in result

    def test_removes_markdown_bold(self):
        text = "**Important**: Pay now"
        result = _sanitize_speech_output(text)
        assert "**" not in result
        assert "*" not in result

    def test_removes_emojis(self):
        text = "Thank you 🙏 for your payment ⏰"
        result = _sanitize_speech_output(text)
        assert "🙏" not in result
        assert "⏰" not in result

    def test_collapses_whitespace(self):
        text = "Hello    world   ji"
        result = _sanitize_speech_output(text)
        assert "  " not in result

    def test_removes_generic_brackets(self):
        text = "Click here: [LINK] or [URL]"
        result = _sanitize_speech_output(text)
        assert "[LINK]" not in result
        assert "[URL]" not in result

    def test_preserves_currency_symbol(self):
        text = "Your balance is ₹18,500"
        result = _sanitize_speech_output(text)
        assert "₹18,500" in result

    def test_clean_text_unchanged(self):
        text = "Namaste ji, aapka payment pending hai"
        result = _sanitize_speech_output(text)
        assert result == text


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY FALLBACK RULES PRIORITY ORDERING
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyFallbackPriority:
    """Verify legacy _FALLBACK_RULES order: DISPUTE > HARD_REFUSAL > DISCOUNT > PTP > NEGOTIATION > AGREED_TO_PAY > LINK."""

    def test_legacy_rules_dispute_first(self):
        from app.engine.gemini_service import _FALLBACK_RULES
        # First rule should be DISPUTE
        assert _FALLBACK_RULES[0][1] == "DISPUTE"

    def test_legacy_rules_hard_refusal_second(self):
        from app.engine.gemini_service import _FALLBACK_RULES
        assert _FALLBACK_RULES[1][1] == "HARD_REFUSAL"

    def test_legacy_rules_discount_before_ptp(self):
        from app.engine.gemini_service import _FALLBACK_RULES
        discount_idx = None
        ptp_idx = None
        for i, (_, intent, _) in enumerate(_FALLBACK_RULES):
            if intent == "REQUEST_DISCOUNT" and discount_idx is None:
                discount_idx = i
            if intent in ("PROMISE_TO_PAY", "PTP_EXCEEDS_POLICY") and ptp_idx is None:
                ptp_idx = i
        assert discount_idx is not None, "REQUEST_DISCOUNT not found in _FALLBACK_RULES"
        assert ptp_idx is not None, "PTP not found in _FALLBACK_RULES"
        assert discount_idx < ptp_idx, f"REQUEST_DISCOUNT (idx={discount_idx}) must come before PTP (idx={ptp_idx})"

    def test_legacy_rules_discount_before_agreed_to_pay(self):
        from app.engine.gemini_service import _FALLBACK_RULES
        discount_idx = None
        atp_idx = None
        for i, (_, intent, _) in enumerate(_FALLBACK_RULES):
            if intent == "REQUEST_DISCOUNT" and discount_idx is None:
                discount_idx = i
            if intent == "AGREED_TO_PAY" and atp_idx is None:
                atp_idx = i
        assert discount_idx is not None
        assert atp_idx is not None
        assert discount_idx < atp_idx, f"REQUEST_DISCOUNT (idx={discount_idx}) must come before AGREED_TO_PAY (idx={atp_idx})"


# ─────────────────────────────────────────────────────────────────────────────
# SPLIT PAYMENT FLOW TESTS (MARGIN PRESERVATION)
# ─────────────────────────────────────────────────────────────────────────────

class TestSplitPaymentFlow:
    """Tests Margin-Preserving Split Offer upon initial refusal before discount ladder."""

    def test_first_refusal_offers_split_plan(self):
        import asyncio
        import uuid
        from datetime import datetime, timezone
        from decimal import Decimal
        from app.models import Merchant, Customer, Invoice, RecoveryEvent
        from app.engine.state_machine import State
        from app.engine.policy_wrapper import execute_policy_turn
        from app.schemas import DebtorIntentClassification

        class MockSession:
            def add(self, obj): pass
            async def flush(self): pass

        m = Merchant(id=uuid.uuid4(), name="M", default_discount_cap=Decimal("0.10"), created_at=datetime.now(timezone.utc))
        c = Customer(id=uuid.uuid4(), merchant_id=m.id, name="Test Cust", phone="+919876543210", ltv_inr=Decimal("10000"), consecutive_discount_months=0)
        inv = Invoice(id=uuid.uuid4(), customer_id=c.id, merchant_id=m.id, amount_inr=Decimal("10000.00"), status="UNPAID", created_at=datetime.now(timezone.utc))
        inv.merchant = m
        inv.customer = c
        inv.recovery_events = [
            RecoveryEvent(id=uuid.uuid4(), invoice_id=inv.id, current_state=State.REMINDER_SENT, discount_offered=0.0, timestamp=datetime.now(timezone.utc))
        ]

        intent = DebtorIntentClassification(intent="REFUSAL", confidence=0.95, sentiment="HOSTILE")
        decision = asyncio.run(execute_policy_turn(inv, intent, MockSession()))

        # First refusal must offer SPLIT_OFFERED with 0% discount
        assert decision.resulting_state == State.SPLIT_OFFERED
        assert decision.authorized_discount_rate == Decimal("0.0")
        assert "Split Payment Plan" in decision.action_executed

    def test_second_refusal_advances_to_tier_1_discount(self):
        import asyncio
        import uuid
        from datetime import datetime, timezone
        from decimal import Decimal
        from app.models import Merchant, Customer, Invoice, RecoveryEvent
        from app.engine.state_machine import State
        from app.engine.policy_wrapper import execute_policy_turn
        from app.schemas import DebtorIntentClassification

        class MockSession:
            def add(self, obj): pass
            async def flush(self): pass

        m = Merchant(id=uuid.uuid4(), name="M", default_discount_cap=Decimal("0.10"), created_at=datetime.now(timezone.utc))
        c = Customer(id=uuid.uuid4(), merchant_id=m.id, name="Test Cust", phone="+919876543210", ltv_inr=Decimal("10000"), consecutive_discount_months=0)
        inv = Invoice(id=uuid.uuid4(), customer_id=c.id, merchant_id=m.id, amount_inr=Decimal("10000.00"), status="UNPAID", created_at=datetime.now(timezone.utc))
        inv.merchant = m
        inv.customer = c
        inv.recovery_events = [
            RecoveryEvent(id=uuid.uuid4(), invoice_id=inv.id, current_state=State.SPLIT_OFFERED, discount_offered=0.0, timestamp=datetime.now(timezone.utc))
        ]

        intent = DebtorIntentClassification(intent="REFUSAL", confidence=0.95, sentiment="HOSTILE")
        decision = asyncio.run(execute_policy_turn(inv, intent, MockSession()))

        # Second refusal after SPLIT_OFFERED must advance to TIER_1_DISCOUNT (5% on 10% cap)
        assert decision.resulting_state == State.TIER_1_DISCOUNT
        assert decision.authorized_discount_rate == Decimal("0.05")
        assert decision.authorized_net_amount == Decimal("9500.00")

    def test_split_offered_speech_format(self):
        import asyncio
        from app.engine.state_machine import State
        from app.engine.gemini_service import generate_grounded_speech
        from app.schemas import AgentTurnDecision
        from decimal import Decimal

        turn = AgentTurnDecision(
            intent="REFUSAL",
            confidence=0.95,
            authorized_discount_rate=Decimal("0.0"),
            authorized_net_amount=Decimal("10000.00"),
            previous_state=State.REMINDER_SENT,
            resulting_state=State.SPLIT_OFFERED,
            new_invoice_status="UNPAID",
            action_executed="Refused full payment -> Offered Split Payment Plan",
            trigger_auto_close=False,
        )
        ctx = {"customer_name": "Aarav Sharma", "merchant_name": "DemoMerchant", "amount_inr": 10000.0}
        speech = asyncio.run(generate_grounded_speech(ctx, turn))

        assert "50%" in speech
        assert "₹5,000" in speech
        assert "3 dinon" in speech or "3 din" in speech
        assert "[" not in speech and "]" not in speech  # sanitized

    def test_affirmative_acceptance_hindi_transcripts(self):
        import asyncio
        from app.engine.gemini_service import classify_debtor_intent

        # User's exact screenshot transcripts:
        res1 = asyncio.run(classify_debtor_intent("हाँ, मैं यह कर सकता हूँ"))
        assert res1.intent == "PAY_NOW"

        res2 = asyncio.run(classify_debtor_intent("ओके, मैं यह पेमेंट कर दूंगा"))
        assert res2.intent == "PAY_NOW"

        res3 = asyncio.run(classify_debtor_intent("theek hai main payment kar deta hoon"))
        assert res3.intent == "PAY_NOW"

    def test_split_plan_acceptance_transitions_to_ptp_active(self):
        import asyncio
        import uuid
        from datetime import datetime, timezone
        from decimal import Decimal
        from app.models import Merchant, Customer, Invoice, RecoveryEvent
        from app.engine.state_machine import State
        from app.engine.policy_wrapper import execute_policy_turn
        from app.engine.gemini_service import generate_grounded_speech
        from app.schemas import DebtorIntentClassification

        class MockSession:
            def add(self, obj): pass
            async def flush(self): pass

        m = Merchant(id=uuid.uuid4(), name="M", default_discount_cap=Decimal("0.10"), created_at=datetime.now(timezone.utc))
        c = Customer(id=uuid.uuid4(), merchant_id=m.id, name="Test Cust", phone="+919876543210", ltv_inr=Decimal("10000"), consecutive_discount_months=0)
        inv = Invoice(id=uuid.uuid4(), customer_id=c.id, merchant_id=m.id, amount_inr=Decimal("12000.00"), status="UNPAID", created_at=datetime.now(timezone.utc))
        inv.merchant = m
        inv.customer = c
        inv.recovery_events = [
            RecoveryEvent(id=uuid.uuid4(), invoice_id=inv.id, current_state=State.SPLIT_OFFERED, discount_offered=0.0, timestamp=datetime.now(timezone.utc))
        ]

        intent = DebtorIntentClassification(intent="PAY_NOW", confidence=0.95, sentiment="COOPERATIVE")
        decision = asyncio.run(execute_policy_turn(inv, intent, MockSession()))

        assert decision.resulting_state == State.PTP_ACTIVE
        assert decision.authorized_discount_rate == Decimal("0.0")
        assert "Split Payment Plan" in decision.action_executed
        assert decision.trigger_auto_close is True

        ctx = {"customer_name": "Vikram Malhotra", "merchant_name": "DemoMerchant", "amount_inr": 12000.0}
        speech = asyncio.run(generate_grounded_speech(ctx, decision))
        assert "split payment plan" in speech.lower()
        assert "50%" in speech


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
