/**
 * RecoveryAI — API client (Phase 1 + 2)
 * Type-safe wrapper around the FastAPI backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// ── Domain types ──────────────────────────────────────────────────────────────

export interface Merchant {
  id: string;
  name: string;
  default_discount_cap: string;
  created_at: string;
}

export interface Customer {
  id: string;
  merchant_id: string;
  name: string;
  phone: string;
  email: string | null;
  ltv_inr: string;
  consecutive_discount_months: number;
}

export interface RecoveryEvent {
  id: string;
  invoice_id: string;
  current_state: string;
  discount_offered: string;
  ptp_deadline: string | null;
  log_message: string | null;
  timestamp: string;
}

export interface Invoice {
  id: string;
  amount_inr: string;
  original_amount_inr?: string | null;
  recovered_amount_inr?: string | null;
  status: string;
  failure_reason: string | null;
  created_at: string;
  due_date: string | null;
  next_action_due_at: string | null;
  call_pending: boolean;
  customer: Customer;
  merchant: Merchant;
  recovery_events: RecoveryEvent[];
}

export interface SeedResponse {
  message: string;
  invoices_created: number;
  scenarios: string[];
}

export interface HealthResponse {
  status: string;
  service: string;
}

// ── Phase 2 types ─────────────────────────────────────────────────────────────

export interface DiagnoseResponse {
  invoice_id: string;
  previous_state: string;
  new_state: string;
  failure_reason: string | null;
  recommended_action: string;
  log_message: string;
  event_id: string;
}

export interface TierPreview {
  tier: number;
  tier_state: string;
  discount_rate: string;
  discount_pct: string;
  discount_amount_inr: string;
  net_payable_inr: string;
  is_accessible: boolean;
  blocked_reason: string | null;
}

export interface DiscountPreview {
  merchant_cap: string;
  merchant_cap_pct: string;
  effective_cap: string;
  effective_cap_pct: string;
  consecutive_months: number;
  gross_amount_inr: string;
  tiers: TierPreview[];
}

export type ActionType =
  | "SEND_LINK"
  | "OFFER_DISCOUNT"
  | "SET_PTP"
  | "FLAG_DISPUTE"
  | "RESOLVE_PAYMENT";

export interface ActionRequest {
  action_type: ActionType;
  ptp_deadline?: string;
  tier?: number;
  notes?: string;
}

export interface TransitionResult {
  invoice_id: string;
  action_type: string;
  previous_state: string;
  new_state: string;
  new_invoice_status: string;
  discount_offered: string;
  net_payable_inr: string | null;
  ptp_deadline: string | null;
  event_id: string;
  audit_log: string;
  discount_preview: DiscountPreview | null;
}

export interface SimulateTimeoutResult {
  invoice_id: string;
  simulated_scenario: string;
  previous_state: string;
  new_state: string;
  new_invoice_status: string;
  discount_offered: string;
  net_payable_inr: string | null;
  event_id: string;
  audit_log: string;
}

// ── Phase 3 types ─────────────────────────────────────────────────────────────

export interface DebtorIntentResult {
  intent: string;
  ptp_deadline: string | null;
  dispute_reason: string | null;
  confidence: number;
  explanation: string;
  used_fallback: boolean;
}

export interface DunningMessageResult {
  subject: string;
  body: string;
  channel: string;
  action_type: string;
  used_fallback: boolean;
}

export interface InterpretReplyResponse {
  invoice_id: string;
  raw_message: string;
  intent_result: DebtorIntentResult;
  state_changed: boolean;
  previous_state: string;
  new_state: string;
  new_invoice_status: string;
  event_id: string | null;
  agent_reply: DunningMessageResult;
  audit_log: string;
}

export interface GenerateMessageResponse {
  invoice_id: string;
  action_type: string;
  message: DunningMessageResult;
}

// ── Phase 4 types ─────────────────────────────────────────────────────────────

export interface VoiceCallResponse {
  invoice_id: string;
  transcription: string;
  parsed_intent: string;
  confidence?: number;
  ptp_deadline: string | null;
  dispute_reason: string | null;
  agent_reply_text: string;
  audio_base64: string;
  audio_format: string;
  previous_state: string;
  new_state: string;
  new_invoice_status: string;
  event_id?: string | null;
  audit_log?: string;
  used_stt_fallback: boolean;
  used_tts_fallback: boolean;
  applied_discount: number;
  authorized_discount_rate?: number;
  authorized_net_amount?: number;
  customer_stated_discount_pct?: number | null;
  action_executed: string;
  trigger_auto_close?: boolean;
}

export interface VoiceGreetingResponse {
  invoice_id: string;
  greeting_text: string;
  audio_base64: string;
  audio_format: string;
  used_tts_fallback: boolean;
}

// ── HTTP helper ───────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> ?? {}) };
  if (!isFormData) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Network disconnected";
    throw new Error(`API connection failed for ${path}: ${msg}`);
  }

  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail ?? JSON.stringify(body);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  // Phase 1
  health: () => apiFetch<HealthResponse>("/health"),
  seed: () => apiFetch<SeedResponse>("/api/seed", { method: "POST" }),
  invoices: () => apiFetch<Invoice[]>("/api/invoices"),
  invoice: (id: string) => apiFetch<Invoice>(`/api/invoices/${id}`),

  // Phase 2
  diagnose: (id: string) =>
    apiFetch<DiagnoseResponse>(`/api/invoices/${id}/diagnose`, { method: "POST" }),

  discountPreview: (id: string) =>
    apiFetch<DiscountPreview>(`/api/invoices/${id}/discount-preview`),

  action: (id: string, payload: ActionRequest) =>
    apiFetch<TransitionResult>(`/api/invoices/${id}/action`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  simulateTimeout: (id: string) =>
    apiFetch<SimulateTimeoutResult>(`/api/invoices/${id}/simulate-timeout`, {
      method: "POST",
    }),

  // Phase 3
  interpretReply: (id: string, message: string) =>
    apiFetch<InterpretReplyResponse>(`/api/invoices/${id}/interpret-reply`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  generateMessage: (id: string, actionType: string = "SOFT_REMINDER", tier?: number) =>
    apiFetch<GenerateMessageResponse>(
      `/api/invoices/${id}/generate-message?action_type=${encodeURIComponent(actionType)}${tier ? `&tier=${tier}` : ""}`
    ),

  // Phase 4
  voiceGreeting: (id: string) =>
    apiFetch<VoiceGreetingResponse>(`/api/invoices/${id}/voice/greeting`, {
      method: "POST",
    }),

  voiceCall: (id: string, audioBlob?: Blob, textFallback?: string) => {
    const formData = new FormData();
    if (audioBlob) {
      formData.append("audio_file", audioBlob, "recording.webm");
    }
    if (textFallback) {
      formData.append("text_fallback", textFallback);
    }
    return apiFetch<VoiceCallResponse>(`/api/invoices/${id}/voice/transcribe-and-reply`, {
      method: "POST",
      body: formData,
    });
  },

  // Phase 5
  analyticsSummary: () => apiFetch<AnalyticsSummaryResponse>("/api/analytics/summary"),
  analyticsOverview: () => apiFetch<AnalyticsOverview>("/api/analytics/overview"),
  runBatchSimulation: () => apiFetch<BatchSimulationResponse>("/api/simulation/batch-run", { method: "POST" }),
  exportAudit: (id: string) => apiFetch<AuditExportResponse>(`/api/invoices/${id}/audit-export`),

  // Phase 6 — Autonomous Agent Operations
  fastForward: (minutes: number = 10, invoiceId?: string, allCases: boolean = false) =>
    apiFetch<FastForwardResponse>("/api/simulation/fast-forward", {
      method: "POST",
      body: JSON.stringify({ minutes, invoice_id: invoiceId, all_cases: allCases }),
    }),

  /**
   * Advance a single invoice by exactly ONE autonomous step.
   * Returns trigger_call=true when the next action is a voice call.
   */
  skipWait: (id: string) =>
    apiFetch<SkipWaitResponse>(`/api/invoices/${id}/skip-wait`, { method: "POST" }),

  createInvoice: (payload: ManualInvoiceCreatePayload) =>
    apiFetch<Invoice>("/api/invoices", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  acknowledgeCallPending: (id: string) =>
    apiFetch<AcknowledgeCallResponse>(`/api/invoices/${id}/acknowledge-call`, { method: "POST" }),

  operatorOverride: (id: string, payload: OperatorOverridePayload) =>
    apiFetch<Invoice>(`/api/invoices/${id}/override`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  recordPayment: (id: string, payload: RecordPaymentPayload) =>
    apiFetch<RecordPaymentResponse>(`/api/invoices/${id}/record-payment`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

export interface RecordPaymentPayload {
  payment_type: "HALF" | "FULL";
  notes?: string;
}

export interface RecordPaymentResponse {
  invoice_id: string;
  payment_type: string;
  amount_paid_inr: string;
  remaining_balance_inr: string;
  previous_state: string;
  new_state: string;
  new_status: string;
  ptp_deadline: string | null;
  message: string;
  invoice: Invoice;
}

export interface AcknowledgeCallResponse {
  invoice_id: string;
  call_pending: boolean;
  message: string;
}

// ── Phase 5 & 6 types ─────────────────────────────────────────────────────────

export interface AnalyticsOverview {
  summary: {
    total_cases: number;
    total_at_risk: number;
    gross_recovered: number;
    net_collected: number;
    discounts_granted: number;
    recovery_rate: number;
    margin_preserved: number;
  };
  funnel: Array<{ stage: string; count: number }>;
  by_reason: Array<{
    reason: string;
    total_cases: number;
    resolved_cases: number;
    amount_at_risk: number;
    recovery_rate: number;
  }>;
  concessions: Array<{
    tier: string;
    resolved_cases: number;
    volume_inr: number;
  }>;
}

export interface AnalyticsSummaryResponse {
  total_at_risk_inr: number;
  total_recovered_inr: number;
  margin_preserved_inr: number;
  active_ptp_inr: number;
  frozen_dispute_inr: number;
  recovery_rate_pct: number;
  total_invoices: number;
  resolved_count: number;
  ptp_count: number;
  disputed_count: number;
}

export interface BatchSimulationResponse {
  total_created: number;
  resolved_count: number;
  ptp_count: number;
  disputed_count: number;
  total_recovered_inr: number;
  summary_message: string;
}

export interface AuditExportResponse {
  dossier_id: string;
  exported_at: string;
  merchant: Record<string, unknown>;
  customer: Record<string, unknown>;
  invoice: Record<string, unknown>;
  diagnostic: Record<string, unknown>;
  event_timeline: Array<Record<string, unknown>>;
  settlement: Record<string, unknown>;
}

export interface FastForwardResponse {
  advanced_minutes: number;
  actions_triggered: number;
  events_updated: Array<{ invoice_id: string; customer_name: string; action: string }>;
  message: string;
  /** Invoice IDs whose immediate next step is a voice call — enqueue these into callQueue */
  call_triggered_ids: string[];
}

export interface SkipWaitResponse {
  invoice_id: string;
  previous_state: string;
  new_state: string;
  action_taken: string;
  /** True when the invoice is ready for a voice call — frontend must open VoiceCallModal */
  trigger_call: boolean;
  discount_offered: string;
  net_payable_inr: string | null;
}

export interface ManualInvoiceCreatePayload {
  customer_name: string;
  phone: string;
  amount_inr: number;
  failure_reason: string;
  ltv_inr?: number;
  consecutive_discount_months?: number;
  merchant_name?: string;
  merchant_cap?: number;
}

export interface OperatorOverridePayload {
  override_type: "MANUAL_LINK" | "SIMULATE_PTP" | "FORCE_DISCOUNT" | "FLAG_DISPUTE" | "MARK_SETTLED" | "ESCALATE_HUMAN";
  reason: string;
}

