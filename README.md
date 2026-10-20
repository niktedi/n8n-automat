# n8n AI Automation

Business process automations built with n8n, LLMs and Python, self-hosted and deployed from Git.

Each scenario solves one concrete process and is built the way it would run in production, not as a demo on a laptop:

- **AI only where it helps.** An agent where the task needs decisions, a plain workflow where it doesn't.
- **Limits outside the model.** What an agent can touch is restricted by the infrastructure (allowlists, fixed queries, scoped tokens), not only by its prompt.
- **Numbers from code, words from the model.** Where an LLM writes a report, every figure comes from SQL or Python and is checked before it is sent.
- **A human in the loop** for actions that matter.
- **Workflows as code.** Built in a dev n8n, reviewed in Git, deployed to prod by CI. Prod is never edited by hand.

## Scenarios

| # | Scenario | Status |
|---|---|---|
| 00 | [Telegram smoke bot](00-telegram-smoke-bot/README.md): checks the whole CI/CD pipeline | Done |
| 01 | [IT support AI agent](01-it-support-ai-agent/README.md): Telegram bot that diagnoses the server, follows a knowledge base and opens GitHub issues | Done · [demo video](https://vimeo.com/1236476626) |
| 02 | [Invoice processing](02-invoice-processing/README.md): PDF invoices from a mailbox are read by an LLM, checked in code (NIP, totals, VAT white list, NBP rates), approved in Telegram and kept in a Postgres register | Done · [demo video](https://vimeo.com/1237086810) |
| 03 | [FX treasury reporting](03-fx-treasury-reporting/README.md): daily NBP rates in BigQuery, currency exposure and FX revaluation in SQL views, a Telegram alert on sharp moves, a weekly Gemini summary with every number checked in code, and a Looker Studio dashboard | Done · [dashboard](https://lookerstudio.google.com/reporting/ec10aa46-b0dc-425d-be2d-538ae36d1e70) |
| 04 | — | In progress |

Each scenario folder has its own README: the problem, how the workflow is built, security decisions and how to set it up.

## How a workflow gets to production

```text
dev n8n  ──export──▶  Git  ──push──▶  Checks  ──▶  Deploy  ──▶  prod n8n
```

1. A workflow is built and tested in a separate **dev** n8n instance.
2. It is exported to JSON. Personal and environment-specific values (chat IDs, the BigQuery dataset) are replaced with `__PLACEHOLDERS__`; credentials never leave n8n.
3. A **pre-commit hook** checks the files and scans for secrets before anything is committed; GitHub push protection and a **Checks** job do it again after push.
4. A push to `main` that touches a scenario folder triggers **its own deploy**: a self-hosted runner on the server fills the placeholders from GitHub secrets, maps dev credentials to prod ones by name, imports and publishes the workflows, and waits for n8n to report healthy.

## Repository layout

```text
00-telegram-smoke-bot/      scenario: pipeline check
01-it-support-ai-agent/     scenario: IT support AI agent
02-invoice-processing/      scenario: invoice processing (+ shared error-alerts workflow)
03-fx-treasury-reporting/   scenario: FX rates, exposure and weekly summary (BigQuery, Gemini, Looker Studio)
infra/                      shared server pieces: Postgres setup and migrations, SSH wrapper
scripts/                    workflow checks and deploy rendering (Python)
.github/workflows/          checks, reusable deploy template, one deploy per scenario
.githooks/                  pre-commit: workflow checks + gitleaks
```

## Stack

n8n (self-hosted, Docker Compose), PostgreSQL, Google BigQuery, Vertex AI (Gemini), Looker Studio, LLMs via OpenRouter, Python, SQL, GitHub Actions with a self-hosted runner, gitleaks, Telegram Bot API, GitHub API, Microsoft Graph (Outlook), Polish VAT white list API, NBP exchange rate API.
