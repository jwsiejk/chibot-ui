#!/usr/bin/env python3
import os, re, sys

root = os.path.dirname(os.path.dirname(__file__))
admin = os.path.join(root, "templates", "admin.html")
html = open(admin, "r", encoding="utf-8", errors="ignore").read()

required = [
  r'id="cfg-audio_worklet_enabled"',
  r'id="cfg-vad_attack_ms"',
  r'id="cfg-vad_release_ms"',
  r'id="cfg-vad_dbfs_threshold"',
  r'id="cfgAudioSave"',
  r'aria-controls="tab-config-audio"'
]

for pat in required:
  if re.search(pat, html) is None:
    raise SystemExit(f"Missing UI element: {pat}")
print("PH14 UI: inputs present and wired")
