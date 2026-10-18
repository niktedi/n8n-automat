# 02 · Invoice processing

An n8n workflow that picks up incoming invoices from a mailbox, reads the PDF with an LLM, checks the data in code and against public registers, and records every invoice in a Postgres register. A person is involved only when a decision is needed — through Approve / Reject buttons in Telegram.

Built in n8n, deployed from Git to a self-hosted n8n by CI.

> **Demo:** _video coming soon_

**The problem.** Accounts payable re-types data from PDFs, checks the arithmetic, verifies the supplier's bank account against the VAT white list, looks up the NBP exchange rate for foreign-currency invoices, and makes sure the same invoice is not booked twice. It is repetitive work where mistakes are easy and expensive.

**Why e-mail and not only KSeF.** Since 2026 Polish suppliers issue invoices through KSeF, where the data is already structured. Invoices from foreign suppliers — cloud, SaaS, EU contractors — still arrive as PDF attachments. This scenario covers that stream; KSeF is planned as a second input into the same pipeline.

## How it works

```text
Mailbox (Microsoft Graph)
  → PDF → text
  → LLM: text → JSON (fixed schema)
  → Python: data checks
  → Postgres: duplicate lookup
  → VAT white list (Ministry of Finance API)
  → NBP exchange rate (foreign currency only)
  → cost category and cost centre (MPK)
  → decision: approved / needs_review / error / duplicate
  → write to the register
  → Telegram: notification or approval request
  → e-mail moved to Processed or Failed
```

A second workflow, `invoice-approval.workflow.json`, handles the Approve / Reject buttons.

**Core principle: the LLM only reads; code decides.** The model copies what is printed on the invoice into JSON. It never calculates, corrects or completes anything. If the printed gross amount is wrong, the model returns it as printed and the Python checks catch the error — a supplier's mistake cannot disappear because a model was being helpful.

## Stages

### 1. Mail intake
Every 5 minutes the workflow takes up to 10 messages with attachments from the Inbox (Microsoft Graph, OAuth). Only PDF attachments go further.

State lives in the mailbox itself: unprocessed mail stays in the Inbox, processed mail moves to `Processed`, unreadable mail to `Failed`. The read/unread flag is not used — a person may open a message before the workflow does.

### 2. PDF → text
The text layer is extracted from the PDF. If the file is broken, or there are fewer than 50 characters of text (a scanned image), the invoice is **not sent to the LLM**: on empty input a model may invent an invoice, and the call costs money for nothing.

### 3. Data extraction (LLM)
Basic LLM Chain, temperature 0, with a Structured Output Parser and a fixed JSON schema: number, dates, seller (Polish NIP or foreign VAT ID), buyer, bank account, amounts, currency, reverse charge, line items, category. Amounts come back as strings so no cents are lost to floating point. The category must be one value from a fixed list.

### 4. Data checks (Python)

| Check | Error code |
|---|---|
| Required fields present: number, issue date, currency, amounts | `missing:<field>` |
| Seller NIP checksum | `seller_nip_checksum` |
| Foreign seller has a VAT ID | `missing:seller_tax_id` |
| Invoice is addressed to us (buyer NIP = our NIP) | `buyer_not_us` |
| Net + VAT = gross (exact, `Decimal`) | `amounts_mismatch` |
| Reverse charge → VAT is 0 | `reverse_charge_with_vat` |
| Issue date is not in the future | `issue_date_in_future` |
| Due date is not before the issue date | `due_before_issue` |
| Bank account checksum (IBAN mod 97) | `bank_account_checksum` |
| Sum of line items = net | warning |
| Missing due date or bank account | warning |

The same node computes the SHA-256 of the file (for duplicate detection) and picks the date for the exchange rate: the date of sale, or the issue date if there is none.

### 5. Duplicates
The register is queried for **the same file** (SHA-256) or **the same seller + invoice number** (a re-sent scan, a copy). `UNIQUE` constraints in the database are the last line of defence: they catch a duplicate even when one invoice arrives in two e-mails within the same run.

### 6. VAT white list
Only for Polish sellers with a Polish account, and only if the checks in stage 4 passed. The workflow asks `wl-api.mf.gov.pl` whether the account belongs to that NIP. The answer (`TAK` / `NIE`) and the **request ID** are stored in the register. The request ID is the proof that the check was made: if a payment above 15,000 PLN goes to an account that is not on the white list, the buyer cannot deduct it as a cost and becomes jointly liable for the seller's VAT. Foreign sellers get `SKIPPED`.

### 7. NBP exchange rate
For invoices not in PLN: the NBP average rate (table A) from the last business day **before** the date of sale, as the VAT Act requires. Instead of maintaining a holiday calendar, the workflow asks NBP for a 10-day range and takes the last published rate. The rate, its date and the table number (e.g. `196/A/NBP/2026`) are stored. The PLN amount is computed in `Decimal` and rounded to the grosz.

### 8. Category and cost centre
The LLM picks the category in stage 3 (`telecom`, `software`, `cloud_hosting`, `it_services`, `catering`, `office`, `other`). The cost centre (MPK) is assigned by a lookup table in the `Config` node, not by the model — it is a business rule, not a judgement.

### 9. Decision
All routing rules live in one Python node, evaluated in order; the first match wins:

| Status | When |
|---|---|
| `duplicate` | the invoice is already in the register |
| `error` | at least one stage-4 check failed |
| `needs_review` | the data is valid, but: the account is `NIE` on the white list, the category is not recognised, the PLN amount is above the approval threshold (15,000 PLN), or there are warnings |
| `approved` | everything is in order |

The difference between `error` and `needs_review` matters. `error` means "this cannot be paid as it is — ask the supplier for a corrected invoice", so there is no Approve button. `needs_review` means "the data is correct, but a person has to decide".

### 10. Register, notifications, moving the e-mail
Every invoice except duplicates is written to `business.invoices`. Then:

| Status | Telegram | E-mail |
|---|---|---|
| `approved` | no message — it shows up in the weekly summary | → `Processed` |
| `needs_review` | 🟡 message with the reasons and ✅ Approve / ❌ Reject buttons | → `Processed` |
| `error` | 🔴 message with the list of errors, no buttons | → `Processed` |
| `duplicate` | ⚪ "duplicate skipped" | → `Processed` |
| unreadable PDF | ⚠️ "please process manually" | → `Failed` |

Approved invoices are silent on purpose: a bot that reports every normal invoice soon stops being read, and the ones that need a decision get missed.

### 11. Approval (second workflow)
A button press arrives at a Telegram Trigger as a `callback_query`:

1. The workflow checks that the person who pressed it is the allowed approver (by Telegram user ID) — anyone who knows the bot can send a callback.
2. An `UPDATE` sets the status to `approved` or `rejected` and records who decided and when — **only if the invoice is still `needs_review`**. A second tap, an old message, or a forged callback for an invoice with errors changes nothing.
3. The bot answers the button press, removes the buttons and appends the decision to the message.

The decision lives in the database, not in a paused n8n execution: the main workflow finishes immediately, and approvals survive restarts and deploys.

## When an invoice goes into the register — and when it does not

| Situation | In the register | Why |
|---|---|---|
| Everything is in order | ✅ `approved`, `approved_by = auto` | |
| A person has to decide | ✅ `needs_review` → later `approved` / `rejected` | the approval state lives in the database |
| Checks failed | ✅ `error` with the list of errors | a record that the invoice arrived and why it was rejected |
| Duplicate | ❌ | the original is already there |
| Unreadable PDF | ❌ | there is no data; the e-mail goes to `Failed` for manual entry |
| Technical failure (Graph, database or an API down) | ❌ not in this run | see below |

## Technical failures

External calls are retried (2–3 attempts with a pause). If a service stays down longer, the whole run fails — deliberately:

- the e-mail is moved out of the Inbox **at the very end**, so after a failure it stays where it was and the next run processes it again;
- writing it again cannot create a duplicate — the SHA-256 lookup and the `UNIQUE` constraints prevent that.

At-least-once delivery plus idempotent writes gives effectively-once processing with no manual clean-up. The shared error workflow (`error-alerts.workflow.json`) reports the failure to Telegram: which workflow, which node, the error message and a link to the execution.

## Why keep an invoice register

n8n's execution history is not a register: it is pruned, hard to query and not business data. The register in Postgres exists to:

- **never book an invoice twice** — across runs and, later, across channels (e-mail and KSeF);
- **hold the approval state** — a Telegram button updates a row instead of waiting in memory;
- **keep an audit trail** — who approved and when, what the white list check returned (with its request ID), which NBP rate and table were used, and what exactly the model read (the raw JSON is stored);
- **answer accounting questions** — what is waiting for a decision, what was rejected and why, spend by category and cost centre;
- **feed the next processes** — the weekly summary and the FX exposure in scenario 03 (unpaid foreign-currency invoices).

Invoices are never deleted: the n8n database role has no `DELETE` privilege; only the status changes.

## Where the scenario ends

The result of this workflow is an invoice that is **checked and approved for booking** — not a booked or paid one. `approved` means the data is correct and a person (or the rules) has accepted it; nothing has been posted to the ledger or paid yet.

What normally comes next, and is out of scope here:

1. **Booking** in the accounting system or ERP (Comarch Optima, enova, Symfonia, SAP…). The register already has a `booked` status reserved for this step.
2. **Payment run.** Approved invoices due soon are collected into a bank file (Elixir-O for PLN, SEPA XML for EUR), with the split payment (MPP) flag where required. The VAT white list is **checked again on the payment date**: an account that was on the list when the invoice arrived may be gone by then.
3. **Marking as paid** after the bank statement.
4. **Archiving** the original for the statutory period. Today the original stays in the `Processed` mail folder and the register keeps only its SHA-256; a production setup would store the PDF in a document archive and keep a link in the register.

## Security and access

- **Microsoft Graph:** a dedicated Entra app registration with four delegated permissions (`Mail.ReadWrite`, `Files.ReadWrite`, `offline_access`, `User.Read`) — no access to calendars, contacts or sending mail. Separate client secrets for dev and prod.
- **Postgres:** a dedicated `invoice_agent` role with `SELECT / INSERT / UPDATE` on the `business` schema only; the tables are owned by the admin.
- **No SQL injection:** every value that comes from a PDF goes into SQL as a parameter (`$1…$n`), never into the query text.
- **Python in Code nodes:** the task runner allows only the standard-library modules the workflow needs.
- **One approver:** only one Telegram user can approve, checked inside the workflow.
- **Secrets never reach Git:** personal values are `__PLACEHOLDERS__` in the exported JSON, filled in by CI from GitHub secrets.

## Limitations and next steps

- One e-mail, one invoice. If a message has several PDFs and only some of them are readable, it cannot be moved to both folders.
- A corrected invoice re-sent with **the same number** is treated as a duplicate of the original (stored as `error`). The fix is uniqueness only among invoices not in `error`.
- The white list is checked on the day the invoice is received; re-checking on the day of payment belongs to the payment process.
- Scanned PDFs without a text layer are not recognised (no OCR) — they go to `Failed` for manual entry.
- If the model still fails after three retries, the whole run stops: an alert is sent and the e-mail stays in the Inbox for the next run. An invoice that keeps failing has to be moved to `Failed` by hand.

**Next steps**

- **KSeF input.** Structured FA(3) XML from the national e-invoicing system — no LLM needed for extraction; the invoice joins the same pipeline at the checks stage.
- **Messenger input.** Everything after "PDF → text" does not care where the file came from, so a second entry point is cheap: a Telegram bot that accepts a PDF or a photo of an invoice (Telegram Trigger → download the file → the same pipeline). The register already records the source of each invoice; `telegram` would become a new allowed value.
- **Scans and photos.** Two options, both keeping the rule that code does the checks:
  - OCR before the LLM (e.g. Tesseract with Polish language data in a sidecar container) — the model still reads text;
  - a vision-capable model that reads the page image directly — fewer moving parts, higher cost per invoice.
  
  For production volumes a dedicated invoice-parsing service (Azure AI Document Intelligence, Google Document AI) is another option.
- **Hand-off to accounting.** A weekly Excel export to OneDrive (Microsoft Graph) of approved invoices, marking them `booked`.
- **Payment preparation** for treasury: a `paid` status and payment date, white-list re-check on the payment date, and a bank payment file — this also feeds the FX exposure in scenario 03.

## Setup

1. **Database:** run `infra/postgres/02_001_initdb.sql` (with `\set app_role invoice_agent`) and then `infra/postgres/02_002_seller_tax_id.sql` as the database owner.
2. **Task runner:** allow the modules used by the Code nodes:
   `N8N_RUNNERS_STDLIB_ALLOW: "re,datetime,decimal,json,hashlib,base64"` on the runners container, and mount a copy of the runner's `/etc/n8n-task-runners.json` that lists it in `allowed-env` — the stock file forces it to an empty value.
3. **Credentials** in n8n, with exactly these names: `Microsoft Graph invoices` (Microsoft OAuth2, scope `offline_access User.Read Mail.ReadWrite Files.ReadWrite`), `Postgres invoices`, `Telegram invoices`, and the OpenRouter credential.
4. **Mailbox:** create the `Processed` and `Failed` folders.
5. **Telegram:** press `/start` in the bot, so it is allowed to write to you.
6. **Error workflow:** `error-alerts.workflow.json` must be imported and published before the other two — both point to it by ID (the deploy imports `*.workflow.json` files in alphabetical order, so it goes first).

## Demo data

`demo/generate_invoices.py` generates 12 invoices; each one exercises one branch (`demo/expected.csv` lists the expected result). It uses real NIPs and bank accounts from the white list, so the Ministry of Finance check really passes; every page carries a "DEMO — test data, not a real invoice" watermark. `demo/special/` holds a scanned PDF without a text layer and a broken file, for the `Failed` branch.

## Files

```text
02-invoice-processing/
├── workflow.json                    main workflow
├── invoice-approval.workflow.json   Approve / Reject buttons
├── error-alerts.workflow.json       shared error workflow (Telegram alerts)
├── code/                            Python from the Code nodes (validate, decide, format_message)
├── sql/queries/                     SQL of the Postgres nodes (insert, approval decision)
└── demo/                            invoice generator and test files

infra/postgres/02_001_initdb.sql, 02_002_seller_tax_id.sql   database migrations
```
