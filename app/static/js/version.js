// Version helper for static assets.
// This MUST stay in sync with:
// - window.BUILD_ID injected by the HTML template
// - sw.js, which precaches /static/...js?v={{ BUILD_ID }}

// Return the current build id, or null if not set.
export function getBuildId() {
  try {
    if (
      typeof window !== "undefined" &&
      typeof window.BUILD_ID === "string" &&
      window.BUILD_ID
    ) {
      return window.BUILD_ID;
    }
  } catch (_) {
    // ignore
  }
  return null;
}

// Append ?v=<BUILD_ID> (or &v= if there is already a query string).
export function withVersion(url) {
  const buildId = getBuildId();
  if (!buildId) {
    return url;
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}v=${encodeURIComponent(buildId)}`;
}

// Dynamic import that prefers the versioned URL and retries once
// on failure before giving up.
export async function importV(path, attempt = 1) {
  const url = withVersion(path);
  try {
    return await import(/* @vite-ignore */ url);
  } catch (err) {
    if (attempt >= 2) {
      throw err;
    }
    try {
      console.warn("importV failed, retrying once", { url, attempt, err });
    } catch (_) {
      // ignore logging failures
    }
    return importV(path, attempt + 1);
  }
}

// Expose the current BUILD_ID for any consumers that care.
export const BUILD_ID = getBuildId();

// Default export for compatibility with any existing default import usage.
export default {
  getBuildId,
  withVersion,
  importV,
  BUILD_ID,
};
