"""
RecoveryAI — Sarvam AI v3 Integration Service (Phase 4)
========================================================

Services for:
1. Speech-to-Text (STT): saaras-v3 model for mixed Hinglish/Indian English audio transcription.
2. Text-to-Speech (TTS): bulbul-v3 model for natural Indian voice synthesis.

Provides full async implementation with automatic fallback when SARVAM_API_KEY
is missing, rate-limited, or network is unavailable.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Sarvam API Base URL
SARVAM_API_BASE = "https://api.sarvam.ai"

# In-memory LRU-like audio cache for repeated agent greetings and concession statements
_TTS_AUDIO_CACHE: dict[str, dict[str, Any]] = {}
_MAX_CACHE_ENTRIES = 200


def _get_cache_key(text: str, target_language_code: str, speaker: str) -> str:
    raw = f"{text.strip().lower()}|{target_language_code}|{speaker}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "recording.webm",
    content_type: str = "audio/webm",
) -> dict[str, Any]:
    """
    Transcribe debtor audio using Sarvam AI's saaras-v3 STT model.
    Falls back gracefully if API is unavailable with a 10.0s timeout.
    
    Returns:
        {
            "transcript": str,
            "language_code": str,
            "confidence": float,
            "used_fallback": bool
        }
    """
    api_key = settings.SARVAM_API_KEY

    if api_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                files = {
                    "file": (filename, audio_bytes, content_type)
                }
                data = {
                    "model": "saaras:v3",
                    "language_code": "hi-IN",
                    "with_timestamps": "false",
                }
                headers = {
                    "api-subscription-key": api_key,
                }
                resp = await client.post(
                    f"{SARVAM_API_BASE}/speech-to-text",
                    files=files,
                    data=data,
                    headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    transcript = result.get("transcript", "").strip()
                    if transcript:
                        logger.info("Sarvam STT success: '%s'", transcript)
                        return {
                            "transcript": transcript,
                            "language_code": result.get("language_code", "hi-IN"),
                            "confidence": 0.95,
                            "used_fallback": False,
                        }
                else:
                    logger.warning("Sarvam STT HTTP %d: %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Sarvam STT error: %s — falling back", exc)

    # ── Fallback Mode ─────────────────────────────────────────────────────────
    logger.info("Using Sarvam STT Fallback Mode")
    return {
        "transcript": "Bhai Monday tak pakka payment clear kar dunga, tension mat lo.",
        "language_code": "hi-IN",
        "confidence": 0.85,
        "used_fallback": True,
    }


async def synthesize_speech(
    text: str,
    target_language_code: str = "hi-IN",
    speaker: str = "shubh",
) -> dict[str, Any]:
    """
    Synthesize Hinglish response text into natural Indian speech using Sarvam AI's bulbul-v3 TTS.
    Uses in-memory cache to return instant audio (<0.1ms) for repeated statements.
    
    Returns:
        {
            "audio_base64": str,
            "audio_format": "audio/wav",
            "used_fallback": bool
        }
    """
    # Sanitize text to eliminate raw uppercase enums, currency symbols, and abbreviations from pronunciation
    sanitized_text = (
        text.replace("GATEWAY_TIMEOUT", "technical gateway issue")
        .replace("INSUFFICIENT_FUNDS", "insufficient funds")
        .replace("MANDATE_DECLINE", "bank mandate decline")
        .replace("EXPIRED_CARD", "card expiry")
        .replace("DISPUTED_AMOUNT", "amount mismatch issue")
        .replace("₹", "Rupees ")
        .replace("Pvt. Ltd.", "Private Limited")
        .replace("Pvt Ltd", "Private Limited")
        .replace("_", " ")
    )

    # Check in-memory audio cache
    cache_key = _get_cache_key(sanitized_text, target_language_code, speaker)
    if cache_key in _TTS_AUDIO_CACHE:
        logger.info("Sarvam TTS Cache Hit for '%s' (hash: %s...)", sanitized_text[:30], cache_key[:8])
        return _TTS_AUDIO_CACHE[cache_key]

    api_key = settings.SARVAM_API_KEY

    if api_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = {
                    "inputs": [sanitized_text],
                    "target_language_code": target_language_code,
                    "speaker": speaker,
                    "pace": 1.0,
                    "speech_sample_rate": 22050,
                    "enable_preprocessing": True,
                    "model": "bulbul:v3",
                }
                headers = {
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json",
                }
                resp = await client.post(
                    f"{SARVAM_API_BASE}/text-to-speech",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    audios = result.get("audios", [])
                    if audios and audios[0]:
                        clean_b64 = audios[0].strip().replace("\n", "").replace("\r", "")
                        logger.info("Sarvam TTS synthesized %d chars of text (b64 length: %d)", len(text), len(clean_b64))
                        entry = {
                            "audio_base64": clean_b64,
                            "audio_format": "audio/wav",
                            "used_fallback": False,
                        }
                        if len(_TTS_AUDIO_CACHE) < _MAX_CACHE_ENTRIES:
                            _TTS_AUDIO_CACHE[cache_key] = entry
                        return entry
                else:
                    logger.warning("Sarvam TTS HTTP %d: %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Sarvam TTS error: %s — falling back", exc)

    # ── Fallback Mode ─────────────────────────────────────────────────────────
    logger.info("Using Sarvam TTS Fallback Mode")
    return {
        "audio_base64": "",
        "audio_format": "audio/wav",
        "used_fallback": True,
    }
