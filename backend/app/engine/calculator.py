"""
RecoveryAI — Dynamic INR Concession Ladder & Anti-Gaming Calculator
====================================================================

Rules
-----
Clean history (consecutive_discount_months == 0):
  Tier 1 → 50%  of merchant cap  (e.g. 5.0% on a 10% cap)
  Tier 2 → 80%  of merchant cap  (e.g. 8.0% on a 10% cap)
  Tier 3 → 100% of merchant cap  (e.g. 10.0% on a 10% cap)

1 consecutive month of discounts:
  Effective cap clamped to 80% of threshold.
  Tier 3 is BLOCKED → ceiling is Tier 2 max.

2+ consecutive months (chronic exploiter):
  Effective cap clamped to 50% of threshold.
  Tiers 2 and 3 are BLOCKED → ceiling is Tier 1 max.

All monetary outputs are in INR (₹).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


# ── Anti-gaming multipliers ───────────────────────────────────────────────────
# consecutive_discount_months -> effective cap multiplier
CONSEC_CAP_MULTIPLIER: dict[int, Decimal] = {
    0: Decimal("1.00"),   # Full cap (10%)
    1: Decimal("0.80"),   # 80% of cap (8%)
    2: Decimal("0.50"),   # 50% of cap (5%)
}
ZERO_DISCOUNT_THRESHOLD = 3   # 3+ consecutive months (>2 mo) → 0% cap (NO discount)


# ── Tier discount ratios as fraction of effective cap ─────────────────────────
TIER_RATIOS: dict[int, Decimal] = {
    1: Decimal("0.50"),   # 50% of effective cap
    2: Decimal("0.80"),   # 80% of effective cap
    3: Decimal("1.00"),   # 100% of effective cap
}

# ── Tier state names (for audit strings) ─────────────────────────────────────
TIER_STATE_NAMES = {1: "TIER_1_DISCOUNT", 2: "TIER_2_DISCOUNT", 3: "TIER_3_FLOOR"}


@dataclass(frozen=True)
class DiscountResult:
    """Immutable output of the calculator — everything needed for UI and audit."""

    tier: int
    consecutive_months: int

    # The merchant's raw configured cap (e.g. 0.10 for 10%)
    merchant_cap: Decimal
    # After anti-gaming penalty (may be lower than merchant_cap)
    effective_cap: Decimal
    # The discount actually offered at this tier
    discount_rate: Decimal

    # Gross invoice amount in ₹
    gross_amount_inr: Decimal
    # Discount amount deducted in ₹
    discount_amount_inr: Decimal
    # What the customer pays in ₹
    net_payable_inr: Decimal

    # Whether this tier was accessible (False if blocked by penalty)
    is_accessible: bool

    # Human-readable audit trail
    audit_reason: str

    @property
    def discount_pct(self) -> str:
        """Human-readable discount percentage string."""
        return f"{self.discount_rate * 100:.2f}%"

    @property
    def effective_cap_pct(self) -> str:
        return f"{self.effective_cap * 100:.2f}%"

    @property
    def merchant_cap_pct(self) -> str:
        return f"{self.merchant_cap * 100:.2f}%"


class DiscountCalculator:
    """
    Stateless calculator.  Call ``calculate`` for a specific tier.
    """

    @staticmethod
    def effective_cap(
        merchant_cap: Decimal,
        consecutive_discount_months: int,
    ) -> Decimal:
        """Return the penalised effective cap based on abuse history."""
        if consecutive_discount_months >= ZERO_DISCOUNT_THRESHOLD:
            return Decimal("0.00")
        multiplier = CONSEC_CAP_MULTIPLIER.get(
            consecutive_discount_months,
            Decimal("0.50") if consecutive_discount_months >= 2 else Decimal("1.00"),
        )
        return (merchant_cap * multiplier).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def tier_ceiling(consecutive_discount_months: int) -> int:
        """
        Return the highest tier the customer is eligible for.
          0 months  → Tier 3 (no restriction)
          1 month   → Tier 2 max
          2 months  → Tier 1 max
          3+ months → Tier 0 (NO discount allowed)
        """
        if consecutive_discount_months >= ZERO_DISCOUNT_THRESHOLD:
            return 0
        if consecutive_discount_months == 2:
            return 1
        if consecutive_discount_months == 1:
            return 2
        return 3

    def calculate(
        self,
        merchant_cap: Decimal | float,
        consecutive_discount_months: int,
        tier: int,
        gross_amount_inr: Decimal | float,
    ) -> DiscountResult:
        """
        Core calculation method.

        Parameters
        ----------
        merchant_cap : Decimal | float
            Raw merchant-configured discount cap, e.g. 0.10 for 10%.
        consecutive_discount_months : int
            Number of consecutive months this customer has received a discount.
        tier : int
            Requested discount tier (1, 2, or 3).
        gross_amount_inr : Decimal | float
            Invoice amount before any discount.

        Returns
        -------
        DiscountResult
        """
        merchant_cap = Decimal(str(merchant_cap))
        gross_amount_inr = Decimal(str(gross_amount_inr))

        if tier not in TIER_RATIOS:
            raise ValueError(f"Invalid tier {tier!r}. Must be 1, 2, or 3.")

        # ── Effective cap (post-penalty) ──────────────────────────────────────
        eff_cap = self.effective_cap(merchant_cap, consecutive_discount_months)
        max_tier = self.tier_ceiling(consecutive_discount_months)
        is_accessible = tier <= max_tier

        # ── Build audit reason ────────────────────────────────────────────────
        penalty_label = _penalty_label(consecutive_discount_months)
        tier_name = TIER_STATE_NAMES[tier]

        if not is_accessible:
            # Blocked tier — return zero discount with explanation
            audit = (
                f"⛔ Tier {tier} BLOCKED. "
                f"Customer has {consecutive_discount_months} consecutive discount months "
                f"({penalty_label}). Effective cap = {eff_cap*100:.2f}% "
                f"(max eligible tier = Tier {max_tier}). "
                f"Merchant cap = {merchant_cap*100:.2f}%."
            )
            return DiscountResult(
                tier=tier,
                consecutive_months=consecutive_discount_months,
                merchant_cap=merchant_cap,
                effective_cap=eff_cap,
                discount_rate=Decimal("0"),
                gross_amount_inr=gross_amount_inr,
                discount_amount_inr=Decimal("0"),
                net_payable_inr=gross_amount_inr,
                is_accessible=False,
                audit_reason=audit,
            )

        # ── Compute discount ──────────────────────────────────────────────────
        tier_ratio = TIER_RATIOS[tier]
        discount_rate = (merchant_cap * tier_ratio).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        discount_amount = (gross_amount_inr * discount_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net_payable = gross_amount_inr - discount_amount

        audit = (
            f"✅ {tier_name} approved. "
            f"Merchant cap = {merchant_cap*100:.2f}% | "
            f"Consecutive discount months = {consecutive_discount_months} ({penalty_label}) | "
            f"Effective ceiling = {eff_cap*100:.2f}% | "
            f"Tier ratio = {tier_ratio*100:.0f}% of merchant cap | "
            f"Discount rate = {discount_rate*100:.2f}% | "
            f"Gross = ₹{gross_amount_inr:,.2f} | "
            f"Discount deducted = ₹{discount_amount:,.2f} | "
            f"Net payable = ₹{net_payable:,.2f}."
        )

        return DiscountResult(
            tier=tier,
            consecutive_months=consecutive_discount_months,
            merchant_cap=merchant_cap,
            effective_cap=eff_cap,
            discount_rate=discount_rate,
            gross_amount_inr=gross_amount_inr,
            discount_amount_inr=discount_amount,
            net_payable_inr=net_payable,
            is_accessible=True,
            audit_reason=audit,
        )

    def preview_all_tiers(
        self,
        merchant_cap: Decimal | float,
        consecutive_discount_months: int,
        gross_amount_inr: Decimal | float,
    ) -> list[DiscountResult]:
        """Compute all three tiers for preview/UI display."""
        return [
            self.calculate(merchant_cap, consecutive_discount_months, t, gross_amount_inr)
            for t in [1, 2, 3]
        ]


# ── Private helpers ───────────────────────────────────────────────────────────
def _penalty_label(consecutive_months: int) -> str:
    if consecutive_months >= ZERO_DISCOUNT_THRESHOLD:
        return "EXCESSIVE EXPLOITER (3+ mo) — 0% discount permitted"
    if consecutive_months == 2:
        return "CHRONIC EXPLOITER (2 mo) — 50% cap penalty"
    if consecutive_months == 1:
        return "REPEAT DISCOUNTER (1 mo) — 80% cap penalty"
    return "CLEAN HISTORY — no penalty"


# ── Singleton instance for import convenience ─────────────────────────────────
calculator = DiscountCalculator()
