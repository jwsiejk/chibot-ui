#!/usr/bin/env python3
import os, sys, subprocess, json, hashlib, time, platform
from pathlib import Path

ART_DIR = Path("artifacts")
ART_DIR.mkdir(exist_ok=True)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def snapshot_tree(root="."):
    files = []
    for dp, dn, fn in os.walk(root):
        if any(seg in dp for seg in (".venv","node_modules","__pycache__","dist-packages","site-packages")):
            continue
        for name in fn:
            p = Path(dp) / name
            if p.is_file():
                files.append(str(p).replace("\\","/"))
    files = sorted(files)
    hashes = {fp: sha256_file(Path(fp)) for fp in files}
    return files, hashes

def run(cmd):
    print(f"\n>>> {cmd}")
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr)
    return p

def main():
    import os
    os.environ.setdefault("RATE_LIMIT_WINDOW_S","0.05")
    os.environ.setdefault("RATE_LIMIT_MAX","100")

    results = {"steps":[]}
    rc = 0
    stages = [
        "python3 scripts/phase6_checks.py",
        "python3 scripts/route_linter.py",
        "python3 scripts/test_api_contract.py",
        "python3 scripts/test_static.py",
        "python3 scripts/test_packaging.py",
    ]
    for s in stages:
        r = run(s)
        results["steps"].append({"cmd": s, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr})
        if r.returncode != 0:
            rc = r.returncode
            break

    # Snapshot after running tests
    files, hashes = snapshot_tree(".")
    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "platform": platform.platform(),
        "result": "PASS" if rc == 0 else "FAIL",
        "stages": [s for s in stages],
        "hashes_sha256": hashes,
    }
    (ART_DIR / "preflight_summary.json").write_text(json.dumps(summary, indent=2))
    (ART_DIR / "preflight_report.txt").write_text("\n\n".join([f"$ {s['cmd']}\n{s['stdout']}" for s in results["steps"]]))
    if rc == 0:
        (ART_DIR / "preflight.ok").write_text(summary["ts"])
    print("\nPRE-FLIGHT RESULT:", "PASS" if rc == 0 else "FAIL")
    sys.exit(rc)

if __name__ == "__main__":
    main()
