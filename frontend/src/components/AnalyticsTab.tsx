"use client";

import React, { useEffect, useState, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api, AnalyticsOverview } from "../lib/api";

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const fmtInr = (v: number) => INR.format(v);

export function AnalyticsTab() {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchAnalytics = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      const res = await api.analyticsOverview();
      setData(res);
    } catch (err) {
      console.warn("Analytics sync error:", err);
    } finally {
      setLoading(false);
      if (isManual) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    const run = async () => {
      try {
        const res = await api.analyticsOverview();
        if (mounted) setData(res);
      } catch (err) {
        console.warn("Analytics sync error:", err);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    run();
    const interval = setInterval(run, 10000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center p-12 text-zinc-500 gap-2 bg-white border border-zinc-200 rounded-md">
        <RefreshCw size={14} className="animate-spin text-zinc-600" />
        <span className="text-xs font-medium">Loading portfolio recovery intelligence...</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-6 text-center bg-white border border-zinc-200 rounded-md">
        <p className="text-xs text-zinc-600">Analytics data currently unavailable.</p>
        <button
          onClick={() => fetchAnalytics(true)}
          className="mt-2.5 px-2.5 py-1 text-xs font-medium bg-zinc-900 text-white rounded hover:bg-zinc-800"
        >
          Retry
        </button>
      </div>
    );
  }

  const { summary, funnel, by_reason, concessions } = data;

  return (
    <div className="space-y-4">
      {/* Page Title Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 bg-white border border-zinc-200 rounded-md px-4 py-3">
        <div>
          <h2 className="text-sm font-bold text-zinc-900">Portfolio Recovery Intelligence</h2>
          <p className="text-xs text-zinc-500">
            Real-time aggregate performance across the recovery portfolio.
          </p>
        </div>
        <button
          onClick={() => fetchAnalytics(true)}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-50 rounded transition-colors disabled:opacity-50 self-start sm:self-auto"
        >
          <RefreshCw size={13} className={refreshing ? "animate-spin text-zinc-600" : "text-zinc-500"} />
          <span>{refreshing ? "Syncing..." : "Refresh Intelligence"}</span>
        </button>
      </div>

      {/* 1. KPI Cards (4 compact operational metric cards) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Card 1: Total Volume at Risk */}
        <div className="bg-white border border-zinc-200 rounded-md p-3.5 space-y-1">
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
            TOTAL VOLUME AT RISK
          </div>
          <div className="text-2xl font-bold text-zinc-900">
            {fmtInr(summary.total_at_risk)}
          </div>
          <div className="text-[11px] text-zinc-500">
            {summary.total_cases} total accounts in portfolio
          </div>
        </div>

        {/* Card 2: Gross Recovered */}
        <div className="bg-white border border-zinc-200 rounded-md p-3.5 space-y-1">
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
            GROSS RECOVERED
          </div>
          <div className="text-2xl font-bold text-green-700">
            {fmtInr(summary.gross_recovered)}
          </div>
          <div className="text-[11px] text-green-600 font-medium">
            {summary.recovery_rate}% portfolio recovery rate
          </div>
        </div>

        {/* Card 3: Net Margin Collected */}
        <div className="bg-white border border-zinc-200 rounded-md p-3.5 space-y-1">
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
            NET MARGIN COLLECTED
          </div>
          <div className="text-2xl font-bold text-zinc-900">
            {fmtInr(summary.net_collected)}
          </div>
          <div className="text-[11px] text-zinc-500">
            {fmtInr(summary.discounts_granted)} concessions granted
          </div>
        </div>

        {/* Card 4: Autonomous Settlement */}
        <div className="bg-white border border-zinc-200 rounded-md p-3.5 space-y-1">
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
            AUTONOMOUS SETTLEMENT
          </div>
          <div className="text-2xl font-bold text-zinc-900">
            {funnel[funnel.length - 1]?.count || 0} Cases
          </div>
          <div className="text-[11px] text-zinc-500">
            Resolved without human escalation
          </div>
        </div>
      </div>

      {/* 2. Middle Row: Autonomous Recovery Funnel & Category Win Rates */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Funnel Card */}
        <div className="bg-white border border-zinc-200 rounded-md p-4 space-y-3">
          <div>
            <h3 className="text-xs font-bold text-zinc-900 uppercase tracking-wider">
              Autonomous Recovery Funnel
            </h3>
            <p className="text-[11px] text-zinc-500">
              Drop-offs across autonomous stages from initial breach to resolution
            </p>
          </div>

          <div className="space-y-3 pt-1">
            {funnel.map((step, idx) => {
              const maxCount = funnel[0]?.count || 1;
              const pct = Math.round((step.count / maxCount) * 100);
              const barColor = idx === funnel.length - 1 ? "bg-green-600" : "bg-zinc-800";

              return (
                <div key={idx} className="space-y-1">
                  <div className="flex justify-between text-xs text-zinc-700">
                    <span className="font-medium text-zinc-800">
                      {idx + 1}. {step.stage}
                    </span>
                    <span className="font-semibold text-zinc-900">
                      {step.count} <span className="text-zinc-400 font-normal">({pct}%)</span>
                    </span>
                  </div>
                  <div className="w-full bg-zinc-100 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
                      style={{ width: `${Math.max(3, pct)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Category Table Card */}
        <div className="bg-white border border-zinc-200 rounded-md p-4 space-y-3">
          <div>
            <h3 className="text-xs font-bold text-zinc-900 uppercase tracking-wider">
              Win Rate by Payment Failure Category
            </h3>
            <p className="text-[11px] text-zinc-500">
              Autonomous conversion and resolution rates grouped by failure root cause
            </p>
          </div>

          <div className="overflow-x-auto pt-1">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-zinc-500 border-b border-zinc-200 pb-2">
                  <th className="py-2 text-[11px] font-semibold uppercase tracking-wider">Category</th>
                  <th className="py-2 text-center text-[11px] font-semibold uppercase tracking-wider">Accounts</th>
                  <th className="py-2 text-right text-[11px] font-semibold uppercase tracking-wider">At Risk</th>
                  <th className="py-2 text-right text-[11px] font-semibold uppercase tracking-wider">Win Rate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {by_reason.map((row, idx) => (
                  <tr key={idx} className="hover:bg-zinc-50">
                    <td className="py-2 font-medium text-zinc-800">
                      {row.reason}
                    </td>
                    <td className="py-2 text-center text-zinc-600">
                      {row.total_cases} <span className="text-zinc-400">({row.resolved_cases} res)</span>
                    </td>
                    <td className="py-2 text-right text-zinc-700 font-medium">
                      {fmtInr(row.amount_at_risk)}
                    </td>
                    <td className="py-2 text-right font-semibold text-green-700">
                      {row.recovery_rate}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 3. Bottom Row: Settlement Concession Ladder & Anti-Gaming Panel */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Concession Distribution (2 cols) */}
        <div className="lg:col-span-2 bg-white border border-zinc-200 rounded-md p-4 space-y-3">
          <div>
            <h3 className="text-xs font-bold text-zinc-900 uppercase tracking-wider">
              Settlement Concession Ladder Distribution
            </h3>
            <p className="text-[11px] text-zinc-500">
              Recovered volume grouped by discount tier (percentages are based on merchant max cap)
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-1">
            {concessions.map((tier, idx) => (
              <div key={idx} className="p-3 bg-zinc-50 border border-zinc-200 rounded space-y-1">
                <div className="text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">
                  {tier.tier}
                </div>
                <div className="text-base font-bold text-zinc-900">
                  {tier.resolved_cases} Cases
                </div>
                <div className="text-xs font-medium text-zinc-700">
                  {fmtInr(tier.volume_inr)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Anti-Gaming Margin Retention (1 col) */}
        <div className="bg-white border border-zinc-200 rounded-md p-4 space-y-2.5 flex flex-col justify-between">
          <div className="space-y-1.5">
            <div className="text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">
              ANTI-GAMING MARGIN RETENTION
            </div>
            <div className="text-2xl font-bold text-zinc-900">
              {fmtInr(summary.margin_preserved)}
            </div>
            <p className="text-xs text-zinc-600 leading-normal">
              Retained revenue saved by enforcing abuse penalty caps on repeat concession accounts.
            </p>
          </div>

          <div className="p-2.5 bg-zinc-50 border border-zinc-200 rounded text-[11px] text-zinc-700 space-y-1">
            <div className="font-semibold text-zinc-800">Policy Enforcement Rules:</div>
            <div>• 0 mo: 10% max cap (Tiers 1, 2, 3)</div>
            <div>• 1 mo: 8% cap ceiling (Tier 3 blocked)</div>
            <div>• 2 mo: 5% cap ceiling (Tiers 2, 3 blocked)</div>
            <div>• 3+ mo: 0% discount permitted (No Concession)</div>
          </div>
        </div>
      </div>
    </div>
  );
}
