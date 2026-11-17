import os
import re

surfaces = ["ui.py", "main.py", "gui", "dashboard/static"]
pattern = re.compile(r"\b(jarvis|mark[- ]?lvii)\b", re.IGNORECASE)

findings = []
for s in surfaces:
    if os.path.isfile(s):
        files = [s]
    else:
        files = [os.path.join(r, f) for r, _, fns in os.walk(s) for f in fns]

    for f in files:
        if f.endswith((".py", ".html")):
            with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                for idx, line in enumerate(fp, 1):
                    clean = line.strip()
                    if clean.startswith("#") or clean.startswith('"""') or clean.startswith("'''"):
                        continue
                    if any(k in clean for k in [
                        "JarvisUI", "JarvisLive", "shutdown_jarvis",
                        "jarvis_token", "jarvis_key", "jarvis_device_token",
                        "hey_jarvis", "jarvis.ico", "jarvis.key", "jarvis.crt"
                    ]):
                        continue
                    if pattern.search(line):
                        findings.append(f"{f}:{idx} -> {clean[:90]}")

print(f"Total user-facing surface findings: {len(findings)}")
for it in findings:
    print(" ", it)
