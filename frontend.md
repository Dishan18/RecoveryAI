# RecoveryAI Frontend Documentation

> **Real-time Operations Console and Autonomous Revenue Recovery Dashboard.**

---

## 1. Frontend Overview

The RecoveryAI frontend is a Next.js 16 (App Router) single-page operations application styled with TailwindCSS and Lucide Icons. It serves as the single pane of glass for credit control and recovery operations teams, providing:
- Real-time case tracking across the 9-state recovery Finite State Machine (FSM).
- Active countdown timers driven by server-authoritative UTC deadlines.
- Interactive multi-turn outbound voice call negotiation with native Sarvam AI (`bulbul-v3`) audio and live transcript history.
- Global FIFO call queue management.
- Anti-gaming concession visualization and preserved margin tracking.
- Optimistic settlement with persistent lock protection against transient state rollbacks.
- Executive Portfolio Analytics with recovery win rates and funnel drop-offs.

---

## 2. Technology Stack

| Layer | Technology | Details |
| :--- | :--- | :--- |
| **Framework** | Next.js 16.3.2 | App Router (`frontend/src/app`) with Turbopack |
| **Language** | TypeScript 5.8 | Strict mode typing across all API contracts |
| **UI Library** | React 19 | Functional components, custom hooks, and concurrent rendering |
| **Styling** | TailwindCSS 4 | Utility-first CSS with zinc/slate neutral palette |
| **Icons** | Lucide React | Lightweight, consistent SVG icon set |
| **Audio** | HTML5 Audio API | Direct WAV base64 decoding and live audio playback |
| **Speech STT** | MediaRecorder API | WebM microphone audio capture for Sarvam STT |

---

## 3. Frontend Project Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx         # Root layout with Inter font and metadata
│   │   ├── page.tsx           # Main Operations Console & Recovery Analytics Dashboard
│   │   └── globals.css        # Tailwind directives and custom animation utilities
│   ├── components/
│   │   ├── AnalyticsTab.tsx   # Executive recovery metrics, funnels & win rates
│   │   ├── CallQueueDrawer.tsx# Global slide-out FIFO call queue
│   │   ├── ManualEntryModal.tsx# Ingest custom failed invoices into active recovery
│   │   └── VoiceCallModal.tsx # Multi-turn outbound voice negotiation dialog
│   └── lib/
│       └── api.ts             # Strongly typed REST client for all backend endpoints
├── public/
│   ├── file.svg
│   ├── globe.svg
│   ├── next.svg
│   ├── vercel.svg
│   └── window.svg
├── .env.local.example         # Secret-free frontend environment template
├── .env.local                 # Local API connection config (git-ignored)
├── next.config.ts             # Next.js configuration
├── package.json               # Node package manifest
├── tsconfig.json              # TypeScript compiler settings
└── postcss.config.mjs         # PostCSS configuration
```

---

## 4. Application Entry Point & Navigation

- **Entry Point (`src/app/layout.tsx`)**: Wraps the application with global typography (`Inter` font), viewport definitions, and responsive containers.
- **Main View (`src/app/page.tsx`)**:
  - Top Navigation Bar: Seed Database, Fast Forward All, Run Batch Simulation, + Manual Entry, and Refresh.
  - Tab Switcher:
    1. **Operations Console**: Live recovery case table, KPI summary cards, FIFO call queue, and recent agent activity feed.
    2. **Recovery Analytics**: Macro-level portfolio recovery rate, gross recovered, preserved margin, failure win-rates, and funnel progression.

---

## 5. Key UI Components

### 1. Operations Case Table (`src/app/page.tsx`)
- **Interactive Rows**: Displays Customer, Failure Reason, Gross Amount in INR (₹), Autonomous State Badge, Dynamic Concession Floor, and Active Countdown.
- **Optimistic Settlement**: Instant zero-millisecond green confirmation badge upon clicking "Confirm Payment", protected by `settledInvoiceIds` state to prevent transient polling rollback.
- **Step Fast-Forwarding**: Individual "Skip Wait" buttons to advance a single invoice by one autonomous step without waiting for real-world timers.

### 2. Multi-Turn Voice Call Modal (`src/components/VoiceCallModal.tsx`)
- **Outbound Opening**: Automatically fetches and plays Sarvam `bulbul-v3` (`shubh` voice model) audio greeting stating the debtor name, merchant name, amount due, and reason.
- **Microphone Recording**: Uses `navigator.mediaDevices.getUserMedia` and `MediaRecorder` to send WebM audio to `/api/invoices/{id}/voice/transcribe-and-reply`.
- **Hinglish Quick Prompts**: Provides instant simulation buttons for 10 realistic debtor negotiation scenarios (e.g. 3-day PTP, 5-day PTP refusal, 5% discount rejection, GST billing dispute).
- **Universal Replay**: Allows re-listening to any turn's high-fidelity audio buffer.

### 3. Global Call Queue Drawer (`src/components/CallQueueDrawer.tsx`)
- Slide-out drawer tracking all debtor accounts marked `call_pending = True` by the autonomous backend scheduler.
- Direct "Start Call" triggers opening `VoiceCallModal`.

### 4. Recovery Analytics Tab (`src/components/AnalyticsTab.tsx`)
- **Summary Cards**: Total at Risk, Total Recovered, Margin Preserved via Anti-Gaming rules, and Collection Rate.
- **Funnel Progression**: Visual stage-by-stage pipeline drop-off (Ingested $\rightarrow$ WhatsApp Reminders $\rightarrow$ Voice Calls $\rightarrow$ PTP Agreed $\rightarrow$ Resolved).
- **Win Rate by Failure Category**: Win rates for `GATEWAY_TIMEOUT`, `INSUFFICIENT_FUNDS`, `MANDATE_DECLINE`, `EXPIRED_CARD`, and `DISPUTED_AMOUNT`.
- **Concession Ladder Distribution**: Volume breakdown across Full Price (0%), Tier 1 (5%), Tier 2 (8%), and Tier 3 (10%).

---

## 6. Server Truth vs. Client Countdown Architecture

```
[ PostgreSQL TIMESTAMPTZ (next_action_due_at) ]
                       │
                       ▼ (HTTP GET /api/invoices)
             [ Frontend Invoice State ]
                       │
                       ▼
      [ Client setInterval Tick (1,000ms) ]
      Computes: Math.max(0, targetTime - Date.now())
                       │
                       ▼
         [ Renders MM:SS Countdown Badge ]
```

1. **Server Truth**: The exact deadline is computed and stored as an immutable UTC `TIMESTAMPTZ` in PostgreSQL.
2. **Client Display**: A 1-second `setInterval` hook updates local state to render a smooth `MM:SS` countdown.
3. **Expiry Handshake**: When the countdown reaches `00:00`, the autonomous backend scheduler (`app/scheduler.py`) evaluates the expired row and advances the state machine.

---

## 7. API Communication Layer (`src/lib/api.ts`)

Encapsulates all typed HTTP calls to the FastAPI backend:
- `api.invoices()`: Fetch active recovery cases.
- `api.seed()`: Populate 6 representative test scenarios.
- `api.skipWait(invoiceId)`: Advance a single case by one step.
- `api.fastForward(minutes, invoiceId, allCases)`: Wind back stored deadlines in bulk.
- `api.voiceGreeting(invoiceId)`: Fetch outbound voice greeting text and audio base64.
- `api.voiceCall(invoiceId, audioBlob, textFallback)`: Submit multi-turn voice turn.
- `api.operatorOverride(invoiceId, payload)`: Manual state transition with operator rationale.
- `api.analyticsSummary()` / `api.analyticsOverview()`: Portfolio analytics.

---

## 8. Running Frontend Locally

```bash
cd frontend

# Install dependencies (if first time)
npm install

# Start Next.js development server with Turbopack
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 9. Production Build

```bash
cd frontend
npm run build
npm run start
```
