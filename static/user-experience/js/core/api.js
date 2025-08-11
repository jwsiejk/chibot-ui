// core/api.js — fetch wrapper
export async function j(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts
  });
  const ct = r.headers.get("content-type") || "";
  let data = null; try { data = ct.includes("application/json") ? await r.json() : null; } catch {}
  return { ok: r.ok, status: r.status, data, raw: r };
}
