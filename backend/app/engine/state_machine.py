"""
RecoveryAI — Core Deterministic State Machine
==============================================

All transitions are:
  - DETERMINISTIC  : same inputs always yield same output
  - IMMUTABLE      : past events are never modified; a new RecoveryEvent row is appended
  - AUDITABLE      : every transition creates a timestamped log row in `recovery_events`

Valid FSM Graph
---------------
TRIGGERED         -> REMINDER_SENT             (autonomous: diagnosis + reminder, 10m timer)
REMINDER_SENT     -> PTP_ACTIVE                (during call: customer agrees to pay)
REMINDER_SENT     -> TIER_1_DISCOUNT           (during call: customer refuses)
REMINDER_SENT     -> FROZEN_DISPUTE            (during call: customer disputes amount)
REMINDER_SENT     -> RESOLVED                  (during call: direct payment)
PTP_ACTIVE        -> RESOLVED                  (payment confirmed)
PTP_ACTIVE        -> LINK_SENT                 (payment link generated after PTP intent)
PTP_ACTIVE        -> TIER_1_DISCOUNT           (PTP deadline breached)
LINK_SENT         -> RESOLVED                  (payment confirmed via link)
LINK_SENT         -> TIER_1_DISCOUNT           (link unaccepted, timer expired)
TIER_1_DISCOUNT   -> RESOLVED | TIER_2_DISCOUNT | PTP_ACTIVE
TIER_2_DISCOUNT   -> RESOLVED | TIER_3_FLOOR   | PTP_ACTIVE
TIER_3_FLOOR      -> RESOLVED | ESCALATED_HUMAN
<ANY>             -> FROZEN_DISPUTE             (dispute flag — global escape)
<ANY>             -> RESOLVED                   (direct payment webhook — global escape)

Terminal states: RESOLVED, ESCALATED_HUMAN, FROZEN_DISPUTE

NOTE: DIAGNOSED is kept as a valid log state for backward compatibility but is no longer
      a waiting state. The scheduler transitions TRIGGERED → REMINDER_SENT in one atomic
      step, logging the diagnosis result inside the RecoveryEvent message.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models import Invoice, RecoveryEvent

logger = logging.getLogger(__name__)


# ── State constants ───────────────────────────────────────────────────────────
class State:
    TRIGGERED                 = "TRIGGERED"
    DIAGNOSED                 = "DIAGNOSED"                 # internal/legacy — logged inside REMINDER_SENT step
    REMINDER_SENT             = "REMINDER_SENT"             # autonomous reminder dispatched; 10m call timer starts
    SPLIT_OFFERED             = "SPLIT_OFFERED"             # 50% now + 50% in 3 days offered upon initial refusal
    SPLIT_FIRST_HALF_PENDING  = "SPLIT_FIRST_HALF_PENDING"  # Debtor accepted split; 1-hour countdown for 1st 50%
    LINK_SENT                 = "LINK_SENT"                 # payment link sent AFTER customer PTP intent in call
    PTP_ACTIVE                = "PTP_ACTIVE"                # customer promised to pay; awaiting confirmation
    TIER_1_DISCOUNT           = "TIER_1_DISCOUNT"
    TIER_2_DISCOUNT           = "TIER_2_DISCOUNT"
    TIER_3_FLOOR              = "TIER_3_FLOOR"
    RESOLVED                  = "RESOLVED"
    FROZEN_DISPUTE            = "FROZEN_DISPUTE"
    ESCALATED_HUMAN           = "ESCALATED_HUMAN"

    # States that can still accept action transitions
    ACTIVE_STATES = {
        TRIGGERED, DIAGNOSED, REMINDER_SENT, SPLIT_OFFERED, SPLIT_FIRST_HALF_PENDING,
        LINK_SENT, PTP_ACTIVE, TIER_1_DISCOUNT, TIER_2_DISCOUNT, TIER_3_FLOOR,
    }

    # Terminal / locked states
    TERMINAL_STATES = {RESOLVED, FROZEN_DISPUTE, ESCALATED_HUMAN}

    # Discount tier ordering
    DISCOUNT_TIERS = [TIER_1_DISCOUNT, TIER_2_DISCOUNT, TIER_3_FLOOR]


# ── Transition table ─────────────────────────────────────────────────────────
#   Maps: current_state -> frozenset of allowed next states
#   Global overrides (FROZEN_DISPUTE, RESOLVED) are handled separately.

TRANSITION_MAP: dict[str, frozenset[str]] = {
    State.TRIGGERED: frozenset({
        State.REMINDER_SENT,
        State.DIAGNOSED,
        State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.PTP_ACTIVE,
        State.TIER_1_DISCOUNT,
        State.LINK_SENT,
        State.ESCALATED_HUMAN,
    }),
    State.DIAGNOSED: frozenset({
        State.REMINDER_SENT,
        State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.LINK_SENT,
        State.TIER_1_DISCOUNT,
        State.PTP_ACTIVE,
        State.ESCALATED_HUMAN,
    }),
    State.REMINDER_SENT: frozenset({
        State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.PTP_ACTIVE,
        State.LINK_SENT,
        State.TIER_1_DISCOUNT,
        State.ESCALATED_HUMAN,
    }),
    State.SPLIT_OFFERED: frozenset({
        State.RESOLVED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.PTP_ACTIVE,
        State.LINK_SENT,
        State.TIER_1_DISCOUNT,
        State.ESCALATED_HUMAN,
        State.FROZEN_DISPUTE,
    }),
    State.SPLIT_FIRST_HALF_PENDING: frozenset({
        State.RESOLVED,
        State.PTP_ACTIVE,
        State.LINK_SENT,
        State.ESCALATED_HUMAN,
        State.FROZEN_DISPUTE,
        State.TIER_1_DISCOUNT,
    }),
    State.LINK_SENT: frozenset({
        State.RESOLVED,
        State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.TIER_1_DISCOUNT,
        State.PTP_ACTIVE,
        State.ESCALATED_HUMAN,
    }),
    State.PTP_ACTIVE: frozenset({
        State.RESOLVED,
        State.SPLIT_OFFERED,
        State.SPLIT_FIRST_HALF_PENDING,
        State.LINK_SENT,
        State.TIER_1_DISCOUNT,
        State.ESCALATED_HUMAN,
    }),
    State.TIER_1_DISCOUNT: frozenset({
        State.RESOLVED,
        State.TIER_2_DISCOUNT,
        State.PTP_ACTIVE,
        State.ESCALATED_HUMAN,
        State.LINK_SENT,
        State.SPLIT_OFFERED,
    }),
    State.TIER_2_DISCOUNT: frozenset({
        State.RESOLVED,
        State.TIER_3_FLOOR,
        State.PTP_ACTIVE,
        State.ESCALATED_HUMAN,
        State.LINK_SENT,
    }),
    State.TIER_3_FLOOR: frozenset({
        State.RESOLVED,
        State.ESCALATED_HUMAN,
        State.PTP_ACTIVE,
        State.LINK_SENT,
    }),
    # Terminal states — no valid onward transitions
    State.RESOLVED:        frozenset(),
    State.FROZEN_DISPUTE:  frozenset(),
    State.ESCALATED_HUMAN: frozenset(),
}


# ── Custom exceptions ─────────────────────────────────────────────────────────
class InvalidTransitionError(ValueError):
    """Raised when a state transition is not in the allowed graph."""

class TerminalStateError(ValueError):
    """Raised when attempting to transition out of a terminal state."""


# ── StateMachine ─────────────────────────────────────────────────────────────
class StateMachine:
    """
    Encapsulates all recovery FSM logic.

    Usage:
        sm = StateMachine(invoice, db_session)
        event = await sm.transition(
            target_state=State.REMINDER_SENT,
            log_message="Reminder dispatched via WhatsApp.",
        )
    """

    def __init__(self, invoice: "Invoice", session: AsyncSession) -> None:
        self.invoice = invoice
        self.session = session

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_state(self) -> str:
        """Latest state from the most recent recovery event."""
        if not self.invoice.recovery_events:
            return State.TRIGGERED
        events = sorted(self.invoice.recovery_events, key=lambda e: e.timestamp)
        return events[-1].current_state

    def can_transition(self, target: str) -> bool:
        """Return True if the transition from current_state -> target is valid."""
        current = self.current_state
        if current in State.TERMINAL_STATES:
            return False
        # Global escapes — any active state can go to FROZEN_DISPUTE or RESOLVED
        if target in {State.FROZEN_DISPUTE, State.RESOLVED}:
            return current in State.ACTIVE_STATES
        return target in TRANSITION_MAP.get(current, frozenset())

    async def transition(
        self,
        target_state: str,
        discount_offered: float = 0.0,
        ptp_deadline: datetime | None = None,
        log_message: str | None = None,
    ) -> "RecoveryEvent":
        """
        Validate and execute a state transition.  Creates a new RecoveryEvent
        row and updates the invoice status.  Raises on invalid transitions.
        """
        from app.models import RecoveryEvent  # avoid circular at module level

        current = self.current_state

        # ── Guard: terminal state ─────────────────────────────────────────────
        if current in State.TERMINAL_STATES:
            raise TerminalStateError(
                f"Invoice {self.invoice.id} is in terminal state '{current}'. "
                "No further transitions are allowed."
            )

        # ── Guard: valid graph edge ──────────────────────────────────────────
        allowed = TRANSITION_MAP.get(current, frozenset())
        is_global_escape = target_state in {State.FROZEN_DISPUTE, State.RESOLVED}
        if not is_global_escape and target_state not in allowed:
            raise InvalidTransitionError(
                f"Transition '{current}' -> '{target_state}' is not defined. "
                f"Allowed targets from '{current}': {sorted(allowed) or '[]'}"
            )

        # ── Build log message ─────────────────────────────────────────────────
        auto_log = (
            f"[FSM] {current} → {target_state}"
            + (f" | discount={discount_offered*100:.2f}%" if discount_offered else "")
            + (f" | ptp_deadline={ptp_deadline.isoformat()}" if ptp_deadline else "")
        )
        combined_log = f"{auto_log}\n{log_message}" if log_message else auto_log

        # ── Create RecoveryEvent ──────────────────────────────────────────────
        event = RecoveryEvent(
            id=uuid.uuid4(),
            invoice_id=self.invoice.id,
            current_state=target_state,
            discount_offered=discount_offered,
            ptp_deadline=ptp_deadline,
            log_message=combined_log,
            timestamp=datetime.now(timezone.utc),
        )
        self.session.add(event)

        # Append to in-memory list so subsequent calls read the new state
        self.invoice.recovery_events.append(event)

        # ── Update invoice status ─────────────────────────────────────────────
        self._sync_invoice_status(target_state)

        logger.info(
            "Invoice %s  %s → %s  (discount=%.4f)",
            self.invoice.id,
            current,
            target_state,
            discount_offered,
        )
        return event

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sync_invoice_status(self, new_state: str) -> None:
        """Keep invoice.status in sync with the FSM state."""
        if new_state == State.RESOLVED:
            self.invoice.status = "RESOLVED"
        elif new_state == State.FROZEN_DISPUTE:
            self.invoice.status = "DISPUTED"
        elif new_state == State.ESCALATED_HUMAN:
            self.invoice.status = "ESCALATED"
        else:
            self.invoice.status = "UNPAID"

    # ── Convenience factory ───────────────────────────────────────────────────

    @classmethod
    def next_discount_tier(cls, current_state: str) -> str | None:
        """
        Return the next discount tier state, or None if no escalation path exists.
        """
        try:
            idx = State.DISCOUNT_TIERS.index(current_state)
            if idx + 1 < len(State.DISCOUNT_TIERS):
                return State.DISCOUNT_TIERS[idx + 1]
            return None  # already at TIER_3_FLOOR — escalate to human
        except ValueError:
            # Not a discount tier
            if current_state in {State.PTP_ACTIVE, State.LINK_SENT, State.REMINDER_SENT}:
                return State.TIER_1_DISCOUNT
            return None
