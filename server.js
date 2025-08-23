// server.js — robust, self-diagnosing proxy (CommonJS)
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();

// === 1) Configure target via env var (no more hardcoding) ===
// In Render, set TARGET to your API's full base URL, e.g.
//   https://ask-chip.onrender.com        (or your actual API service URL)
const TARGET = process.env.TARGET || "https://ask-chip.onrender.com";

// === 2) Minimal request logging so you can see what's happening ===
app.use((req, _res, next) => {
  console.log(`[ui] ${req.method} ${req.url}`);
  next();
});

// === 3) Friendly root so "/" doesn't 404 ===
app.get("/", (_req, res) => {
  res
    .type("text/plain")
    .send(
`chibot-ui proxy is running.

Target: ${TARGET}

Try one of these (depending on your API):
- POST  /api/chat        -> forwards to ${TARGET}/chat
- POST  /api-keep/chat   -> forwards to ${TARGET}/api/chat

Diagnostics:
- GET   /_whoami         -> shows current TARGET
`)
});

// === 4) Diagnostics endpoint ===
app.get("/_whoami", (_req, res) => {
  res.json({ target: TARGET });
});

// === 5) Proxy variant A: strip the "/api" prefix ===
//     /api/chat    ->  ${TARGET}/chat
//     /api/voice   ->  ${TARGET}/voice
app.use(
  "/api",
  createProxyMiddleware({
    target: TARGET,
    changeOrigin: true,
    ws: true,
    pathRewrite: { "^/api": "" },
    proxyTimeout: 300000,
    timeout: 300000,
  })
);

// === 6) Proxy variant B: KEEP the "/api" prefix ===
//     /api-keep/chat  ->  ${TARGET}/api/chat
//     /api-keep/health->  ${TARGET}/api/health
app.use(
  "/api-keep",
  createProxyMiddleware({
    target: TARGET,
    changeOrigin: true,
    ws: true,
    pathRewrite: { "^/api-keep": "/api" },
    proxyTimeout: 300000,
    timeout: 300000,
  })
);

// === 7) Final: start server ===
const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`UI proxy listening on :${port} -> ${TARGET}`));
