-- Query of the n8n Postgres node "Save invoice" (Execute Query).
-- Always returns exactly one row: invoice_id, or NULL when a UNIQUE constraint
-- (same file, or same seller + invoice number) already holds this invoice.
-- One row per input item keeps the following "Merge by position" aligned.

WITH ins AS (
    INSERT INTO business.invoices (
        source, source_message_id, file_name, file_sha256,
        seller_tax_id, seller_name, invoice_number,
        issue_date, sale_date, due_date, bank_account,
        currency, net_amount, vat_amount, gross_amount,
        fx_rate, fx_rate_date, fx_table_no, gross_pln,
        whitelist_status, whitelist_request_id, validation_errors,
        category, cost_center, status, approved_by, approved_at,
        extracted
    )
    VALUES (
        'email', $1, $2, $3,
        $4, $5, $6,
        $7::date, $8::date, $9::date, $10,
        $11, $12::numeric, $13::numeric, $14::numeric,
        $15::numeric, $16::date, $17, $18::numeric,
        $19, $20, $21::jsonb,
        $22, $23, $24::text,
        CASE WHEN $24::text = 'approved' THEN 'auto' END,
        CASE WHEN $24::text = 'approved' THEN now() END,
        $25::jsonb
    )
    ON CONFLICT DO NOTHING
    RETURNING id
)
SELECT (SELECT id FROM ins) AS invoice_id;
