# 01 · IT support AI agent

Telegram bot that acts as first-line IT support for the server: it checks the server itself before answering.

- AI agent (OpenRouter) with per-chat memory in Postgres
- Tools: `server_status`, `disk_usage`, `container_logs`, all via one SSH sub-workflow
- Commands on the host go through a forced-command wrapper with an allowlist,
  so the agent cannot run anything outside it, even under prompt injection
- Messages from other chats are ignored

**Workflows:** `workflow.json` (main), `ssh-runner.workflow.json` (SSH sub-workflow).
**Credentials in n8n:** Telegram API, OpenRouter, Postgres, SSH Private Key.
**Placeholders filled by CI:** `__ALLOWED_CHAT_ID__`.