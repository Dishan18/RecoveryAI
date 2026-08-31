import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""
RecoveryAI — Verification of Hybrid Analytics Overview Endpoint
===============================================================
"""

import asyncio
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.database import AsyncSessionLocal
from app.routes import get_analytics_overview
from seed import run_seed


async def test_analytics_overview():
    print("==================================================================")
    print("STARTING HYBRID RECOVERY ANALYTICS OVERVIEW VERIFICATION")
    print("==================================================================")

    # 1. Run seed to ensure 10 live invoices exist
    await run_seed()

    async with AsyncSessionLocal() as session:
        overview = await get_analytics_overview(db=session)

        print("\n--- Executive Summary KPIs ---")
        print(f"Total Cases: {overview.summary.total_cases}")
        print(f"Total Portfolio at Risk: ₹{overview.summary.total_at_risk:,.2f}")
        print(f"Gross Recovered: ₹{overview.summary.gross_recovered:,.2f}")
        print(f"Net Margin Collected: ₹{overview.summary.net_collected:,.2f}")
        print(f"Discounts Granted: ₹{overview.summary.discounts_granted:,.2f}")
        print(f"Recovery Rate: {overview.summary.recovery_rate}%")
        print(f"Margin Preserved: ₹{overview.summary.margin_preserved:,.2f}")

        # Assertions
        assert overview.summary.total_cases >= 126, f"Expected total cases >= 126 (120 baseline + 6 live), got {overview.summary.total_cases}"
        assert overview.summary.total_at_risk >= 1480000.0
        assert overview.summary.gross_recovered >= 1065600.0

        print("\n--- Funnel Progression ---")
        for step in overview.funnel:
            print(f"  {step.stage}: {step.count} cases")
        assert len(overview.funnel) == 5

        print("\n--- Win Rate by Failure Category ---")
        for r in overview.by_reason:
            print(f"  {r.reason}: {r.resolved_cases}/{r.total_cases} ({r.recovery_rate}%) | ₹{r.amount_at_risk:,.2f} at risk")
        assert len(overview.by_reason) == 5

        print("\n--- Concession Ladder Distribution ---")
        for c in overview.concessions:
            print(f"  {c.tier}: {c.resolved_cases} cases | ₹{c.volume_inr:,.2f} volume")
        assert len(overview.concessions) == 4

        print("\n  [PASS] Hybrid Analytics Overview endpoint verified successfully!")

    print("\n==================================================================")
    print(">>> HYBRID ANALYTICS OVERVIEW VERIFIED CLEANLY! <<<")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(test_analytics_overview())
