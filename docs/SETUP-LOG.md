# Agent Vault setup on vps-ovh (Hermes credential brokering)

Complete record of everything done to this VPS to put Hermes' model-provider
credentials behind [Infisical Agent Vault](https://github.com/Infisical/agent-vault)
v0.39.3, with the exact commands that made it work. Secrets never appear here —
placeholders like `<token>` mark where values were involved.

Date: 2026-09-30 · Host: Ubuntu 26.04, 2 vCPU / 3.7 GB, Docker, Tailscale, fail2ban, auditd
GitHub user: DUDE616 · Docs home on server: `/opt/agent-vault-docs/`

## Decisions (agreed with ABXBA before execution)

1. Goal: protect Hermes' OpenRouter API key — agent processes never hold real credentials; a MITM forward proxy injects them server-side.
2. Mode B only (HTTPS_PROXY + transparent CA). Mode A (base-URL) does not exist in the open-source product.
3. Native binary + hardened systemd service (not Docker).
4. Sandbox first, production after go/no-go.
5. CA trust scoped to the agent process (env vars), never the system store.
6. Hermes moves to a dedicated `hermes` user (no docker group, no sudo) BEFORE the firewall is enabled; migration rehearsed on a copy.
7. nftables UID rules in a dedicated table with an armed auto-revert; re-tested after Docker restart + fail2ban reload.
8. Old key revoked immediately after cutover verifies (no soak period).

## Current state (before the live window)

| Component | State |
|---|---|
| agent-vault.service | active, hardened, loopback-only 127.0.0.1:14321 (mgmt) + 14322 (MITM proxy) |
| Vault | `hermes` — `unmatched_host_policy=deny` |
| Credential | `OPENROUTER_API_KEY` (static) — **currently the OLD key, pending replacement by ABXBA** |
| Hermes `.env` | placeholder `__openrouter_api_key__` + proxy env block (see Wiring) |
| Gateway | restarted, Slack connected via passthrough |
| Management UI | https://vps-ovh.tail95f2ee.ts.net (Tailscale Serve, tailnet-only) |
| Old key | still in pre-cutover process envs; revocation scheduled post-cutover |
| Cutover | NOT done yet — dedicated `hermes` user migration + nftables pending (RUNBOOK.md) |

---

## Phase 1 — sandbox (throwaway, destroyed after report)

Sandboxed in dedicated users on the same VPS, ports 24321/24322, dummy
credentials only. Full results: `PHASE1-REPORT.md`. Key commands:

```bash
# 0. Download + integrity (pinned release)
curl -sL -o agent-vault.tar.gz \
  https://github.com/Infisical/agent-vault/releases/download/v0.39.3/agent-vault_0.39.3_linux_amd64.tar.gz
sha256sum agent-vault.tar.gz   # edacbddb03c6f4221515d9875c3a7b0943c71434708e7fd13be0f7635a7baecc

# 1. Throwaway users (no sudo, no docker)
sudo useradd -m -s /bin/bash av-sbx     # server uid 1003
sudo useradd -m -s /bin/bash ag-sbx     # agent  uid 1004
sudo install -m 755 agent-vault /home/av-sbx/bin/agent-vault

# 2. Server start — master password via STDIN (key discovery: `--password-stdin` exists)
sudo -u av-sbx bash -c 'AGENT_VAULT_TELEMETRY=0 /home/av-sbx/bin/agent-vault server -d \
  --password-stdin --port 24321 --mitm-port 24322 --host 127.0.0.1 \
  < /home/av-sbx/.agent-vault/master.sandbox'

# 3. Vault bootstrap (owner CLI session stored on disk after register)
sudo -u av-sbx /home/av-sbx/bin/agent-vault auth register \
  --address http://127.0.0.1:24321 --email owner@av-sbx.local --password-stdin  # pw via stdin
sudo -u av-sbx /home/av-sbx/bin/agent-vault vault create sandbox
sudo -u av-sbx /home/av-sbx/bin/agent-vault vault use sandbox
sudo -u av-sbx /home/av-sbx/bin/agent-vault vault credential set 'TEST_ECHO_KEY=sk-dummy-echo-12345-DISPOSABLE'
sudo -u av-sbx /home/av-sbx/bin/agent-vault vault service add --name test-echo \
  --host httpbin.org --auth-type bearer --token-key TEST_ECHO_KEY
sudo -u av-sbx /home/av-sbx/bin/agent-vault agent create ag-sbx-test --vault sandbox:proxy --token-only

# 4. THE test that matters — agent env has NO credential, upstream receives it:
sudo -u ag-sbx env AGENT_VAULT_TOKEN=<token> AGENT_VAULT_ADDR=http://127.0.0.1:24321 \
  AGENT_VAULT_VAULT=sandbox /home/ag-sbx/bin/agent-vault vault run -- \
  curl -s https://httpbin.org/headers
#   → httpbin saw "Authorization": "Bearer sk-dum...ABLE" — injected server-side ✓

# 5. Netguard / rebinding (all blocked 502): 169.254.169.254, RFC1918 via raw IP,
#    and rebinding names like http://169.254.169.254.nip.io/ through the proxy

# 6. Strict deny (default is FORWARD-unmatched! must be flipped):
curl -s -c /tmp/avo.txt -X POST -H 'Content-Type: application/json' \
  -d '{"email":...,"password":...}' http://127.0.0.1:24321/v1/auth/login
curl -s -b /tmp/avo.txt -X PATCH -H 'Content-Type: application/json' \
  -d '{"unmatched_host_policy":"deny"}' http://127.0.0.1:24321/v1/vaults/sandbox/settings

# 7. Fail-closed test: `agent-vault server stop` (no flags; --address NOT supported)
#    → proxied curl exits 7; direct curl still 200 (no firewall yet); restart → 200

# 8. nftables sandbox rehearsal (see Phase 2 firewall — same pattern, uid 1004)
```

Phase 1 findings: F1 argv-leak (test hygiene), F2 agent can enumerate credential
KEY NAMES, F3 MITM cert issued for unmatched hosts before the 403, F4 upstream
echo reveals injected credential (inherent), F5 settings-GET visible to agent token.

## Phase 2 — production install

```bash
# Attestation (gh 2.46 was too old — fetched gh 2.102.0, checksum-verified against
# the release's checksums.txt, then):
gh attestation verify agent-vault.tar.gz --repo Infisical/agent-vault --format json
# → provenance from https://github.com/Infisical/agent-vault/.github/workflows/release.yml@refs/tags/v0.39.3
#   our exact sha256 among the 5 subjects

# Dedicated system user
sudo useradd --system --home-dir /var/lib/agent-vault --shell /usr/sbin/nologin agent-vault

# Binary + master password (root-only file; systemd LoadCredential, stdin to server)
sudo install -m 755 agent-vault /usr/local/bin/agent-vault
sudo install -m 700 -d /etc/agent-vault
sudo sh -c 'umask 077 && openssl rand -base64 36 > /etc/agent-vault/master'

# see scripts/agent-vault.service + scripts/agent-vault-cred-wrapper in this repo
sudo install -m 755 agent-vault-cred-wrapper /usr/local/bin/agent-vault-cred-wrapper
sudo install -m 644 agent-vault.service /etc/systemd/system/agent-vault.service
sudo systemctl daemon-reload && sudo systemctl enable --now agent-vault.service
```

Unit-file pitfalls hit (documented so the next person doesn't):
- `226/NAMESPACE` crash: `ProtectSystem=strict` + `ReadWritePaths=/var/lib/agent-vault`
  without `StateDirectory=agent-vault` — the dir must exist → add `StateDirectory=agent-vault`.
- Master password in env var (`AGENT_VAULT_MASTER_PASSWORD`) STAYS visible in
  `/proc/<pid>/environ` when running in the foreground (the "wiped after read"
  behavior only applies to the `-d` daemonize path). Fix: wrapper feeds
  `--password-stdin < $CREDENTIALS_DIRECTORY/avmaster` — the var never exists.

### Master-password rotation (incident response)
The first master was echoed into a terminal transcript during verification →
treated as exposed. Rotation via CLI failed (`master-password change` wants a
TTY — "inappropriate ioctl for device"). Since the vault was still EMPTY: wiped
`/var/lib/agent-vault/.agent-vault`, generated a new master, restarted —
exposed value dead, active master never displayed.

### Production vault + rules
```bash
sudo -u agent-vault env HOME=/var/lib/agent-vault /usr/local/bin/agent-vault \
  auth register --address http://127.0.0.1:14321 --email operator@vps-1558ffa7.local --password-stdin
sudo -u agent-vault env HOME=/var/lib/agent-vault /usr/local/bin/agent-vault vault create hermes
sudo -u agent-vault env HOME=/var/lib/agent-vault /usr/local/bin/agent-vault \
  vault service add --name openrouter --host openrouter.ai:443 \
  --auth-type bearer --token-key OPENROUTER_API_KEY --vault hermes
# + strict deny PATCH (as above, port 14321, vault "hermes")
sudo -u agent-vault env HOME=/var/lib/agent-vault /usr/local/bin/agent-vault \
  agent create hermes --vault hermes:proxy --token-only   # → /etc/agent-vault/hermes-agent-token (0600)
```

Final service table (all :443):

| name | host | auth |
|---|---|---|
| openrouter | openrouter.ai | bearer → OPENROUTER_API_KEY (vault credential) |
| slack | slack.com | passthrough |
| slack-ws | *.slack.com | passthrough |
| nous-portal | portal.nousresearch.com | passthrough |
| hermes-agent | hermes-agent.nousresearch.com | passthrough |
| github-raw | raw.githubusercontent.com | passthrough |
| github | *.github.com | passthrough |
| github-apex | github.com | passthrough |
| github-objects | objects.githubusercontent.com | passthrough |

**Wildcard gotcha (cost a debugging round):** a one-level wildcard `*.github.com`
matches `api.github.com` / `codeload.github.com` but NOT the bare apex
`github.com` — it 403'd with "No broker service matching host". Apex hosts need
their own explicit rule.

**git gotcha:** git's libcurl ignores `CURL_CA_BUNDLE` and `SSL_CERT_FILE` —
behind the MITM proxy, git operations need
`GIT_SSL_CAINFO=/home/ubuntu/.hermes/av-ca.pem` (or
`git config http.sslCAInfo <pem>` for the repo/user).

**Redirect gotcha:** `hermes-agent.nousresearch.com` 301-redirects to
`nousresearch.github.io`, which is NOT allowlisted → 403 on Hermes' update
check. Add a passthrough for it if update checks should work (pending decision).

Passthrough = allowlist + netguard + audit logging, NO credential injection;
the agent's own headers flow unchanged. These hosts are data-egress paths by
design (recorded in the threat model). Unmatched hosts → 403 + proposal hint.

## Wiring Hermes (ubuntu user, pre-cutover)

`.env` (`/home/ubuntu/.hermes/.env`, 0600) — `OPENROUTER_API_KEY` replaced with
`__openrouter_api_key__` and this block appended (by scripts/wire-env.py, run as
root so the token never touched a terminal):

```dotenv
AGENT_VAULT_ADDR=http://127.0.0.1:14321
AGENT_VAULT_VAULT=hermes
AGENT_VAULT_TOKEN=<token>
HTTPS_PROXY=http://<token>:hermes@127.0.0.1:14322
HTTP_PROXY=http://<token>:hermes@127.0.0.1:14322
NO_PROXY=localhost,127.0.0.1
NODE_USE_ENV_PROXY=1
SSL_CERT_FILE=/home/ubuntu/.hermes/av-ca.pem
REQUESTS_CA_BUNDLE=/home/ubuntu/.hermes/av-ca.pem
CURL_CA_BUNDLE=/home/ubuntu/.hermes/av-ca.pem
NODE_EXTRA_CA_CERTS=/home/ubuntu/.hermes/av-ca.pem
```

CA: `agent-vault ca fetch` → `~/.hermes/av-ca.pem` — contains ONLY the vault root
(`CN=Agent Vault Root CA`), so TLS outside the proxy fails closed by design.

Verification chain:
1. Clean-env one-shot: `env -i HOME=... PATH=... hermes -z "Reply with exactly the word BROKERED"` → `BROKERED`
2. Request log: `GET /v1/vaults/hermes/logs` → POST openrouter.ai:443 status 200 at that timestamp
3. Spend limit live: `GET /api/v1/key` through the proxy → limit 20, usage ticking
4. Gateway restart picked up the new env: `hermes gateway restart` (user-unit service)

Consequence observed (expected): Slack 403'd immediately under strict deny until
the slack passthrough rules were added; same for nous/github infra hosts.

## Management UI over Tailscale

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:14321
# one-time tailnet approval at https://login.tailscale.com/f/serve?node=...
# → https://vps-ovh.tail95f2ee.ts.net (tailnet only; 14322 never exposed)
sudo tailscale serve status   # verify
```

## Next: live cutover window (RUNBOOK.md — requires ABXBA present)

1. Stop all Hermes processes + `/proc/*/environ` old-key scan
2. `hermes` user + migrated tree + full-tree path rewrite (venv shebangs included!)
3. System-unit gateway as `hermes` (draft in this repo) + Slack/model verify
4. nftables `inet av_hermes` for hermes uid (auto-revert armed first)
5. Verification suite + external port scan → revoke old key → destroy backups