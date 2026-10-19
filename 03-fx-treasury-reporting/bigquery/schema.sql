-- Scenario 03 · FX treasury reporting — BigQuery schema.
-- __BQ_DATASET__ is replaced with the target dataset when applying (views must use
-- dataset-qualified names, so a default dataset is not enough), e.g.
--   sed 's/__BQ_DATASET__/fx_dev/g' schema.sql | bq query --location=EU --use_legacy_sql=false
-- Every statement is idempotent (IF NOT EXISTS / OR REPLACE), so the file can be re-applied.

-- Daily NBP table A mid rates.
CREATE TABLE IF NOT EXISTS __BQ_DATASET__.rates (
  rate_date  DATE      NOT NULL OPTIONS (description = 'NBP effectiveDate (table A)'),
  currency   STRING    NOT NULL OPTIONS (description = 'ISO 4217 code, e.g. EUR'),
  mid        NUMERIC   NOT NULL OPTIONS (description = 'NBP mid rate, PLN per 1 unit'),
  table_no   STRING    NOT NULL OPTIONS (description = 'NBP table number, e.g. 203/A/NBP/2026'),
  loaded_at  TIMESTAMP NOT NULL OPTIONS (description = 'When the row was written by n8n'),
  PRIMARY KEY (rate_date, currency) NOT ENFORCED
)
PARTITION BY DATE_TRUNC(rate_date, MONTH)
CLUSTER BY currency
OPTIONS (
  description = 'Daily NBP table A mid rates',
  require_partition_filter = TRUE
);

-- Demo FX payables and receivables (loaded from seed.sql).
CREATE TABLE IF NOT EXISTS __BQ_DATASET__.positions (
  position_id   STRING  NOT NULL OPTIONS (description = 'Document id, e.g. AP-0007 or AR-0003'),
  direction     STRING  NOT NULL OPTIONS (description = 'payable (we pay) or receivable (we get paid)'),
  counterparty  STRING  NOT NULL OPTIONS (description = 'Supplier or customer name'),
  currency      STRING  NOT NULL OPTIONS (description = 'ISO 4217 code: EUR, USD, CHF, GBP'),
  amount        NUMERIC NOT NULL OPTIONS (description = 'Document amount in its currency, always positive'),
  booked_date   DATE    NOT NULL OPTIONS (description = 'Document date; the booking rate is NBP mid of the previous business day'),
  due_date      DATE    NOT NULL OPTIONS (description = 'Payment due date'),
  settled_date  DATE             OPTIONS (description = 'Payment date; NULL while the position is open'),
  PRIMARY KEY (position_id) NOT ENFORCED
)
OPTIONS (description = 'Demo FX payables and receivables (seeded). In production this would be fed from ERP / AP and AR modules.');

-- Rate metrics per currency and NBP publication day.
-- Changes are in %, volatility is annualised (daily returns over 30 calendar days × √252).
CREATE OR REPLACE VIEW __BQ_DATASET__.rate_metrics
OPTIONS (description = 'Daily, weekly change, 30-day min/max and volatility per currency, computed from rates')
AS
WITH r AS (
  SELECT rate_date, currency, mid, table_no
  FROM __BQ_DATASET__.rates
  WHERE rate_date >= DATE_SUB(CURRENT_DATE('Europe/Warsaw'), INTERVAL 3 YEAR)  -- partition filter
),
with_prev AS (
  SELECT
    r.*,
    LAG(mid) OVER (PARTITION BY currency ORDER BY rate_date) AS prev_mid,
    LAST_VALUE(mid) OVER (
      PARTITION BY currency ORDER BY UNIX_DATE(rate_date)
      RANGE BETWEEN UNBOUNDED PRECEDING AND 7 PRECEDING
    ) AS week_ago_mid
  FROM r
),
with_ret AS (
  SELECT
    with_prev.*,
    CAST(SAFE_DIVIDE(mid - prev_mid, prev_mid) AS FLOAT64) AS ret_1d
  FROM with_prev
)
SELECT
  rate_date,
  currency,
  mid,
  table_no,
  ROUND(ret_1d * 100, 3)                                         AS chg_1d_pct,
  ROUND(SAFE_DIVIDE(mid - week_ago_mid, week_ago_mid) * 100, 3)  AS chg_1w_pct,
  MIN(mid) OVER w30                                              AS min_30d,
  MAX(mid) OVER w30                                              AS max_30d,
  ROUND(STDDEV_SAMP(ret_1d) OVER w30 * SQRT(252) * 100, 2)       AS vol_30d_ann_pct,
  COUNT(*) OVER w30                                              AS sessions_30d
FROM with_ret
WINDOW w30 AS (
  PARTITION BY currency ORDER BY UNIX_DATE(rate_date)
  RANGE BETWEEN 29 PRECEDING AND CURRENT ROW
);

-- Every position with its sign and booking rate (NBP mid of the last publication BEFORE booked_date).
-- Base for the two exposure views below, so the booking-rate rule lives in one place.
CREATE OR REPLACE VIEW __BQ_DATASET__.positions_booked
OPTIONS (description = 'Positions with sign (+1 receivable, -1 payable) and booking rate')
AS
WITH r AS (
  SELECT rate_date, currency, mid, table_no
  FROM __BQ_DATASET__.rates
  WHERE rate_date >= DATE_SUB(CURRENT_DATE('Europe/Warsaw'), INTERVAL 3 YEAR)  -- partition filter
)
SELECT
  p.*,
  IF(p.direction = 'receivable', 1, -1) AS sign,
  r.rate_date AS booked_rate_date,
  r.table_no  AS booked_table_no,
  r.mid       AS booked_rate
FROM __BQ_DATASET__.positions AS p
JOIN r
  ON  r.currency  = p.currency
  AND r.rate_date <  p.booked_date
  AND r.rate_date >= DATE_SUB(p.booked_date, INTERVAL 10 DAY)
WHERE TRUE
QUALIFY ROW_NUMBER() OVER (PARTITION BY p.position_id ORDER BY r.rate_date DESC) = 1;

-- Open positions today, valued at the latest NBP rate, with unrealised FX result and due bucket.
CREATE OR REPLACE VIEW __BQ_DATASET__.exposure_positions
OPTIONS (description = 'Open positions valued at the latest NBP rate: PLN value, unrealised FX gain/loss, due bucket')
AS
WITH latest AS (
  SELECT currency, rate_date, mid
  FROM __BQ_DATASET__.rates
  WHERE rate_date >= DATE_SUB(CURRENT_DATE('Europe/Warsaw'), INTERVAL 30 DAY)  -- partition filter
  QUALIFY ROW_NUMBER() OVER (PARTITION BY currency ORDER BY rate_date DESC) = 1
),
today AS (SELECT CURRENT_DATE('Europe/Warsaw') AS d)
SELECT
  b.position_id,
  b.direction,
  b.counterparty,
  b.currency,
  b.amount,
  b.sign * b.amount                                    AS signed_amount,
  b.booked_date,
  b.booked_rate_date,
  b.booked_rate,
  ROUND(b.sign * b.amount * b.booked_rate, 2)          AS booked_pln,
  l.rate_date                                          AS current_rate_date,
  l.mid                                                AS current_rate,
  ROUND(b.sign * b.amount * l.mid, 2)                  AS current_pln,
  ROUND(b.sign * b.amount * (l.mid - b.booked_rate), 2) AS unrealised_pln,
  b.due_date,
  DATE_DIFF(b.due_date, today.d, DAY)                  AS days_to_due,
  CASE
    WHEN b.due_date <  today.d                    THEN '0 overdue'
    WHEN DATE_DIFF(b.due_date, today.d, DAY) <= 30 THEN '1 within 30 days'
    WHEN DATE_DIFF(b.due_date, today.d, DAY) <= 60 THEN '2 31-60 days'
    WHEN DATE_DIFF(b.due_date, today.d, DAY) <= 90 THEN '3 61-90 days'
    ELSE '4 over 90 days'
  END                                                  AS due_bucket
FROM __BQ_DATASET__.positions_booked AS b
CROSS JOIN today
JOIN latest AS l USING (currency)
WHERE b.booked_date <= today.d
  AND (b.settled_date IS NULL OR b.settled_date > today.d);

-- Net position and unrealised FX result per currency for every NBP publication day.
-- A position counts on day d if booked on or before d and not yet settled on d.
CREATE OR REPLACE VIEW __BQ_DATASET__.exposure_daily
OPTIONS (description = 'Daily net open position per currency: amount, PLN value and unrealised FX gain/loss')
AS
WITH r AS (
  SELECT rate_date, currency, mid
  FROM __BQ_DATASET__.rates
  WHERE rate_date >= DATE_SUB(CURRENT_DATE('Europe/Warsaw'), INTERVAL 3 YEAR)  -- partition filter
)
SELECT
  r.rate_date,
  r.currency,
  COUNT(*)                                               AS open_docs,
  SUM(b.sign * b.amount)                                 AS net_amount,
  ROUND(SUM(b.sign * b.amount * r.mid), 2)               AS net_pln,
  ROUND(SUM(b.sign * b.amount * (r.mid - b.booked_rate)), 2) AS unrealised_pln
FROM __BQ_DATASET__.positions_booked AS b
JOIN r
  ON  r.currency  = b.currency
  AND r.rate_date >= b.booked_date
  AND (b.settled_date IS NULL OR r.rate_date < b.settled_date)
GROUP BY r.rate_date, r.currency;
