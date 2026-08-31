# RecoveryAI Backend Documentation

> **Autonomous, bounded B2B revenue recovery system for Indian payment workflows.**

---

## 1. Backend Overview

The RecoveryAI backend is an asynchronous, event-driven FastAPI application. It is designed to autonomously handle the post-failure lifecycle of high-value Indian B2B and SaaS transactions. When a webhook or scheduler marks an invoice payment as failed, the backend:
1. Performs root-cause failure diagnosis (differentiating transient gateway timeouts from fundamental liquidity failures or disputes).
2. Computes anti-gaming concession limits based on the customer's historical discount abuse and merchant caps.
3. Advances an autonomous, deterministic 9-state Finite State Machine (FSM).
4. Dispatches contextual Indian English/Hinglish reminders via SMS/WhatsApp.
5. Manages an automated outbound multilingual voice queue using **Sarvam AI** (`saaras:v3` STT, `bulbul:v3` TTS) and **Google Gemini 3.6 Flash** for conversational intent classification.
6. Maintains a tamper-evident audit trail with microsecond timestamping for dispute resolution and financial compliance.

---

## 2. Backend Architecture

```
                                  [ Client UI / Next.js App Router ]
                                                  │
                                                  ▼ (HTTP / REST API)
                                       [ FastAPI Entry (main.py) ]
                                                  │
                      ┌───────────────────────────┴───────────────────────────┐
                      ▼                                                       ▼
            [ API Routers (routes.py) ]                             [ Background Worker (scheduler.py) ]
                      │                                                       │ (20s Polling Loop)
                      ▼                                                       ▼
  ┌───────────────────────────────────────┐               ┌───────────────────────────────────────┐
  │         Business Logic Engine         │               │     process_expired_deadlines()       │
  ├───────────────────────────────────────┤               └───────────────────┬───────────────────┘
  │ • Concession Calculator (calculator.py)│                                  │
  │ • Deterministic FSM (state_machine.py)│◄──────────────────────────────────┘
  │ • Dunning & Intent (gemini_service.py)│
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

## 3. Backend Project Structure

```
backend/
├── app/
│   ├── __init__.py           # Package marker
│   ├── config.py             # Pydantic v2 Settings (reads environment variables)
│   ├── database.py           # Async SQLAlchemy engine & async_sessionmaker
│   ├── migrations.py         # Idempotent DDL migration runner (CREATE & ALTER)
│   ├── models.py             # SQLAlchemy ORM models (Merchant, Customer, Invoice, RecoveryEvent)
│   ├── routes.py             # FastAPI APIRouter with all REST endpoints
│   ├── schemas.py            # Pydantic request & response schemas
│   ├── scheduler.py          # 20-second autonomous polling worker & deadline processor
│   └── engine/
│       ├── __init__.py
│       ├── calculator.py     # Deterministic concession ladder & anti-gaming rules
│       ├── gemini_service.py # Gemini 3.6 Flash intent parser & Hinglish copy generator
│       ├── sarvam_service.py # Sarvam AI saaras:v3 (STT) & bulbul:v3 (TTS with cache)
│       └── state_machine.py  # Deterministic 9-state FSM & transition validations
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Test configuration & path bootstrap
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
├── main.py                   # FastAPI application factory, middleware, and lifespan handler
├── seed.py                   # 6 realistic Indian B2B test scenario generator
├── schema.sql                # Pure DDL SQL for Supabase SQL Editor execution
├── requirements.txt          # Python pinned dependency manifest
├── .env.example              # Secret-free environment variable template
└── .env                      # Local environment configuration (git-ignored)
```

---

## 4. Application Startup Lifecycle

Defined in `main.py`:
1. **Lifespan Context Manager**:
   - `startup`:
     - Runs idempotent database migrations via `run_migrations()` in `app/migrations.py`.
     - Spawns the autonomous background scheduler (`asyncio.create_task(run_scheduler_loop())`).
     - Logs startup latency and port binding.
   - `shutdown`:
     - Gracefully cancels the background scheduler task and disposes the SQLAlchemy engine connection pool.
2. **Middleware**:
   - **CORS Middleware**: Allows cross-origin communication from `http://localhost:3000` (and configurable origins).
   - **Process Time Middleware (`add_process_time_header`)**: Computes exact execution duration per request and attaches the `X-Process-Time` header.

---

## 5. Database Layer

- **Driver**: `postgresql+asyncpg` for non-blocking asynchronous execution.
- **Engine Configuration**: `create_async_engine(settings.SUPABASE_DB_URL, echo=False, pool_pre_ping=True)`.
- **Session Management**: Fast dependency injection via `get_db()` yielding an `AsyncSession` tied to the request lifecycle.
- **Migration Strategy**: `app/migrations.py` applies `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` and `"pgcrypto"`, followed by table generation and safe `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for schema evolution.

---

## 6. Database Models

### `Merchant` (`merchants`)
- `id` (`UUID`, PK): Unique merchant identifier.
- `name` (`VARCHAR(255)`): Merchant business name.
- `default_discount_cap` (`NUMERIC(5,4)`): Default ceiling for concessions (e.g. `0.1000` = 10%).
- `created_at` (`TIMESTAMPTZ`): Merchant creation timestamp.

### `Customer` (`customers`)
- `id` (`UUID`, PK): Unique debtor customer identifier.
- `merchant_id` (`UUID`, FK `merchants.id`): Associated merchant.
- `name` (`VARCHAR(255)`): Debtor representative name.
- `phone` (`VARCHAR(20)`): Contact phone in E.164 / Indian standard (e.g. `+919876543210`).
- `email` (`VARCHAR(255)`, nullable): Debtor email address.
- `ltv_inr` (`NUMERIC(12,2)`): Historical lifetime value in Indian Rupees (₹).
- `consecutive_discount_months` (`INT`): Count of consecutive past billing cycles where concessions were granted (used for anti-gaming penalty enforcement).

### `Invoice` (`invoices`)
- `id` (`UUID`, PK): Unique invoice identifier.
- `customer_id` (`UUID`, FK `customers.id`): Debtor reference.
- `merchant_id` (`UUID`, FK `merchants.id`): Merchant reference.
- `amount_inr` (`NUMERIC(12,2)`): Gross invoice outstanding in Indian Rupees (₹).
- `status` (`VARCHAR(50)`): High-level operational status (`UNPAID`, `RESOLVED`, `DISPUTED`, `ESCALATED`).
- `failure_reason` (`VARCHAR(100)`): Root cause classification (`GATEWAY_TIMEOUT`, `INSUFFICIENT_FUNDS`, `MANDATE_DECLINE`, `EXPIRED_CARD`, `DISPUTED_AMOUNT`).
- `created_at` (`TIMESTAMPTZ`): Invoice creation timestamp.
- `due_date` (`TIMESTAMPTZ`): Original invoice payment due date.
- `next_action_due_at` (`TIMESTAMPTZ`, nullable): Server truth for autonomous deadline countdowns.
- `call_pending` (`BOOLEAN`): Set to `True` when the autonomous scheduler queues an outbound voice call for client consumption.

### `RecoveryEvent` (`recovery_events`)
- `id` (`UUID`, PK): Unique event log identifier.
- `invoice_id` (`UUID`, FK `invoices.id`): Target invoice.
- `current_state` (`VARCHAR(50)`): Deterministic FSM state recorded at transition.
- `discount_offered` (`NUMERIC(5,4)`): Concession fraction granted (e.g. `0.0500` = 5%).
- `ptp_deadline` (`TIMESTAMPTZ`, nullable): Exact deadline negotiated for promises-to-pay.
- `log_message` (`TEXT`): Human-readable and compliance audit log entry.
- `timestamp` (`TIMESTAMPTZ`): UTC event creation time.

---

## 7. API Endpoints Catalog

| Method | Endpoint | Purpose | Request Body / Params | Response Model |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/health` | Liveness & readiness probe | None | `HealthResponse` |
| `POST` | `/api/seed` | Reset & seed 6 test scenarios | None | `SeedResponse` |
| `GET` | `/api/invoices` | List all recovery invoices | None | `List[InvoiceOut]` |
| `GET` | `/api/invoices/{id}` | Fetch detailed invoice with history | `id: UUID` | `InvoiceOut` |
| `POST` | `/api/invoices` | Manually ingest a failed invoice | `ManualInvoiceCreateRequest` | `InvoiceOut` |
| `POST` | `/api/invoices/{id}/diagnose` | Run root-cause failure diagnosis | `id: UUID` | `DiagnoseResponse` |
| `GET` | `/api/invoices/{id}/discount-preview` | Calculate available concession tiers | `id: UUID` | `DiscountPreview` |
| `POST` | `/api/invoices/{id}/action` | Apply manual FSM state transition | `ActionRequest` | `TransitionResult` |
| `POST` | `/api/invoices/{id}/simulate-timeout` | Expire active step wait timer | `id: UUID` | `SimulateTimeoutResult` |
| `POST` | `/api/invoices/{id}/interpret-reply` | Parse inbound text & advance FSM | `InterpretReplyRequest` | `InterpretReplyResponse` |
| `GET` | `/api/invoices/{id}/generate-message` | Synthesize tailored dunning copy | `action_type, tier` | `GenerateMessageResponse` |
| `POST` | `/api/invoices/{id}/voice/greeting` | Generate outbound voice greeting | `id: UUID` | `VoiceGreetingResponse` |
| `POST` | `/api/invoices/{id}/voice/transcribe-and-reply` | Multi-turn speech STT, intent & TTS | `audio_file, text_fallback` | `VoiceCallResponse` |
| `GET` | `/api/invoices/{id}/audit-export` | Export compliance dossier (.json) | `id: UUID` | `AuditExportResponse` |
| `POST` | `/api/invoices/{id}/skip-wait` | Advance invoice by 1 autonomous step | `id: UUID` | `SkipWaitResponse` |
| `POST` | `/api/invoices/{id}/acknowledge-call` | Dequeue completed outbound voice call | `id: UUID` | `AcknowledgeCallResponse` |
| `POST` | `/api/invoices/{id}/override` | Operator exception override | `OperatorOverrideRequest` | `InvoiceOut` |
| `POST` | `/api/simulation/fast-forward` | Advance stored deadlines in bulk | `FastForwardRequest` | `FastForwardResponse` |
| `POST` | `/api/simulation/batch-run` | Run autonomous batch simulation | None | `BatchSimulationResponse` |
| `GET` | `/api/analytics/summary` | Real-time portfolio KPI counts | None | `AnalyticsSummaryResponse` |
| `GET` | `/api/analytics/overview` | Detailed executive analytics & funnel | None | `AnalyticsOverview` |

---

## 8. Deterministic Recovery Workflow & FSM

RecoveryAI implements a bounded state graph. Large Language Models classify unstructured customer intent, but have **zero authority** to modify balances or skip state guards.

```
       [TRIGGERED]
            │
            ▼
       [DIAGNOSED]
            │
            ▼
     [REMINDER_SENT]
            │
            ▼
       [LINK_SENT]
            │
   ┌────────┴───────────────────────────┐
   ▼                                    ▼
[PTP_ACTIVE]                    [TIER_1_DISCOUNT]
   │                                    │
   │ (Breach)                           ▼
   │                             [TIER_2_DISCOUNT]
   │                                    │
   │                                    ▼
   │                             [TIER_3_FLOOR]
   │                                    │
   ├────────────────┬───────────────────┤
   ▼                ▼                   ▼
[RESOLVED]   [FROZEN_DISPUTE]   [ESCALATED_HUMAN]
```

### FSM States:
1. `TRIGGERED`: Invoice payment failure detected.
2. `DIAGNOSED`: Failure reason analyzed, customer profile and anti-gaming limits evaluated.
3. `REMINDER_SENT`: Initial non-concession WhatsApp/SMS reminder dispatched.
4. `LINK_SENT`: Alternate payment rail dispatched (UPI / Card / NetBanking).
5. `PTP_ACTIVE`: Customer promised payment by a verified deadline ($\le 3$ days).
6. `TIER_1_DISCOUNT`: First concession tier offered (50% of allowable merchant cap).
7. `TIER_2_DISCOUNT`: Second concession tier offered (80% of allowable merchant cap).
8. `TIER_3_FLOOR`: Final concession floor offered (100% of allowable merchant cap).
9. `RESOLVED`: Payment confirmed and account reconciled (Terminal).
10. `FROZEN_DISPUTE`: Account frozen for invoice audit review (Terminal).
11. `ESCALATED_HUMAN`: Escalated to senior credit control officer (Terminal).

---

## 9. Dynamic Concession Engine & Anti-Gaming

Calculated in `app/engine/calculator.py`:

$$\text{Allowable Cap} = \text{Merchant Cap} \times \text{Multiplier}(\text{Consecutive Discount Months})$$

| Consecutive Months | Multiplier | Tier 1 (50%) | Tier 2 (80%) | Tier 3 (100%) | Policy Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **0 months (Clean)** | **1.0x** | 5.0% | 8.0% | 10.0% | Full ladder accessible |
| **1 month** | **0.8x** | 4.0% | 6.4% | 8.0% | Tier 3 blocked; floor capped at 8% |
| **2 months** | **0.5x** | 2.5% | 4.0% | 5.0% | Tiers 2 & 3 blocked; floor capped at 5% |
| **3+ months (Chronic)**| **0.0x** | 0.0% | 0.0% | 0.0% | **Zero discounts allowed**; Full recovery or escalation |

---

## 10. Autonomous Scheduler & Fast-Forward Engine

- **Polling Loop (`app/scheduler.py`)**: Runs every 20 seconds. Evaluates invoices where `status == 'UNPAID'` and `next_action_due_at <= now()`.
- **Autonomous Transitions**:
  - `TRIGGERED` $\rightarrow$ `DIAGNOSED`
  - `DIAGNOSED` $\rightarrow$ `REMINDER_SENT`
  - `REMINDER_SENT` $\rightarrow$ `LINK_SENT`
  - `LINK_SENT` $\rightarrow$ sets `call_pending = True` (enqueues outbound voice call)
  - `PTP_ACTIVE` (expired) $\rightarrow$ logs breach and sets `call_pending = True`
  - `TIER_3_FLOOR` (expired) $\rightarrow$ `ESCALATED_HUMAN`
- **Fast-Forward (`/api/simulation/fast-forward`)**: Rather than mocking logic, Fast-Forward shifts stored UTC deadlines backward in the PostgreSQL database (`next_action_due_at = now - 1 second`) and immediately invokes `process_expired_deadlines()`.

---

## 11. Multilingual Voice System

- **Sarvam AI `saaras:v3` STT**: Transcribes debtor spoken Hindi/Hinglish/English audio.
- **Gemini 3.6 Flash Intent Engine**: Evaluates debtor reply against 6 structured intents (`AGREED_TO_PAY`, `PROMISE_TO_PAY`, `DISPUTE`, `HARD_REFUSAL`, `REQUEST_DISCOUNT`, `UNKNOWN`) with a strict 1.8s timeout cap and deterministic fallback rules.
- **Sarvam AI `bulbul:v3` TTS**: Synthesizes natural Indian-accented voice replies (`speaker="shubh"`).
- **In-Memory Audio Caching (`_TTS_AUDIO_CACHE`)**: Caches synthesized WAV audio chunks to deliver `< 0.1ms` response times for common statements.

---

## 12. Running Tests Locally

All tests are located in `backend/tests/`. Run standalone or via pytest:

```bash
cd backend
..\.venv\Scripts\activate

# Run full test suite
python -m pytest tests/

# Run individual verification suites
python tests/test_anti_gaming_and_manual_seeding.py
python tests/test_analytics_overview.py
python tests/test_perf_audit.py
```

---

## 13. Environment Variables Reference

| Variable | Required | Purpose | Example |
| :--- | :--- | :--- | :--- |
| `SUPABASE_DB_URL` | Yes | PostgreSQL connection string via asyncpg | `postgresql+asyncpg://postgres:pass@db.supabase.co:5432/postgres` |
| `GEMINI_API_KEY` | Optional | Google Gemini 3.6 Flash API key for LLM intent parsing | `AIzaSy...` (Falls back to regex rules if omitted) |
| `SARVAM_API_KEY` | Optional | Sarvam AI API key for Indian STT/TTS | `sk_...` (Falls back gracefully if omitted) |
