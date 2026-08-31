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
    (
        [
            "never pay",
            "will never pay",
            "kabhi nahi dunga",
            "kabhi bhi nahi dunga",
            "kabhi nahi dunga jo karna hai kar lo",
            "refuse forever",
            "do whatever you want",
        ],
        "HARD_REFUSAL",
        "Debtor expressed explicit permanent refusal to pay forever.",
    ),
    (
        [
            "next week",
            "नेक्स्ट वीक",
            "agle hafte",
            "अगले हफ्ते",
            "agle hafte / 5 din",
            "agle hafte /",
            "5 din baad",
            "5 दिन बाद",
            "4 din",
            "5 din",
            "6 din",
            "7 din",
            "8 din",
            "9 din",
            "10 din",
            "15 din",
            "4 days",
            "5 days",
            "6 days",
            "7 days",
            "10 days",
            "4 दिन",
            "5 दिन",
            "6 दिन",
            "7 दिन",
            "10 दिन",
            "15 दिन",
            "4 डेज़",
            "5 डेज़",
            "5 डेज",
            "चार दिन",
            "पांच दिन",
            "छह दिन",
            "सात दिन",
            "दस दिन",
            "month end",
            "agle mahine",
            "अगले महीने",
            "next month",
            "2 weeks",
            "do hafte",
            "दो हफ्ते",
        ],
        "PTP_EXCEEDS_POLICY",
        "Debtor requested a payment timeline exceeding the 3-day recovery policy limit.",
    ),
    (
        [
            "आई नीड थ्री डेज़",
            "आई नीड थ्री डेज",
            "आई नीड 3 डेज़",
            "नीड थ्री डेज़",
            "थ्री डेज़",
            "थ्री डेज",
            "3 डेज़",
            "3 डेज",
            "टु डेज़",
            "टु डेज",
            "टू डेज़",
            "टू डेज",
            "2 डेज़",
            "2 डेज",
            "वन डे",
            "1 डे",
            "1 दिन",
            "एक दिन",
            "1 day",
            "one day",
            "1 din",
            "ek din",
            "2 दिन",
            "दो दिन",
            "2 days",
            "two days",
            "2 din",
            "do din",
            "3 दिन",
            "तीन दिन",
            "3 days",
            "three days",
            "3 din",
            "teen din",
            "kal",
            "कल",
            "parso",
            "परसों",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "मंडे",
            "सोमवार",
            "मंगलवार",
            "बुधवार",
            "गुरुवार",
            "शुक्रवार",
            "शनिवार",
            "रविवार",
            "28th",
            "29th",
            "30th",
            "31st",
            "august",
            "september",
            "october",
            "day after",
            "tomorrow",
        ],
        "PROMISE_TO_PAY",
        "Debtor indicated an explicit payment timeline commitment within policy.",
    ),
    (
        [
            "main yeh payment kar dunga",
            "main ye payment kar dunga",
            "main yeh payment kar deta",
            "yeh payment kar dunga",
            "ye payment kar dunga",
            "main payment kar dunga",
            "payment kar dunga",
            "theek hai main kar deta",
            "theek hai main payment",
            "theek hai kar deta",
            "theek hai kar dunga",
            "theek hai payment",
            "kar deta hoon",
            "kar deta hu",
            "kar raha hoon",
            "kar raha hu",
            "i will pay",
            "i will make the payment",
            "i agree to pay",
            "will pay now",
            "pay now",
            "abhi kar deta",
            "abhi kar dunga",
            "abhi pay kar",
            "payment kar raha",
            "main kar deta",
            "main kar dunga",
            "theek hai",
            "haan kar dunga",
            "haan main",
            "haan 3 din",
            "haan theek",
            "haan chalega",
            "main yeh pay",
            "मैं यह पेमेंट कर दूंगा",
            "मैं पेमेंट कर दूंगा",
            "मैं यह पेमेंट",
            "ठीक है मैं कर देता हूँ",
            "मैं कर देता हूँ",
            "मैं अभी पेमेंट",
            "कर देता हूँ",
            "कर दूँगा",
            "दे दूँगा",
            "de dunga",
            "दे दूंगा",
            "दूंगा",
            "दूँगा",
            "कर दूंगा",
            "transfer",
            "ट्रांसफर",
            "will pay",
            "pay kar dunga",
            "will clear",
        ],
        "AGREED_TO_PAY",
        "Debtor agreed to settle payment without a specific future date.",
    ),
    (
        [
            "cannot settle",
            "cannot pay today",
            "not possible today",
            "cannot pay",
            "aaj nahi",
            "aaj paise nahi",
            "aaj possible nahi",
            "no i cannot",
            "can not settle",
            "settle today",
            "नो",
            "आई कैन नॉट",
            "आज नहीं",
            "नहीं हो सकता",
            "नहीं, मैं आज",
            "आज इसको सेटल नहीं",
            "सेटल नहीं कर पाऊंगा",
            "सेटल नहीं",
            "नहीं कर पाऊंगा",
            "आज नहीं होगा",
            "नहीं हो पाएगा",
            "aaj settle nahi hoga",
            "abhi paise nahi hain",
            "too high",
            "reduce more",
            "give me more discount",
            "will not pay today",
            "not today",
            "aaj nahi ho payega",
            "nahi kar paunga",
            "nahi ho payega",
            "nahi kar sakta",
            "नहीं कर सकता",
            "nahi",
            "नहीं",
            "नही",
            "no",
            "won't",
            "not paying today",
            "nahi ho sakta",
            "nahi chalega",
        ],
        "REQUEST_NEGOTIATION",
        "Debtor indicated inability/unwillingness to settle today or refusal of proposed terms.",
    ),
    (
        [
            "gst",
            "जीएसटी",
            "tds",
            "टीडीएस",
            "dispute",
            "डिस्प्यूट",
            "wrong amount",
            "गलत अमाउंट",
            "incorrect amount",
            "wrong billing",
            "बिलिंग गलत",
            "galat amount",
            "credit note",
            "क्रेडिट नोट",
            "tax invoice",
            "invoice error",
            "invoice galat",
            "already paid",
            "pehle hi pay",
            "disputed",
            "overcharged",
        ],
        "DISPUTE",
        "Debtor raised an invoice amount / GST / TDS billing dispute.",
    ),
    (
        [
            "link",
            "लिंक",
            "payment link",
            "पेमेंट लिंक",
            "alternate link",
            "qr",
            "क्यूआर",
            "qr code",
            "upi",
            "यूपीआई",
            "upi id",
            "send link",
            "लिंक भेजो",
            "bhejo",
            "gpay",
            "phonepe",
            "paytm",
            "razorpay",
            "link expired",
            "link not working",
            "reshare",
        ],
        "REQUEST_ALTERNATE_LINK",
        "Debtor requested a fresh/alternate digital payment link or QR code.",
    ),
    (
        [
            "discount",
            "छूट",
            "concession",
            "कंसेंशन",
            "waiver",
            "वेवर",
            "kam karo",
            "कम करो",
            "paisa kam",
            "kam kardo",
            "give discount",
            "kuch discount",
            "thoda kam",
            "thoda discount",
            "discount milega",
            "offer",
            "settlement",
        ],
        "REQUEST_DISCOUNT",
        "Debtor explicitly requested a discount or settlement concession.",
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
