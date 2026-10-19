-- Scenario 03 · FX treasury reporting — BigQuery schema.
-- Table names are unqualified: the target dataset is chosen when applying, e.g.
--   bq query --location=EU --use_legacy_sql=false --dataset_id=n8n-automat-fx:fx_dev < schema.sql
-- Every statement is idempotent (IF NOT EXISTS / OR REPLACE), so the file can be re-applied.

-- Daily NBP table A mid rates.
CREATE TABLE IF NOT EXISTS rates (
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
CREATE TABLE IF NOT EXISTS positions (
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

-- Views (rate_metrics, exposure) are added in step 2.4.
