# RecoveryAI

> **Autonomous, bounded B2B revenue recovery agent for Indian payment workflows.**

RecoveryAI detects failed invoices, diagnoses root causes, contacts debtors via autonomous WhatsApp/SMS and natural Hinglish voice calls, and negotiates promises-to-pay (PTP) or strict merchant-capped concessions (5% → 8% → 10%) without sacrificing unearned margin.

---

## Key Highlights

- **Autonomous Scheduler**: 20-second background polling loop with persisted UTC deadlines and instant Fast-Forward simulation.
- **Multilingual Voice Agent**: Low-latency outbound voice negotiation in Hindi/Hinglish/English using **Sarvam AI** (`saaras:v3` STT, `bulbul:v3` TTS with in-memory caching) and **Gemini 3.6 Flash**.
- **Anti-Gaming Concession Engine**: Concessions are dynamically computed against merchant caps and penalized for repeat discount abuse.
- **Deterministic FSM**: 9 formal states (`TRIGGERED` → `REMINDER_SENT` → `PTP_ACTIVE` / `TIER_1_DISCOUNT` → `RESOLVED` / `FROZEN_DISPUTE` / `ESCALATED_HUMAN`).
- **Portfolio Analytics**: Real-time tracking of Volume at Risk, Gross Recovered, and Anti-Gaming Margin Preserved.

---

## Documentation Links

| Document | Purpose & Audience |
| :--- | :--- |
| 📖 [**`guide.html`**](guide.html) | **System Guide & Interactive Sandbox** — Executive framing, 7-stage pipeline, state transition graphs, and interactive concession calculator. |
| ⚙️ [**`backend.md`**](backend.md) | **Backend Technical Deep Dive** — Architecture, FastAPI routes, FSM models, scheduler, voice pipeline, and security. |
| 💻 [**`frontend.md`**](frontend.md) | **Frontend Technical Deep Dive** — Next.js 16 App Router, real-time countdown architecture, audio player, and analytics. |

---

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend** | Python 3.11+, FastAPI, SQLAlchemy (Async), Pydantic v2, Uvicorn |
| **Database** | PostgreSQL / Supabase (with `pgcrypto` & `uuid-ossp`) |
| **AI / Voice** | Google Gemini 3.6 Flash (1.8s timeout cap), Sarvam AI (`saaras:v3`, `bulbul:v3`) |
| **Frontend** | Next.js 16 (App Router), React 19, TypeScript, TailwindCSS, Lucide Icons |

---

## Quick Start

### 1. Backend Setup

```bash
# Navigate and activate virtual environment
cd backend
..\.venv\Scripts\activate

# Install dependencies (if first time)
pip install -r requirements.txt

# Start FastAPI server (loads .env / backend/.env)
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup

```bash
# In a separate terminal
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## Seed Test Scenarios (6 Accounts)

Click **Seed Database** on the Operations Console or run:

```bash
curl -X POST http://localhost:8000/api/seed
```

| Account | Amount | Failure Reason | LTV | History & Policy Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **Aarav Sharma** | ₹18,500 | `GATEWAY_TIMEOUT` | ₹1,50,000 | Clean history (0 mo) → 0% discount, full recovery |
| **Priya Verma** | ₹45,000 | `INSUFFICIENT_FUNDS` | ₹65,000 | 1 mo history → 8% cap penalty (Tier 3 blocked) |
| **Vikram Malhotra** | ₹12,000 | `MANDATE_DECLINE` | ₹40,000 | 2 mo history → 5% cap penalty (Tiers 2 & 3 blocked) |
| **Ananya Iyer** | ₹95,000 | `DISPUTED_AMOUNT` | ₹2,10,000 | High LTV dispute → Frozen for human finance review |
| **Rohan Mehta** | ₹28,000 | `EXPIRED_CARD` | ₹95,000 | 3+ mo history → 0% discount permitted (Chronic) |
| **Kavya Patel** | ₹52,000 | `GATEWAY_TIMEOUT` | ₹1,80,000 | High LTV → Alternate link retry with follow-up call |

---

## Running Test Suite

```bash
# Run backend test suite
cd backend
..\.venv\Scripts\activate
python -m pytest tests/
```
