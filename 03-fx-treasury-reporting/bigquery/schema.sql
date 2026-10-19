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

-- Exposure views are added in step 2.4b.
