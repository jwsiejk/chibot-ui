#!/usr/bin/env python3
import os, re, csv, sys
from collections import defaultdict
from pathlib import PurePosixPath
def posix(p): 
    return str(PurePosixPath(str(p)))

root = sys.argv[1] if len(sys.argv) > 1 else "."

route_re = re.compile(r"""@(?:[A-Za-z_][A-Za-z0-9_]*\.)?route\(\s*['"]([^'"]+)['"]\s*(?:,\s*methods\s*=\s*\[([^\]]+)\])?\)""")
http_dec_re = re.compile(r"@(?:[A-Za-z_][A-Za-z0-9_]*)\.(get|post|put|delete|patch|options|head)\(\s*['\"]([^'\"]+)['\"]\s*\)")
method_list_re = re.compile(r"""['"]([A-Z]+)['"]""")

fetch_re = re.compile(r"""fetch\(\s*(['"])(/[^'"]*?)\1\s*(?:,\s*\{(.*?)\})?\s*\)""", re.S)
axios_re = re.compile(r"""axios\.(get|post|put|delete|patch)\(\s*(['"])(/[^'"]*?)\2""")
xhr_re = re.compile(r"""new\s+XMLHttpRequest\(\).*?open\(\s*(['"])([A-Z]+)\1\s*,\s*(['"])(/[^'"]*?)\3""", re.S)
eventsource_re = re.compile(r"""new\s+EventSource\(\s*(['"])(/[^'"]*?)\1""")
get_by_id_re = re.compile(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)""")
query_sel_re = re.compile(r"""querySelector\(\s*['"]([^'"]+)['"]\s*\)""")
html_id_re = re.compile(r"""id\s*=\s*['"]([^'"]+)['"]""")

def norm_path(s):
    s = re.sub(r'//+', '/', s)
    return s[:-1] if len(s)>1 and s.endswith('/') else s

code_files, html_files = [], []
for r, d, fs in os.walk(root):
    for f in fs:
        p = os.path.join(r, f)
        ext = f.lower().split(".")[-1]
        if ext in ["py","js","ts","jsx","tsx"]:
            code_files.append(p)
        elif ext in ["html","htm"]:
            html_files.append(p)

backend_routes = []
frontend_calls = []
dom_ids = defaultdict(set)
html_ids = defaultdict(set)

def read(p):
    try:
        return open(p,'r',encoding='utf-8',errors='ignore').read()
    except:
        return ""

for p in code_files:
    if not p.endswith(".py"): continue
    txt = read(p)
    for m in route_re.finditer(txt):
        path = norm_path(m.group(1))
        methods_block = m.group(2)
        methods = set(method_list_re.findall(methods_block)) if methods_block else {"GET"}
        backend_routes.append({"path": path, "methods": sorted(methods), "file": os.path.relpath(p, root)})
    for m in http_dec_re.finditer(txt):
        method = m.group(1).upper()
        path = norm_path(m.group(2))
        backend_routes.append({"path": path, "methods": [method], "file": os.path.relpath(p, root)})

for p in code_files:
    if not p.endswith((".js",".ts",".jsx",".tsx")): continue
    txt = read(p)
    for m in fetch_re.finditer(txt):
        path = norm_path(m.group(2)); options = m.group(3) or ""
        mm = re.search(r"""method\s*:\s*['"]([A-Z]+)['"]""", options)
        method = mm.group(1) if mm else "GET"
        frontend_calls.append({"lib":"fetch","method":method,"path":path,"file":os.path.relpath(p, root)})
    for m in axios_re.finditer(txt):
        httpm, _, path = m.groups()
        frontend_calls.append({"lib":"axios","method":httpm.upper(),"path":norm_path(path),"file":os.path.relpath(p, root)})
    for m in xhr_re.finditer(txt):
        httpm, _, path = m.groups()
        frontend_calls.append({"lib":"xhr","method":httpm.upper(),"path":norm_path(path),"file":os.path.relpath(p, root)})
    for m in eventsource_re.finditer(txt):
        path = norm_path(m.group(1))
        frontend_calls.append({"lib":"eventsource","method":"GET","path":path,"file":os.path.relpath(p, root)})
    for m in get_by_id_re.finditer(txt):
        dom_ids[m.group(1)].add(os.path.relpath(p, root))
    for m in query_sel_re.finditer(txt):
        sel = m.group(1)
        if sel.startswith("#"):
            dom_ids[sel[1:]].add(os.path.relpath(p, root))

for p in html_files:
    txt = read(p)
    for m in html_id_re.finditer(txt):
        html_ids[m.group(1)].add(os.path.relpath(p, root))

# Build maps
from collections import defaultdict
be_paths = defaultdict(set)
for r in backend_routes:
    for mth in r["methods"]:
        be_paths[(r["path"], mth)].add(r["file"])
fe_paths = defaultdict(set)
for c in frontend_calls:
    fe_paths[(c["path"], c["method"])].add(c["file"])

# Mismatches
fe_missing = []
for (path, mth), fileset in fe_paths.items():
    if (path, mth) not in be_paths:
        any_method = any(k[0]==path for k in be_paths.keys())
        fe_missing.append({
            "method": mth, "path": path, "frontend_files": "; ".join(sorted(fileset)), "path_exists_with_other_methods": "yes" if any_method else "no"
        })
be_unreferenced = []
for (path, mth), fileset in be_paths.items():
    if (path, mth) not in fe_paths:
        any_front = any(k[0]==path for k in fe_paths.keys())
        be_unreferenced.append({
            "method": mth, "path": path, "backend_files": "; ".join(sorted(fileset)), "path_used_with_other_methods": "yes" if any_front else "no"
        })

dom_missing = []
for id_, fileset in dom_ids.items():
    if id_ not in html_ids:
        dom_missing.append({"id": id_, "js_files": "; ".join(sorted(fileset))})
html_orphans = []
for id_, fileset in html_ids.items():
    if id_ not in dom_ids:
        html_orphans.append({"id": id_, "html_files": "; ".join(sorted(fileset))})

outdir = os.path.join(root, "wiring")
os.makedirs(outdir, exist_ok=True)

def write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)

write_csv(os.path.join(outdir, "frontend_missing_backend.csv"), fe_missing, ["method","path","frontend_files","path_exists_with_other_methods"])
write_csv(os.path.join(outdir, "backend_unreferenced.csv"), be_unreferenced, ["method","path","backend_files","path_used_with_other_methods"])
write_csv(os.path.join(outdir, "dom_ids_missing_in_html.csv"), dom_missing, ["id","js_files"])
write_csv(os.path.join(outdir, "html_ids_not_used_by_js.csv"), html_orphans, ["id","html_files"])

report = []
report.append("# Ask Chip Wiring Report")
report.append("")
report.append("NOTE: Static scan; blueprint url_prefix may not be reflected (e.g., '/email/send' vs '/api/email/send').")
report.append("")
report.append("## Frontend calls without backend (top 100)")
for r in fe_missing[:100]:
    report.append(f"- `{r['method']}` {r['path']} — {r['frontend_files']}" + (" (path exists with other methods)" if r['path_exists_with_other_methods']=='yes' else ""))
if len(fe_missing) > 100:
    report.append(f"... and {len(fe_missing)-100} more")

report.append("\n## Backend routes with no frontend callers (top 100)")
for r in be_unreferenced[:100]:
    report.append(f"- `{r['method']}` {r['path']} — {r['backend_files']}" + (" (path used with other methods)" if r['path_used_with_other_methods']=='yes' else ""))
if len(be_unreferenced) > 100:
    report.append(f"... and {len(be_unreferenced)-100} more")

with open(os.path.join(outdir, "askchip_wiring_report.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(report))

print("Wiring report written to ./wiring/")
