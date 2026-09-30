# LIVE CUTOVER RUNBOOK — Hermes → dedicated `hermes` user + nftables

Execute in the maintenance window with ABXBA present (provider console/rescue tested first).
Every step has VERIFY + UNDO. Estimated window: ~25–40 min (rehearsal timings in rehearsal.log).
Run as the admin login (ubuntu via SSH). Do NOT run unattended.

## Pre-window checklist (before touching anything)
- [ ] USER: out-of-band access confirmed (OVH KVM/rescue console reachable, admin SSH + sudo tested from a second device)
- [ ] Full pristine backup: `sudo rsync -a /home/ubuntu/.hermes/ /root/cutover-backup/.hermes/ && sudo chmod -R go-rwx /root/cutover-backup`
      UNDO-source for everything below.
- [ ] Key-rotation verified: vault credential ≠ backup old key (compare via
      `vault credential get` as owner vs backup, equality-only). NOTE: OpenRouter's
      `GET /api/v1/key` returns USER-AGGREGATE label/usage, not per-key data — it
      cannot fingerprint a key. Fingerprint via the dashboard, or the equality test.
- [ ] NOTE: dotenv-loaded vars are invisible in /proc/PID/environ — the definitive direct-path closure is the nftables table (step 4), not the environ scan alone.

## Step 1 — Stop ALL Hermes processes
```
hermes gateway stop                          # user-unit gateway
pkill -u ubuntu -f '/bin/herdr' ; pkill -u ubuntu -f 'hermes-agent' ; pkill -u ubuntu -f 'hermes '
sleep 2
```
VERIFY: `pgrep -u ubuntu -af 'hermes|herdr'` → empty; scan envs:
`for p in /proc/[0-9]*; do sudo grep -aq "sk-or-" $p/environ 2>/dev/null && echo "LEAK: $p"; done` → nothing
UNDO: `hermes gateway start` (user unit still intact until step 3 disables it)

## Step 2 — Create hermes user + migrate tree
```
sudo useradd -m -s /bin/bash hermes          # NO extra groups, NO sudo
sudo rsync -a /root/cutover-backup/.hermes/ /home/hermes/.hermes/
sudo chown -R hermes:hermes /home/hermes/.hermes
sudo chmod 700 /home/hermes/.hermes
sudo install -m 700 -d /home/hermes/.local/bin
sudo install -m 755 /home/ubuntu/.local/bin/herdr /home/hermes/.local/bin/herdr   # verified: static ELF, copyable
# FULL-TREE path rewrite (REHEARSAL-VALIDATED): venv shebangs, pyvenv.cfg, launchers, .env, config —
# every text file under the tree containing /home/ubuntu/.hermes. The per-file fix-paths.py alone is NOT enough.
sudo bash -c 'grep -rl --binary-files=without-match "/home/ubuntu/.hermes" /home/hermes/.hermes 2>/dev/null | while read f; do python3 -c "
import sys
p = sys.argv[1]
t = open(p, encoding=\"utf-8\", errors=\"surrogateescape\").read()
open(p, \"w\", encoding=\"utf-8\", errors=\"surrogateescape\").write(t.replace(\"/home/ubuntu/.hermes\", \"/home/hermes/.hermes\"))
" "$f"; done'
```
REHEARSAL TIMINGS: rsync 3.5G = 45s · chown = 2s · full-tree rewrite = 30s · FIRST hermes run as new user = 2m11s (tool prep; warm runs fast). Budget ~5 min for step 2, several minutes for gateway cold start.
VERIFY: `sudo grep -rn "/home/ubuntu" /home/hermes/.hermes/.env /home/hermes/.hermes/config.yaml` → empty
`sudo grep -n "OPENROUTER_API_KEY" /home/hermes/.hermes/.env` → placeholder only
`id hermes` → no docker group, no sudo group
UNDO: original ubuntu tree never touched; `sudo userdel -r hermes` reverts cleanly.

## Step 3 — System units for gateway (+ herdr) as hermes user
```
# /etc/systemd/system/hermes-gateway.service (drafted at /opt/agent-vault-docs/hermes-gateway.system-unit)
# User=hermes, Environment=HERMES_HOME=/home/hermes/.hermes, ExecStart=/home/hermes/.hermes/hermes-agent/.hermes/bin/hermes gateway run
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-gateway
systemctl --user -M ubuntu@ disable hermes-gateway.service 2>/dev/null || systemctl --user disable hermes-gateway.service  # kill the old user-unit path
```
VERIFY: `systemctl status hermes-gateway` active as USER=hermes; journal shows Slack connected;
USER: send a Slack :eyes: message and confirm gateway reacts; model call:
`sudo -u hermes env -i HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes PATH="/home/hermes/.hermes/hermes-agent/.hermes/bin:/usr/local/bin:/usr/bin:/bin" TERM=xterm hermes -z "say CUTOVER-OK"`
UNDO: disable system unit, re-enable user unit, restart. (Session tools like herdr: relaunch as hermes; CLI sessions via `sudo -u hermes hermes` or herdr as hermes.)

## Step 4 — nftables for hermes UID (AFTER gateway verified working)
```
sudo systemd-run --on-active=10m --unit=av-nft-revert nft delete table inet av_hermes   # ARM REVERT FIRST
sudo nft add table inet av_hermes
sudo nft add chain inet av_hermes output '{ type filter hook output priority 10; policy accept; }'
sudo nft add rule inet av_hermes output skuid $(id -u hermes) ip daddr 127.0.0.1 tcp dport 14322 counter accept
sudo nft add rule inet av_hermes output skuid $(id -u hermes) udp dport 53 counter accept
sudo nft add rule inet av_hermes output skuid $(id -u hermes) tcp dport 53 counter accept
sudo nft add rule inet av_hermes output skuid $(id -u hermes) counter drop
```
VERIFY: as hermes uid — direct `curl https://httpbin.org/get` FAILS; proxied model call WORKS;
ubuntu/SSH unaffected; gateway still connected.
RESILIENCE: `sudo systemctl restart docker` + `sudo fail2ban-client reload` → re-run VERIFY both.
DISARM: `sudo systemctl stop av-nft-revert.timer` only after all verifies pass.
UNDO: `sudo nft delete table inet av_hermes`.

## Step 5 — Verification suite (acceptance criteria)
1. `sudo grep -rn "sk-or-" /home/hermes/.hermes/.env /home/hermes/.hermes/config.yaml` → placeholder/none
2. environ scan (step 1 command) → no sk-or- anywhere
3. Vault request log: model traffic → openrouter.ai:443 status 200; NO direct-to-openrouter from hermes uid (firewall proves)
4. USER: from an EXTERNAL device: `nc -zv -w3 <VPS_PUBLIC_IP> 14321` and `14322` → both MUST refuse; provider firewall confirms
5. Spend limit: `GET /api/v1/key` through proxy — usage > 0 and ticking; limit = 20
6. Old key: after revocation, `GET /api/v1/key` with old key → 401

## Step 6 — Revoke old key (USER, OpenRouter dashboard)
Immediately after steps 1–3 verify clean (no soak period per ABXBA decision). Then confirm: proxied model call still 200; old-key API call 401.
Rollback after revocation = mint a fresh temporary key and enter into vault; NEVER resurrect the revoked one.

## Step 7 — Cleanup (after ABXBA confirms stable)
- destroy `/root/av-prechange-backup` and `/root/cutover-backup` (they contain the old key)
- remove cron restic considerations: restic backs up only CODE + docs (verified — no ~/.hermes)
- update PLAN.md threat model: passthrough hosts (slack.com, *.slack.com, portal/hermes-agent.nousresearch.com, raw.githubusercontent.com) are data-egress paths by design; Slack tokens remain in hermes' .env until a later phase brokers them
- final: docs at /opt/agent-vault-docs (PLAN.md, PHASE1-REPORT.md, RUNBOOK.md, rehearsal.log)