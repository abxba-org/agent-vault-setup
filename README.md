# agent-vault-setup

Hermes Agent on a single VPS with its model-provider credentials brokered through
[Infisical Agent Vault](https://github.com/Infisical/agent-vault) — agents never hold real API keys;
a MITM forward proxy injects credentials server-side, with strict-deny egress and per-agent tokens.

## What's in here

| Path | Contents |
|---|---|
| docs/SETUP-LOG.md | The complete record: every command run, in order, with the exact ones that made it work |
| docs/PLAN.md | The approved design: threat model, scope, phases, acceptance criteria |
| docs/PHASE1-REPORT.md | Sandbox test report — all core fail-closed properties PASS, findings F1–F5 |
| docs/RUNBOOK.md | The live-cutover runbook (dedicated `hermes` user + nftables), rehearsed with timings |
| scripts/agent-vault.service | Hardened systemd unit (LoadCredential, stdin password feed) |
| scripts/agent-vault-cred-wrapper | ExecStart wrapper — master password never touches the process environment |
| scripts/wire-env.py | Rewrites the agent's `.env`: placeholder key + proxy env block |
| scripts/fix-paths.py | Path rewriter for migrated trees (use the full-tree grep loop in RUNBOOK.md for venvs) |
| scripts/hermes-gateway.system-unit | Gateway as a system service under the dedicated `hermes` user |
| scripts/vault-mgmt | ON/OFF toggle for the mgmt UI's tailnet exposure — default OFF (localhost/SSH-tunnel only), ON auto-reverts after 30 min unless --persist |

## Automatic security updates

On Ubuntu/Debian systems, install and enable unattended security updates with:

```bash
sudo apt update
sudo apt install unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades
```

The `dpkg-reconfigure` command enables the distribution's unattended-upgrades configuration. Review the package and reboot settings before using this on production systems. Do not run a separate cron job for the same package updates, because it can overlap with the system's built-in `apt-daily` and `apt-daily-upgrade` timers.

Check the update timer with:

```bash
systemctl status apt-daily-upgrade.timer
systemctl list-timers --all | grep apt
```

## Status

Production LIVE: Hermes gateway runs as the dedicated `hermes` user behind the MITM proxy,
with persisted nftables egress enforcement (inet av_hermes). Management-UI exposure is
toggle-controlled and defaults OFF — tailnet access via `sudo vault-mgmt on` (auto-revert)
or an SSH tunnel; proxy port 14322 is never exposed in any mode.

Pending: old-key revocation (criterion 20), tailnet ACL scoping :443 while ON, agent-token
rotation, optional service-rule dedupe (see docs/SETUP-LOG.md tail).

No secrets are committed anywhere in this repo — credentials live only in the
vault (SQLite, AES-256-GCM) and root-owned 0600 files on the VPS.
