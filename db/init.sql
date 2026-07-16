-- Every processed invoice lands here, whether auto-approved or flagged.
-- This table IS the audit trail — flags and reasons are stored per row,
-- so "why did this get flagged three weeks ago" is always answerable
-- without digging through logs.
CREATE TABLE IF NOT EXISTS invoices (
    id              SERIAL PRIMARY KEY,
    source_filename TEXT,
    vendor_name     TEXT,
    invoice_number  TEXT,
    issue_date      TEXT,
    total           NUMERIC,
    outcome         TEXT NOT NULL,          -- 'auto_approve' | 'flag_for_review'
    flags           JSONB,                  -- e.g. ["unapproved_vendor", "total_mismatch"]
    reasons         JSONB,                  -- flag_name -> human-readable explanation
    extracted_data  JSONB,                  -- full extraction result, for audit/debugging
    review_status   TEXT DEFAULT 'pending', -- for flagged ones: pending | approved | rejected
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoices_outcome ON invoices(outcome);
CREATE INDEX IF NOT EXISTS idx_invoices_review_status ON invoices(review_status);

-- Anything that fails at the extraction or validation stage lands here
-- instead of vanishing — same dead-letter pattern as Project 1.
CREATE TABLE IF NOT EXISTS invoice_dead_letter (
    id              SERIAL PRIMARY KEY,
    source_filename TEXT,
    stage           TEXT,      -- 'extraction' | 'validation'
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
