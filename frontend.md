# RecoveryAI — Frontend Engineering & Operations Console Guide

> **Real-Time Operations Cockpit & Multi-Turn Voice Recovery Interface**

---

## 1. Overview

The RecoveryAI frontend is a Next.js 16 (App Router) single-page application engineered with TypeScript, TailwindCSS 4, and Lucide React Icons. It functions as the central operations console for credit control, collections, and financial operations teams.

Key capabilities:
- **Real-Time FSM Lifecycle Tracking**: Live monitoring across all 13 states with server-synchronized UTC deadline countdowns.
- **2-Stage Partial & Full Payment Workflow**:
  - `Payment Received` $\rightarrow$ Dropdown with `Half (50%)` and `Full Payment`.
  - Recording `Half (50%)` immediately updates the balance, sets a 3-day PTP commitment, and transforms the button to a yellow `Half Paid (Click for Full)` button.
  - Recording `Full Payment` settles the invoice completely into green `Resolved`.
- **Interactive Multi-Turn Voice Dialog**: Outbound voice call simulation with native **Sarvam AI** (`bulbul:v3` `shubh` model) audio synthesis and real-time STT speech recognition.
- **1-Hour Split Payment Countdown**: Dedicated 1-hour countdown display for accounts in `SPLIT_FIRST_HALF_PENDING`.
- **Global FIFO Call Queue**: Slide-out drawer tracking debtors requiring urgent outbound voice contact.
- **Executive Portfolio Analytics**: Telemetry for Total Recovered, Volume at Risk, Anti-Gaming Margin Preserved, Collection Win-Rates, and Pipeline Funnels.

---

## 2. Tech Stack

| Layer | Technology | Details |
| :--- | :--- | :--- |
| **Framework** | Next.js 16.3.2 | App Router (`frontend/src/app`) with Turbopack |
| **Language** | TypeScript 5.8 | Strict type checking across all data contracts |
| **UI Library** | React 19 | Hooks, concurrent rendering, optimistic UI state |
| **Styling** | TailwindCSS 4 | Utility-first CSS with dark/zinc modern aesthetic |
| **Icons** | Lucide React | Clean, scalable SVG icons |
| **Audio** | HTML5 Audio API | Base64 WAV decoding and low-latency audio buffer replay |
| **Microphone** | MediaRecorder API | WebM audio capture for Sarvam STT transcription |

---

## 3. Directory Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx         # Root layout with Inter font & metadata
│   │   ├── page.tsx           # Operations Console & KPI Dashboard
│   │   └── globals.css        # Tailwind directives and animation utilities
│   ├── components/
│   │   ├── AnalyticsTab.tsx   # Recovery analytics, win-rates, and funnel progression
│   │   ├── CallQueueDrawer.tsx# Global slide-out FIFO call queue
│   │   ├── ManualEntryModal.tsx# Custom failed invoice ingestion dialog
│   │   └── VoiceCallModal.tsx # Multi-turn outbound voice negotiation dialog
│   └── lib/
│       └── api.ts             # Strongly typed REST client for backend endpoints
├── next.config.ts             # Next.js configuration
├── package.json               # Node package manifest
└── tsconfig.json              # TypeScript compiler settings
```

---

## 4. Core Workflows & UI Components

### 1. Operations Case Table (`src/app/page.tsx`)
- **Live Cases**: Renders debtor name, failure reason, gross amount, remaining balance, active state badge, authorized concession floor, and countdown timers.
- **2-Stage Payment Recording**:
  - `Payment Received` button opens a popover: `Half (50%)` or `Full Payment`.
  - Clicking `Half (50%)` triggers an optimistic zero-latency update: increments total recovered KPI, sets remaining balance to 50%, and switches the button to yellow `Half Paid (Click for Full)`.
  - Clicking the yellow button allows recording the remaining 50% to transition the invoice to green `Resolved`.

### 2. Multi-Turn Voice Call Modal (`src/components/VoiceCallModal.tsx`)
- **Outbound Opening**: Fetches and autoplays high-fidelity Sarvam AI audio greeting (`shubh` voice model) stating the debtor name, merchant name, amount due, and reason.
- **Live Audio Recording**: Captures debtor microphone input and streams WebM audio to `/api/invoices/{id}/voice/transcribe-and-reply`.
- **Hinglish Quick Simulation Prompts**: Provides instant simulation buttons for 10 realistic debtor negotiation scenarios (e.g. Split request, 50% discount request, dispute, promise to pay).
- **Turn Audio Replay**: Direct replay button for every turn's synthesized speech.

### 3. Global Call Queue Drawer (`src/components/CallQueueDrawer.tsx`)
- Slide-out drawer displaying all accounts with `call_pending = True`.
- Provides 1-click `Start Call` triggers that immediately open the voice modal.

### 4. Recovery Analytics Tab (`src/components/AnalyticsTab.tsx`)
- **Executive KPIs**: Total at Risk, Total Recovered (aggregating full and partial payments), Margin Preserved via Anti-Gaming, and Collection Rate %.
- **Funnel Progression**: Visual pipeline tracking from Ingested $\rightarrow$ WhatsApp Reminders $\rightarrow$ Voice Calls $\rightarrow$ PTP Agreed $\rightarrow$ Resolved.
- **Win Rate by Failure Reason**: Granular recovery metrics across `GATEWAY_TIMEOUT`, `INSUFFICIENT_FUNDS`, `MANDATE_DECLINE`, `EXPIRED_CARD`, and `DISPUTED_AMOUNT`.

---

## 5. Server Truth vs. Client Countdown Architecture

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

- **Server Truth**: The exact deadline is stored in PostgreSQL as UTC `TIMESTAMPTZ`.
- **Client Rendering**: A 1-second `setInterval` hook updates local state to render smooth `MM:SS` countdowns.
- **Autonomous Triggering**: When the timer reaches `00:00`, the background worker (`app/scheduler.py`) evaluates the expired row and advances the state machine.

---

## 6. Running Frontend Locally

```bash
cd frontend

# Install dependencies
npm install

# Start Next.js development server with Turbopack
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 7. Production Build

```bash
cd frontend
npm run build
npm run start
```
