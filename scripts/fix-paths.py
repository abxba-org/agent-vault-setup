#!/usr/bin/env python3
"""Rewrite /home/ubuntu/.hermes references to a target home in migrated Hermes trees.
Usage: sudo python3 fix-paths.py /home/hermes/.hermes
Only rewrites inside the target tree; prints every file changed."""
import os, sys

target = sys.argv[1].rstrip("/")
old = "/home/ubuntu/.hermes"
new = target
changed = []
for root, dirs, files in os.walk(target):
    # skip heavy binary/cache dirs where path rewrites are pointless or risky
    dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", "cache", "tools", "installs", "venv")]
    for f in files:
        if not f.endswith((".env", ".yaml", ".yml", ".json", ".toml", ".sh", ".service", ".conf")) and f != ".env":
            continue
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 5_000_000:
                continue
            t = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if old in t:
            open(p, "w", encoding="utf-8").write(t.replace(old, new))
            changed.append(p)
print(f"rewrote {old} -> {new} in {len(changed)} files:")
for p in changed:
    print(" ", p)