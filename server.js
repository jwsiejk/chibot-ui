// server.js
const express = require("express");
const path = require("path");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();

// --- Proxy: send all /api/* calls to your backend ---
app.use(
  "/api",
  createProxyMiddleware({
    target: "https://ask-chip.onrender.com",
    changeOrigin: true,
    ws: true,                       // if your API uses websockets
    pathRewrite: { "^/api": "/api" },
    proxyTimeout: 300000,           // handle long requests
    timeout: 300000
  })
);

// --- Serve your built frontend ---
app.use(express.static("build"));   // change to "dist" if needed
app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "build", "index.html"));
});

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`UI + API proxy running on :${port}`));
