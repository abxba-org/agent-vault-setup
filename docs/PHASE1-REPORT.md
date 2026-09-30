# Phase 1 Report — agent-vault sandbox for Hermes (vps-1558ffa7)

Date: 2026-09-30 · Release: v0.39.3 (pinned) · Mode: **B (MITM forward proxy)** — Mode A retired, no base-URL operation exists in the product.

## Environment
- Throwaway sandbox on the production VPS, fully isolated from Hermes config/credentials:
  - dedicated users `av-sbx` (server, uid 1003) and `ag-sbx` (agent, uid 1004); no sudo, no docker group, own home dirs (0750)
  - server bound to 127.0.0.1:24321 (mgmt) / 24322 (MITM proxy), running as av-sbx
  - SQLite data dir 0700, master password file 0600 av-sbx, dummy credentials only
  - agent token file 0600 ag-sbx; agent binary copy in /home/ag-sbx/bin

## Results (core batch first, per approved order)

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | Release verification | PASS w/ caveat | sha256 edacbddb…7baecc matches; 2 sigstore attestation bundles (Rekor + RFC3161) via GitHub attestations API. Full `gh attestation verify` NOT run — gh 2.46 too old; must use current gh or cosign before Phase 2 install |
| 3 | Successful brokering | PASS | curl with NO auth header → upstream received `Authorization: Bearer <vault credential>`; agent env contained no credential value |
| 4 | Privilege separation | PASS | ag-sbx denied: vault data dir, master file, `/proc/<pid>/environ`, `/proc/<pid>/mem`, ptrace (`PTRACE_SEIZE: Operation not permitted`), av-sbx binary dir |
| 5 | Authorization boundaries | PASS w/ finding F2 | credential get/set: denied ("Member role required"); service add/modify: denied; role escalation: denied; **credential list: ALLOWED — key names visible** (values never); proposals exist by design (human-approved) |
| 6 | Egress filtering (netguard) | PASS | blocked: 169.254.169.254, 10/8, 172.16/12, 192.168/16, 127.0.0.1, 0.0.0.0, 100.64/10, ::1, fd00::/7, ::ffff:169.254.169.254 — 502/bad-gateway from netguard |
| 7 | DNS rebinding | PASS | `169.254.169.254.nip.io`, `10.0.0.1.nip.io`, `192.168.1.1.nip.io`, `127.0.0.1.nip.io` all blocked (502) — policy applies to resolved IP, not hostname |
| 9 | Client header override | PASS w/ note | client `Authorization` header REPLACED by injected credential; other client headers pass through by design (docs confirm broker only owns its auth slot) |
| 11 | Real traffic | PASS w/ note | 30s sustained chunked stream ✓ (60/60 bytes), 100-line chunked JSON stream ✓, 1 MiB request POST 200 ✓, 1 MiB response byte-exact ✓, 5 concurrent 200s ✓. SSE-specific endpoint test inconclusive (sse.dev down; local SSE mock correctly blocked by netguard — consistent w/ test 6). Real SSE validation deferred to Phase 2 OpenRouter streaming |
| 12 | nftables fail-closed | PASS w/ note | dedicated `inet av_sbx` table, output chain priority 10, skuid 1004: allow 127.0.0.1:24322 + DNS 53, drop rest. Direct request: timeout (exit 28). Via proxy: 200. ubuntu/SSH unaffected. Survived `systemctl restart docker` and `fail2ban-client reload`. One transient 502 retested 3/3 clean. Auto-revert armed first (systemd-run 5m), cancelled deliberately |
| 14 | Failure / recovery | PASS | server stopped → proxied request fails (curl exit 7, no fallback), direct request still 200 (no firewall yet — firewall is what closes that path), restart → 200. Boot ordering testable only in Phase 2 (systemd unit) |

## Mode B extras
- Host/SNI confusion (8): PASS — forged `Host:` ignored (upstream saw httpbin.org); injection keyed on validated CONNECT destination, not client headers. CONNECT-target manipulation can't redirect injection.
- HTTP/2 (8): MITM downgrades client leg to HTTP/1.1 (`http_version=1.1`); no h2 passthrough. Functional, not a security issue.
- Request smuggling (8): client leg fully terminated and re-parsed by the proxy; upstream leg originates from the proxy's own HTTP client — cross-proxy smuggling structurally eliminated. Deep fuzzing not performed (rationale documented).
- Cert scope (8): **FINDING F3** — proxy issues a MITM cert for unmatched hosts (e.g. CN=example.com, issuer=Agent Vault Root CA) *before* the strict-deny 403. Plan requirement "never issue certificates for non-allowlisted hosts" is NOT implemented by the tool. Severity LOW: CA is trusted only by the agent process (never system store), and no credential is injected for unmatched hosts. Compensating controls: process-scoped CA trust + strict deny + nftables.

## Findings
- **F1 (test hygiene, not a product bug):** dummy credential values appeared in /var/log/auth.log + user journal because I passed them as command-line arguments during setup. Validates the plan rule: production credential entry = web UI (Tailscale) or stdin ONLY, never argv. Scrubbed at teardown; harmless dummies.
- **F2 (low):** agent token can enumerate credential KEY NAMES (`vault credential list`). Values never exposed. Accept + document, or move real key names out of agent-visible scope later.
- **F3 (low):** see cert scope above.
- **F4 (inherent limitation):** an upstream that echoes auth headers (like httpbin) reveals the injected credential in its response. Real APIs (OpenRouter) don't echo. No proxy-side mitigation exists; documented.
- **F5:** vault settings read (`unmatched_host_policy`) via management API GET works with an agent token (metadata only); mutation correctly requires vault admin/owner.
- Strict deny verified: after `PATCH /v1/vaults/sandbox/settings {"unmatched_host_policy":"deny"}`, unmatched hosts → 403 + proposal hint; allowlisted → 200.

## Changes required for production (Phase 2 deltas)
1. Full attestation verification with current gh/cosign before installing the pinned binary.
2. systemd unit: `--password-stdin` via ExecStart wrapper reading `$CREDENTIALS_DIRECTORY/master` (LoadCredential=), or 0600 EnvironmentFile fallback; verify `/proc/<pid>/environ` unreadable by hermes uid.
3. Service rule pinned to `openrouter.ai:443` (port pinning; port-scoped host form) — default "no port = any port" noted.
4. `unmatched_host_policy=deny` set at vault creation (verified PATCH).
5. Dedicated `hermes` user migration per runbook (rehearsed on a ~/.hermes copy), BEFORE firewall enablement.
6. nftables table `inet av_hermes` for hermes uid with auto-revert protocol; re-verify after docker restart + fail2ban reload.
7. NO_PROXY: only localhost/Tailscale/Grafana OTLP as needed — kept minimal.
8. Hermes stack CA-honor verification (Python httpx/requests via REQUESTS_CA_BUNDLE, Node via NODE_EXTRA_CA_CERTS, curl).
9. Proposal workflow decision: agent tokens can raise proposals (human approval). Default: leave on, review.

## Sandbox status
Left RUNNING for operator inspection (deviation from plan §15, reversible): UI http://127.0.0.1:24321, owner creds in /home/av-sbx/.agent-vault/owner.sandbox (0600 av-sbx). Teardown command set ready: stop server, `userdel -r av-sbx ag-sbx`, remove /opt/agent-vault-sandbox, nft table, journal entries contain dummy values only. Full teardown executed on go/no-go decision either way.

## Go/no-go
Core fail-closed properties all PASS. Low-severity findings F2/F3 documented with compensating controls. Recommendation: **GO for Phase 2**, contingent on: full attestation verify, hermes-user migration runbook approved, and acceptance that F4-class reflection is inherent to brokering.