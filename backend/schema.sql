"""
RecoveryAI — SQL Schema (Supabase-ready)
Run this in Supabase SQL Editor to create tables from scratch.
SQLAlchemy ORM in models.py mirrors this exactly.
"""

-- Enable UUID support
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─────────────────────────────────────────────────────────────────────────────
-- merchants
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS merchants (
    id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  VARCHAR(255)  NOT NULL,
    default_discount_cap  NUMERIC(5,4)  NOT NULL DEFAULT 0.1000,  -- e.g. 0.1000 = 10%
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- customers
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customers (
    id                           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id                  UUID          NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    name                         VARCHAR(255)  NOT NULL,
    phone                        VARCHAR(20)   NOT NULL,          -- Indian format: +919876543210
    email                        VARCHAR(255),
    ltv_inr                      NUMERIC(12,2) NOT NULL DEFAULT 0.00,  -- Lifetime value in ₹
    consecutive_discount_months  INT           NOT NULL DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────────────────────
-- invoices
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id           UUID          NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    merchant_id           UUID          NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    amount_inr            NUMERIC(12,2) NOT NULL,          -- Amount due in ₹
    status                VARCHAR(50)   NOT NULL DEFAULT 'UNPAID',
    -- status values: UNPAID | RESOLVED | DISPUTED | ESCALATED
    failure_reason        VARCHAR(100),
    -- failure_reason values:
    --   GATEWAY_TIMEOUT | INSUFFICIENT_FUNDS | MANDATE_DECLINE | EXPIRED_CARD | DISPUTED_AMOUNT
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    due_date              TIMESTAMPTZ,
    next_action_due_at    TIMESTAMPTZ,                     -- Server truth for autonomous scheduling
    call_pending          BOOLEAN       NOT NULL DEFAULT FALSE
);

-- ─────────────────────────────────────────────────────────────────────────────
-- recovery_events
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recovery_events (
    id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id        UUID          NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    current_state     VARCHAR(50)   NOT NULL,
    -- state values:
    --   TRIGGERED | DIAGNOSED | LINK_SENT | PTP_ACTIVE
    --   TIER_1_DISCOUNT | TIER_2_DISCOUNT | TIER_3_FLOOR
    --   RESOLVED | FROZEN_DISPUTE | ESCALATED_HUMAN
    discount_offered  NUMERIC(5,4)  NOT NULL DEFAULT 0.0000,  -- e.g. 0.0500 = 5%
    ptp_deadline      TIMESTAMPTZ,                             -- nullable; set during PTP state
    log_message       TEXT,
    timestamp         TIMESTAMPTZ   NOT NULL DEFAULT now()
);
