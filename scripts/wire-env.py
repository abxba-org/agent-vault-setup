#!/usr/bin/env python3
"""Wire Hermes .env to agent-vault: replace real OpenRouter key with placeholder,
append proxy env block. Run as root. Secrets read from /etc/agent-vault, never printed."""
import re, os, sys

ENV = "/home/ubuntu/.hermes/.env"
TOKEN_FILE = "/etc/agent-vault/hermes-agent-token"
CA = "/home/ubuntu/.hermes/av-ca.pem"

token = open(TOKEN_FILE).read().strip()
text = open(ENV).read()

# Replace any existing OPENROUTER_API_KEY value with the placeholder
new, n = re.subn(r"(?m)^OPENROUTER_API_KEY=.*$", "OPENROUTER_API_KEY=__openrouter_api_key__", text)
assert n >= 1, "OPENROUTER_API_KEY line not found"

# Drop any pre-existing agent-vault block (idempotent re-runs)
block_keys = ("AGENT_VAULT_ADDR", "AGENT_VAULT_VAULT", "AGENT_VAULT_TOKEN", "AV_PROXY_BLOCK_START")
lines = [l for l in new.splitlines()
         if not any(l.startswith(k) for k in block_keys) and "AV_PROXY_BLOCK_END" not in l]

proxy_url = f"http://{token}:hermes@127.0.0.1:14322"
block = [
    "# AV_PROXY_BLOCK_START — agent-vault credential brokering",
    "AGENT_VAULT_ADDR=http://127.0.0.1:14321",
    "AGENT_VAULT_VAULT=hermes",
    f"AGENT_VAULT_TOKEN={token}",
    f"HTTPS_PROXY={proxy_url}",
    f"HTTP_PROXY={proxy_url}",
    "NO_PROXY=localhost,127.0.0.1",
    "NODE_USE_ENV_PROXY=1",
    f"SSL_CERT_FILE={CA}",
    f"REQUESTS_CA_BUNDLE={CA}",
    f"CURL_CA_BUNDLE={CA}",
    f"NODE_EXTRA_CA_CERTS={CA}",
    "# AV_PROXY_BLOCK_END",
]
open(ENV, "w").write("\n".join(lines + block) + "\n")
os.chmod(ENV, 0o600)
os.chown(ENV, 1000, 1000)
print("env wired: OPENROUTER_API_KEY replaced (n=%d), proxy block appended" % n)