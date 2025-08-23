// server.js (CommonJS)
const express = require("express");
const path = require("path");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();

/**
 * Proxy: forward all /api/* requests to your backend API.
 * Example: GET /api/health -> https://ask-chip.onrender.com/api/health
 */
app.use(
  "/api",
  createProxyMiddleware({
    target: "https://ask-chip.onrender.com",
    changeOrigin: true,
    ws: true,                       // allow websockets if you add them later
    pathRewrite: { "^/api": "/api" },
    proxyTimeout: 300000,           // long-running streams are fine
    timeout: 300000
  })
);

/**
 * Static files (optional):
 * If you have a built UI, put it in a folder named "build" (or change below).
 * Serving this is harmless even if you only need the proxy.
 */
app.use(express.static("build"));
app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "build", "index.html"));
});

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`UI + API proxy running on :${port}`));
