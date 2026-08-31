import sys
import os
import asyncio

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tests.test_gemini_policy_boundary as t_boundary
import tests.test_voice_conversation as t_voice

async def main():
    print("=================================================================")
    print("RECOVERYAI VOICE INTENT & DETERMINISTIC POLICY ENGINE TEST RUNNER")
    print("=================================================================\n")

    print("--- SUITE 1: 12 VOICE CONVERSATION REGRESSION TESTS ---")
    print("[1/12] Testing Test 1: 50% discount request on 10% cap...")
    await t_voice.test_1_discount_request_50_pct()

    print("[2/12] Testing Test 2: Hindi 50% discount ('मुझे फिफ्टी परसेंट डिस्काउंट चाहिए')...")
    await t_voice.test_2_hindi_fifty_percent_discount()

    print("[3/12] Testing Test 3: 'discount chahiye'...")
    await t_voice.test_3_discount_chahiye()

    print("[4/12] Testing Test 4: Sequential refusals (Tier 0 -> Tier 1 -> Tier 2 -> Tier 3 -> Escalation)...")
    await t_voice.test_4_sequential_refusals_climb_ladder()

    print("[5/12] Testing Test 5: Merchant cap 20% (Tier 1 = 10%, Tier 2 = 16%, Tier 3 = 20%)...")
    await t_voice.test_5_merchant_cap_20_percent()

    print("[6/12] Testing Test 6: Merchant cap 15% (Tier 1 = 7.5%, Tier 2 = 12%, Tier 3 = 15%)...")
    await t_voice.test_6_merchant_cap_15_percent()

    print("[7/12] Testing Test 7: Anti-gaming blocks concession (3+ consecutive discount months)...")
    await t_voice.test_7_anti_gaming_blocks_concession()

    print("[8/12] Testing Test 8: Dispute immediately halts dunning ('Invoice amount is wrong')...")
    await t_voice.test_8_dispute_halts_dunning()

    print("[9/12] Testing Test 9: Promise to pay 3 days ('I\\'ll pay in 3 days')...")
    await t_voice.test_9_promise_to_pay_3_days()

    print("[10/12] Testing Test 10: PAY_NOW sends link without resolving invoice ('I\\'ll pay now')...")
    await t_voice.test_10_pay_now_sends_link()

    print("[11/12] Testing Test 11: Technical issue gives zero discount ('UPI isn\\'t working')...")
    await t_voice.test_11_technical_problem_zero_discount()

    print("[12/12] Testing Test 12: Final refusal at Tier 3 escalates with senior financial officer speech...")
    await t_voice.test_12_final_refusal_speech_escalation()

    print("\n--- SUITE 2: POLICY BOUNDARY & INVARIANT UNIT TESTS ---")
    await t_boundary.test_intent_classification_discount_request()
    await t_boundary.test_intent_classification_promise_to_pay()
    await t_boundary.test_intent_classification_dispute()
    await t_boundary.test_intent_classification_technical_problem()
    await t_boundary.test_intent_classification_request_link()
    await t_boundary.test_intent_classification_refusal()
    await t_boundary.test_customer_50_percent_request_on_10_percent_cap()
    await t_boundary.test_customer_25_percent_request_on_20_percent_cap()
    await t_boundary.test_sequential_refusals_climb_ladder_to_escalation()
    await t_boundary.test_chronic_discount_exploiter_blocked()
    await t_boundary.test_pay_now_does_not_mark_paid_without_verification()
    await t_boundary.test_dispute_freezes_collection_immediately()
    await t_boundary.test_technical_problem_offers_zero_discount()
    t_boundary.test_relative_ptp_date_resolution()

    print("\n=================================================================")
    print("SUCCESS: ALL 26 TESTS PASSED CLEANLY WITH 100% SPEC COMPLIANCE!")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(main())
