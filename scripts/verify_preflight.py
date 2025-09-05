#!/usr/bin/env python3
import os, sys, json, hashlib
from pathlib import Path

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    art = Path("artifacts")
    summ = art / "preflight_summary.json"
    okf  = art / "preflight.ok"
    if not summ.exists() or not okf.exists():
        print("FAIL: preflight artifacts missing (artifacts/preflight_summary.json or artifacts/preflight.ok)")
        sys.exit(1)
    data = json.loads(summ.read_text())
    if data.get("result") != "PASS":
        print("FAIL: preflight summary result is not PASS")
        sys.exit(1)
    expected = data.get("hashes_sha256") or {}
    # Recompute hashes of current tree
    current = {}
    for dp, dn, fn in os.walk("."):
        if any(seg in dp for seg in (".venv","node_modules","__pycache__","dist-packages","site-packages")):
            continue
        for name in fn:
            p = Path(dp) / name
            if p.is_file():
                current[str(p).replace("\\","/")] = sha256_file(p)
    # Compare subset (only expect that files recorded in preflight match now)
    mismatches = []
    for fp, h in expected.items():
        p = Path(fp)
        if not p.exists():
            mismatches.append((fp, "missing_now"))
        else:
            ch = sha256_file(p)
            if ch != h:
                mismatches.append((fp, "hash_changed"))
    if mismatches:
        print("FAIL: tree changed since preflight:")
        for fp, why in mismatches[:20]:
            print("  -", why, fp)
        sys.exit(1)
    print("PASS: preflight artifacts verified (hashes match, result PASS)")
    sys.exit(0)

if __name__ == "__main__":
    main()
