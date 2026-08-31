"use client";

import React, { useState } from "react";
import { api, ManualInvoiceCreatePayload } from "../lib/api";

interface ManualEntryModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function ManualEntryModal({ isOpen, onClose, onSuccess }: ManualEntryModalProps) {
  const [formData, setFormData] = useState<ManualInvoiceCreatePayload>({
    customer_name: "",
    phone: "+91",
    amount_inr: 15000,
    failure_reason: "GATEWAY_TIMEOUT",
    ltv_inr: 50000,
    consecutive_discount_months: 0,
    merchant_name: "TechCorp B2B India",
    merchant_cap: 0.1,
  });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const applyPreset = (preset: "HDFC" | "MANDATE" | "CHRONIC") => {
    if (preset === "HDFC") {
      setFormData({
        customer_name: "Rohan Varma",
        phone: "+919812345678",
        amount_inr: 24000,
        failure_reason: "GATEWAY_TIMEOUT",
        ltv_inr: 120000,
        consecutive_discount_months: 0,
        merchant_name: "TechCorp B2B India",
        merchant_cap: 0.1,
      });
    } else if (preset === "MANDATE") {
      setFormData({
        customer_name: "Pooja Sharma",
        phone: "+919876543211",
        amount_inr: 16500,
        failure_reason: "MANDATE_DECLINE",
        ltv_inr: 85000,
        consecutive_discount_months: 1,
        merchant_name: "TechCorp B2B India",
        merchant_cap: 0.1,
      });
    } else if (preset === "CHRONIC") {
      setFormData({
        customer_name: "Vikram Malhotra",
        phone: "+919888877766",
        amount_inr: 12000,
        failure_reason: "INSUFFICIENT_FUNDS",
        ltv_inr: 45000,
        consecutive_discount_months: 2,
        merchant_name: "TechCorp B2B India",
        merchant_cap: 0.1,
      });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.customer_name.trim()) {
      setError("Customer name is required.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.createInvoice(formData);
      onSuccess();
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to create manual entry.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
      <div className="bg-white rounded-lg border border-zinc-200 shadow-lg w-full max-w-lg overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-5 py-4 border-b border-zinc-200 flex items-center justify-between bg-zinc-50">
          <div>
            <h3 className="text-sm font-semibold text-zinc-900">+ Manual Case Ingestion</h3>
            <p className="text-xs text-zinc-500 mt-0.5">
              Add a new payment failure directly into the autonomous recovery workflow
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-zinc-600 rounded hover:bg-zinc-100 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-5 overflow-y-auto space-y-4 flex-1 text-xs">
          {error && (
            <div className="p-2.5 text-xs bg-red-50 text-red-700 rounded border border-red-200">
              {error}
            </div>
          )}

          {/* Presets */}
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">Quick Presets</label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => applyPreset("HDFC")}
                className="px-2.5 py-1 text-xs font-medium bg-zinc-100 text-zinc-700 hover:bg-zinc-200 rounded border border-zinc-200 transition-colors"
              >
                HDFC Timeout (₹24,000)
              </button>
              <button
                type="button"
                onClick={() => applyPreset("MANDATE")}
                className="px-2.5 py-1 text-xs font-medium bg-zinc-100 text-zinc-700 hover:bg-zinc-200 rounded border border-zinc-200 transition-colors"
              >
                Mandate Bounce (₹16,500)
              </button>
              <button
                type="button"
                onClick={() => applyPreset("CHRONIC")}
                className="px-2.5 py-1 text-xs font-medium bg-zinc-100 text-zinc-700 hover:bg-zinc-200 rounded border border-zinc-200 transition-colors"
              >
                Chronic Discounter (₹12,000)
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Customer Name *</label>
              <input
                type="text"
                value={formData.customer_name}
                onChange={(e) => setFormData({ ...formData, customer_name: e.target.value })}
                placeholder="e.g. Vikram Malhotra"
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Phone (+91) *</label>
              <input
                type="text"
                value={formData.phone}
                onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                placeholder="+919876543210"
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600"
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Amount at Risk (₹) *</label>
              <input
                type="number"
                value={formData.amount_inr}
                onChange={(e) => setFormData({ ...formData, amount_inr: parseFloat(e.target.value) || 0 })}
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Failure Reason *</label>
              <select
                value={formData.failure_reason}
                onChange={(e) => setFormData({ ...formData, failure_reason: e.target.value })}
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600 bg-white"
              >
                <option value="GATEWAY_TIMEOUT">GATEWAY_TIMEOUT</option>
                <option value="MANDATE_DECLINE">MANDATE_DECLINE</option>
                <option value="INSUFFICIENT_FUNDS">INSUFFICIENT_FUNDS</option>
                <option value="EXPIRED_CARD">EXPIRED_CARD</option>
                <option value="DISPUTED_AMOUNT">DISPUTED_AMOUNT</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Customer LTV (₹)</label>
              <input
                type="number"
                value={formData.ltv_inr}
                onChange={(e) => setFormData({ ...formData, ltv_inr: parseFloat(e.target.value) || 0 })}
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-700 mb-1">Discount History</label>
              <select
                value={formData.consecutive_discount_months}
                onChange={(e) => setFormData({ ...formData, consecutive_discount_months: parseInt(e.target.value, 10) })}
                className="w-full px-2.5 py-1.5 text-xs border border-zinc-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-600 bg-white"
              >
                <option value={0}>0 months (Clean history)</option>
                <option value={1}>1 month (80% cap ceiling)</option>
                <option value={2}>2+ months (Chronic - 50% cap ceiling)</option>
              </select>
            </div>
          </div>

          {/* Footer buttons */}
          <div className="pt-3 border-t border-zinc-200 flex items-center justify-end space-x-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium text-zinc-600 hover:text-zinc-900 rounded hover:bg-zinc-100 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-1.5 text-xs font-medium bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {loading ? "Ingesting Case..." : "Inject Case"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
