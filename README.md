# RecoveryAI

> **Autonomous Voice & Multi-Rail Debt Recovery Engine for Indian FinTech & B2B SaaS**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Vercel%20Production-blue?style=for-the-badge&logo=vercel)](https://recovery-ai-swart.vercel.app/)
[![Interactive Pitch Deck](https://img.shields.io/badge/Pitch%20Deck-Interactive%20Presentation-6366f1?style=for-the-badge)](https://recovery-ai-swart.vercel.app/)

🌐 **Live Hosted Console**: [**https://recovery-ai-swart.vercel.app/**](https://recovery-ai-swart.vercel.app/)  
👉 **Quick Start**: Open the live link and click **`[Seed Database]`** to load realistic Indian B2B recovery scenarios and run the autonomous dunning, voice negotiation, and split-payment engine.

---

## Overview

RecoveryAI is an enterprise-grade autonomous debt recovery system built for the Indian digital economy. It autonomously diagnoses failed invoices, initiates contextual multi-channel dunning (WhatsApp, SMS), conducts multi-turn negotiation calls in natural conversational Hinglish/Hindi/English using **Sarvam AI** and **Google Gemini 2.5 Flash**, and enforces a **deterministic financial policy engine** to maximize debt collection while strictly preserving merchant profit margins.

---

## Key Highlights

- **Deterministic Financial Boundary**: Generative AI is strictly restricted to conversational intent classification. All rupee amounts, concession tiers, PTP deadlines, and state transitions are computed and enforced by deterministic Python state machines and policy calculators (Zero LLM Balance Hallucination).
- **Margin-Preserving Split Payment Engine**: Debtor refusals automatically trigger an offer for a **Split Payment Plan** (50% in 1 hour + 50% via a 3-day Promise-to-Pay) *before* conceding any discounts.
- **Strict Anti-Gaming & Concession Ladder**: Concessions follow an authorized tier ladder (Tier 1: 50% of cap $\rightarrow$ Tier 2: 80% of cap $\rightarrow$ Tier 3: 100% Floor) capped by merchant policies and blocked for repeat discount abusers (3+ consecutive months).
- **Multilingual Voice Negotiation**: Real-time outbound voice call dialog powered by Sarvam AI (`saaras:v3` STT, `bulbul:v3` TTS `shubh` model) and Gemini 2.5 Flash, handling complex multilingual Indian linguistic patterns, code-switching, and Devanagari phonetics.
- **13-State Deterministic FSM**: Fully auditable lifecycle tracking (`TRIGGERED`, `DIAGNOSED`, `REMINDER_SENT`, `SPLIT_OFFERED`, `SPLIT_FIRST_HALF_PENDING`, `LINK_SENT`, `PTP_ACTIVE`, `TIER_1_DISCOUNT`, `TIER_2_DISCOUNT`, `TIER_3_FLOOR`, `RESOLVED`, `FROZEN_DISPUTE`, `ESCALATED_HUMAN`).
- **Real-Time Operator Console**: Optimistic 0ms UI settlement, 2-stage partial payment accounting (50% Half Paid $\rightarrow$ Yellow, Full Paid $\rightarrow$ Green), dynamic countdown timers, call queue, and full recovery telemetry.

---

## System Architecture

```
                                  [ Operations Console / Next.js 16 ]
                                  (https://recovery-ai-swart.vercel.app/)
                                                   │
                                                   ▼ (REST API / JSON)
                                        [ FastAPI Application ]
                                                   │
                       ┌───────────────────────────┴───────────────────────────┐
                       ▼                                                       ▼
             [ API Routers (routes.py) ]                             [ Background Worker (scheduler.py) ]
                       │                                                       │ (20s Polling Loop)
                       ▼                                                       ▼
   ┌───────────────────────────────────────┐               ┌───────────────────────────────────────┐
   │         Business Logic Engine         │               │     process_expired_deadlines()       │
   ├───────────────────────────────────────┤               └───────────────────┬───────────────────┘
   │ • Policy Wrapper (policy_wrapper.py)  │                                   │
   │ • Concession Engine (calculator.py)   │◄──────────────────────────────────┘
   │ • Deterministic FSM (state_machine.py)│
   │ • Gemini Intent (gemini_service.py)   │
   │ • Voice STT/TTS (sarvam_service.py)   │
   └───────────────────┬───────────────────┘
                       │
                       ▼
            [ Data Access Layer ]
          (SQLAlchemy 2.0 Async + asyncpg)
                       │
                       ▼
      [ PostgreSQL Database (Supabase) ]
  (merchants, customers, invoices, recovery_events)
```

---

## Deterministic Finite State Machine (FSM)

```
                       [TRIGGERED]
                            │
                            ▼
                       [DIAGNOSED]
                            │
                            ▼
                     [REMINDER_SENT] (10-min countdown)
                            │
          ┌─────────────────┼─────────────────┐
          │ (Refusal)       │ (Split Accepted)│ (Agreed to Pay Full)
          ▼                 ▼                 ▼
   [SPLIT_OFFERED] ──► [SPLIT_FIRST_HALF_PENDING] ──► [LINK_SENT]
          │             (1-hr timer / 50% paid)        │
          │ (Reject Split)  │ (1-hr Breach)            │ (Paid)
          ▼                 ▼                          ▼
   [TIER_1_DISCOUNT]  [ESCALATED_HUMAN]           [RESOLVED]
          │
          │ (Refusal at Tier 1 / Insist Split)
          ▼
   [TIER_2_DISCOUNT]
          │
          │ (Refusal at Tier 2 / Insist Split)
          ▼
   [TIER_3_FLOOR]
          │
          │ (Refusal at Tier 3 / Persistent Split Demand)
          ▼
   [ESCALATED_HUMAN]

   * Global Escape States:
     - Any Dispute ──► [FROZEN_DISPUTE] (Immediate freeze on dunning)
     - Any Full Payment ──► [RESOLVED]
```

---

## Tech Stack

| Layer | Technology | Version / Specification |
| :--- | :--- | :--- |
| **Backend** | Python, FastAPI, Uvicorn | Python 3.11+, FastAPI 0.115, Pydantic v2 |
| **Database** | PostgreSQL, asyncpg, SQLAlchemy | PostgreSQL 15+ (Supabase) with asyncpg driver |
| **AI / NLP** | Google Gemini 2.5 Flash | Structured Pydantic intent classification |
| **Voice STT/TTS**| Sarvam AI | `saaras:v3` (STT), `bulbul:v3` (TTS `shubh` model) |
| **Frontend** | Next.js, React, TypeScript | Next.js 16.3.2 (Turbopack), React 19, TypeScript 5.8 |
| **Styling** | TailwindCSS | TailwindCSS 4, Lucide React Icons |
| **Deployment** | Vercel (Frontend), FastAPI / Supabase (Backend) | Production environment with zero-cold-start latency |

---

## Live Demo & Test Scenarios

To test the system live on **[https://recovery-ai-swart.vercel.app/](https://recovery-ai-swart.vercel.app/)**:
1. Click **`[Seed Database]`** in the top action bar (shows animated provisioning overlay).
2. Explore active recovery cases, failure reason badges, and live countdown timers.
3. Test **Voice Call Modal** with real microphone input in Hinglish/Hindi or audio fallback.
4. Click **`[Fast Forward All]`** or **`[Skip Wait]`** to simulate 10-minute reminder expirations and 1-hour split deadlines.
5. Record partial payments with the **2-stage settlement button** (Yellow $\rightarrow$ Green).

### Seeded Accounts & Policies:

| Account | Outstanding | Failure Reason | LTV | Policy & Negotiation Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **Aarav Sharma** | ₹18,500 | `GATEWAY_TIMEOUT` | ₹1,50,000 | Clean history → Instant split offer on refusal, full recovery |
| **Priya Verma** | ₹45,000 | `INSUFFICIENT_FUNDS` | ₹65,000 | 1 mo history → Tier 1 (5%) → Tier 2 (8%) concession ladder |
| **Vikram Malhotra** | ₹12,000 | `MANDATE_DECLINE` | ₹40,000 | 2 mo history → 5% max cap penalty (Tiers 2 & 3 blocked) |
| **Ananya Iyer** | ₹95,000 | `DISPUTED_AMOUNT` | ₹2,10,000 | High LTV dispute → Immediate freeze (`FROZEN_DISPUTE`) |
| **Rohan Mehta** | ₹28,000 | `EXPIRED_CARD` | ₹95,000 | 3+ mo history → Anti-Gaming blocks all discounts (0%) |
| **Kavya Patel** | ₹52,000 | `GATEWAY_TIMEOUT` | ₹1,80,000 | High LTV → Alternate link retry with follow-up call |

---

## Local Development Quick Start

### 1. Backend Setup

```bash
cd backend

# Create & activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env

# Start FastAPI server
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start Next.js development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## Verification & Test Suite

Run the full automated test suite containing unit tests, conversational edge cases, and deterministic policy runners:

```bash
# 1. Run all 63 conversational edge case and split-discount tests
python -m pytest backend/tests/test_conversational_edge_cases.py -v

# 2. Run the 26-test regression suite
python backend/tests/run_tests.py
```

---

## Project Documentation Index

- 🚀 [**`pitch.html`**](pitch.html) — Interactive 5-Minute Minimal Light Presentation Deck.
- 📖 [**`guide.html`**](guide.html) — Interactive System Architecture & Operations Guide.
- ⚙️ [**`backend.md`**](backend.md) — Exhaustive Backend Engineering Guide, FSM Transitions & API Schema.
- 💻 [**`frontend.md`**](frontend.md) — Comprehensive Frontend Documentation & Component Specifications.
