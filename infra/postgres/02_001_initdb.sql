-- 02-invoice-processing/sql/02_001_init.sql
-- Business tables for scenario 02 (invoice processing). Safe to run more than once.
-- Run as the database owner:
--   psql -v ON_ERROR_STOP=1 -1 -v app_role=<n8n business user> -f 001_init.sql

CREATE SCHEMA IF NOT EXISTS business;

-- Invoice register
CREATE TABLE IF NOT EXISTS business.invoices (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- source
    source               text        NOT NULL DEFAULT 'email' CHECK (source IN ('email', 'ksef')),
    source_message_id    text,
    file_name            text,
    file_sha256          char(64)    UNIQUE,          -- same PDF sent twice

    -- invoice data
    seller_nip           char(10),
    seller_name          text,
    invoice_number       text,
    issue_date           date,
    sale_date            date,
    due_date             date,
    bank_account         text,

    -- amounts (numeric, never float)
    currency             char(3)     NOT NULL DEFAULT 'PLN',
    net_amount           numeric(14,2),
    vat_amount           numeric(14,2),
    gross_amount         numeric(14,2),
    fx_rate              numeric(12,6),
    fx_rate_date         date,
    fx_table_no          text,
    gross_pln            numeric(14,2),

    -- checks
    whitelist_status     text,
    whitelist_request_id text,                        -- proof of the MF check
    validation_errors    jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- decision
    category             text,
    cost_center          text,
    status               text        NOT NULL DEFAULT 'received'
                         CHECK (status IN ('received', 'needs_review', 'approved', 'rejected', 'booked', 'error')),
    approved_by          text,
    approved_at          timestamptz,

    -- audit
    extracted            jsonb,                       -- raw LLM output
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    UNIQUE (seller_nip, invoice_number)               -- duplicates, also under concurrency
);

-- VAT white list cache (a check is valid for one specific day)
CREATE TABLE IF NOT EXISTS business.vat_whitelist_cache (
    nip          char(10)    NOT NULL,
    bank_account text        NOT NULL,
    check_date   date        NOT NULL,
    status       text        NOT NULL,                -- accountAssigned: TAK / NIE
    request_id   text,
    response     jsonb,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (nip, bank_account, check_date)
);

-- NBP exchange rates (filled on demand by scenario 02, later also by 03)
CREATE TABLE IF NOT EXISTS business.fx_rates (
    currency    char(3)       NOT NULL,
    rate_date   date          NOT NULL,               -- effectiveDate of the NBP table
    table_no    text          NOT NULL,               -- e.g. 201/A/NBP/2026
    mid         numeric(12,6) NOT NULL,
    fetched_at  timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (currency, rate_date)
);

-- n8n business user: read, insert, update. No DELETE: invoice history stays for audit.
GRANT USAGE ON SCHEMA business TO :"app_role";
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA business TO :"app_role";
