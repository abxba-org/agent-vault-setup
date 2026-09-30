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

## Status

Sandbox tested (Phase 1 PASS), production installed and wired (Hermes live through
the proxy), pending: dedicated-user cutover + nftables + old-key revocation
(see docs/RUNBOOK.md — requires the operator present).

No secrets are committed anywhere in this repo — credentials live only in the
vault (SQLite, AES-256-GCM) and root-owned 0600 files on the VPS.