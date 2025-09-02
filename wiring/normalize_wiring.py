# scripts/normalize_wiring.py
import pathlib
root = pathlib.Path("wiring")
for fp in root.glob("*"):
    if fp.suffix not in {".md",".csv"}: continue
    s = fp.read_text(encoding="utf-8", errors="ignore")
    s = s.replace("\\", "/").replace("\r\n","\n").replace("\r","\n")
    fp.write_text(s, encoding="utf-8")
print("Normalized wiring/*.md,*.csv to POSIX + LF")
