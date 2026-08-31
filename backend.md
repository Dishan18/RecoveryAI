# RecoveryAI — Backend Engineering & Policy Architecture Guide

> **Autonomous, Bounded B2B & FinTech Revenue Recovery Engine for Indian Payment Workflows**

---

## 1. System Overview

The RecoveryAI backend is an asynchronous, event-driven FastAPI application engineered to handle the post-failure lifecycle of high-value Indian B2B and SaaS transactions. When a payment failure occurs, the backend:
1. **Performs Root-Cause Failure Diagnosis**: Distinguishes transient gateway timeouts from fundamental liquidity failures, card expiry, eMandate rejections, or billing disputes.
2. **Enforces Strict Financial Policy Boundaries**: Evaluates customer history against merchant concession caps, automatically blocking discount abuse via deterministic anti-gaming rules.
3. **Executes Autonomous Finite State Machine Transitions**: Advances invoices across 13 deterministic states with persisted UTC deadlines.
4. **Negotiates Multi-Turn Voice Recovery**: Powers conversational outbound calls in natural Hinglish, Hindi, and English using **Sarvam AI** (`saaras:v3` STT, `bulbul:v3` TTS) and **Google Gemini 2.5 Flash** for structured intent parsing.
5. **Maintains Immutable Compliance Audit Logs**: Every transition, discount calculation, and debtor turn appends an immutable, timestamped record in `recovery_events`.

---

## 2. Backend Architecture

```
                                  [ Client UI / Next.js 16 App Router ]
                                                   │
                                                   ▼ (REST API / JSON)
                                        [ FastAPI Application (main.py) ]
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
   │ • Concession Calculator (calculator.py)│◄──────────────────────────────────┘
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
      [ Supabase PostgreSQL 15+ Instance ]
  (Tables: merchants, customers, invoices, recovery_events)
```

---

## 3. Directory Structure

```
backend/
├── app/
│   ├── __init__.py           # Package marker
│   ├── config.py             # Pydantic Settings (reads environment variables)
│   ├── database.py           # Async SQLAlchemy engine & async_sessionmaker
│   ├── migrations.py         # Idempotent DDL migration runner (CREATE & ALTER)
│   ├── models.py             # SQLAlchemy ORM models (Merchant, Customer, Invoice, RecoveryEvent)
│   ├── routes.py             # FastAPI APIRouter with all REST endpoints
│   ├── schemas.py            # Pydantic v2 request & response schemas
│   ├── scheduler.py          # 20-second autonomous polling worker & deadline processor
│   └── engine/
│       ├── __init__.py
│       ├── calculator.py     # Deterministic concession ladder & anti-gaming rules
│       ├── gemini_service.py # Gemini 2.5 Flash intent parser & Hinglish copy generator
│       ├── policy_wrapper.py # Server-authoritative turn execution & negotiation rules
│       ├── sarvam_service.py # Sarvam AI saaras:v3 (STT) & bulbul:v3 (TTS with cache)
│       └── state_machine.py  # Deterministic 13-state FSM & transition validations
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Test configuration & path bootstrap
│   ├── run_tests.py          # 26-test voice intent & policy engine regression runner
│   ├── test_conversational_edge_cases.py # 63 comprehensive conversational edge case tests
│   ├── test_analytics_overview.py
│   ├── test_anti_gaming_and_manual_seeding.py
│   ├── test_audit.py
│   ├── test_e2e_autonomous.py
│   ├── test_excessive_discount_and_seed_diversity.py
│   ├── test_kavya_negotiation.py
│   ├── test_lifecycle_progression.py
│   ├── test_perf_audit.py
│   ├── test_phase6.py
│   ├── test_ptp_breach_direct_call.py
│   ├── test_ptp_policy_and_seed.py
│   ├── test_single_ptp_and_breach_escalation.py
│   └── test_spoken_enums_and_resilience.py
├── main.py                   # FastAPI application factory, middleware & lifespan
├── seed.py                   # 6 realistic Indian B2B test scenario generator
├── schema.sql                # Pure DDL SQL for Supabase SQL Editor execution
└── requirements.txt          # Python pinned dependency manifest
```

---

## 4. Deterministic Finite State Machine (FSM)

RecoveryAI enforces a **13-state FSM** with strict transition validation in `app/engine/state_machine.py`.

### State Definitions
| State Constant | Description |
| :--- | :--- |
| `TRIGGERED` | Invoice failure ingested into the recovery pipeline. |
| `DIAGNOSED` | Root cause diagnosed (logged as internal transition). |
| `REMINDER_SENT` | Contextual WhatsApp/SMS reminder dispatched; 10-min timer initiated. |
| `SPLIT_OFFERED` | Margin-preserving offer (50% in 1 hr + 50% in 3 days) made upon refusal. |
| `SPLIT_FIRST_HALF_PENDING` | Debtor accepted split; 1-hour countdown for first 50% payment active. |
| `LINK_SENT` | Direct multi-rail payment link generated and dispatched. |
| `PTP_ACTIVE` | Debtor committed to a payment date (strictly capped at 3 days max). |
| `TIER_1_DISCOUNT` | Initial concession authorized (e.g. 50% of merchant cap, typically 5%). |
| `TIER_2_DISCOUNT` | Secondary concession authorized (e.g. 80% of merchant cap, typically 8%). |
| `TIER_3_FLOOR` | Final concession floor authorized (100% of merchant cap, typically 10%). |
| `RESOLVED` | Full debt settled and verified. |
| `FROZEN_DISPUTE` | Global freeze triggered by debtor dispute for finance team review. |
| `ESCALATED_HUMAN` | Terminal escalation triggered by repeat breach, policy lockout, or refusal. |

### Complete Transition Matrix

```python
TRANSITION_MAP = {
    State.TRIGGERED: {
        State.REMINDER_SENT, State.DIAGNOSED, State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING, State.PTP_ACTIVE, State.TIER_1_DISCOUNT,
        State.LINK_SENT, State.ESCALATED_HUMAN,
    },
    State.DIAGNOSED: {
        State.REMINDER_SENT, State.SPLIT_OFFERED, State.SPLIT_FIRST_HALF_PENDING,
        State.LINK_SENT, State.TIER_1_DISCOUNT, State.PTP_ACTIVE, State.ESCALATED_HUMAN,
    },
    State.REMINDER_SENT: {
        State.SPLIT_OFFERED, State.SPLIT_FIRST_HALF_PENDING, State.PTP_ACTIVE,
        State.LINK_SENT, State.TIER_1_DISCOUNT, State.ESCALATED_HUMAN,
    },
    State.SPLIT_OFFERED: {
        State.RESOLVED, State.SPLIT_FIRST_HALF_PENDING, State.PTP_ACTIVE,
        State.LINK_SENT, State.TIER_1_DISCOUNT, State.ESCALATED_HUMAN, State.FROZEN_DISPUTE,
    },
    State.SPLIT_FIRST_HALF_PENDING: {
        State.RESOLVED, State.PTP_ACTIVE, State.LINK_SENT,
        State.ESCALATED_HUMAN, State.FROZEN_DISPUTE, State.TIER_1_DISCOUNT,
    },
    State.LINK_SENT: {
        State.RESOLVED, State.SPLIT_OFFERED, State.SPLIT_FIRST_HALF_PENDING,
        State.TIER_1_DISCOUNT, State.PTP_ACTIVE, State.ESCALATED_HUMAN,
    },
    State.PTP_ACTIVE: {
        State.RESOLVED, State.SPLIT_OFFERED, State.SPLIT_FIRST_HALF_PENDING,
        State.LINK_SENT, State.TIER_1_DISCOUNT, State.ESCALATED_HUMAN,
    },
    State.TIER_1_DISCOUNT: {
        State.RESOLVED, State.TIER_2_DISCOUNT, State.PTP_ACTIVE,
        State.ESCALATED_HUMAN, State.LINK_SENT, State.SPLIT_OFFERED, State.SPLIT_FIRST_HALF_PENDING,
    },
    State.TIER_2_DISCOUNT: {
        State.RESOLVED, State.TIER_3_FLOOR, State.PTP_ACTIVE,
        State.ESCALATED_HUMAN, State.LINK_SENT, State.SPLIT_FIRST_HALF_PENDING,
    },
    State.TIER_3_FLOOR: {
        State.RESOLVED, State.ESCALATED_HUMAN, State.PTP_ACTIVE,
        State.LINK_SENT, State.SPLIT_FIRST_HALF_PENDING,
    },
    State.RESOLVED: set(),
    State.FROZEN_DISPUTE: set(),
    State.ESCALATED_HUMAN: set(),
}
```

---

## 5. Policy Engine & Negotiation Rules

### Rule 1: Margin Preservation via Split Payment Plan
When a debtor refuses immediate payment, the engine does not immediately surrender discount margin. Instead, it offers a **Split Payment Plan**:
- **First 50%**: Due within **1 hour** (`SPLIT_FIRST_HALF_PENDING`).
- **Second 50%**: Scheduled for **3 days later** as a Promise-to-Pay upon operator recording of the first half.

### Rule 2: Split-on-Discount Clarification Ladder
- **Core Policy**: Discounts are strictly for one-time upfront payments. Split payment is only available on the gross full amount.
- **Clarification**: If a debtor asks to split a discounted amount (*"5% discount me split payment"*), the agent clarifies:
  > *"Discounted payment split mein available nahi hai. Yeh concession sirf one-time payment ke liye hai. Kya aap full amount par split payment karna chahenge, ya yeh one-time discounted price pay karenge?"*
- **Debtor Accepts Split on Gross**: Immediately transitions to `SPLIT_FIRST_HALF_PENDING` (0% discount).
- **Debtor Insists on Split with Discount**: Advances down the one-time discount ladder (`TIER_1_DISCOUNT` $\rightarrow$ `TIER_2_DISCOUNT` $\rightarrow$ `TIER_3_FLOOR`).
- **Persistent Demands After Tier 3 Floor**: Automatically escalates to `ESCALATED_HUMAN`.

### Rule 3: Anti-Gaming & Concession Bounds
- **Tier 1**: Authorized at `50%` of merchant default cap (e.g. `5%` on a 10% cap).
- **Tier 2**: Authorized at `80%` of merchant default cap (e.g. `8%` on a 10% cap).
- **Tier 3**: Authorized at `100%` of merchant default cap (e.g. `10%` on a 10% cap).
- **Repeat Concession Abuse Penalty**: If `consecutive_discount_months >= 3`, all discounts are completely blocked (`0%` max authorized).

### Rule 4: Strict 3-Day PTP Policy Cap
Promises to pay are deterministically clamped to a maximum of **3 calendar days** from the call time, in accordance with RBI fair-recovery standards.

---

## 6. Database Models & Schema

### `invoices` Table
- `id` (`UUID`, PK): Unique invoice identifier.
- `customer_id` (`UUID`, FK `customers.id`): Debtor reference.
- `merchant_id` (`UUID`, FK `merchants.id`): Merchant reference.
- `amount_inr` (`NUMERIC(12,2)`): Active remaining balance in INR.
- `original_amount_inr` (`NUMERIC(12,2)`): Original invoice gross amount before partial payments.
- `recovered_amount_inr` (`NUMERIC(12,2)`): Total collected revenue from partial or full payments.
- `status` (`VARCHAR(50)`): High-level operational status (`UNPAID`, `RESOLVED`, `DISPUTED`, `ESCALATED`).
- `failure_reason` (`VARCHAR(100)`): Root cause classification.
- `next_action_due_at` (`TIMESTAMPTZ`, nullable): Server truth for countdown timers.
- `call_pending` (`BOOLEAN`): Indicates an active outbound call queued for the debtor.

### `recovery_events` Table
- `id` (`UUID`, PK): Event UUID.
- `invoice_id` (`UUID`, FK `invoices.id`): Invoice reference.
- `current_state` (`VARCHAR(50)`): Deterministic FSM state.
- `discount_offered` (`NUMERIC(5,4)`): Concession fraction applied.
- `ptp_deadline` (`TIMESTAMPTZ`, nullable): Negotiated PTP deadline.
- `log_message` (`TEXT`): Human-readable audit log.
- `timestamp` (`TIMESTAMPTZ`): UTC creation time.

---

## 7. REST API Reference

| Method | Route | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness & readiness probe |
| `POST` | `/api/seed` | Atomically wipes and seeds 6 diverse recovery test scenarios |
| `GET` | `/api/invoices` | List all recovery invoices with customer/merchant metadata |
| `GET` | `/api/invoices/{id}` | Fetch single invoice with complete recovery event audit log |
| `POST` | `/api/invoices` | Ingest a manual failed invoice |
| `POST` | `/api/invoices/{id}/diagnose` | Run root-cause failure diagnosis |
| `GET` | `/api/invoices/{id}/discount-preview` | Calculate available concession tiers and anti-gaming status |
| `POST` | `/api/invoices/{id}/record-payment` | Record partial (50%) or full (100%) payment with KPI update |
| `POST` | `/api/invoices/{id}/voice/greeting` | Generate outbound voice greeting and Sarvam TTS audio |
| `POST` | `/api/invoices/{id}/voice/transcribe-and-reply`| Multi-turn speech STT, intent classification, policy execution & TTS |
| `POST` | `/api/invoices/{id}/skip-wait` | Fast-forward single invoice deadline |
| `POST` | `/api/simulation/fast-forward` | Bulk fast-forward stored deadlines |
| `GET` | `/api/analytics/overview` | Macro-level recovery rate, volume at risk, and funnel KPIs |

---

## 8. Verification & Running Tests

```bash
# Run all conversational edge cases and split-discount tests
python -m pytest backend/tests/test_conversational_edge_cases.py -v

# Run the 26-test regression runner
python backend/tests/run_tests.py
```
