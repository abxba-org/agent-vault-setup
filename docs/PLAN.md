# agent-vault for Hermes on vps-1558ffa7

## Goal

Protect Hermes' model-provider credentials so that:

* Real API keys are absent from Hermes' environment, config files, and process environment.
* Hermes can use only explicitly authorized credentials and destinations.
* A compromised Hermes cannot enumerate or retrieve other vault credentials.
* The vault and proxy are never publicly exposed.
* Credential use fails closed at the network layer, not merely by convention.
* The brokered OpenRouter key has an explicit spend/credit limit.

### Phase 1 scope

OpenRouter/model-provider credentials used through standard HTTP(S) authentication.

Out of scope:

* Telegram
* Slack
* URL/path-embedded tokens
* non-HTTP credentials
* automatic secret discovery
* automatic migration of every Hermes secret
* arbitrary TLS interception

This is intentionally partial protection until the other credential types are handled.

### Threat model

The design protects against a compromised or malicious Hermes agent and application-level compromise.

A hostile root account or compromised kernel on the VPS defeats the design.

Because Hermes can still make requests through the brokered credential, the OpenRouter key should be dedicated to Hermes and spend-limited.

---

## Approved amendments & refinements (2026-09-30)

1. **Injection mode = Mode B only.** docs.agent-vault.dev exposes no base-URL/reverse-proxy operation in the open-source Agent Vault; every integration path is HTTPS_PROXY/HTTP_PROXY + transparent CA. Mode A is retired. Mode B carries the extra test burden (HTTP/2, smuggling, cert scope).
2. **CA trust is scoped to the agent process only** (SSL_CERT_FILE, REQUESTS_CA_BUNDLE, NODE_EXTRA_CA_CERTS, CURL_CA_BUNDLE, GIT_SSL_CAINFO, DENO_CERT set by `vault run`), never the system store. Verified: bare curl without the CA env fails TLS against the MITM (fail closed). The Hermes stack (Python httpx/requests, Node, curl) must be verified to honor these in Phase 2.
3. **Hermes user migration is its own runbook + rehearsal**: order = proxy works → migrate Hermes to dedicated user (no docker group, no sudo) → enable firewall. Rehearsed on a copy of ~/.hermes as a rollback-able maintenance window. Never debug both changes at once.
4. **nftables safety protocol**: dedicated table with explicit hook priorities, auto-revert armed (systemd-run --on-active=5m) before any application, network-namespace/VM isolation where possible, re-test after Docker restart and fail2ban reload.
5. **Master password boot path**: preferred = ExecStart wrapper reading $CREDENTIALS_DIRECTORY/master into AGENT_VAULT_MASTER_PASSWORD (server supports --password-stdin, verified); fallback = root-owned 0600 EnvironmentFile=. Either way /proc/<pid>/environ must be unreadable by the Hermes uid.
6. **IP ranges restored** (original paste had CIDRs sanitized to placeholders): 0.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 127.0.0.0/8, 100.64.0.0/10 IPv4; ::1, fc00::/7, fe80::/10, IPv4-mapped, other reserved IPv6.
7. **Test order**: 3 → 4 → 6 → 9 → 11 → 12 → 14 are the core fail-closed batch (privilege separation confirmed inside it, before firewall work); 5/7/8/10/13 follow.

---

# 0. Pre-decisions

## 0.1 Choose the credential injection mode

There are two viable designs.

### Mode A — Base-URL mode

Preferred if Hermes supports it.

Hermes points its OpenRouter API base URL at the local proxy. The proxy makes the external TLS connection itself and injects the credential.

Advantages:

* no custom CA
* no TLS interception
* smaller attack surface
* simpler certificate handling

Requirement:

* Hermes must support a base-URL/API-endpoint override compatible with this architecture.

### Mode B — Allowlisted-host TLS interception

Use only if Mode A is not possible.

The proxy terminates TLS only for explicitly allowlisted hosts. Hermes trusts a CA scoped specifically to Hermes.

Requirements:

* never issue certificates for non-allowlisted hosts
* reject non-allowlisted CONNECT targets
* never use a system-wide trust modification
* test HTTP/2 and request-smuggling behavior
* ensure credential injection is tied to the validated destination, not merely a user-controlled Host/SNI value

**Do not proceed until the injection mode is explicitly chosen and tested in Phase 1.**

---

## 0.2 Confirm privilege separation

Hermes must run as a separate unprivileged Unix user.

Verify:

* Hermes is not root.
* Hermes has no passwordless sudo.
* Hermes is not in a privileged group such as `docker`.
* Hermes cannot read agent-vault's files.
* Hermes cannot read the vault's process environment.
* Hermes cannot ptrace or inspect the vault process.
* Hermes cannot access `/proc/<agent-vault-pid>` in a way that defeats the separation.
* Hermes cannot reach the management interface.

If any of these fail, fix them before proceeding.

---

## 0.3 Confirm upstream details

Before creating allowlist rules, verify the actual OpenRouter API hostname, port, and path from current OpenRouter documentation.

Do not rely on a remembered hostname.

The allowlist should be based on the verified endpoint actually used by Hermes.

---

## 0.4 Key hygiene

Assume the existing Hermes OpenRouter key is compromised because it has previously existed in:

* environment variables
* config files
* shell history
* backups
* process state

Create a **new dedicated OpenRouter key for Hermes**.

Apply the smallest practical spend/credit limit.

Only this new key goes into agent-vault.

After production verification succeeds, revoke the old key.

---

# Architecture

```text
Hermes
  |
  | request + scoped av_agt_ token
  v
127.0.0.1:14322
credential proxy
  |
  | validate agent capability
  | validate service
  | validate destination
  | strip client credentials
  | inject vault credential
  v
verified OpenRouter endpoint
```

agent-vault runs under a dedicated non-root system user.

### Ports

```text
127.0.0.1:14322
credential proxy

127.0.0.1:14321
management API/UI
```

The management interface should preferably use a Unix socket or otherwise be inaccessible to Hermes.

If remote administration is required, expose the management UI through Tailscale rather than binding it broadly.

The credential proxy remains localhost-only.

---

# Network-level enforcement

`HTTPS_PROXY` is not itself a security boundary.

Enforce the boundary using nftables rules tied to Hermes' Unix UID.

Hermes should be permitted to reach only:

1. the credential proxy;
2. explicitly approved `NO_PROXY` infrastructure;
3. DNS, if genuinely required.

Everything else from the Hermes UID is dropped.

The management interface must not be in Hermes' allowed destinations.

This prevents a compromised Hermes from simply unsetting `HTTPS_PROXY` and opening a direct connection.

Important consequence:

> Any Hermes functionality using Telegram, Slack, or other out-of-scope services will also be blocked unless it is explicitly routed through an approved mechanism.

Do not quietly weaken the firewall to accommodate those services.

---

# Authorization model

Use default-deny authorization.

Each agent token represents a capability, for example:

```text
agent: hermes
services:
  - openrouter
```

Each service rule specifies:

* credential
* exact allowed hostname(s)
* allowed port(s)
* protocol
* any applicable path restrictions

The agent token must not be able to:

* enumerate vault credentials
* retrieve raw credentials
* modify credentials
* modify service rules
* modify its own permissions
* access another agent's credentials

Tokens should be:

* scoped
* revocable
* rotatable
* short-lived where supported

---

# Destination and egress security

The proxy must validate the **actual IP address used for the connection**, not merely the supplied hostname.

Block at minimum:

### IPv4

```text
0.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
127.0.0.0/8
100.64.0.0/10
multicast/reserved ranges
```

### IPv6

```text
::1
fc00::/7
fe80::/10
IPv4-mapped forms of blocked addresses
other reserved ranges
```

Explicitly protect against cloud metadata access.

The proxy must not blindly follow redirects.

A redirect must be revalidated against the same destination policy.

Where possible, resolve and validate the destination and connect to the validated address so the address cannot change between validation and connection.

---

# Credential handling

Use SQLite in a dedicated directory:

```text
directory: 0700
files:     0600
owner:     agent-vault
```

Hermes must not be able to read the database.

Credential encryption should use established cryptographic libraries and primitives.

Use:

```text
DEK
  |
  v
KEK wrapping
  |
  v
encrypted SQLite credential data
```

Do not implement custom cryptography.

## Unattended boot

Use systemd `LoadCredential=` with a root-owned `0600` secret file where supported.

The master secret should not be readable by Hermes.

Passwordless plaintext-DEK storage is not acceptable.

The limitation is explicit:

> A root compromise of the same machine defeats this protection.

---

# Logging

Never log:

* Authorization headers
* Proxy-Authorization
* API-key headers
* query-string credentials
* injected credentials
* sensitive request/response bodies

Log only useful audit metadata:

```text
timestamp
agent identity
service
destination
method
status
allow/deny
reason
request ID
```

Logs must not be readable by Hermes.

Set a defined retention policy.

If shipping logs to Alloy/OTLP, verify that the logging pipeline cannot receive credential material.

---

# systemd hardening

Run agent-vault as a dedicated non-root user.

Apply appropriate hardening:

```text
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
```

Restrict write access using `ReadWritePaths=`.

Allow writes only to required data/runtime/log directories.

Use bounded automatic restart.

Hermes should have:

```text
Requires=agent-vault.service
After=agent-vault.service
```

so Hermes cannot start while the credential broker is unavailable.

There must be no automatic fallback to direct API access.

---

# Phase 1 — Sandbox

No production changes.

Use a throwaway container/environment with:

* dummy credentials
* separate vault and agent UIDs
* the intended network restrictions

## Go/no-go rule

Any failed security property must result in one of:

1. fix the implementation;
2. add a compensating control and retest;
3. choose a different implementation/tool.

Do not proceed to production with an unexplained or unmitigated failure.

---

## 1. Verify release

Use a pinned release version.

Verify the artifact using the project's documented attestation mechanism, e.g.:

```text
gh attestation verify ...
```

Do not install an unverified binary on the production VPS.

Review the credential path enough to confirm that the claimed functionality actually exists:

* credential storage
* credential injection
* header stripping
* IP validation
* redirect handling
* per-agent authorization
* attestation/release provenance

---

## 2. Create sandbox vault

Create:

* recognizable dummy OpenRouter credential
* OpenRouter service rule
* Hermes test capability token
* placeholder client credential

---

## 3. Test successful brokering

Using the selected injection mode:

* client contains only the placeholder;
* proxy authenticates the agent;
* proxy authorizes OpenRouter;
* proxy injects the vault credential;
* upstream receives the expected credential;
* agent cannot read the credential.

---

## 4. Test privilege separation

As the Hermes UID verify that it cannot:

* read vault data;
* read the master-secret file;
* read logs;
* ptrace the vault;
* inspect sensitive vault process state;
* access the management socket/API;
* obtain sudo/root through an obvious local path.

---

## 5. Test authorization boundaries

Verify Hermes cannot:

* access another credential;
* enumerate credentials;
* create credentials;
* modify credentials;
* modify service rules;
* modify its own capabilities;
* bypass the service allowlist.

---

## 6. Test egress filtering

Attempt access to:

```text
169.254.169.254
localhost
10.x.x.x
172.16.x.x
192.168.x.x
169.254.169.254
IPv6 loopback
IPv6 private/link-local
IPv4-mapped IPv6 equivalents
```

All must fail.

---

## 7. Test DNS and redirect attacks

Test:

* DNS rebinding;
* hostname resolving to private IP;
* public hostname resolving to forbidden IP;
* public endpoint redirecting to private IP;
* redirecting to an unauthorized public host;
* alternate IP encodings;
* TOCTOU between address validation and connection.

All must fail closed.

---

## 8. Test host/SNI/port confusion

Verify:

* allowed CONNECT target + different Host header;
* allowed target + different SNI;
* allowed hostname on forbidden port;
* credential injection cannot be redirected by Host/SNI manipulation.

For Mode B additionally test:

* HTTP/2;
* request smuggling;
* certificate issuance for non-allowlisted hosts.

---

## 9. Test credential header handling

Send client-controlled:

```text
Authorization
Proxy-Authorization
API-key headers
```

Verify the client cannot override, merge with, or influence the vault credential.

---

## 10. Test credential reflection

Use an echo endpoint.

Verify the credential cannot appear unintentionally in:

* response headers;
* response bodies;
* error messages;
* redirects;
* proxy-generated diagnostics.

---

## 11. Test real traffic characteristics

Test:

* SSE;
* chunked responses;
* long-lived streams;
* concurrent requests;
* large request bodies;
* large response bodies;
* timeout behavior.

Hermes/model traffic must work without bypassing the security boundary.

---

## 12. Test network-level fail closed

With nftables enabled:

* Hermes can reach the proxy;
* Hermes can reach only explicitly approved `NO_PROXY` infrastructure;
* Hermes cannot reach arbitrary internet destinations;
* unsetting `HTTPS_PROXY` does not restore direct access.

---

## 13. Test logging

Search:

* proxy logs;
* system logs;
* process environment;
* crash/core output;
* temporary files

for the dummy credential.

The credential must not appear.

---

## 14. Test failure and recovery

Stop agent-vault.

Expected:

```text
Hermes model request → failure
```

There must be:

* no direct fallback;
* no alternate credential;
* no bypass through another interface.

Restart agent-vault and verify recovery.

Also test system boot ordering.

---

## 15. Destroy sandbox

Delete:

* container;
* credentials;
* tokens;
* vault data;
* temporary logs.

Produce a Phase 1 report.

The report must contain:

* tests performed;
* results;
* limitations;
* discovered vulnerabilities;
* compensating controls;
* chosen injection mode;
* changes required for production.

Then make a separate production go/no-go decision.

---

# Phase 2 — Production

Requires explicit approval after Phase 1.

## 16. Pre-change backup

Back up Hermes' relevant configuration.

The backup contains the old real key, so:

* make it root-only;
* use `0600`;
* keep it out of shared backup locations;
* do not synchronize it unnecessarily;
* destroy it once rollback is no longer required and the old key has been revoked.

---

## 17. Install

Install the attestation-verified pinned binary.

Create the dedicated `agent-vault` user.

Apply:

* systemd hardening;
* `LoadCredential=`;
* filesystem permissions;
* log permissions;
* log rotation.

---

## 18. Create production vault

Create the vault.

Enter the **new spend-limited OpenRouter key** through the supported secure mechanism.

Never put it into:

* shell history;
* Hermes environment;
* Hermes configuration;
* scripts;
* command-line arguments;
* ordinary logs.

---

## 19. Configure service rules

Authorize only the verified OpenRouter endpoint.

Use explicit hostname and port rules.

No broad rules such as:

```text
*.com
0.0.0.0/0
```

---

## 20. Mint Hermes capability

Create a dedicated Hermes capability token:

```text
agent: hermes
services: [openrouter]
```

Enable expiry if supported.

---

## 21. Wire Hermes

### Mode A

Set Hermes' model-provider base URL to the local proxy endpoint.

### Mode B

Set:

```text
HTTPS_PROXY=http://127.0.0.1:14322
```

and install the Hermes-scoped CA.

In both modes:

* supply the capability token;
* replace the real key with a placeholder;
* configure only necessary `NO_PROXY` entries;
* enable nftables UID restrictions;
* enable systemd dependency ordering.

Do not create a broad proxy bypass.

---

## 22. Verify production

Inspect:

* Hermes environment;
* Hermes configuration;
* running process environment;
* systemd environment;
* vault data permissions;
* vault logs;
* proxy logs.

Confirm the real credential exists only within the intended vault path/process.

Perform:

* normal model request;
* streaming request;
* authorization-boundary test;
* egress test.

Confirm:

```text
Hermes
  →
localhost proxy
  →
authorized OpenRouter endpoint
```

Perform an external scan to confirm ports 14321 and 14322 are not publicly reachable.

---

## 23. Revoke old key

Only after production verification passes:

1. revoke the old OpenRouter key;
2. securely destroy the pre-change backup;
3. confirm Hermes continues operating with the new brokered credential.

---

# Rollback

Rollback must never be automatic.

If rollback is required before the old key is revoked:

1. Stop Hermes.
2. Restore the protected backup.
3. Remove proxy/base-URL overrides.
4. Remove the Hermes UID nftables rules.
5. Restart Hermes.
6. Verify normal operation.

If the old key has already been revoked, create a new temporary key rather than resurrecting the revoked one.

Rehearse rollback during Phase 1/production preparation.

---

# Explicit Phase 1 exclusions

Not handled by this implementation:

* Telegram tokens
* Slack tokens
* URL/path-embedded credentials
* non-HTTP credential mechanisms
* arbitrary TLS interception
* automatic credential discovery
* automatic migration of every Hermes secret

These require service-specific handling in a later phase.

---

# Production acceptance criteria

Production is acceptable only when all are true:

1. Real OpenRouter credentials are absent from Hermes environment/config/process state.
2. Hermes cannot read vault data, secrets, or logs.
3. Hermes cannot ptrace or inspect the vault process.
4. Hermes can make authorized model requests, including streaming.
5. Hermes cannot retrieve the raw credential.
6. The agent token cannot access unrelated credentials.
7. The agent token cannot modify authorization rules.
8. Unauthorized destinations and ports are blocked.
9. Private, link-local, and metadata destinations are blocked.
10. DNS rebinding is blocked.
11. Redirect-based bypasses are blocked.
12. Host/SNI manipulation cannot redirect credential injection.
13. Client-supplied credentials cannot override vault credentials.
14. Credentials do not appear in logs or error/response paths.
15. Vault failure causes requests to fail closed.
16. nftables prevents direct network bypass.
17. Management and proxy ports are not publicly reachable.
18. Management access is restricted to localhost/Tailscale.
19. The OpenRouter key is spend-limited.
20. The old OpenRouter key is revoked.
21. Rollback has been rehearsed.

# Decision gate

**Phase 1 first.**

Do not modify production Hermes credentials until the sandbox passes the complete acceptance suite.

After the Phase 1 report, make a separate go/no-go decision for production.

# Status addendum — 2026-09-30 (post-cutover, live state)

- Cutover EXECUTED (dedicated hermes user, system gateway, nftables, verification suite) — full record in SETUP-LOG, procedures in RUNBOOK.
- Criterion 16 (nftables prevents direct bypass): the cutover table was transient and lost at a reboot — re-created and PERSISTED (`/etc/av-hermes.nft` + oneshot `av-hermes-nftables.service`, enabled). Deviation: allow rule for 127.0.0.1:14321 added (AGENT_VAULT_ADDR points at mgmt; gateway must re-fetch post-restart).
- Criteria 17/18 (ports not public; mgmt localhost/Tailscale only): now enforced AND toggleable — `scripts/vault-mgmt` (deployed /usr/local/bin/vault-mgmt), default OFF, temp-ON auto-reverts 30m unless --persist, funnel asserted off, public-IP silence verified.
- Criterion 20 (old key revoked): OPEN — accepted deviation; still the blocking precondition for any migration window.
- Owner mgmt auth: Bearer token from `/var/lib/agent-vault/.agent-vault/session.json`; owner login password is separate from the master file. Batch rule API: POST /v1/vaults/hermes/services {"services":[{name,host,auth:{type}}]}, upsert by name.
- Service rules: 38 entries (openrouter bearer; all else passthrough) incl. pypi, npm, composio, firecrawl, HF, Debian/Ubuntu repos, openai/anthropic APIs. Duplicate hosts under different rule names — dedupe optional.
- Open items: criterion 20 revocation; tailnet ACL scoping vps-ovh:443 while ON; agent-token rotation (leaked in an agent session log); optional rule dedupe.