INSERT INTO kb.articles (title, body) VALUES
($$n8n is not opening or returns 502/503$$,
$$Keywords: n8n, down, unavailable, not opening, 502, 503, bad gateway, outage.
Check: check_service for the affected service; server_status for the container n8n-stack-n8n-1 (prod) or n8n-dev-n8n-1 (dev).
If the container is Exited or Restarting, read container_logs for it. Common causes: Postgres is not healthy, the disk is full, a configuration error after an update.
If the container is Up but check_service returns 502, the problem is between the reverse proxy and n8n.
Only an administrator restarts services or changes configuration: open a ticket.$$),

($$Disk is full$$,
$$Keywords: disk, space, full, storage, no space left on device, df.
Check: disk_usage. Above 85% is a warning, above 95% is critical: services start failing.
Common causes: old Docker images and layers, oversized container logs.
Cleanup is done by an administrator (docker system df, docker image prune). If usage is above 85%, open a ticket.$$),

($$Container keeps restarting$$,
$$Keywords: restarting, restart loop, crash, crash loop, keeps failing, up for a few seconds.
Sign: server_status shows Restarting, or Up for only a few seconds on repeated checks.
Check: container_logs for that container; look for the error right before it exits.
Common causes: wrong environment variables, the database is unavailable, not enough memory (the dev environment has strict memory limits).
If the cause is not clear from the logs, open a ticket and include the last log lines.$$),

($$Telegram bot is not responding$$,
$$Keywords: telegram, bot, not responding, no reply, silent, webhook.
Check: check_service for n8n-prod; server_status to see whether n8n-stack-n8n-1 is running.
If n8n is running but the bot is silent, the webhook is probably wrong: a bot can have only one webhook, and a test run in dev switches it to the test URL. The workflow must be published again.
If n8n is not running, see the article "n8n is not opening or returns 502/503".$$),

($$When to open a ticket$$,
$$Keywords: ticket, issue, escalate, escalation, administrator, report.
The agent does not restart services or change configuration; it only diagnoses.
Open a ticket when the problem cannot be fixed without action on the server, or when the cause is not found.
Ask the user before opening a ticket.
Include in the ticket: the symptom, what was checked, the key tool outputs (short), and the suspected cause.$$);