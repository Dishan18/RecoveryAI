"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock,
  Download,
  Filter,
  PhoneCall,
  RefreshCw,
  Search,
  Users,
  X,
} from "lucide-react";
import {
  AnalyticsSummaryResponse,
  api,
  AuditExportResponse,
  Invoice,
  OperatorOverridePayload,
  RecoveryEvent,
  SkipWaitResponse,
} from "../lib/api";
import { CallQueueDrawer } from "../components/CallQueueDrawer";
import { ManualEntryModal } from "../components/ManualEntryModal";
import { VoiceCallModal } from "../components/VoiceCallModal";
import { AnalyticsTab } from "../components/AnalyticsTab";

// ─────────────────────────────────────────────────────────────────────────────
// Formatters
// ─────────────────────────────────────────────────────────────────────────────
const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const fmtInr = (v: string | number) =>
  INR.format(typeof v === "string" ? parseFloat(v) : v);

const fmtDate = (iso: string | null) => {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const fmtTime = (iso: string | null) => {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

function formatRemainingTime(deadlineIso: string | null, currentTimestamp: number): string {
  if (!deadlineIso) return "00:00";
  const diff = new Date(deadlineIso).getTime() - currentTimestamp;
  if (diff <= 0) return "00:00";
  const totalSecs = Math.floor(diff / 1000);
  const mins = Math.floor(totalSecs / 60);
  const secs = totalSecs % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

// Restrained Status Config
const STATUS_CFG: Record<string, { label: string; textClass: string; bgClass: string }> = {
  UNPAID:    { label: "Unpaid",    textClass: "text-amber-700", bgClass: "bg-amber-50" },
  RESOLVED:  { label: "Resolved",  textClass: "text-green-700", bgClass: "bg-green-50" },
  DISPUTED:  { label: "Disputed",  textClass: "text-red-700",   bgClass: "bg-red-50"   },
  ESCALATED: { label: "Escalated", textClass: "text-red-700",   bgClass: "bg-red-50"   },
};

const STATE_CFG: Record<string, { label: string; textClass: string; bgClass: string }> = {
  TRIGGERED:       { label: "Triggered",        textClass: "text-blue-700",   bgClass: "bg-blue-50"   },
  DIAGNOSED:       { label: "Diagnosed",        textClass: "text-blue-700",   bgClass: "bg-blue-50"   },
  REMINDER_SENT:   { label: "Reminder Sent",    textClass: "text-blue-700",   bgClass: "bg-blue-50"   },
  LINK_SENT:       { label: "Link Sent",         textClass: "text-blue-700",   bgClass: "bg-blue-50"   },
  PTP_ACTIVE:      { label: "PTP Active",        textClass: "text-amber-700",  bgClass: "bg-amber-50"  },
  TIER_1_DISCOUNT: { label: "Tier 1 Discount",  textClass: "text-purple-700", bgClass: "bg-purple-50" },
  TIER_2_DISCOUNT: { label: "Tier 2 Discount",  textClass: "text-purple-700", bgClass: "bg-purple-50" },
  TIER_3_FLOOR:    { label: "Tier 3 Floor",      textClass: "text-purple-700", bgClass: "bg-purple-50" },
  RESOLVED:        { label: "Recovered",         textClass: "text-green-700",  bgClass: "bg-green-50"  },
  FROZEN_DISPUTE:  { label: "Frozen",            textClass: "text-red-700",    bgClass: "bg-red-50"    },
  ESCALATED_HUMAN: { label: "Escalated",         textClass: "text-red-700",    bgClass: "bg-red-50"    },
};

const FSM_STEPS = ["Triggered", "Reminder Sent", "Voice Call", "PTP / Discount", "Resolved"];

// Returns a rich badge describing what the autonomous agent is currently waiting for.
function getNextActionBadge(inv: Invoice, isInQueue: boolean, now: number): {
  icon: string;
  label: string;
  subLabel: string;
  isSkippable: boolean;
  isTerminal: boolean;
} {
  const TERMINAL = { icon: "", label: "", subLabel: "", isSkippable: false, isTerminal: true };

  if (inv.status === "RESOLVED") {
    return { ...TERMINAL, icon: "✅", label: "Payment recovered", subLabel: "Workflow complete" };
  }
  if (inv.status === "DISPUTED") {
    return { ...TERMINAL, icon: "🔒", label: "Dispute Frozen", subLabel: "Outreach paused — finance review" };
  }
  if (inv.status === "ESCALATED") {
    return { ...TERMINAL, icon: "🚨", label: "Escalated to Human", subLabel: "Senior officer assigned" };
  }

  const latest = inv.recovery_events?.[inv.recovery_events.length - 1];
  const currentState = latest?.current_state ?? "TRIGGERED";

  if (isInQueue || inv.call_pending) {
    return { icon: "📞", label: "Call Queued", subLabel: "Voice call ready / in queue", isSkippable: false, isTerminal: false };
  }

  if (currentState === "FROZEN_DISPUTE") {
    return { ...TERMINAL, icon: "🔒", label: "Dispute Frozen", subLabel: "Outreach paused — finance review" };
  }
  if (currentState === "ESCALATED_HUMAN") {
    return { ...TERMINAL, icon: "🚨", label: "Escalated to Human", subLabel: "Senior officer assigned" };
  }
  if (currentState === "RESOLVED") {
    return { ...TERMINAL, icon: "✅", label: "Payment recovered", subLabel: "Workflow complete" };
  }

  const remaining = formatRemainingTime(inv.next_action_due_at, now);

  if (currentState === "TRIGGERED") {
    return {
      icon: "⏳",
      label: "Triggered",
      subLabel: `Diagnosis & Reminder in ${remaining}`,
      isSkippable: true,
      isTerminal: false,
    };
  }
  if (currentState === "DIAGNOSED") {
    return {
      icon: "⏳",
      label: "Diagnosed",
      subLabel: `Reminder in ${remaining}`,
      isSkippable: true,
      isTerminal: false,
    };
  }
  if (currentState === "REMINDER_SENT") {
    return {
      icon: "✓",
      label: "Reminder Sent ✓",
      subLabel: `Waiting for payment · Call in ${remaining}`,
      isSkippable: true,
      isTerminal: false,
    };
  }
  if (currentState === "LINK_SENT") {
    return {
      icon: "✓",
      label: "Link Sent ✓",
      subLabel: `Waiting for payment · Call in ${remaining}`,
      isSkippable: true,
      isTerminal: false,
    };
  }
  if (currentState === "PTP_ACTIVE") {
    const ptpDeadline = latest?.ptp_deadline;
    const deadlineStr = ptpDeadline ? new Date(ptpDeadline).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "pending";
    return {
      icon: "⏸️",
      label: "PTP Active",
      subLabel: `Paused until ${deadlineStr} (${remaining})`,
      isSkippable: true,
      isTerminal: false,
    };
  }
  if (currentState.startsWith("TIER_")) {
    const discRatio = latest?.discount_offered ? parseFloat(latest.discount_offered) : 0;
    const netAmt = parseFloat(inv.amount_inr) * (1 - discRatio);
    const tierName = currentState === "TIER_1_DISCOUNT" ? "Tier 1" : currentState === "TIER_2_DISCOUNT" ? "Tier 2" : "Tier 3 Floor";
    return {
      icon: "⏳",
      label: `Awaiting Payment (₹${Math.round(netAmt).toLocaleString("en-IN")})`,
      subLabel: `Follow-up call in ${remaining}`,
      isSkippable: true,
      isTerminal: false,
    };
  }

  return { icon: "⏳", label: "Waiting", subLabel: `Next action in ${remaining}`, isSkippable: true, isTerminal: false };
}

// Dynamically compute whether an FSM step is completed, current, or pending
function getStepStatus(
  stepIdx: number,
  currentState: string,
  events: RecoveryEvent[],
  isCalling: boolean,
  isInQueue: boolean,
  status: string
): { isCurrent: boolean; isCompleted: boolean } {
  const history = new Set(events?.map((e) => e.current_state) ?? []);
  const hasVoiceCall = events?.some(
    (e) => (e.log_message && e.log_message.includes("[VOICE CALL]")) ||
           e.current_state.startsWith("TIER_") ||
           e.current_state === "PTP_ACTIVE" ||
           (e.current_state === "LINK_SENT" && (e.log_message?.includes("Voice") || e.log_message?.includes("voice")))
  );
  const hasReminder = history.has("REMINDER_SENT");
  const hasPtpOrDiscount = history.has("PTP_ACTIVE") ||
    history.has("TIER_1_DISCOUNT") ||
    history.has("TIER_2_DISCOUNT") ||
    history.has("TIER_3_FLOOR") ||
    currentState.startsWith("TIER_") ||
    currentState === "PTP_ACTIVE";
  const isResolved = status === "RESOLVED" || currentState === "RESOLVED";

  // Step 0: Triggered
  if (stepIdx === 0) {
    if (currentState === "TRIGGERED" && !hasReminder && !hasVoiceCall && !isResolved) {
      return { isCurrent: true, isCompleted: false };
    }
    return { isCurrent: false, isCompleted: true };
  }

  // Step 1: Reminder Sent
  if (stepIdx === 1) {
    if (currentState === "REMINDER_SENT") {
      return { isCurrent: true, isCompleted: false };
    }
    if (hasReminder || hasVoiceCall || hasPtpOrDiscount || isResolved) {
      return { isCurrent: false, isCompleted: true };
    }
    return { isCurrent: false, isCompleted: false };
  }

  // Step 2: Voice Call
  if (stepIdx === 2) {
    if (isCalling || isInQueue) {
      return { isCurrent: true, isCompleted: false };
    }
    if (hasVoiceCall) {
      return { isCurrent: false, isCompleted: true };
    }
    return { isCurrent: false, isCompleted: false };
  }

  // Step 3: PTP / Discount
  if (stepIdx === 3) {
    if (currentState === "PTP_ACTIVE" || currentState.startsWith("TIER_") || (currentState === "LINK_SENT" && hasVoiceCall)) {
      return { isCurrent: true, isCompleted: false };
    }
    if (hasPtpOrDiscount) {
      return { isCurrent: false, isCompleted: true };
    }
    return { isCurrent: false, isCompleted: false };
  }

  // Step 4: Resolved
  if (stepIdx === 4) {
    if (isResolved) {
      return { isCurrent: false, isCompleted: true };
    }
    return { isCurrent: false, isCompleted: false };
  }

  return { isCurrent: false, isCompleted: false };
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Operations Dashboard Component
// ─────────────────────────────────────────────────────────────────────────────
export default function OperationsConsole() {
  const [currentTab, setCurrentTab] = useState<"operations" | "analytics">("operations");
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Single high-level ticking hook
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Modals & Call Queue
  const [isManualModalOpen, setIsManualModalOpen] = useState(false);
  const [voiceCallInvoice, setVoiceCallInvoice] = useState<Invoice | null>(null);
  const [callQueue, setCallQueue] = useState<Invoice[]>([]);
  const [expandedInvoiceId, setExpandedInvoiceId] = useState<string | null>(null);

  // Override Dropdown & Modal State
  const [activeOverrideMenuId, setActiveOverrideMenuId] = useState<string | null>(null);
  const [overrideModalTarget, setOverrideModalTarget] = useState<Invoice | null>(null);
  const [overrideType, setOverrideType] = useState<OperatorOverridePayload["override_type"]>("MANUAL_LINK");
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideSubmitting, setOverrideSubmitting] = useState(false);

  // Payment confirmation & lock state
  const [paymentConfirmId, setPaymentConfirmId] = useState<string | null>(null);
  const [paymentSubmittingId, setPaymentSubmittingId] = useState<string | null>(null);
  const [settledInvoiceIds, setSettledInvoiceIds] = useState<Set<string>>(new Set());
  const [halfSettledInvoiceIds, setHalfSettledInvoiceIds] = useState<Set<string>>(new Set());

  const isInvoiceHalfSettled = (inv: Invoice) => {
    if (halfSettledInvoiceIds.has(inv.id)) return true;
    return (inv.recovery_events || []).some(
      (e) =>
        e.log_message?.includes("50% Partial Payment") ||
        e.log_message?.includes("MARK_HALF_SETTLED") ||
        e.log_message?.includes("Half payment recorded") ||
        e.log_message?.includes("First 50%")
    );
  };

  // Fast forward state
  const [fastForwarding, setFastForwarding] = useState(false);
  const [skipWaitLoading, setSkipWaitLoading] = useState<Set<string>>(new Set());
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Search & Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [showAllActivity, setShowAllActivity] = useState(false);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  };

  const loadData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    try {
      const [invData, statsData] = await Promise.all([
        api.invoices().catch((e) => {
          console.warn("Invoices sync paused (backend reconnecting):", e);
          return null;
        }),
        api.analyticsSummary().catch((e) => {
          console.warn("Analytics sync paused (backend reconnecting):", e);
          return null;
        }),
      ]);
      if (invData !== null) {
        setInvoices((prev) => {
          return invData.map((inv) => {
            if (settledInvoiceIds.has(inv.id) || inv.status === "RESOLVED") {
              return { ...inv, status: "RESOLVED", next_action_due_at: null, call_pending: false };
            }
            return inv;
          });
        });
      }
      if (statsData !== null) setAnalytics(statsData);

      // Auto-enqueue any backend call_pending invoices into FIFO queue
      if (invData) {
        const pendingCalls = invData.filter((inv) => inv.call_pending && inv.status === "UNPAID");
        if (pendingCalls.length > 0) {
          setCallQueue((prev) => {
            const toAdd = pendingCalls.filter((p) => !prev.some((q) => q.id === p.id));
            return [...prev, ...toAdd];
          });
          for (const p of pendingCalls) {
            api.acknowledgeCallPending(p.id).catch(console.warn);
          }
        }
      }
    } catch (err: unknown) {
      console.warn("Background sync paused (backend re-connecting)...", err);
    } finally {
      setLoading(false);
      if (isRefresh) setRefreshing(false);
    }
  }, [settledInvoiceIds]);

  useEffect(() => {
    loadData();
    const interval = setInterval(() => loadData(false), 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  // Fast Forward All — expires all active pending timers simultaneously
  const handleFastForwardAll = async () => {
    setFastForwarding(true);
    try {
      const res = await api.fastForward(60, undefined, true);
      showToast(res.message);
      if (res.call_triggered_ids && res.call_triggered_ids.length > 0) {
        const freshInvoices = await api.invoices();
        const toEnqueue = freshInvoices.filter(
          (inv) =>
            res.call_triggered_ids.includes(inv.id) &&
            !callQueue.some((q) => q.id === inv.id)
        );
        if (toEnqueue.length > 0) {
          setCallQueue((prev) => [...prev, ...toEnqueue]);
        }
        setInvoices(freshInvoices);
      } else {
        await loadData(true);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Fast forward failed";
      showToast("Error: " + msg);
    } finally {
      setFastForwarding(false);
    }
  };

  // Skip Wait — immediately expire wait time with optimistic UI feedback
  const handleSkipWait = async (inv: Invoice) => {
    setSkipWaitLoading((prev) => new Set(prev).add(inv.id));
    const previousInvoices = invoices;

    // Optimistically update invoice state immediately (0ms visual latency)
    setInvoices((prev) =>
      prev.map((item) =>
        item.id === inv.id
          ? { ...item, next_action_due_at: null }
          : item
      )
    );

    try {
      const res: SkipWaitResponse = await api.skipWait(inv.id);
      showToast(`${inv.customer.name}: ${res.action_taken}`);
      if (res.trigger_call) {
        if (!callQueue.some((q) => q.id === inv.id)) {
          setCallQueue((prev) => [...prev, inv]);
        }
      }
      await loadData(false);
    } catch (err: unknown) {
      setInvoices(previousInvoices);
      const msg = err instanceof Error ? err.message : "Skip wait failed";
      showToast("Error: " + msg);
    } finally {
      setSkipWaitLoading((prev) => {
        const next = new Set(prev);
        next.delete(inv.id);
        return next;
      });
    }
  };

  // Optimistic Payment Received Handler (0ms visual feedback with Half / Full 1-click actions)
  const handlePaymentReceivedOptimistic = async (inv: Invoice, paymentType: "HALF" | "FULL" = "FULL") => {
    setPaymentSubmittingId(inv.id);
    setPaymentConfirmId(null);
    const previousInvoices = invoices;
    const grossVal = parseFloat(inv.amount_inr) || 0;

    if (paymentType === "FULL") {
      setSettledInvoiceIds((prev) => new Set(prev).add(inv.id));
      setHalfSettledInvoiceIds((prev) => {
        const next = new Set(prev);
        next.delete(inv.id);
        return next;
      });
      // Instantly mark resolved locally
      setInvoices((prev) =>
        prev.map((item) =>
          item.id === inv.id
            ? {
                ...item,
                amount_inr: "0.00",
                status: "RESOLVED",
                next_action_due_at: null,
                call_pending: false,
                recovery_events: [
                  ...(item.recovery_events || []),
                  {
                    id: "optimistic-" + Date.now(),
                    invoice_id: item.id,
                    current_state: "RESOLVED",
                    discount_offered: item.recovery_events?.[item.recovery_events.length - 1]?.discount_offered || "0.0",
                    ptp_deadline: null,
                    log_message: "[PAYMENT RECEIVED] Full settlement confirmed (100%). Invoice marked RESOLVED.",
                    timestamp: new Date().toISOString(),
                  } as RecoveryEvent,
                ],
              }
            : item
        )
      );
      showToast(`Full payment (100%) received & settled for ${inv.customer.name}`);
    } else {
      setHalfSettledInvoiceIds((prev) => new Set(prev).add(inv.id));
      const halfVal = (grossVal / 2).toFixed(2);
      const ptpDate = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString();
      // Instantly update remaining amount & PTP
      setInvoices((prev) =>
        prev.map((item) =>
          item.id === inv.id
            ? {
                ...item,
                amount_inr: halfVal,
                status: "UNPAID",
                next_action_due_at: ptpDate,
                call_pending: false,
                recovery_events: [
                  ...(item.recovery_events || []),
                  {
                    id: "optimistic-" + Date.now(),
                    invoice_id: item.id,
                    current_state: "PTP_ACTIVE",
                    discount_offered: item.recovery_events?.[item.recovery_events.length - 1]?.discount_offered || "0.0",
                    ptp_deadline: ptpDate,
                    log_message: `[PAYMENT RECEIVED] 50% Partial Payment Received (₹${halfVal}); remaining 50% due in 3 days.`,
                    timestamp: new Date().toISOString(),
                  } as RecoveryEvent,
                ],
              }
            : item
        )
      );
      showToast(`50% payment received for ${inv.customer.name}; remaining ₹${halfVal} due in 3 days`);
    }

    try {
      const res = await api.recordPayment(inv.id, {
        payment_type: paymentType,
        notes: `Operator 1-click ${paymentType} settlement`,
      });
      // Replace with authoritative server invoice
      setInvoices((prev) =>
        prev.map((item) => (item.id === inv.id ? res.invoice : item))
      );
      api.analyticsSummary().then(setAnalytics).catch(console.warn);
    } catch (e: unknown) {
      if (paymentType === "FULL") {
        setSettledInvoiceIds((prev) => {
          const next = new Set(prev);
          next.delete(inv.id);
          return next;
        });
      } else {
        setHalfSettledInvoiceIds((prev) => {
          const next = new Set(prev);
          next.delete(inv.id);
          return next;
        });
      }
      setInvoices(previousInvoices);
      showToast("Error: " + (e instanceof Error ? e.message : "Failed to record payment"));
    } finally {
      setPaymentSubmittingId(null);
    }
  };

  // Simulate Breach — immediately expire PTP deadline to place outbound recovery call
  const handleSimulateBreach = async (inv: Invoice) => {
    setSkipWaitLoading((prev) => new Set(prev).add(inv.id));
    try {
      await api.simulateTimeout(inv.id);
      showToast(`${inv.customer.name}: PTP Commitment Breached → Outbound Recovery Call Initiated`);
      if (!callQueue.some((q) => q.id === inv.id)) {
        setCallQueue((prev) => [...prev, inv]);
      }
      await loadData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Simulate breach failed";
      showToast("Error: " + msg);
    } finally {
      setSkipWaitLoading((prev) => {
        const next = new Set(prev);
        next.delete(inv.id);
        return next;
      });
    }
  };

  // Open Override Dialog
  const openOverrideDialog = (inv: Invoice, actionType: OperatorOverridePayload["override_type"]) => {
    setActiveOverrideMenuId(null);
    setOverrideType(actionType);
    setOverrideModalTarget(inv);
  };

  // Submit Operator Override
  const submitOverride = async () => {
    if (!overrideModalTarget || !overrideReason.trim()) return;
    setOverrideSubmitting(true);
    try {
      await api.operatorOverride(overrideModalTarget.id, {
        override_type: overrideType,
        reason: overrideReason.trim(),
      });
      showToast("Operator override applied for " + overrideModalTarget.customer.name);
      setOverrideModalTarget(null);
      setOverrideReason("");
      await loadData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Override failed";
      showToast("Error: " + msg);
    } finally {
      setOverrideSubmitting(false);
    }
  };

  // Export Compliance Dossier JSON
  const handleExportDossier = async (invoiceId: string, customerName: string) => {
    try {
      const data: AuditExportResponse = await api.exportAudit(invoiceId);
      const jsonStr = JSON.stringify(data, null, 2);
      const blob = new Blob([jsonStr], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `compliance_dossier_${customerName.replace(/\s+/g, "_")}_${invoiceId.slice(0, 8)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast("Exported compliance dossier for " + customerName);
    } catch (err: unknown) {
      showToast("Export failed: " + (err instanceof Error ? err.message : "Unknown error"));
    }
  };

  // Filtered Invoices
  const filteredInvoices = invoices.filter((inv) => {
    const matchesSearch =
      inv.customer.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      inv.customer.phone.includes(searchQuery) ||
      (inv.failure_reason && inv.failure_reason.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesStatus = statusFilter === "ALL" || inv.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // Compiled Chronological Activity Feed (Strictly Recent First)
  const allEvents = invoices
    .flatMap((inv) =>
      (inv.recovery_events || []).map((evt, idx) => ({
        ...evt,
        customerName: inv.customer?.name || "Customer",
        amount: inv.amount_inr,
        _orderIndex: idx,
      }))
    )
    .sort((a, b) => {
      const timeDiff = new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
      if (!isNaN(timeDiff) && timeDiff !== 0) return timeDiff;
      return b._orderIndex - a._orderIndex;
    });

  const visibleEvents = showAllActivity ? allEvents.slice(0, 20) : allEvents.slice(0, 5);

  return (
    <div className="min-h-screen bg-zinc-100 text-zinc-900 font-sans pb-16">
      {/* Toast Banner */}
      {toastMessage && (
        <div className="fixed top-4 right-4 z-50 bg-zinc-900 text-white text-xs px-3.5 py-2.5 rounded-md shadow-md border border-zinc-800 flex items-center space-x-2">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Header Bar */}
      <header className="bg-white border-b border-zinc-200 px-6 py-3.5 sticky top-0 z-30">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
          {/* Brand & Status */}
          <div className="flex items-center space-x-3">
            <h1 className="text-xl font-bold tracking-tight text-zinc-900">RecoveryAI</h1>
            <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-zinc-100 border border-zinc-200 text-xs text-zinc-700 font-medium">
              <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
              <span>Autonomous Agent Active</span>
            </div>
          </div>

          {/* Right Action Bar */}
          <div className="flex items-center space-x-2">
            <button
              onClick={async () => {
                try {
                  setSettledInvoiceIds(new Set());
                  setHalfSettledInvoiceIds(new Set());
                  setCallQueue([]);
                  setPaymentConfirmId(null);
                  setActiveOverrideMenuId(null);
                  setVoiceCallInvoice(null);
                  const res = await api.seed();
                  showToast(res.message || "Database reset & seeded fresh with 6 recovery cases");
                  await loadData(true);
                } catch (e: unknown) {
                  showToast("Error: " + (e instanceof Error ? e.message : "Error"));
                }
              }}
              className="px-2.5 py-1.5 text-xs border border-zinc-300 rounded bg-white hover:bg-zinc-50 text-zinc-700 font-medium transition-colors"
            >
              Seed Database
            </button>
            <button
              onClick={handleFastForwardAll}
              disabled={fastForwarding}
              className="px-2.5 py-1.5 text-xs border border-zinc-300 rounded bg-white hover:bg-zinc-50 text-zinc-700 font-medium transition-colors disabled:opacity-50"
            >
              {fastForwarding ? "Running..." : "Fast Forward All"}
            </button>
            <button
              onClick={async () => {
                try {
                  const res = await api.runBatchSimulation();
                  showToast(res.summary_message);
                  await loadData(true);
                } catch (e: unknown) {
                  showToast("Error: " + (e instanceof Error ? e.message : "Error"));
                }
              }}
              className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors"
            >
              Run Batch Simulation
            </button>
            <button
              onClick={() => setIsManualModalOpen(true)}
              className="px-3 py-1.5 text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-50 rounded transition-colors"
            >
              + Manual Entry
            </button>
            <button
              onClick={() => loadData(true)}
              disabled={refreshing}
              className="p-1.5 text-zinc-600 bg-white border border-zinc-300 hover:bg-zinc-50 rounded transition-colors"
              title="Refresh"
            >
              <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 pt-4 space-y-4">
        {/* Navigation Tabs */}
        <div className="flex items-center gap-1.5 border-b border-zinc-200 pb-3">
          <button
            onClick={() => setCurrentTab("operations")}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              currentTab === "operations"
                ? "bg-zinc-900 text-white"
                : "bg-white text-zinc-700 border border-zinc-200 hover:bg-zinc-50"
            }`}
          >
            Operations Console
          </button>
          <button
            onClick={() => setCurrentTab("analytics")}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              currentTab === "analytics"
                ? "bg-zinc-900 text-white"
                : "bg-white text-zinc-700 border border-zinc-200 hover:bg-zinc-50"
            }`}
          >
            Recovery Analytics
          </button>
        </div>

        {currentTab === "analytics" ? (
          <AnalyticsTab />
        ) : (
          <>
            {/* Top Grid: KPI Cards + Global Call Queue + Agent Activity */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              {/* Left Column: 4 Compact KPI Cards */}
              <div className="lg:col-span-2 grid grid-cols-2 gap-3">
                {/* Card 1: Total at Risk */}
                <div className="bg-white border border-zinc-200 rounded-md p-3.5">
                  <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                    TOTAL AT RISK
                  </div>
                  <div className="text-2xl font-bold text-zinc-900 mt-1">
                    {analytics ? fmtInr(analytics.total_at_risk_inr) : fmtInr(invoices.reduce((acc, i) => acc + parseFloat(i.amount_inr), 0))}
                  </div>
                  <div className="text-[11px] text-zinc-500 mt-1">
                    {invoices.length} active recovery cases
                  </div>
                </div>

            {/* Card 2: Total Recovered */}
            <div className="bg-white border border-zinc-200 rounded-md p-3.5">
              <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                TOTAL RECOVERED
              </div>
              <div className="text-2xl font-bold text-green-700 mt-1">
                {analytics ? fmtInr(analytics.total_recovered_inr) : "₹0"}
              </div>
              <div className="text-[11px] text-green-600 mt-1 font-medium">
                {analytics ? `${analytics.recovery_rate_pct.toFixed(1)}% recovery rate` : "0% rate"} ({analytics?.resolved_count ?? 0} resolved)
              </div>
            </div>

            {/* Card 3: Margin Preserved */}
            <div className="bg-white border border-zinc-200 rounded-md p-3.5">
              <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                MARGIN PRESERVED
              </div>
              <div className="text-2xl font-bold text-zinc-900 mt-1">
                {analytics ? fmtInr(analytics.margin_preserved_inr) : "₹0"}
              </div>
              <div className="text-[11px] text-zinc-500 mt-1">
                Retained via policy ceiling rules
              </div>
            </div>

            {/* Card 4: Global Call Queue Status Card */}
            <div className="bg-white border border-zinc-200 rounded-md p-3.5 text-xs flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase flex items-center gap-1.5">
                  <PhoneCall size={12} className="text-blue-600" />
                  GLOBAL CALL QUEUE
                </div>
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${callQueue.length > 0 ? "bg-blue-100 text-blue-800" : "bg-zinc-100 text-zinc-500"}`}>
                  {callQueue.length} {callQueue.length === 1 ? "waiting" : "waiting"}
                </span>
              </div>

              <div className="mt-2 space-y-1.5">
                {callQueue.length === 0 ? (
                  <div className="text-zinc-400 text-[11px]">0 calls waiting in queue.</div>
                ) : (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between text-blue-900 font-semibold bg-blue-50/80 px-2.5 py-1.5 rounded border border-blue-100">
                      <div className="flex items-center gap-2 truncate">
                        <span className="truncate">Active: {callQueue[0]?.customer.name}</span>
                        <span className="font-mono text-[11px] text-blue-700">{fmtInr(callQueue[0]?.amount_inr || 0)}</span>
                      </div>
                      <button
                        onClick={() => setVoiceCallInvoice(callQueue[0])}
                        className="px-2 py-0.5 text-[11px] font-bold text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors flex items-center gap-1 shrink-0 ml-2"
                      >
                        <PhoneCall size={10} /> Call
                      </button>
                    </div>
                    {callQueue.slice(1, 3).map((q, idx) => (
                      <div key={q.id} className="flex items-center justify-between text-zinc-600 px-2 text-[11px]">
                        <span>#{idx + 2} {q.customer.name}</span>
                        <button
                          onClick={() => setVoiceCallInvoice(q)}
                          className="text-[11px] font-semibold text-blue-600 hover:underline flex items-center gap-1"
                        >
                          Start Call →
                        </button>
                      </div>
                    ))}
                    {callQueue.length > 3 && (
                      <div className="text-[10px] text-zinc-400 px-2">
                        +{callQueue.length - 3} more queued
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Right Column: Global Agent Activity Chronological Feed */}
          <div className="bg-white border border-zinc-200 rounded-md p-3.5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between border-b border-zinc-200 pb-2 mb-2.5">
                <span className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                  AGENT ACTIVITY
                </span>
                <span className="text-[10px] text-zinc-400">Autonomous Timeline</span>
              </div>

              <div className="space-y-2 text-xs">
                {visibleEvents.length === 0 ? (
                  <div className="text-zinc-400 py-3 text-center">No recent activities logged.</div>
                ) : (
                  visibleEvents.map((evt) => (
                    <div key={evt.id} className="flex items-start space-x-2">
                      <span className="text-[11px] text-zinc-400 font-mono w-10 flex-none pt-0.5">
                        {fmtTime(evt.timestamp)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="font-semibold text-zinc-800 truncate">
                          {evt.customerName}
                        </div>
                        <div className="text-zinc-500 text-[11px] truncate">
                          {evt.log_message || evt.current_state}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {allEvents.length > 5 && (
              <div className="pt-2 border-t border-zinc-200 mt-2 text-right">
                <button
                  onClick={() => setShowAllActivity(!showAllActivity)}
                  className="text-xs text-blue-600 hover:underline font-medium"
                >
                  {showAllActivity ? "See less ↑" : `See more (${allEvents.length}) →`}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Case List Header & Filters */}
        <div className="bg-white border border-zinc-200 rounded-md overflow-hidden">
          {/* Controls Bar */}
          <div className="p-3.5 border-b border-zinc-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-zinc-50">
            {/* Search Input */}
            <div className="relative w-full sm:w-72">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search customer or phone..."
                className="w-full pl-8 pr-3 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600 bg-white"
              />
              <Search size={13} className="absolute left-2.5 top-2 text-zinc-400" />
            </div>

            {/* Filter Tabs */}
            <div className="flex items-center space-x-1 text-xs">
              <span className="text-zinc-500 font-medium mr-1">Status:</span>
              {["ALL", "UNPAID", "RESOLVED", "DISPUTED", "ESCALATED"].map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                    statusFilter === st
                      ? "bg-zinc-900 text-white"
                      : "bg-white text-zinc-600 border border-zinc-300 hover:bg-zinc-100"
                  }`}
                >
                  {st}
                </button>
              ))}
            </div>
          </div>

          {/* Cases List Rows */}
          {loading ? (
            <div className="p-8 text-center text-zinc-400 text-xs flex items-center justify-center space-x-2">
              <RefreshCw size={14} className="animate-spin text-zinc-500" />
              <span>Loading recovery cases...</span>
            </div>
          ) : invoices.length === 0 ? (
            <div className="p-12 text-center text-zinc-500 text-xs space-y-3 bg-zinc-50/50">
              <div className="w-10 h-10 rounded-full bg-zinc-100 border border-zinc-200 text-zinc-400 flex items-center justify-center mx-auto">
                <Bot size={20} />
              </div>
              <div className="max-w-md mx-auto space-y-1">
                <p className="font-semibold text-zinc-800 text-sm">No active dunning cases in flight</p>
                <p className="text-zinc-500 text-xs">
                  No active dunning cases in flight. Click <strong className="text-zinc-700">[Seed DB]</strong> to load representative Indian recovery scenarios or create a manual entry.
                </p>
              </div>
              <div className="flex items-center justify-center gap-2 pt-1">
                <button
                  onClick={async () => {
                    try {
                      setSettledInvoiceIds(new Set());
                      const res = await api.seed();
                      showToast(res.message || "Database seeded with 6 initial breach recovery cases");
                      await loadData(true);
                    } catch (e: unknown) {
                      showToast("Error: " + (e instanceof Error ? e.message : "Error"));
                    }
                  }}
                  className="px-3 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors"
                >
                  Seed Database
                </button>
                <button
                  onClick={() => setIsManualModalOpen(true)}
                  className="px-3 py-1.5 text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-50 rounded transition-colors"
                >
                  + Manual Entry
                </button>
              </div>
            </div>
          ) : filteredInvoices.length === 0 ? (
            <div className="p-8 text-center text-zinc-400 text-xs">
              No recovery cases found matching filter.
            </div>
          ) : (
            <div className="divide-y divide-zinc-200">
              {filteredInvoices.map((inv) => {
                const isExpanded = expandedInvoiceId === inv.id;
                const latestEvent = inv.recovery_events?.[inv.recovery_events.length - 1];
                const currentState = latestEvent?.current_state ?? "TRIGGERED";

                const statusCfg = STATUS_CFG[inv.status] || {
                  label: inv.status,
                  textClass: "text-zinc-700",
                  bgClass: "bg-zinc-100",
                };

                const discRatio = latestEvent?.discount_offered ? parseFloat(latestEvent.discount_offered) : 0;
                const discLabel = discRatio > 0 ? ` (${(discRatio * 100).toFixed(0)}%)` : "";

                const baseStateCfg = STATE_CFG[currentState] || {
                  label: currentState,
                  textClass: "text-zinc-700",
                  bgClass: "bg-zinc-100",
                };

                const stateCfg = {
                  ...baseStateCfg,
                  label: currentState.startsWith("TIER_") ? `${baseStateCfg.label}${discLabel}` : baseStateCfg.label,
                };

                const isInQueue = callQueue.some((q) => q.id === inv.id);
                const isCalling = voiceCallInvoice?.id === inv.id;
                const badge = getNextActionBadge(inv, isInQueue, now);

                const isOverrideOpen = activeOverrideMenuId === inv.id;

                return (
                  <div key={inv.id} className="transition-colors hover:bg-zinc-50/80">
                    {/* Compact Case Row */}
                    <div className="p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 text-xs">
                      {/* Customer Info Column */}
                      <div className="flex items-start space-x-3 min-w-[240px]">
                        <div className="w-8 h-8 rounded bg-zinc-200 text-zinc-700 font-bold flex items-center justify-center text-xs flex-none">
                          {inv.customer.name.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <div className="font-semibold text-zinc-900 text-sm">
                            {inv.customer.name}
                          </div>
                          <div className="text-zinc-500 text-[11px] mt-0.5 space-x-1.5">
                            <span>{inv.customer.phone}</span>
                            <span>·</span>
                            <span>LTV {fmtInr(inv.customer.ltv_inr)}</span>
                            <span>·</span>
                            <span>{inv.customer.consecutive_discount_months} mo discount history</span>
                          </div>
                          <div className="text-zinc-500 text-[11px] mt-0.5">
                            {inv.failure_reason ? inv.failure_reason.replace(/_/g, " ") : "Failure reason unspecified"}
                          </div>
                        </div>
                      </div>

                      {/* Amount Column */}
                      <div className="min-w-[100px]">
                        <div className="text-[11px] text-zinc-400">Amount at Risk</div>
                        <div className="text-sm font-bold text-zinc-900">{fmtInr(inv.amount_inr)}</div>
                      </div>

                      {/* Agent State & Countdown Column */}
                      <div className="min-w-[240px]">
                        <div className="flex items-center space-x-2">
                          <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${stateCfg.textClass} ${stateCfg.bgClass}`}>
                            {stateCfg.label}
                          </span>
                        </div>
                        {isCalling ? (
                          <div className="text-[11px] text-blue-600 font-bold mt-1">
                            📞 CALLING · Live
                          </div>
                        ) : (
                          <div className="flex items-center gap-2 mt-1 flex-wrap">
                            <span className="text-[11px] text-zinc-600 font-medium">
                              {badge.icon} {badge.subLabel}
                            </span>
                            {badge.isSkippable && !badge.isTerminal && (
                              currentState === "PTP_ACTIVE" ? (
                                <button
                                  onClick={() => handleSimulateBreach(inv)}
                                  disabled={skipWaitLoading.has(inv.id)}
                                  title="Simulate debtor breaking the promise to pay"
                                  className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-rose-700 bg-rose-50 border border-rose-200 rounded hover:bg-rose-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                >
                                  {skipWaitLoading.has(inv.id) ? (
                                    <>
                                      <span className="inline-block w-2 h-2 border border-rose-600 border-t-transparent rounded-full animate-spin" />
                                      Simulating...
                                    </>
                                  ) : (
                                    "Simulate Breach"
                                  )}
                                </button>
                              ) : (
                                <button
                                  onClick={() => handleSkipWait(inv)}
                                  disabled={skipWaitLoading.has(inv.id)}
                                  title="Skip the current wait and execute the immediate next step"
                                  className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded hover:bg-amber-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                >
                                  {skipWaitLoading.has(inv.id) ? (
                                    <>
                                      <span className="inline-block w-2 h-2 border border-amber-600 border-t-transparent rounded-full animate-spin" />
                                      Skipping...
                                    </>
                                  ) : (
                                    "Skip Wait"
                                  )}
                                </button>
                              )
                            )}
                          </div>
                        )}
                      </div>

                      {/* Actions Column: Resolve Button + Override Dropdown + Expand Chevron */}
                      <div className="flex items-center space-x-2 relative">
                        {(inv.call_pending || isInQueue || isCalling) && (
                          <button
                            onClick={() => setVoiceCallInvoice(inv)}
                            className="px-2.5 py-1 text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white rounded flex items-center gap-1 shadow-xs transition-colors"
                          >
                            <PhoneCall size={11} />
                            {isCalling ? "Inspect Call" : "Start Call"}
                          </button>
                        )}

                        {/* Payment Received Action: Red outline first, Yellow when Half is clicked, Green when Full is settled */}
                        {inv.status === "RESOLVED" || settledInvoiceIds.has(inv.id) ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-300 rounded whitespace-nowrap shadow-xs">
                            Payment Received
                          </span>
                        ) : isInvoiceHalfSettled(inv) ? (
                          <button
                            onClick={() => handlePaymentReceivedOptimistic(inv, "FULL")}
                            disabled={paymentSubmittingId === inv.id}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-amber-900 bg-amber-100 border border-amber-400 hover:bg-emerald-600 hover:text-white hover:border-emerald-600 rounded transition-colors whitespace-nowrap shadow-xs disabled:opacity-50"
                            title="50% already received. Click to record remaining 50% payment and settle in full."
                          >
                            <span>{paymentSubmittingId === inv.id ? "Saving..." : "Half Paid (Click for Full)"}</span>
                          </button>
                        ) : inv.status === "UNPAID" ? (
                          paymentConfirmId === inv.id ? (
                            <div className="flex items-center gap-1.5 animate-in fade-in zoom-in-95">
                              <button
                                onClick={() => handlePaymentReceivedOptimistic(inv, "HALF")}
                                disabled={paymentSubmittingId === inv.id}
                                className="bg-amber-500 hover:bg-amber-600 text-white font-medium px-2.5 py-1 rounded text-xs transition-colors shadow-xs flex items-center gap-1 disabled:opacity-50"
                                title="Settle 50% immediate, 50% in 3 days"
                              >
                                <span>Half (50%)</span>
                              </button>
                              <button
                                onClick={() => handlePaymentReceivedOptimistic(inv, "FULL")}
                                disabled={paymentSubmittingId === inv.id}
                                className="bg-emerald-600 hover:bg-emerald-700 text-white font-medium px-2.5 py-1 rounded text-xs transition-colors shadow-xs flex items-center gap-1 disabled:opacity-50"
                                title="Settle 100% full payment"
                              >
                                <span>Full (100%)</span>
                              </button>
                              <button
                                onClick={() => setPaymentConfirmId(null)}
                                className="p-1 text-zinc-400 hover:text-zinc-700 hover:bg-zinc-100 rounded transition-colors"
                                title="Cancel"
                              >
                                <X size={13} />
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setPaymentConfirmId(inv.id)}
                              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-300 hover:bg-rose-100 hover:border-rose-400 rounded transition-colors whitespace-nowrap"
                              title="Click to record payment"
                            >
                              <span>Payment Received</span>
                            </button>
                          )
                        ) : null}

                        {/* Single Override Dropdown */}
                        <div className="relative">
                          <button
                            onClick={() => setActiveOverrideMenuId(isOverrideOpen ? null : inv.id)}
                            className="px-2.5 py-1 text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-50 rounded flex items-center space-x-1"
                          >
                            <span>Override</span>
                            <ChevronDown size={12} />
                          </button>

                          {isOverrideOpen && (
                            <div className="absolute right-0 mt-1 w-52 bg-white border border-zinc-200 rounded shadow-lg z-40 py-1 text-xs">
                              <div className="px-3 py-1 text-[10px] font-bold text-zinc-400 uppercase border-b border-zinc-100">
                                Operator Override
                              </div>
                              <button
                                onClick={() => openOverrideDialog(inv, "MANUAL_LINK")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Send Payment Link
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "MANUAL_LINK")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Send Reminder
                              </button>
                              <button
                                onClick={() => {
                                  setActiveOverrideMenuId(null);
                                  if (!callQueue.some((q) => q.id === inv.id)) {
                                    setCallQueue((prev) => [...prev, inv]);
                                    showToast(`Voice call queued for ${inv.customer.name}`);
                                  }
                                }}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Start Call
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "SIMULATE_PTP")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Start PTP
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "FORCE_DISCOUNT")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Tier 1 Discount
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "FORCE_DISCOUNT")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Tier 2 Discount
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "FORCE_DISCOUNT")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Tier 3 Discount
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "FLAG_DISPUTE")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Freeze Dispute
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "ESCALATE_HUMAN")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Escalate to Human
                              </button>
                              <button
                                onClick={() => openOverrideDialog(inv, "MARK_SETTLED")}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
                              >
                                Resolve
                              </button>
                              <button
                                onClick={() => setActiveOverrideMenuId(null)}
                                className="w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-500 border-t border-zinc-100"
                              >
                                No Action
                              </button>
                            </div>
                          )}
                        </div>

                        {/* Expand Chevron */}
                        <button
                          onClick={() => setExpandedInvoiceId(isExpanded ? null : inv.id)}
                          className="p-1 text-zinc-400 hover:text-zinc-700 hover:bg-zinc-100 rounded"
                        >
                          <ChevronDown
                            size={16}
                            className={`transform transition-transform ${isExpanded ? "rotate-180" : ""}`}
                          />
                        </button>
                      </div>
                    </div>

                    {/* Expanded Case View */}
                    {isExpanded && (
                      <div className="px-5 py-4 bg-zinc-50 border-t border-zinc-200 text-xs space-y-4">
                        {/* Compact State Machine Horizontal Line */}
                        <div className="bg-white p-3 rounded border border-zinc-200">
                          <div className="text-[11px] font-semibold text-zinc-500 uppercase mb-2">
                            STATE MACHINE PROGRESSION
                          </div>
                          <div className="flex items-center justify-between text-xs font-medium text-zinc-600">
                            {FSM_STEPS.map((step, idx) => {
                              const { isCurrent, isCompleted } = getStepStatus(
                                idx,
                                currentState,
                                inv.recovery_events || [],
                                isCalling,
                                isInQueue,
                                inv.status
                              );
                              return (
                                <div key={step} className="flex items-center space-x-1.5">
                                  {isCompleted ? (
                                    <span className="w-4 h-4 rounded-full bg-blue-600 text-white flex items-center justify-center text-[10px]">✓</span>
                                  ) : isCurrent ? (
                                    <span className="w-4 h-4 rounded-full bg-blue-600 text-white flex items-center justify-center text-[10px] ring-2 ring-blue-300">●</span>
                                  ) : (
                                    <span className="w-4 h-4 rounded-full bg-zinc-200 text-zinc-400 flex items-center justify-center text-[10px]">○</span>
                                  )}
                                  <span className={isCurrent ? "font-bold text-blue-700" : isCompleted ? "text-zinc-800" : "text-zinc-400"}>
                                    {step}
                                  </span>
                                  {idx < FSM_STEPS.length - 1 && <span className="text-zinc-300 ml-2">→</span>}
                                </div>
                              );
                            })}
                          </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                          {/* 1. Recovery Status */}
                          <div className="bg-white p-3 rounded border border-zinc-200 space-y-1.5">
                            <div className="text-[11px] font-semibold text-zinc-500 uppercase">
                              RECOVERY STATUS
                            </div>
                            <div>Current State: <strong className="text-zinc-900">{currentState}</strong></div>
                            <div>Next Action: <span className="text-zinc-700">{badge.subLabel}</span></div>
                            <div>Agent Status: <span className="text-emerald-700 font-medium">Autonomous Loop</span></div>
                          </div>

                          {/* 2. Agent Decision */}
                          <div className="bg-white p-3 rounded border border-zinc-200 space-y-1.5">
                            <div className="text-[11px] font-semibold text-zinc-500 uppercase">
                              AGENT DECISION
                            </div>
                            <div>Root Cause: <strong className="text-zinc-900">{inv.failure_reason || "UNKNOWN"}</strong></div>
                            <div>Customer Profile: LTV {fmtInr(inv.customer.ltv_inr)}</div>
                            <div>Selected Action: Automated WhatsApp Reminder + Dynamic Voice Negotiation</div>
                          </div>

                          {/* 3. Discount History */}
                          <div className="bg-white p-3 rounded border border-zinc-200 space-y-1.5">
                            <div className="text-[11px] font-semibold text-zinc-500 uppercase">
                              DISCOUNT HISTORY
                            </div>
                            <div>Discount History: <strong>{inv.customer.consecutive_discount_months} mo previous concessions</strong></div>
                            <div>Effective Ceiling: <strong className="text-purple-700">
                              {inv.customer.consecutive_discount_months >= 3
                                ? "0% Cap (No Discount - 3+ mo Penalty)"
                                : inv.customer.consecutive_discount_months === 2
                                ? "5% Cap (50% Chronic Penalty)"
                                : inv.customer.consecutive_discount_months === 1
                                ? "8% Cap (80% Ceiling)"
                                : "10% Cap (Clean History)"}
                            </strong></div>
                            <div>Current Concession: {discLabel || "0%"}</div>
                          </div>
                        </div>

                        {/* Chronological Activity Log */}
                        <div className="bg-white p-3 rounded border border-zinc-200 space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] font-semibold text-zinc-500 uppercase">
                              ACTIVITY & AUDIT TIMELINE
                            </span>
                            <button
                              onClick={() => handleExportDossier(inv.id, inv.customer.name)}
                              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline font-medium"
                            >
                              <Download size={12} />
                              Export Compliance Dossier (.json)
                            </button>
                          </div>

                          <div className="space-y-2 max-h-48 overflow-y-auto pt-1">
                            {[...(inv.recovery_events || [])].reverse().map((evt) => (
                              <div key={evt.id} className="p-2 rounded bg-zinc-50 border border-zinc-200 flex items-start space-x-2 text-xs">
                                <span className="font-mono text-zinc-400 text-[11px] w-12 flex-none pt-0.5">
                                  {fmtTime(evt.timestamp)}
                                </span>
                                <div className="flex-1">
                                  <div className="font-semibold text-zinc-800">
                                    {evt.current_state}
                                    {parseFloat(evt.discount_offered) > 0 && ` · ${(parseFloat(evt.discount_offered) * 100).toFixed(0)}% Discount`}
                                  </div>
                                  <div className="text-zinc-600 text-[11px] mt-0.5 whitespace-pre-line">
                                    {evt.log_message || "State transition recorded."}
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
          </>
        )}
      </main>

      {/* Manual Entry Modal */}
      {isManualModalOpen && (
        <ManualEntryModal
          isOpen={isManualModalOpen}
          onClose={() => setIsManualModalOpen(false)}
          onSuccess={() => loadData(true)}
        />
      )}

      {/* Operator Override Modal Dialog */}
      {overrideModalTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-lg border border-zinc-200 shadow-xl max-w-md w-full p-5 space-y-4">
            <div>
              <h3 className="font-bold text-zinc-900 text-sm">Operator Exception Override</h3>
              <p className="text-xs text-zinc-500 mt-1">
                Applying action <strong className="text-zinc-800">{overrideType}</strong> to {overrideModalTarget.customer.name} (Invoice {overrideModalTarget.id.slice(0, 8)}).
              </p>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-medium text-zinc-700">
                Operator Rationale (Required for Audit Logging):
              </label>
              <textarea
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="E.g. Customer called support desk; confirmed willing to settle via alternate payment link."
                rows={3}
                className="w-full p-2 text-xs border border-zinc-300 rounded focus:ring-1 focus:ring-blue-600 focus:outline-none bg-white"
              />
            </div>

            <div className="flex items-center justify-end space-x-2 pt-2 border-t border-zinc-100">
              <button
                type="button"
                onClick={() => setOverrideModalTarget(null)}
                className="px-3 py-1.5 text-xs text-zinc-600 hover:bg-zinc-100 rounded"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submitOverride}
                disabled={overrideSubmitting || !overrideReason.trim()}
                className="px-3 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded disabled:opacity-50"
              >
                {overrideSubmitting ? "Applying..." : "Apply Override"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Global Call Queue Drawer (FIFO Sequential Execution) */}
      <CallQueueDrawer
        queue={callQueue}
        onRemoveFromQueue={(id: string) => setCallQueue((prev) => prev.filter((i) => i.id !== id))}
        onCallCompleted={() => loadData(true)}
      />

      {/* Outbound Voice Call Modal */}
      {voiceCallInvoice && (
        <VoiceCallModal
          invoice={voiceCallInvoice}
          isOpen={true}
          onClose={() => {
            setCallQueue((prev) => prev.filter((i) => i.id !== voiceCallInvoice.id));
            setVoiceCallInvoice(null);
            loadData(true);
          }}
          onStateUpdated={() => loadData(false)}
        />
      )}
    </div>
  );
}
