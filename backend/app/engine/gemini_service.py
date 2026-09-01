"""
RecoveryAI — Gemini AI Integration Service (Phase 3)
=====================================================

Two public functions:
    parse_debtor_message(message, current_date) -> DebtorIntentResult
    generate_dunning_copy(invoice_data, customer_data, action_type, tier) -> DunningMessageResult

Both functions have full deterministic fallbacks — the app NEVER crashes if
GEMINI_API_KEY is missing, rate-limited, or network is down.

SDK: google-genai 1.x (google.genai) or google-generativeai
Model: gemini-3.6-flash (falls back to gemini-2.5-flash on quota errors)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.config import settings

logger = logging.getLogger(__name__)

# ── Model preference ──────────────────────────────────────────────────────────
_MODEL_PRIMARY   = "gemini-3.6-flash"
_MODEL_FALLBACK  = "gemini-3.6-flash"

# ── Lazy client init ─────────────────────────────────────────────────────────
_genai_client = None
_legacy_model = None

def _get_client():
    global _genai_client, _legacy_model
    if _genai_client is not None or _legacy_model is not None:
        return _genai_client or _legacy_model
    if not settings.GEMINI_API_KEY:
        return None

    # Try google-genai first
    try:
        from google import genai  # noqa: PLC0415
        _genai_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini google.genai client initialised.")
        return _genai_client
    except Exception as e1:
        logger.debug("google.genai client init failed: %s. Trying google.generativeai", e1)

    # Try legacy google.generativeai
    try:
        import google.generativeai as genai_legacy  # noqa: PLC0415
        genai_legacy.configure(api_key=settings.GEMINI_API_KEY)
        _legacy_model = genai_legacy.GenerativeModel(_MODEL_PRIMARY)
        logger.info("Gemini google.generativeai client initialised.")
        return _legacy_model
    except Exception as e2:
        logger.warning("Gemini client init failed: %s — using fallback mode", e2)
        return None


def _call_gemini(prompt: str) -> str | None:
    """
    Send a prompt to Gemini and return text response.
    Returns None on any error (triggers fallback in callers).
    """
    client = _get_client()
    if client is None:
        return None

    # Case 1: google-genai Client
    if hasattr(client, "models"):
        for model_name in (_MODEL_PRIMARY, _MODEL_FALLBACK):
            try:
                from google.genai import types as genai_types  # noqa: PLC0415
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=1024,
                    ),
                )
                if hasattr(response, "candidates") and response.candidates:
                    parts = response.candidates[0].content.parts
                    text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
                    if text_parts:
                        return "".join(text_parts)
                if hasattr(response, "text") and response.text:
                    return response.text
            except Exception as exc:
                logger.warning("Gemini google.genai model %s error: %s", model_name, exc)

    # Case 2: google.generativeai GenerativeModel
    elif hasattr(client, "generate_content"):
        try:
            response = client.generate_content(prompt)
            if hasattr(response, "text") and response.text:
                return response.text
        except Exception as exc:
            logger.warning("Gemini legacy model error: %s", exc)

    return None


async def _call_gemini_async(prompt: str, timeout: float = 1.8) -> str | None:
    """
    Non-blocking async wrapper with strict 1.8s timeout.
    Executes in a separate thread so it never stalls the FastAPI event loop.
    """
    if not settings.GEMINI_API_KEY:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_call_gemini, prompt),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("Gemini async execution timed out/failed (%s), proceeding with deterministic fallback", exc)
        return None


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a text response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


_INTENT_PROMPT = """\
You are a payment recovery AI for an Indian B2B & consumer payment system.

Analyse the debtor's message below (which may be in English, Latin Hinglish, or Devanagari Hindi/English transliterations like "नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट", "आई नीड थ्री डेज़", "मंडे ट्रांसफर कर दूंगा", "Main yeh payment kar dunga", "मैं तीन दिन में पेमेंट कर दूंगा", "मैं अगले हफ्ते / 5 दिन बाद करूँगा", "नहीं, मैं आज इसको सेटल नहीं कर पाऊंगा", "Theek hai main kar deta hoon", "मेरे को और 2 दिन चाहिए", or "No, I cannot settle today") and return a JSON object with exactly these fields:
- "intent": one of ["PROMISE_TO_PAY", "PTP_EXCEEDS_POLICY", "AGREED_TO_PAY", "REQUEST_NEGOTIATION", "DISPUTE", "REQUEST_ALTERNATE_LINK", "REQUEST_DISCOUNT", "HARD_REFUSAL", "GENERAL_INQUIRY"]
- "ptp_deadline": ISO 8601 UTC timestamp string if intent is PROMISE_TO_PAY or PTP_EXCEEDS_POLICY, else null.
  Parse relative dates such as "Monday", "मंडे", "सोमवार", "kal", "कल", "Friday", "शुक्रवार", "28th", "day after tomorrow", "परसों",
  "1 day", "वन डे", "1 दिन", "2 days", "टु डेज़", "टू डेज", "2 दिन", "दो दिन", "3 days", "थ्री डेज़", "थ्री डेज", "3 डेज़", "3 दिन", "तीन दिन", "5 days", "5 दिन", "next week", "नेक्स्ट वीक", "agle hafte", "अगले हफ्ते" relative to current_date.
- "dispute_reason": extracted dispute description string if intent is DISPUTE, else null.
- "confidence": float 0.0–1.0
- "explanation": short sentence reasoning.

Current date (IST): {current_date}
Debtor message: "{message}"

Rules:
- Payment commitment within 1 to 3 days (e.g. "आई नीड थ्री डेज़", "थ्री डेज़", "नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट", "मंडे ट्रांसफर कर दूंगा", "kal 5 baje", "1 दिन में कर दूंगा", "दो दिन में", "टु डेज़", "2 days", "3 din mein", "तीन दिन में पेमेंट कर दूंगा", "मेरे को 2 दिन चाहिए", "tomorrow", "parso") -> PROMISE_TO_PAY
  CRITICAL: If debtor specifies a future timeframe (e.g. 'आई नीड थ्री डेज़', '3 दिन में', 'three days', '2 days', 'टु डेज़') even if preceded by 'nahi' or 'नहीं' (e.g. 'नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट', 'nahi main 2 din baad dunga'), this expresses a Promise to Pay (PROMISE_TO_PAY), NOT a discount negotiation.
- Payment commitment or request EXCEEDING 3 days (e.g. "next week", "नेक्स्ट वीक", "agle hafte", "अगले हफ्ते", "4 din baad", "5 din baad", "5 दिन बाद", "10 din", "10 days", "agle mahine") -> PTP_EXCEEDS_POLICY
- Agreeing to pay or accepting terms/counter-offer (e.g. "Main yeh payment kar dunga", "Theek hai main kar deta hoon", "Haan 3 din mein theek hai", "Haan theek hai", "I will pay", "Abhi kar deta hoon") -> AGREED_TO_PAY
- Stating inability/unwillingness to pay today or rejecting immediate payment without any specific future payment date (e.g. "नहीं, मैं आज इसको सेटल नहीं कर पाऊंगा", "No, I cannot settle today", "aaj paise nahi hai", "aaj possible nahi hai", "cannot pay today", "aaj nahi ho payega", "too high", "abhi nahi", "nahi kar sakta") -> REQUEST_NEGOTIATION
- Mentioning wrong amount, GST, TDS, bill discrepancy, tax (e.g. "GST galat hai", "जीएसटी गलत है", "billing error") -> DISPUTE
- Asking for UPI, QR, link, alternate payment method -> REQUEST_ALTERNATE_LINK
- Permanent refusal (e.g. "kabhi nahi dunga", "never pay", "will never pay", "do whatever you want") -> HARD_REFUSAL
"""


_FALLBACK_RULES: list[tuple[list[str], str, str]] = [
    # ─── PRIORITY 1: DISPUTE (Immediate Freeze) ─────────────────────────────
    # Must be checked FIRST — freezes all collection activity immediately.
    (
        [
            "gst", "जीएसटी", "tds", "टीडीएस", "dispute", "डिस्प्यूट",
            "wrong amount", "गलत अमाउंट", "incorrect amount", "wrong billing",
            "बिलिंग गलत", "galat amount", "galat", "गलत", "credit note", "क्रेडिट नोट",
            "tax invoice", "invoice error", "invoice galat", "already paid",
            "pehle hi pay", "disputed", "overcharged", "wrong bill",
            "fraud", "dhokha", "complaint", "nahi mangwaya", "not ordered",
            "defective", "quality issue", "service issue", "nahi liya tha",
            "बिल गलत", "ये amount गलत", "wrong charge", "order nahi kiya",
        ],
        "DISPUTE",
        "Debtor raised an invoice amount / GST / TDS / billing dispute.",
    ),
    # ─── PRIORITY 2: HARD REFUSAL ────────────────────────────────────────────
    (
        [
            "never pay", "will never pay", "kabhi nahi dunga",
            "kabhi bhi nahi dunga", "refuse forever",
            "do whatever you want", "jo karna hai karo", "court jao",
            "cancel karo", "I refuse to pay", "i refuse",
        ],
        "HARD_REFUSAL",
        "Debtor expressed explicit permanent refusal to pay forever.",
    ),
    # ─── PRIORITY 3: REQUEST_DISCOUNT / CONCESSION ───────────────────────────
    # Must be checked BEFORE PTP and PAY_NOW: if discount keywords are present
    # in the same sentence as "payment" or "dunga", discount takes priority.
    (
        [
            "discount", "डिस्काउंट", "छूट", "concession", "कंसेंशन",
            "waiver", "वेवर", "kam karo", "कम करो", "paisa kam",
            "kam kardo", "give discount", "kuch discount",
            "thoda kam", "thoda discount", "discount milega",
            "settlement", "डिस्काउंट चाहिए", "discount chahiye",
            "discount de do", "discount do", "percent off",
            "परसेंट", "प्रतिशत", "chhoot", "riyayat", "रियायत",
            "kam ho", "kam kar", "reduce", "less karo",
        ],
        "REQUEST_DISCOUNT",
        "Debtor explicitly requested a discount or settlement concession.",
    ),
    # ─── PRIORITY 4: PTP_EXCEEDS_POLICY (>3 days) ───────────────────────────
    (
        [
            "next week", "नेक्स्ट वीक", "agle hafte", "अगले हफ्ते",
            "agle hafte /", "5 din baad", "5 दिन बाद",
            "4 din", "5 din", "6 din", "7 din", "8 din", "9 din",
            "10 din", "15 din",
            "4 days", "5 days", "6 days", "7 days", "10 days",
            "4 दिन", "5 दिन", "6 दिन", "7 दिन", "10 दिन", "15 दिन",
            "4 डेज़", "5 डेज़", "5 डेज",
            "चार दिन", "पांच दिन", "छह दिन", "सात दिन", "दस दिन",
            "month end", "agle mahine", "अगले महीने", "next month",
            "2 weeks", "do hafte", "दो हफ्ते",
        ],
        "PTP_EXCEEDS_POLICY",
        "Debtor requested a payment timeline exceeding the 3-day recovery policy limit.",
    ),
    # ─── PRIORITY 5: PROMISE_TO_PAY (within 1-3 days) ───────────────────────
    (
        [
            "आई नीड थ्री डेज़", "आई नीड थ्री डेज", "आई नीड 3 डेज़",
            "नीड थ्री डेज़", "थ्री डेज़", "थ्री डेज",
            "3 डेज़", "3 डेज", "टु डेज़", "टु डेज", "टू डेज़", "टू डेज",
            "2 डेज़", "2 डेज", "वन डे", "1 डे",
            "1 दिन", "एक दिन", "1 day", "one day", "1 din", "ek din",
            "2 दिन", "दो दिन", "2 days", "two days", "2 din", "do din",
            "3 दिन", "तीन दिन", "3 days", "three days", "3 din", "teen din",
            "kal", "कल", "parso", "परसों",
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "मंडे", "सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार",
            "28th", "29th", "30th", "31st",
            "day after", "tomorrow",
        ],
        "PROMISE_TO_PAY",
        "Debtor indicated an explicit payment timeline commitment within policy.",
    ),
    # ─── PRIORITY 6: REQUEST_NEGOTIATION / SOFT REFUSAL ──────────────────────
    (
        [
            "cannot settle", "cannot pay today", "not possible today",
            "cannot pay", "aaj nahi", "aaj paise nahi", "aaj possible nahi",
            "no i cannot", "can not settle",
            "आई कैन नॉट", "आज नहीं", "नहीं हो सकता",
            "नहीं, मैं आज", "आज इसको सेटल नहीं",
            "सेटल नहीं कर पाऊंगा", "सेटल नहीं",
            "नहीं कर पाऊंगा", "आज नहीं होगा", "नहीं हो पाएगा",
            "aaj settle nahi hoga", "abhi paise nahi hain",
            "too high", "reduce more", "give me more discount",
            "will not pay today", "not today",
            "aaj nahi ho payega", "nahi kar paunga",
            "nahi ho payega", "nahi kar sakta", "नहीं कर सकता",
            "not paying today", "nahi ho sakta", "nahi chalega",
            "not enough", "still not enough", "too low",
        ],
        "REQUEST_NEGOTIATION",
        "Debtor indicated inability/unwillingness to settle today or refusal of proposed terms.",
    ),
    # ─── PRIORITY 7: AGREED_TO_PAY (Affirmative commitment) ─────────────────
    # Only matches when NOT preceded by negation in the same sentence.
    # Moved AFTER discount/dispute/refusal to prevent false positives.
    (
        [
            "main yeh payment kar dunga", "main ye payment kar dunga",
            "main yeh payment kar deta", "yeh payment kar dunga",
            "main payment kar dunga", "payment kar dunga",
            "theek hai main kar deta", "theek hai main payment",
            "theek hai kar deta", "theek hai kar dunga",
            "theek hai payment", "kar deta hoon", "kar deta hu",
            "i will pay", "i will make the payment",
            "i agree to pay", "will pay now", "pay now",
            "abhi kar deta", "abhi kar dunga", "abhi pay kar",
            "main kar deta", "main kar dunga", "haan kar dunga",
            "haan main", "haan 3 din", "haan theek", "haan chalega",
            "मैं यह पेमेंट कर दूंगा", "मैं पेमेंट कर दूंगा",
            "ठीक है मैं कर देता हूँ", "मैं कर देता हूँ",
            "मैं अभी पेमेंट",
        ],
        "AGREED_TO_PAY",
        "Debtor agreed to settle payment without a specific future date.",
    ),
    # ─── PRIORITY 8: REQUEST_ALTERNATE_LINK ──────────────────────────────────
    (
        [
            "payment link", "पेमेंट लिंक", "alternate link",
            "qr code", "क्यूआर", "upi", "यूपीआई", "upi id",
            "send link", "लिंक भेजो", "bhejo link", "link bhejo",
            "gpay", "phonepe", "paytm", "razorpay",
            "link expired", "link not working", "reshare",
        ],
        "REQUEST_ALTERNATE_LINK",
        "Debtor requested a fresh/alternate digital payment link or QR code.",
    ),
]


def _extract_ptp_date(msg: str, ref: datetime) -> datetime | None:
    """Rule-based date extraction for fallback mode (Latin + Devanagari)."""
    days = {
        "monday": 0, "मंडे": 0, "सोमवार": 0,
        "tuesday": 1, "ट्यूसडे": 1, "मंगलवार": 1,
        "wednesday": 2, "वेडनसडे": 2, "बुधवार": 2,
        "thursday": 3, "थर्सडे": 3, "गुरुवार": 3,
        "friday": 4, "फ्राइडे": 4, "शुक्रवार": 4,
        "saturday": 5, "सैटरडे": 5, "शनिवार": 5,
        "sunday": 6, "संडे": 6, "रविवार": 6,
    }
    for day_name, day_num in days.items():
        if day_name in msg:
            delta = (day_num - ref.weekday()) % 7
            if delta == 0:
                delta = 7
            return (ref + timedelta(days=delta)).replace(hour=18, minute=0, second=0, microsecond=0)

    if any(k in msg for k in ["3 दिन", "3 days", "तीन दिन", "3 din", "teen din", "थ्री डेज़", "थ्री डेज", "3 डेज़", "3 डेज"]):
        return (ref + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
    if any(k in msg for k in ["2 दिन", "2 days", "दो दिन", "2 din", "do din", "parso", "परसों", "day after", "टु डेज़", "टु डेज", "टू डेज़", "टू डेज", "2 डेज़", "2 डेज"]):
        return (ref + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
    if any(k in msg for k in ["1 दिन", "1 day", "एक दिन", "one day", "1 din", "ek din", "kal", "कल", "tomorrow", "वन डे", "1 डे"]):
        return (ref + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "4 दिन" in msg or "4 days" in msg or "4 din" in msg:
        return (ref + timedelta(days=4)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "5 दिन" in msg or "5 days" in msg or "5 din" in msg:
        return (ref + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "next week" in msg or "agle hafte" in msg or "अगले हफ्ते" in msg:
        return (ref + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)

    m = re.search(r"\b(\d{1,2})(st|nd|rd|th)?\b", msg)
    if m:
        day_num = int(m.group(1))
        if 1 <= day_num <= 31:
            try:
                target = ref.replace(day=day_num, hour=18, minute=0, second=0, microsecond=0)
                if target < ref:
                    if ref.month == 12:
                        target = target.replace(year=ref.year + 1, month=1)
                    else:
                        target = target.replace(month=ref.month + 1)
                return target
            except ValueError:
                pass

    return ref + timedelta(days=3)


def _fallback_parse_intent(message: str, current_date: datetime) -> dict:
    msg_lower = message.lower()
    for keywords, intent, explanation in _FALLBACK_RULES:
        if any(kw in msg_lower for kw in keywords):
            ptp_deadline = None
            if intent == "PROMISE_TO_PAY":
                ptp_deadline = _extract_ptp_date(msg_lower, current_date)
            return {
                "intent": intent,
                "ptp_deadline": ptp_deadline.isoformat() if ptp_deadline else None,
                "dispute_reason": message if intent == "DISPUTE" else None,
                "confidence": 0.88,
                "explanation": explanation,
                "used_fallback": True,
            }
    return {
        "intent": "GENERAL_INQUIRY",
        "ptp_deadline": None,
        "dispute_reason": None,
        "confidence": 0.60,
        "explanation": "General debtor message received.",
        "used_fallback": True,
    }


async def parse_debtor_message(message: str, current_date: datetime | None = None):
    """
    Parse debtor intent and extract PTP date.
    Returns a DebtorIntentResult instance.
    """
    from app.schemas import DebtorIntentResult  # noqa: PLC0415

    if current_date is None:
        current_date = datetime.now(timezone.utc)

    prompt = _INTENT_PROMPT.format(
        current_date=current_date.strftime("%Y-%m-%d %H:%M UTC"),
        message=message,
    )

    raw = await _call_gemini_async(prompt, timeout=1.8)
    parsed = _extract_json(raw) if raw else None

    if parsed and "intent" in parsed:
        ptp = None
        if parsed.get("ptp_deadline"):
            try:
                ptp_str = str(parsed["ptp_deadline"]).replace("Z", "+00:00")
                ptp = datetime.fromisoformat(ptp_str)
                if ptp.tzinfo is None:
                    ptp = ptp.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ptp = _extract_ptp_date(message.lower(), current_date)

        return DebtorIntentResult(
            intent=parsed.get("intent", "GENERAL_INQUIRY"),
            ptp_deadline=ptp,
            dispute_reason=parsed.get("dispute_reason"),
            confidence=float(parsed.get("confidence", 0.90)),
            explanation=str(parsed.get("explanation", "Gemini intent classification completed.")),
            used_fallback=False,
        )
    else:
        logger.info("Gemini API fallback used for intent parsing")
        fb = _fallback_parse_intent(message, current_date)
        ptp = None
        if fb.get("ptp_deadline"):
            try:
                ptp = datetime.fromisoformat(fb["ptp_deadline"])
                if ptp.tzinfo is None:
                    ptp = ptp.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        return DebtorIntentResult(
            intent=fb["intent"],
            ptp_deadline=ptp,
            dispute_reason=fb["dispute_reason"],
            confidence=fb["confidence"],
            explanation=fb["explanation"],
            used_fallback=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Empathetic Dunning Copy Generator
# ─────────────────────────────────────────────────────────────────────────────

_DUNNING_PROMPT = """\
You are a payment recovery AI for an Indian merchant platform.
Generate a concise, polite, culturally appropriate SMS / WhatsApp message in Indian English (with subtle warm Hinglish tone if suitable).

Parameters:
- Customer Name: {customer_name}
- Merchant Name: {merchant_name}
- Invoice Amount: ₹{amount_inr}
- Net Payable: ₹{net_payable}
- Failure Reason: {failure_reason}
- Action Type: {action_type}

Rules:
- Output JSON with keys: "subject", "body", "channel" ("WHATSAPP")
- Make message short (2-3 sentences max).
- Place placeholder [PAYMENT_LINK] where relevant.
- Return ONLY valid JSON.
"""

_FALLBACK_TEMPLATES: dict[str, dict] = {
    "SOFT_REMINDER": {
        "subject": "Payment reminder for your invoice",
        "body": "Namaste {customer_name} ji! A gentle reminder that your invoice of ₹{amount_inr} with {merchant_name} is due. Kindly clear via: [PAYMENT_LINK] 🙏",
    },
    "ALTERNATE_LINK": {
        "subject": "Alternate payment link for your invoice",
        "body": "Hi {customer_name}, your payment of ₹{amount_inr} encountered a gateway issue ({failure_reason_label}). Please use this fresh UPI link to complete payment: [PAYMENT_LINK] 🙏",
    },
    "PTP_CONFIRMATION": {
        "subject": "Payment schedule confirmed",
        "body": "Thank you {customer_name} ji! We have recorded your commitment to complete payment of ₹{amount_inr}. Link for payment: [PAYMENT_LINK] 🙏",
    },
    "DISCOUNT_OFFER": {
        "subject": "Special concession offer",
        "body": "Hi {customer_name}, clear your invoice of ₹{amount_inr} today at a special price of ₹{net_payable}! Claim discount here: [PAYMENT_LINK] ⏰",
    },
    "DISPUTE_ACK": {
        "subject": "Dispute registered for invoice",
        "body": "Namaste {customer_name} ji, we have logged your billing dispute regarding ₹{amount_inr}. Our finance team is investigating and will update you shortly. 🙏",
    },
}

_FAILURE_REASON_LABELS = {
    "GATEWAY_TIMEOUT": "card gateway timeout",
    "INSUFFICIENT_FUNDS": "insufficient balance",
    "MANDATE_DECLINE": "eMandate decline",
    "EXPIRED_CARD": "expired card",
    "DISPUTED_AMOUNT": "disputed amount",
}


async def generate_dunning_copy(
    invoice_data: dict,
    customer_data: dict,
    action_type: str,
    tier: int | None = None,
):
    """
    Generate contextual dunning copy.
    Returns a DunningMessageResult instance.
    """
    from app.schemas import DunningMessageResult  # noqa: PLC0415

    amount = float(invoice_data.get("amount_inr", 0))
    cap = float(invoice_data.get("merchant_cap", 0.10))
    net_payable = amount
    if tier and tier in (1, 2, 3):
        from app.engine.calculator import calculator  # noqa: PLC0415
        from decimal import Decimal
        months = int(customer_data.get("consecutive_discount_months", 0))
        res = calculator.calculate(Decimal(str(cap)), months, tier, Decimal(str(amount)))
        net_payable = float(res.net_payable_inr)

    vars_dict = {
        "customer_name": customer_data.get("name", "Valued Customer"),
        "merchant_name": invoice_data.get("merchant_name", "Merchant"),
        "amount_inr": f"{amount:,.2f}",
        "net_payable": f"{net_payable:,.2f}",
        "failure_reason": invoice_data.get("failure_reason", "Payment Issue"),
        "failure_reason_label": _FAILURE_REASON_LABELS.get(invoice_data.get("failure_reason", ""), "gateway error"),
        "action_type": action_type,
    }

    prompt = _DUNNING_PROMPT.format(**vars_dict)
    raw = await _call_gemini_async(prompt, timeout=1.8)
    parsed = _extract_json(raw) if raw else None

    if parsed and "subject" in parsed and "body" in parsed:
        return DunningMessageResult(
            subject=parsed["subject"],
            body=parsed["body"],
            channel=parsed.get("channel", "WHATSAPP"),
            action_type=action_type,
            used_fallback=False,
        )
    else:
        tpl = _FALLBACK_TEMPLATES.get(action_type, _FALLBACK_TEMPLATES["SOFT_REMINDER"])
        return DunningMessageResult(
            subject=tpl["subject"].format(**vars_dict),
            body=tpl["body"].format(**vars_dict),
            channel="WHATSAPP",
            action_type=action_type,
            used_fallback=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Structured Gemini 2.5 Flash Intent Classification (Pydantic Output)
# ─────────────────────────────────────────────────────────────────────────────

_STRUCTURED_INTENT_PROMPT = """\
You are an intent classification engine for an Indian payment recovery conversational system.
Analyze the debtor's speech (in English, Hindi, Devanagari Hindi, or Latin Hinglish) and return a JSON object conforming strictly to this structure:
- "intent": exactly one of ["PAY_NOW", "PROMISE_TO_PAY", "REQUEST_DISCOUNT", "REFUSAL", "DISPUTE", "TECHNICAL_PROBLEM", "REQUEST_PAYMENT_LINK", "UNKNOWN"]
- "confidence": float between 0.0 and 1.0
- "customer_stated_discount_pct": numeric percentage value if the customer explicitly requested a discount percentage, else null. (Informational only)
- "ptp_date_extracted": raw text timeframe or date if debtor promises future payment, else null.
- "dispute_reason": summary of dispute if customer claims incorrect billing, GST, TDS, quality, or fraud issues, else null.
- "sentiment": one of ["COOPERATIVE", "DISTRESSED", "EVASIVE", "HOSTILE"]

Context:
- Customer Name: {customer_name}
- Invoice Amount: ₹{amount_inr}
- Current State: {current_state}
- Current Discount Tier: {current_tier}
- Failure Reason: {failure_reason}

Debtor Speech Transcript: "{transcript}"

STRICT PRIORITY-ORDERED CLASSIFICATION RULES:

PRIORITY 1 — DISPUTE (highest priority):
If debtor mentions wrong amount, billing error, GST/TDS mismatch, fraud, defective goods, "not ordered", or complaint → "DISPUTE"

PRIORITY 2 — REFUSAL:
If debtor explicitly says "never pay", "refuse", "court jao", "cancel karo", "won't pay ever" → "REFUSAL"

PRIORITY 3 — REQUEST_DISCOUNT (takes precedence over PAY_NOW/PTP):
If debtor mentions discount, concession, percentage off, "kam karo", "छूट", "डिस्काउंट", percentage words — even if the sentence also contains "payment" or "pay" in conditional/negative context → "REQUEST_DISCOUNT"

PRIORITY 4 — PROMISE_TO_PAY:
If debtor mentions a future date/timeframe to pay ("kal", "3 din", "Friday", "next week") → "PROMISE_TO_PAY"

PRIORITY 5 — TECHNICAL_PROBLEM:
If debtor reports UPI failure, gateway timeout, bank issue, OTP not received → "TECHNICAL_PROBLEM"

PRIORITY 6 — PAY_NOW (lowest intent priority):
Only if debtor expresses clear, unconditional, affirmative commitment to pay immediately — WITHOUT negation ("नहीं", "nahi", "won't", "cancel") and WITHOUT discount/concession keywords → "PAY_NOW"

PRIORITY 7 — REQUEST_PAYMENT_LINK:
If debtor asks for payment link or QR code → "REQUEST_PAYMENT_LINK"

CRITICAL: If a sentence contains BOTH discount keywords AND payment/pay words (e.g. "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए नहीं तो मैं यह पेमेंट नहीं कर पाऊंगा"), classify as REQUEST_DISCOUNT, NOT PAY_NOW.

FEW-SHOT EXAMPLES:
- "हाँ, मैं यह कर सकता हूँ" → {{"intent":"PAY_NOW"}}
- "ओके, मैं यह पेमेंट कर दूंगा" → {{"intent":"PAY_NOW"}}
- "Theek hai main 50% abhi pay kar deta hoon" → {{"intent":"PAY_NOW"}}
- "Haan chalega, main kar deta hoon" → {{"intent":"PAY_NOW"}}
- "Yes, I can do this" → {{"intent":"PAY_NOW"}}
- "मुझे फिफ्टी परसेंट डिस्काउंट चाहिए नहीं तो मैं यह पेमेंट नहीं कर पाऊंगा" → {{"intent":"REQUEST_DISCOUNT","customer_stated_discount_pct":50.0}}
- "गलत बिल भेजा है आपने, मैं पैसे नहीं दूंगा" → {{"intent":"DISPUTE","dispute_reason":"Wrong bill sent"}}
- "5 din baad salary aane par dunga" → {{"intent":"PROMISE_TO_PAY","ptp_date_extracted":"5 din"}}
- "मैं इसको पाँच दिन में सेटल कर दूँगा" → {{"intent":"PROMISE_TO_PAY","ptp_date_extracted":"5 din"}}
- "आई विल मेक द पेमेंट विदिन थ्री डेज़" → {{"intent":"PROMISE_TO_PAY","ptp_date_extracted":"3 days"}}
- "abhi link bhejo pay karta hu" → {{"intent":"PAY_NOW"}}
- "UPI reject ho raha hai bar bar" → {{"intent":"TECHNICAL_PROBLEM"}}
- "agar 10% off milega toh aaj pay karunga" → {{"intent":"REQUEST_DISCOUNT","customer_stated_discount_pct":10.0}}
- "पेमेंट नहीं होगा, cancel karo" → {{"intent":"REFUSAL"}}
- "discount chahiye, nahi to nahi dunga" → {{"intent":"REQUEST_DISCOUNT"}}
- "thoda discount de do" → {{"intent":"REQUEST_DISCOUNT"}}

Do NOT include any reasoning or chain-of-thought fields. Output pure JSON only.
"""

_HINDI_NUM_WORDS: dict[str, float] = {
    "फिफ्टी": 50.0, "पचास": 50.0, "fifty": 50.0,
    "पच्चीस": 25.0, "twenty five": 25.0, "twenty-five": 25.0,
    "बीस": 20.0, "twenty": 20.0,
    "पंद्रह": 15.0, "fifteen": 15.0,
    "दस": 10.0, "ten": 10.0,
    "पांच": 5.0, "पाँच": 5.0, "five": 5.0,
    "तीस": 30.0, "thirty": 30.0,
    "चालीस": 40.0, "forty": 40.0,
    "साठ": 60.0, "sixty": 60.0,
    "सत्तर": 70.0, "seventy": 70.0,
    "अस्सी": 80.0, "eighty": 80.0,
    "नब्बे": 90.0, "ninety": 90.0,
    "सौ": 100.0, "hundred": 100.0,
}

def _has_negation(raw: str) -> bool:
    """Check for explicit negation in English, Hinglish, and Hindi Devanagari."""
    # Word boundary regex for Latin negation tokens to avoid false positives (e.g. 'now' matching 'no')
    if re.search(r"\b(no|not|cannot|can't|cant|can not|dont|don't|never|won't|wont|unable|refuse)\b", raw):
        return True
    # Standalone 'ना' or 'na' with word/whitespace boundaries
    if re.search(r"(?:^|\s)(?:ना|na)(?:$|\s|[.,!?])", raw):
        return True
    # Hindi / Devanagari / Transliterated multi-character negation tokens
    hindi_neg_tokens = [
        "नहीं", "नही", "nahi", "nhi", "mat", "मत", "कैंसिल", "cancel",
        "kabhi nahi", "नहीं कर पाऊंगा", "नहीं होगा", "nahi dunga",
        "nahi karunga", "not possible", "नो", "नॉट", "कैन नॉट", "कैनॉट", "कैन नाट",
        "नहीं कर सकता", "nahi kar sakta", "संभव नहीं", "असंभव",
    ]
    return any(neg in raw for neg in hindi_neg_tokens)


def _rule_based_fallback_classification(transcript: str) -> dict:
    """
    Deterministic priority-ordered intent classifier for Hindi/Hinglish/English.

    Priority Order (highest first):
    1. DISPUTE — immediate collection freeze
    2. REFUSAL — hard permanent refusal
    3. REQUEST_DISCOUNT — concession/discount request (even if 'payment' is present)
    4. PROMISE_TO_PAY — future date commitment
    5. TECHNICAL_PROBLEM — gateway/UPI/bank failures
    6. PAY_NOW — affirmative immediate payment (only if no negation/discount present)
    7. REQUEST_PAYMENT_LINK — link/QR request
    8. UNKNOWN — fallback
    """
    raw = transcript.lower().strip()
    has_negation = _has_negation(raw)

    # ── PRIORITY 1: DISPUTE (Immediate Freeze) ──────────────────────────────
    if any(w in raw for w in [
        "galat", "गलत", "wrong", "dispute", "gst", "जीएसटी", "tds", "टीडीएस",
        "billing error", "deliverable", "dhokha", "not owe", "don't owe", "not my",
        "mismatch", "fraud", "complaint", "nahi mangwaya", "not ordered",
        "defective", "quality issue", "service issue", "wrong bill",
        "बिल गलत", "overcharged", "incorrect amount", "already paid",
        "nahi liya tha", "order nahi kiya", "wrong charge",
    ]):
        return {
            "intent": "DISPUTE",
            "confidence": 0.97,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": f"Debtor stated discrepancy: {transcript[:120]}",
            "sentiment": "EVASIVE",
        }

    # ── PRIORITY 2: HARD REFUSAL ─────────────────────────────────────────────
    if any(w in raw for w in [
        "never pay", "will never pay", "kabhi nahi dunga", "kabhi bhi nahi",
        "refuse forever", "do whatever you want", "jo karna hai karo",
        "court jao", "cancel karo", "i refuse to pay", "i refuse",
    ]):
        return {
            "intent": "REFUSAL",
            "confidence": 0.97,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "HOSTILE",
        }

    # Check if raw transcript is describing full amount split or 50% now 50% later (NOT requesting discount)
    is_explicit_full_split = any(w in raw for w in [
        "फुल अमाउंट", "full amount", "ful amount", "पूरा अमाउंट", "ओरिजिनल", "original amount",
        "पचास परसेंट अभी", "पचास परसेंट बाद", "50% abhi", "50% baad", "50% now", "50% later",
        "आधा अभी", "आधा बाद", "aadha abhi", "aadha baad",
    ]) and not any(w in raw for w in ["discount wala", "डिस्काउंट वाला", "discount mein", "डिस्काउंट में", "discount me", "डिस्काउंट मे"])

    # ── PRIORITY 3: REQUEST_DISCOUNT (before PTP and PAY_NOW) ────────────────
    # Discount keywords and Hindi number words take precedence, unless user is describing full-amount split terms.
    has_discount_kw = False
    pct_val: float | None = None

    if not is_explicit_full_split:
        has_discount_kw = any(w in raw for w in [
            "discount", "डिस्काउंट", "chhoot", "छूट", "kam karo", "kam kar", "kam ho",
            "concession", "waiver", "reduce", "रियायत", "कम करो", "कम कर",
            "settlement", "discount chahiye", "डिस्काउंट चाहिए",
            "discount de do", "thoda discount", "thoda kam", "kuch discount",
            "discount milega", "paisa kam", "kam kardo", "less karo",
            "percent off",
        ])

        # Try numeric regex: e.g. "50%", "25 percent", "50 प्रतिशत", "50 परसेंट"
        pct_match = re.search(r"(\d+(\.\d+)?)\s*(%|percent|pratishat|प्रतिशत|परसेंट)?", raw)
        if pct_match and pct_match.group(1):
            try:
                val = float(pct_match.group(1))
                if 1.0 <= val <= 100.0 and (has_discount_kw or "%" in raw or "percent" in raw or "प्रतिशत" in raw or "परसेंट" in raw):
                    pct_val = val
            except (ValueError, TypeError):
                pass

        # Try word numbers: e.g. "fifty percent", "फिफ्टी परसेंट", "पचास प्रतिशत"
        if pct_val is None:
            for word, num in _HINDI_NUM_WORDS.items():
                if word in raw:
                    # Only assign if discount context is present
                    if has_discount_kw or "परसेंट" in raw or "प्रतिशत" in raw or "percent" in raw or "%" in raw:
                        pct_val = num
                        break

    if (has_discount_kw or pct_val is not None) and not is_explicit_full_split:
        return {
            "intent": "REQUEST_DISCOUNT",
            "confidence": 0.95 if pct_val is not None else 0.85,
            "customer_stated_discount_pct": pct_val,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    # ── PRIORITY 4: PROMISE_TO_PAY ───────────────────────────────────────────
    ptp_keywords = [
        "friday", "monday", "tuesday", "wednesday", "thursday", "saturday", "sunday",
        "मंडे", "सोमवार", "ट्यूजडे", "ट्यूसडे", "मंगलवार", "वेडनसडे", "बुधवार", "थर्सडे", "गुरुवार", "फ्राइडे", "शुक्रवार", "सैटरडे", "शनिवार", "संडे", "रविवार",
        "kal", "कल", "parso", "parson", "परसों", "tomorrow", "day after",
        "3 din", "teen din", "तीन दिन", "3 days", "three days", "3 डेज़", "3 डेज", "थ्री डेज़", "थ्री डेज", "विदिन थ्री डेज़", "विदिन 3 डेज़",
        "2 din", "do din", "दो दिन", "2 days", "two days", "2 डेज़", "2 डेज", "टू डेज़", "टू डेज", "टु डेज़", "टु डेज",
        "1 din", "ek din", "एक दिन", "1 day", "one day", "1 डे", "वन डे",
        "4 din", "char din", "चार दिन", "4 days", "4 डेज़", "4 डेज",
        "5 din", "paanch din", "panch din", "पांच दिन", "पाँच दिन", "पाँच", "पांच", "5 days", "5 डेज़", "5 डेज", "फाइव डेज़", "फाइव डेज",
        "6 din", "छह दिन", "6 days", "7 din", "saat din", "सात दिन", "7 days", "10 din", "दस दिन", "10 days", "15 din", "15 days",
        "next week", "agle hafte", "अगले हफ्ते", "नेक्स्ट वीक", "week", "hafte", "hafta", "हफ्ते",
        "salary aane par", "salary aayegi", "tareekh", "tarikh", "महीने", "month end", "agle mahine",
        "विदिन", "within",
    ]
    matched_ptp = None
    for kw in ptp_keywords:
        if kw in raw:
            matched_ptp = kw
            break

    if not matched_ptp:
        ptp_regex = re.search(
            r"(?:विदिन|within|in)?\s*(\d+|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ|दस|one|two|three|four|five|six|seven|eight|nine|ten|थ्री|टू|वन|फोर|फाइव)\s*(days?|दिन|din|डेज़|डेज|hafta|hafte|week|weeks|mahina|mahine|month|months)",
            raw
        )
        if ptp_regex:
            matched_ptp = ptp_regex.group(0)

    if matched_ptp:
        return {
            "intent": "PROMISE_TO_PAY",
            "confidence": 0.95,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": matched_ptp,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    # ── PRIORITY 5: TECHNICAL_PROBLEM (Gateway / UPI) ────────────────────────
    if any(w in raw for w in [
        "gateway", "upi", "यूपीआई", "failed", "fail", "timeout", "debit", "server",
        "bank issue", "payment nahi ja rahi", "nahi ho raha", "stuck", "error",
        "otp", "link expired", "app crash", "credit nahi",
    ]):
        return {
            "intent": "TECHNICAL_PROBLEM",
            "confidence": 0.95,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    # ── PRIORITY 6: REQUEST_PAYMENT_LINK ─────────────────────────────────────
    if any(w in raw for w in [
        "link bhejo", "payment link", "send link", "qr code", "qr", "लिंक भेजो",
        "bhejo link", "link send karo", "send the link",
    ]):
        return {
            "intent": "REQUEST_PAYMENT_LINK",
            "confidence": 0.95,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    # ── PRIORITY 7: PAY_NOW — Only if affirmative AND no negation present ────
    pay_now_phrases = [
        # Multi-word English / Latin Hinglish
        "pay now", "abhi payment kar", "abhi kar deta", "turant pay",
        "clearing now", "abhi kar dunga", "main pay kar raha",
        "karta hoon", "kar deta hoon", "settle now", "ready to pay",
        "abhi pay", "i will pay", "i can pay", "i can do this", "i can do that",
        "i agree", "agreed", "sounds good", "theek hai", "thik hai",
        "chalega", "manzoor", "kar dunga", "kar deta hu",
        "kar sakta hoon", "kar sakta hu", "kar sakte hain", "kar denge", "de dunga",
        "haan main", "yes i can", "i will settle", "settle kar dunga", "payment kar dunga",
        "main kar dunga", "mai kar dunga", "main kar deta", "main yeh kar", "mai yeh kar",
        "yeh kar sakta", "ye kar sakta", "yeh kar dunga", "ye kar dunga",
        "aaj settle", "settle today", "pay today", "aaj pay", "aaj hi", "aaj kar dunga",
        "isko aaj settle", "main isko aaj", "clearing today", "will settle today",
        "dhanyawad", "shukriya", "thanks", "thank you",
        "split karna", "split payment", "split karunga", "split kar do", "split kar doon",
        "karna chahunga", "karna chahungi", "karna chahta", "karna chahta hoon", "karna chahta hu",
        "original amount", "full amount",
        # Devanagari Hindi
        "अभी पे", "abhi pe", "हाँ", "हां", "हूँ", "हूं",
        "कर सकता हूँ", "कर सकता हूं", "कर सकता", "कर सकते हैं",
        "कर दूंगा", "कर दूँगा", "कर देता हूँ", "कर देता हूं", "करूँगा", "करूंगा",
        "यह कर सकता", "ये कर सकता", "यह पेमेंट", "ये पेमेंट", "पेमेंट कर दूंगा", "पेमेंट कर दूँगा",
        "करना चाहूँगा", "करना चाहूंगा", "करना चाहता हूँ", "करना चाहता हूं", "करना चाहूँगी", "करना चाहूंगी",
        "चाहुंगा", "चाहूंगा", "चाहूँगी", "चाहूंगी",
        "स्प्लिट करना", "स्प्लिट", "स्प्लिट पेमेंट", "स्प्लिट ऑप्शन",
        "ओरिजिनल अमाउंट", "ओरिजिनल", "पूरा अमाउंट", "फुल अमाउंट",
        "ओके", "ठीक है", "सही है", "मंज़ूर है", "मंजूर है", "चलेगा", "डन", "सहमत",
        "पे करता हूँ", "पे कर दूंगा", "पे कर दूँगा", "दे दूंगा", "दे दूँगा", "सेटल कर दूंगा", "सेटल कर दूँगा",
        "आज सेटल", "आज ही", "आज कर दूंगा", "आज कर दूँगा", "आज पे", "आज पेमेंट", "इसको आज सेटल",
        "धन्यवाद", "शुक्रिया", "थैंक यू", "थैंक्स",
    ]

    has_standalone_affirmation = bool(re.search(r"\b(ha|haan|ok|okay|yes|done|sure|agree|thanks|thank)\b", raw))

    if not has_negation and (has_standalone_affirmation or any(w in raw for w in pay_now_phrases)):
        return {
            "intent": "PAY_NOW",
            "confidence": 0.95,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    if any(w in raw for w in ["link", "लिंक"]):
        return {
            "intent": "REQUEST_PAYMENT_LINK",
            "confidence": 0.90,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "COOPERATIVE",
        }

    # ── PRIORITY 8: SOFT REFUSAL (negation present but no other match) ───────
    if has_negation or any(w in raw for w in [
        "nahi karunga", "nahi dunga", "refuse", "too low", "too high",
        "not possible", "nahi hoga", "no", "नो", "won't pay", "kabhi nahi",
        "still not enough", "not enough", "cannot pay", "cannot", "can not",
        "not make", "cannot make", "आई कैन नॉट", "कैन नॉट", "कैनॉट", "नहीं कर सकता",
        "nahi kar sakta", "संभव नहीं", "paise nahi", "not today", "aaj nahi",
    ]):
        return {
            "intent": "REFUSAL",
            "confidence": 0.85,
            "customer_stated_discount_pct": None,
            "ptp_date_extracted": None,
            "dispute_reason": None,
            "sentiment": "EVASIVE",
        }

    # ── DEFAULT: UNKNOWN ─────────────────────────────────────────────────────
    return {
        "intent": "UNKNOWN",
        "confidence": 0.50,
        "customer_stated_discount_pct": None,
        "ptp_date_extracted": None,
        "dispute_reason": None,
        "sentiment": "COOPERATIVE",
    }


async def classify_debtor_intent(
    transcript: str,
    invoice_context: dict | None = None,
) -> DebtorIntentClassification:
    """
    Classify debtor speech using Gemini 2.5 Flash with structured Pydantic schema output.
    Falls back to deterministic rule-based parsing on timeout/error.
    """
    from decimal import Decimal
    from app.schemas import DebtorIntentClassification  # noqa: PLC0415

    ctx = invoice_context or {}
    vars_dict = {
        "customer_name": ctx.get("customer_name", "Valued Customer"),
        "amount_inr": f"{float(ctx.get('amount_inr', 0)):,.2f}",
        "current_state": ctx.get("current_state", "TRIGGERED"),
        "current_tier": str(ctx.get("current_tier", 0)),
        "failure_reason": ctx.get("failure_reason", "Payment due"),
        "transcript": transcript,
    }

    prompt = _STRUCTURED_INTENT_PROMPT.format(**vars_dict)
    raw = await _call_gemini_async(prompt, timeout=1.8)
    parsed = _extract_json(raw) if raw else None

    valid_intents = {
        "PAY_NOW", "PROMISE_TO_PAY", "REQUEST_DISCOUNT", "REFUSAL",
        "DISPUTE", "TECHNICAL_PROBLEM", "REQUEST_PAYMENT_LINK", "UNKNOWN"
    }

    raw_lower = transcript.lower()
    is_split = bool(re.search(r"(split|स्प्लिट|aadha|आधा|half|50%)", raw_lower))

    if parsed and parsed.get("intent") in valid_intents:
        try:
            stated_discount = None
            if parsed.get("customer_stated_discount_pct") is not None:
                stated_discount = Decimal(str(parsed["customer_stated_discount_pct"]))

            return DebtorIntentClassification(
                intent=parsed["intent"],
                confidence=float(parsed.get("confidence", 0.95)),
                customer_stated_discount_pct=stated_discount,
                ptp_date_extracted=parsed.get("ptp_date_extracted"),
                dispute_reason=parsed.get("dispute_reason"),
                sentiment=parsed.get("sentiment", "COOPERATIVE"),
                raw_transcript=transcript,
                is_split_requested=is_split,
            )
        except Exception as exc:
            logger.debug("Failed parsing Gemini structured intent object: %s", exc)

    # Use deterministic fallback
    fb = _rule_based_fallback_classification(transcript)
    stated_discount_fb = None
    if fb.get("customer_stated_discount_pct") is not None:
        stated_discount_fb = Decimal(str(fb["customer_stated_discount_pct"]))

    return DebtorIntentClassification(
        intent=fb["intent"],
        confidence=fb["confidence"],
        customer_stated_discount_pct=stated_discount_fb,
        ptp_date_extracted=fb["ptp_date_extracted"],
        dispute_reason=fb["dispute_reason"],
        sentiment=fb.get("sentiment", "COOPERATIVE"),
        raw_transcript=transcript,
        is_split_requested=is_split,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Grounded Conversational Speech Generation
# ─────────────────────────────────────────────────────────────────────────────

async def generate_grounded_speech(
    invoice_context: dict,
    turn_decision: AgentTurnDecision,
) -> str:
    """
    Generate natural Hinglish conversational speech strictly grounded in
    authoritative numbers provided by the deterministic Policy Engine.

    All outputs are sanitized: no [PAYMENT_LINK], markdown, or emoji.
    """
    state = turn_decision.resulting_state
    cust_name = invoice_context.get("customer_name", "Customer")
    merchant_name = invoice_context.get("merchant_name", "our platform")

    # 1. Terminal Escalation Invariant
    if state == "ESCALATED_HUMAN":
        if "split discount" in turn_decision.action_executed.lower():
            return _sanitize_speech_output(
                "Kyunki aap final one-time discount offer se sehmat nahi hain aur split discount policy allow nahi karti, "
                "hum yeh case senior financial officer ko escalate kar rahe hain. Dhanyawad."
            )
        if "breach" in turn_decision.action_executed.lower() or "prohibits" in turn_decision.action_executed.lower():
            return _sanitize_speech_output(
                "Aapka pichla payment commitment breach ho chuka hai, isliye ab mazeed samay allow nahi hai. "
                "Hum aapka case senior financial officer aur recovery legal team ko escalate kar rahe hain. Dhanyawad."
            )
        return _sanitize_speech_output(
            "Since you have declined the available payment options, I will forward "
            "this case to a senior financial officer for formal review. Thank you for your time."
        )

    # 2. Frozen Dispute Invariant
    if state == "FROZEN_DISPUTE":
        reason = turn_decision.dispute_reason or "invoice discrepancy"
        return _sanitize_speech_output(
            f"Maine aapka dispute record kar liya hai regarding {reason}. Humari billing "
            "team iski jaanch karegi aur collection call abhi ke liye rok di gayi hai."
        )

    # 2b. Clarification: Split on Discount Not Allowed
    if "Discounted payment is not available for split payments" in turn_decision.action_executed or "split on discount" in turn_decision.action_executed.lower():
        net_str = f"₹{turn_decision.authorized_net_amount:,.0f}"
        return _sanitize_speech_output(
            f"Discounted payment split mein available nahi hai. Yeh concession sirf one-time payment ({net_str}) ke liye hai. "
            "Kya aap full amount par split payment (50% abhi aur 50% 3 din mein) karna chahenge, ya yeh one-time discounted price pay karenge?"
        )

    # 3. Split Payment Plan Offer Invariant (Margin Preservation)
    if state == "SPLIT_OFFERED":
        gross_val = float(invoice_context.get("amount_inr", 0))
        half_val = gross_val / 2.0
        return _sanitize_speech_output(
            f"Main samajh sakta hoon. Agar poora payment abhi sambhav nahi hai, toh kya aap abhi 50% (₹{half_val:,.0f}) "
            f"1 ghante ke andar clear kar sakte hain, aur baaki 50% (₹{half_val:,.0f}) agle 3 dinon mein? "
            "Isse aapka account bina kisi penalty ke regularize ho jayega."
        )

    # 3b. Split First Half Pending Invariant (1st 50% in 1 hour, remaining 50% in 3 days)
    if state == "SPLIT_FIRST_HALF_PENDING":
        return _sanitize_speech_output(
            "Bahut shukriya! Maine aapka split payment plan confirm kar diya hai. "
            "Pehle 50% ka payment link SMS aur WhatsApp par bhej diya gaya hai jo aap agle 1 ghante mein clear kar sakte hain, "
            "aur baaki 50% agle 3 dinon mein scheduled hai. Dhanyawad!"
        )

    # 4. Promise to Pay / Split Plan Acceptance Invariant
    if state == "PTP_ACTIVE":
        ptp_str = turn_decision.ptp_date.strftime("%d %B %Y") if turn_decision.ptp_date else "the agreed date"
        if "Split Payment Plan" in turn_decision.action_executed or "50%" in turn_decision.action_executed:
            return _sanitize_speech_output(
                f"Bahut shukriya! Maine aapka split payment plan confirm kar diya hai. "
                f"Pehle 50% ka payment link SMS aur WhatsApp par bhej diya gaya hai jo aap agle 1 ghante mein clear kar sakte hain, "
                f"aur baaki 50% {ptp_str} tak scheduled hai. Dhanyawad!"
            )
        if "3-day policy cap applied" in turn_decision.action_executed or "maximum 3 days" in turn_decision.action_executed:
            return _sanitize_speech_output(
                f"Humari policy ke mutabik maximum 3 din ka commitment allow hai. Maine {ptp_str} tak aapka "
                "payment commitment record kar liya hai. Payment link aapke mobile par bhej diya gaya hai."
            )
        return _sanitize_speech_output(
            f"Dhanyawad! Maine {ptp_str} tak aapka payment commitment record kar liya hai. "
            "Payment link aapke mobile par bhej diya gaya hai."
        )

    # 4. Concession Tiers & Discount Handling
    if state in ("TIER_1_DISCOUNT", "TIER_2_DISCOUNT", "TIER_3_FLOOR"):
        pct_str = f"{turn_decision.authorized_discount_rate * 100:.0f}%"
        net_str = f"₹{turn_decision.authorized_net_amount:,.0f}"

        # If debtor insisted on split discount and engine progressed down discount ladder
        if "insisted on split discount" in turn_decision.action_executed.lower() or "persisted on split discount" in turn_decision.action_executed.lower():
            if state == "TIER_3_FLOOR":
                return _sanitize_speech_output(
                    f"Split payment par discount allow nahi hai. Yeh hamara final one-time offer hai: {pct_str} discount, "
                    f"jisse aapko sirf {net_str} pay karna hoga. Kya main one-time payment link bhej doon?"
                )
            elif state == "TIER_2_DISCOUNT":
                return _sanitize_speech_output(
                    f"Split payment par discount allow nahi hai, lekin hum one-time payment par discount badhakar {pct_str} kar sakte hain, "
                    f"jisse aapko sirf {net_str} pay karna hoga. Kya aap ise one-time finalize karenge?"
                )

        # If customer asked for a high discount (e.g. 50%) but policy authorized 5%
        if turn_decision.customer_stated_discount_pct and turn_decision.customer_stated_discount_pct > (turn_decision.authorized_discount_rate * 100):
            req_pct = f"{turn_decision.customer_stated_discount_pct:.0f}%"
            return _sanitize_speech_output(
                f"Main samajh sakta hoon ki aap {req_pct} discount chahte hain, lekin policy ke mutabik "
                f"abhi maximum {pct_str} concession hi allow hai, jisse aapko sirf {net_str} pay karna hoga. "
                "Kya main payment link bhej doon?"
            )

        if state == "TIER_3_FLOOR":
            return _sanitize_speech_output(
                f"Humari policy ke mutabik yeh hamara final discount offer hai: {pct_str} concession, "
                f"jisse aapko sirf {net_str} pay karna hoga. Iske baad koi aur discount sambhav nahi hoga. Kya main link bhej doon?"
            )
        elif state == "TIER_2_DISCOUNT":
            return _sanitize_speech_output(
                f"Hum samajh sakte hain. Hum ise badhakar {pct_str} discount kar sakte hain, "
                f"jisse aapki net payable amount {net_str} ho jayegi. Kya aap ise finalize karenge?"
            )
        return _sanitize_speech_output(
            f"Aapke liye hum {pct_str} concession offer kar sakte hain, jisse aapko sirf {net_str} "
            "pay karna hoga. Kya main aapko payment link bhej doon?"
        )

    # 5. Technical Gateway Issue
    if turn_decision.intent == "TECHNICAL_PROBLEM":
        return _sanitize_speech_output(
            "Payment gateway issue ke liye kshama chahte hain. Maine direct UPI aur alternate "
            "payment rails ka fresh link aapke phone par bhej diya hai."
        )

    # 6. Payment Link Request / Immediate Pay
    if turn_decision.intent in ("REQUEST_PAYMENT_LINK", "PAY_NOW") or state == "LINK_SENT":
        net_str = f"₹{turn_decision.authorized_net_amount:,.0f}"
        if "1 hour" in turn_decision.action_executed:
            return _sanitize_speech_output(
                f"Maine payment link SMS aur WhatsApp par bhej diya hai. Pichle promise breach ki wajah se kripya agle 1 ghante mein {net_str} ki payment "
                "complete karein, warna case escalate ho jayega."
            )
        return _sanitize_speech_output(
            f"Maine payment link SMS aur WhatsApp par bhej diya hai. Kripya {net_str} ki payment "
            "link ke zariye turant complete karein."
        )

    # 7. Safe Default
    net_str = f"₹{turn_decision.authorized_net_amount:,.0f}"
    return _sanitize_speech_output(
        f"Namaste {cust_name} ji! Aapki total gross outstanding amount {net_str} hai with {merchant_name}. "
        "Kripya batayein ki aap payment abhi complete karna chahenge ya koi query hai?"
    )


def _sanitize_speech_output(text: str) -> str:
    """
    Remove all non-speakable artifacts from TTS output:
    - [PAYMENT_LINK], [LINK], [URL], etc.
    - Markdown formatting (**, *, _, `, ##, etc.)
    - Emojis (🙏, ⏰, etc.)
    - Excessive whitespace
    """
    # Remove bracketed placeholders
    text = re.sub(r"\[[\w_]+\]", "", text)
    # Remove markdown bold/italic
    text = re.sub(r"[*_`#]+", "", text)
    # Remove emojis (comprehensive ranges)
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF"
        r"\U0000200D\U00002764\U0001F900-\U0001F9FF"
        r"\U000023E9-\U000023FF\U00002300-\U000023FF]+", "", text
    )
    # Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text

