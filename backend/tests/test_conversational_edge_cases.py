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
    """PTP should match on time-related keywords."""

    def test_ptp_3_din(self):
        result = _rule_based_fallback_classification("3 din mein payment kar dunga")
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
