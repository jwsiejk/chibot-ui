#!/usr/bin/env node
"use strict";

/**
 * Regression repro script for the outbound payload guard.
 *
 * Run with `node scripts/ws_client_guard_repro.js` to verify that array payloads
 * with a `type` property are rejected before serialization.
 */

function isTypedObjectPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  if (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
    return false;
  }
  return true;
}

function validateOutboundPayload(payload, { rawPayload = payload, source = "wsclient" } = {}) {
  if (!isTypedObjectPayload(payload)) {
    return true;
  }
  const structureTag = Object.prototype.toString.call(payload);
  const isPlainJsonObject = structureTag === "[object Object]";
  if (!isPlainJsonObject) {
    const structure = Array.isArray(payload)
      ? "Array"
      : structureTag.slice(8, -1) || "Unknown";
    const keys = Object.keys(payload || {});
    const diagnostic = {
      keys: keys.slice(0, 6),
      payload,
      raw: rawPayload,
      source,
      structure,
    };
    console.warn("WSClient send skipped payload with non type-preserving structure", diagnostic);
    return false;
  }
  const type = payload && typeof payload.type === "string" ? payload.type.trim() : "";
  if (type.length > 0) {
    return true;
  }
  console.warn("WSClient send skipped object payload without type", {
    keys: Object.keys(payload || {}).slice(0, 6),
    payload,
    raw: rawPayload,
    source,
  });
  return false;
}

const arrayPayload = ["foo", "bar"];
arrayPayload.type = "client.ping";
const objectPayload = { type: "client.ping" };

console.log("Array payload allowed?", validateOutboundPayload(arrayPayload, { source: "repro_script" }));
console.log("Object payload allowed?", validateOutboundPayload(objectPayload, { source: "repro_script" }));
