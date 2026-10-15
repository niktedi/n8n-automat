# support-wrapper

Forced-command wrapper for the SSH tools of scenario 01 (`server_status`, `disk_usage`, `container_logs`).

The n8n SSH key can run only this script. It allows exactly:

| Request | What runs |
|---|---|
| `status` | `uptime` and `docker ps -a` |
| `disk` | `df -h` without tmpfs/overlay |
| `logs <container>` | last 50 lines of `docker logs`; the name must match a strict pattern and the container must exist |

Everything else is rejected with exit code 2. Every request, allowed or not, is written to the system journal.

## Install

```bash
sudo install -o root -g root -m 755 support-wrapper /usr/local/bin/support-wrapper

sudo useradd -m -s /bin/bash n8n-support
sudo usermod -p '*' n8n-support      # no password login, key login still allowed
sudo usermod -aG docker n8n-support
```

The script must be owned by root and live in a root-owned directory: the user it restricts must not be able to change it. Membership in `docker` is effectively root, which is exactly why the key is pinned to this script.

## authorized_keys

One key per environment (dev, prod), each on its own line in `/home/n8n-support/.ssh/authorized_keys`:

```text
restrict,command="/usr/local/bin/support-wrapper",from="172.16.0.0/12" ssh-ed25519 AAAA... n8n-support-prod
```

- `command=` — sshd always runs the wrapper; the requested command only arrives in `SSH_ORIGINAL_COMMAND`.
- `restrict` — no shell, no PTY, no port, agent or X11 forwarding, no `~/.ssh/rc`.
- `from=` — the key is accepted only from Docker networks on this host.

Append new keys with `sudo tee -a`, never overwrite the file. Permissions: `.ssh` is `700`, `authorized_keys` is `600`, both owned by `n8n-support`.

## n8n side

- n8n reaches the host as `host.docker.internal`: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the n8n service in compose.
- Leave **Working Directory** empty in the SSH node: otherwise n8n prepends `cd <dir> ;` to the command, and the wrapper rejects it.

## Test

```bash
sudo -u n8n-support env SSH_ORIGINAL_COMMAND="status" /usr/local/bin/support-wrapper
sudo -u n8n-support env SSH_ORIGINAL_COMMAND="logs x; cat /etc/passwd" /usr/local/bin/support-wrapper   # usage: logs <container>
sudo -u n8n-support env SSH_ORIGINAL_COMMAND="cat /etc/passwd" /usr/local/bin/support-wrapper           # not allowed
sudo journalctl -t support-wrapper -n 5 --no-pager
```

Testing over SSH from the host itself does not work: Docker's NAT rewrites the source address to the public IP, and `from=` rejects it. Test from a container in a compose network instead, or ask the bot.
