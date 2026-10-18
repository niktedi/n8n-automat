-- Query of the n8n Postgres node "Apply decision" in workflow "02 Invoice approval".
-- $1 invoice id, $2 action ('approve' | 'reject'), $3 who decided.
-- Changes only invoices that still wait for a decision: a second click, an old
-- message or a forged callback for an invoice in any other status changes nothing.
-- Always returns exactly one row: new_status, or NULL when nothing was changed.

WITH upd AS (
    UPDATE business.invoices
    SET status      = CASE WHEN $2::text = 'approve' THEN 'approved' ELSE 'rejected' END,
        approved_by = $3::text,
        approved_at = now(),
        updated_at  = now()
    WHERE id = $1::bigint
      AND status = 'needs_review'
      AND $2::text IN ('approve', 'reject')
    RETURNING status
)
SELECT (SELECT status FROM upd) AS new_status;
