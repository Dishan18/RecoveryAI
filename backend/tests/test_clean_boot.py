import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import httpx
from main import app, lifespan

async def test_clean_boot_and_analytics():
    print("Testing lifespan clean boot...")
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Invoices on startup
            r = await client.get("/api/invoices")
            assert r.status_code == 200
            invs = r.json()
            print(f"1. Invoices on clean boot: {len(invs)} (Expected 0)")
            assert len(invs) == 0

            # 2. Analytics on clean boot (should return 120 baseline cases)
            r_an = await client.get("/api/analytics/overview")
            assert r_an.status_code == 200
            an = r_an.json()
            print(f"2. Analytics on clean boot: {an['summary']['total_cases']} cases, Rs.{an['summary']['total_at_risk']:,.2f} at risk, {an['summary']['recovery_rate']}% recovery rate")
            assert an['summary']['total_cases'] == 120
            assert len(an['funnel']) == 5
            assert len(an['by_reason']) == 5
            assert len(an['concessions']) == 4

            # 3. Seed database
            r_seed = await client.post("/api/seed")
            assert r_seed.status_code == 200
            print("3. Seeded database successfully.")

            # 4. Check scaled analytics (120 baseline + 6 live = 126)
            r_an_after = await client.get("/api/analytics/overview")
            an_after = r_an_after.json()
            print(f"4. Analytics after seed: {an_after['summary']['total_cases']} cases (Expected 126)")
            assert an_after['summary']['total_cases'] == 126

    print("\n>>> ALL CLEAN BOOT & SYNTHETIC ANALYTICS VERIFICATIONS PASSED! <<<")

if __name__ == "__main__":
    asyncio.run(test_clean_boot_and_analytics())
