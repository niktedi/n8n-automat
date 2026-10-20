# 03 · FX treasury reporting

Daily NBP exchange rates loaded into BigQuery, the company's open currency position and its FX revaluation computed in SQL, a Telegram alert when a rate moves sharply, a weekly summary written by Gemini with every number checked in code, and a Looker Studio dashboard on top of the same data.

Built in n8n, deployed from Git to a self-hosted n8n by CI.

> **Dashboard:** [FX Treasury · n8n-automat](https://lookerstudio.google.com/reporting/ec10aa46-b0dc-425d-be2d-538ae36d1e70) — open to anyone with the link, no Google Cloud account needed.

**The problem.** A Polish company that pays suppliers and bills customers in EUR, USD, CHF or GBP carries currency risk: what it owes or is owed in foreign currency changes its value in PLN every day. Treasury needs to know how the rates are moving, how large the open position is in each currency, and how much has already been won or lost on the rate since the documents were booked. Usually this is done by hand in Excel — copying NBP rates, matching them against unpaid invoices, writing a few lines for management. It is slow, error-prone and irregular, so a sharp rate move is noticed late.

**What the scenario does.** Every business day it loads the NBP rates, recalculates the position and the FX result, and stays silent unless a rate has moved sharply or the data has stopped updating. Every Friday it sends a short summary in plain English. The dashboard shows the same figures at any time.

## How it works

```text
NBP API (table A)
  → n8n: load window ──MERGE──▶ BigQuery: rates
                                BigQuery: positions (demo, seeded)
                                BigQuery views: rate_metrics, positions_booked,
                                                exposure_positions, exposure_daily
  ┌─────────────────────────────────┼──────────────────────────────┐
  ▼                                 ▼                              ▼
n8n daily (Mon–Fri 12:40)       n8n weekly (Fri 13:10)        Looker Studio
alert if a rate moved > 1%      SQL facts → Gemini → number   two pages:
or the data is stale            check → Telegram              Rates, FX exposure
```

| Workflow | File | Trigger | What it does |
|---|---|---|---|
| `03 FX load window` | `load-window.workflow.json` | called by other workflows | Loads NBP table A for a date range (≤ 93 days) into `rates` with `MERGE` |
| `03 FX backfill` | `manual-backfill.workflow.json` | manual, once per environment | Splits the last 3 years into 93-day windows and calls `load window` for each |
| `03 FX daily` | `workflow.json` | Mon–Fri 12:40 Europe/Warsaw | Loads the last 7 days, checks the latest metrics, sends an alert when needed |
| `03 FX weekly report` | `weekly-report.workflow.json` | Fri 13:10 Europe/Warsaw | Collects the week's facts in SQL, has Gemini write the summary, checks it, sends it |

**Core principle: SQL calculates, the model only writes.** Every number — changes, volatility, position, FX result, and even comparisons such as "the biggest mover" or "volatility above usual" — is computed in BigQuery or Python. Gemini receives finished facts and turns them into three or four short paragraphs. Code then checks that every number in its text exists in the facts; if not, the summary is replaced with a plain table of the same facts.

## Data model

Two tables hold data; four views hold all the calculations. A view stores no data and is recomputed on every query, so the alert, the summary and the dashboard always show the same figures.

| Object | Type | Answers |
|---|---|---|
| `rates` | table | What was the NBP mid rate of a currency on a publication day? |
| `positions` | table | Which foreign-currency payables and receivables does the company have? (demo data) |
| `rate_metrics` | view | How did each rate move: day-on-day, week-on-week, 30-day min / max, annualised volatility |
| `positions_booked` | view | At which rate was each document booked, and with which sign does it enter the position? |
| `exposure_positions` | view | What is open **today**, what it is worth in PLN at the latest rate, the unrealised FX result, the due bucket |
| `exposure_daily` | view | Net position and unrealised FX result per currency **for every publication day** |

**`rates`** — one row per publication day and currency, about 3,000 rows for 3 years: `rate_date`, `currency`, `mid` (`NUMERIC`, PLN per unit), `table_no` (e.g. `203/A/NBP/2026` — a reference to the official source), `loaded_at`. Partitioned by month, clustered by currency, and `require_partition_filter` rejects any query without a date condition.

**`positions`** — one row per document: `position_id` (`AP-…` we pay, `AR-…` we get paid), `direction` (`payable` / `receivable`), `counterparty`, `currency`, `amount` (always positive), `booked_date`, `due_date`, `settled_date` (empty while open).

The booking rate is deliberately **not** stored in `positions`. Polish accounting rules use the NBP mid rate of the last business day **before** the document date, and `positions_booked` takes it from `rates` — one source of truth instead of two that can drift apart. The sign (+1 receivable, −1 payable) is also applied in that one view.

Key formulas:

- **Unrealised FX result** = sign × amount × (current rate − booking rate). Example: we owe 1,486.25 GBP booked at 5.0671; at 5.2003 the result is −1,486.25 × 0.1332 = −197.97 PLN.
- **Week-on-week change** compares with the last rate published at least 7 calendar days earlier, so holidays do not break it.
- **Volatility** = standard deviation of daily returns over 30 calendar days × √252, in % per year — comparable across currencies.
- **Position on a past day:** a document counts as open on day *d* if it was booked on or before *d* and not yet settled. This makes the history in `exposure_daily` honest without storing snapshots.

All objects are defined in `bigquery/schema.sql`. The dataset name is a `__BQ_DATASET__` placeholder (views must use dataset-qualified names), so the same file builds both `fx_dev` and `fx`.

## Stages

### 1. Loading rates
`load window` takes a `start_date` and an `end_date`, validates them (format, order — an empty date would make NBP silently return *today's* table), and requests the whole table A for the range in one call: 12 calls cover 3 years instead of 48 per-currency ones.

- **404 "Brak danych"** from NBP is a normal answer — no publication in the window (a weekend or a holiday). Any other non-200 status stops the workflow and triggers the error workflow.
- **Validation before SQL.** Every value that goes into the `MERGE` text is checked by a regular expression first: date, rate (a positive decimal), table number (`NNN/A/NBP/YYYY`), currency from a fixed list. Every published table must contain all four currencies, otherwise the run fails instead of loading a history with gaps.
- **`MERGE`, not insert.** New day + currency → insert; already there and unchanged → nothing; NBP corrected a rate → update. Loading the same window again adds nothing, so any run can be repeated safely.

### 2. Daily check and alert
Every weekday at 12:40 Warsaw time (NBP publishes table A between 11:45 and 12:15) `daily` reloads the last 7 days — if the server or NBP was down on Monday and Tuesday, Wednesday's run fills the gap by itself. Then one SQL query reads the latest day from `rate_metrics` and returns two flags per currency:

- `is_move` — the day-on-day change is at least ±1 %;
- `is_stale` — the latest rate is more than 5 days old, i.e. the load has quietly stopped working (the longest regular pause in NBP publications is about 4–5 days around Christmas).

A message is sent only when a flag is set. A bot that writes every day stops being read.

### 3. Weekly summary
1. **Facts (SQL).** One query returns a row per currency: rate, weekly change, 30-day range, volatility and whether it is above, below or at its usual 1-year level (> 20 % deviation), open documents, net position and its side in words (`net payable (we owe)` / `net receivable (owed to us)`), PLN value, FX result and its change over the week, amounts overdue and due within 30 days, plus totals.
2. **Prompt (Python).** Python ranks the biggest weekly mover and the largest FX result, so the model never compares numbers. The system prompt: use only the facts, copy every number exactly, negative = loss, the weekly change includes both rate moves and new or settled documents, at most 120 words, no advice.
3. **Gemini (Vertex AI).** `gemini-3.8-flash` through the `generateContent` REST endpoint, temperature 0.2, thinking level *low*, at most one retry. Model reasoning is switched off on purpose: for "turn these facts into text" it adds nothing, and it cost ~25× the visible output (3,182 → 1,331 tokens per call after the change).
4. **Number check (Python).** Every number in the model's text is extracted and compared, by value and without sign, with the numbers in the facts (small integers 0–31 for days and counts are allowed). A truncated answer (`finishReason` ≠ `STOP`), an empty answer or a single unknown number → the summary is replaced by a table built only from the facts, with the reason shown. Tested: when told to round, the model produced 596, 1036, 1789…; the check caught them and the table went out instead.
5. **Telegram.** HTML message; the model's text is HTML-escaped.

The figures in the summary and on the dashboard match to the grosz — both read the same views.

### 4. Dashboard
Looker Studio reads only the views, never `rates` directly, and connects with the owner's credentials: viewers need no GCP access and cannot run their own queries. Cached for 4 hours.

- **Rates** — rate of the selected currency over the last 90 days, 30-day volatility of all four currencies, a table of the latest publications. Changes are not coloured: a rising rate is neither good nor bad until you know whether you owe or are owed that currency.
- **FX exposure** — open position (PLN), unrealised FX result (red = loss), open documents; net position by currency; position by due bucket; FX result over time per currency; the list of open documents sorted by due date. There is no date filter on this page on purpose: the position is everything open today, and a filter on the document date would silently drop old debts. Clicking a bar filters the page by that currency or bucket.

## Why BigQuery and not Postgres

For 3,000 rows Postgres on the VPS would do the job just as well — and the other scenarios use it. BigQuery is used here because this is an **analytical** workload, not a transactional one: a history that grows with every currency, entity and bank account, heavy aggregates over it, and BI and an LLM next to the data in the same project, under the same IAM. It also keeps reporting load off the VPS that runs n8n. The trade-offs: costs depend on bytes scanned (hence the quotas below), primary keys are not enforced (hence `MERGE`), queries take seconds rather than milliseconds, and it ties the reporting layer to Google Cloud.

## Cost control

| Layer | What it limits |
|---|---|
| Project quota: 10 GiB of query bytes per day | the month cannot leave the free 1 TiB tier |
| `maximumBytesBilled` = 100 MB on every query from n8n | one heavy query is rejected before it runs, at no cost |
| `require_partition_filter` on `rates` | no accidental full scan of the history |
| Gemini: one call per run, one retry, output cap, reasoning off | there is no per-project quota for the newest Gemini models, so the workflow design is the limit |
| Budget alert in PLN | a notification if anything still goes wrong |

Actual cost at this volume: BigQuery 0 (free tier), Gemini a fraction of a cent per summary, Looker Studio free.

## Security and access

- **Two service accounts**, `n8n-dev` and `n8n-prod`: `bigquery.jobUser` and `aiplatform.user` on the project, `bigquery.dataEditor` **only on their own dataset** (`fx_dev` / `fx`). Dev cannot read or write prod data even with a wrong dataset name — tested: `Access Denied`.
- **Keys** live only in n8n credentials (encrypted with `N8N_ENCRYPTION_KEY`), not in files or in the repository.
- **No SQL injection:** values from the NBP API are validated before they become part of a query; config values (dataset, thresholds) come from our own `Config` node.
- **Secrets never reach Git:** `__BQ_DATASET__` and `__ALLOWED_CHAT_ID__` are filled in by CI from GitHub secrets.
- **Data residency:** BigQuery data stays in the EU. The model is available only on the `global` Vertex endpoint, so a prompt may be processed outside the EU; it contains only public NBP rates and invented demo amounts. With real data under residency rules, a regional EU endpoint and a model available there would be used.

## Limitations and next steps

- **Demo positions.** `positions` is filled from `bigquery/seed.sql` (22 invented documents, fixed random seed, dates relative to an anchor date). In production it would be fed from the ERP or the AP/AR modules — for example from an invoice register like the one in [scenario 02](../02-invoice-processing/README.md).
- **Simplified exposure:** payables and receivables only. A real treasury view also includes bank balances, loans, signed contracts and forecast cash flows.
- **No partial payments**, one booking rate per document.
- **Schema changes are applied by hand** with `bigquery/apply.sh`; CI deploys only the workflows.

**Next steps**

- Hedging view: what share of the position would a forward contract cover, and what it would have saved.
- Feed `positions` from the invoice register of scenario 02 and from bank statements.
- A Telegram command (`/fx EUR`) answering from the same views on demand.

## Setup

1. **GCP:** a project with billing and a budget alert; BigQuery API and Vertex AI API enabled; a project quota on *Query usage per day*.
2. **Datasets** in the `EU` multi-region:
   ```bash
   bq --location=EU mk --dataset n8n-automat-fx:fx_dev
   bq --location=EU mk --dataset n8n-automat-fx:fx
   ```
3. **Service accounts** `n8n-dev` / `n8n-prod` with `roles/bigquery.jobUser` and `roles/aiplatform.user` on the project, and `roles/bigquery.dataEditor` on their own dataset:
   ```sql
   GRANT `roles/bigquery.dataEditor` ON SCHEMA `n8n-automat-fx.fx`
   TO 'serviceAccount:n8n-prod@n8n-automat-fx.iam.gserviceaccount.com';
   ```
   (run with `bq query --location=EU` — DCL statements need the location explicitly).
4. **Schema and demo data:** `./bigquery/apply.sh fx_dev` and `./bigquery/apply.sh fx`. To refresh the demo dates: `python bigquery/generate_seed.py --anchor YYYY-MM-DD`, then `apply.sh` again.
5. **Credentials** in n8n, with exactly these names: `GCP fx` (Google Service Account API, "Set up for use in HTTP Request node" on, scope `https://www.googleapis.com/auth/cloud-platform`) and `Telegram FX`.
6. **Task runner:** the Code nodes use `json`, `re`, `decimal`, `datetime` and `html` — add them to `N8N_RUNNERS_STDLIB_ALLOW`.
7. **GitHub secrets:** `BQ_DATASET` (`fx`) and `ALLOWED_CHAT_ID`.
8. **After the first deploy:** run `03 FX backfill` once in prod, then press `/start` in the bot.

## Notes from building it

- In the n8n BigQuery node, leave **Location** empty: n8n creates the job in the given location but looks up its result without it and gets `404 Not found: Job`. Without it, BigQuery infers the EU location from the dataset.
- The BigQuery node returns **every value as a string**, booleans included: `"false"` is truthy in Python, so flags are compared as text.
- `MERGE` returns no rows, so its node needs **Always Output Data**, or the workflow stops there.
- In Looker Studio, rates need *Missing data → Linear interpolation*; the default draws weekends as zero.

## Files

```text
03-fx-treasury-reporting/
├── workflow.json                    daily: load, check, alert
├── load-window.workflow.json        sub-workflow: NBP → MERGE into rates
├── manual-backfill.workflow.json    3 years of history in 93-day windows
├── weekly-report.workflow.json      SQL facts → Gemini → number check → Telegram
└── bigquery/
    ├── schema.sql                   tables and views (__BQ_DATASET__ placeholder)
    ├── generate_seed.py             demo positions generator
    ├── seed.sql                     generated demo positions
    └── apply.sh                     schema + seed into fx_dev or fx
```
