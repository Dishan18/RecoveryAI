import sys
import os
import asyncio

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tests.test_gemini_policy_boundary as t

async def main():
    print("=== STARTING RECOVERYAI GEMINI INTENT & POLICY BOUNDARY TESTS ===")
    
    print("[1/14] Testing intent classification: discount request...")
    await t.test_intent_classification_discount_request()
    
    print("[2/14] Testing intent classification: promise to pay...")
    await t.test_intent_classification_promise_to_pay()
    
    print("[3/14] Testing intent classification: dispute...")
    await t.test_intent_classification_dispute()
    
    print("[4/14] Testing intent classification: technical problem...")
    await t.test_intent_classification_technical_problem()
    
    print("[5/14] Testing intent classification: request link...")
    await t.test_intent_classification_request_link()
    
    print("[6/14] Testing intent classification: refusal...")
    await t.test_intent_classification_refusal()
    
    print("[7/14] Testing 50% request on 10% merchant cap (Policy offers Tier 1 = 5.0%)...")
    await t.test_customer_50_percent_request_on_10_percent_cap()
    
    print("[8/14] Testing 25% request on 20% merchant cap (Policy offers Tier 1 = 10.0%)...")
    await t.test_customer_25_percent_request_on_20_percent_cap()
    
    print("[9/14] Testing sequential refusals: Tier 0 -> Tier 1 -> Tier 2 -> Tier 3 -> Escalation...")
    await t.test_sequential_refusals_climb_ladder_to_escalation()
    
    print("[10/14] Testing anti-gaming policy (3+ consecutive discount months -> 0% concession)...")
    await t.test_chronic_discount_exploiter_blocked()
    
    print("[11/14] Testing payment safety (PAY_NOW does NOT mark invoice resolved without verification)...")
    await t.test_pay_now_does_not_mark_paid_without_verification()
    
    print("[12/14] Testing dispute safety (DISPUTE freezes collection & halts dunning)...")
    await t.test_dispute_freezes_collection_immediately()
    
    print("[13/14] Testing technical failure safety (TECHNICAL_PROBLEM offers ZERO concession)...")
    await t.test_technical_problem_offers_zero_discount()
    
    print("[14/14] Testing deterministic relative PTP date parsing...")
    t.test_relative_ptp_date_resolution()
    
    print("\n========================================================")
    print("SUCCESS: ALL 14 TESTS PASSED CLEANLY WITH 100% COMPLIANCE!")
    print("========================================================")

if __name__ == "__main__":
    asyncio.run(main())
