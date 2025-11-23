export function getBuildId() {
  if (typeof window !== "undefined" && typeof window.BUILD_ID === "string" && window.BUILD_ID) {
    return window.BUILD_ID;
  }
  return null;
}

export function withVersion(url) {
  const buildId = getBuildId();
  if (!buildId) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}v=${encodeURIComponent(buildId)}`;
}

export async function importV(path, attempt = 1) {
  const url = withVersion(path);
  try {
    return await import(/* @vite-ignore */ url);
  } catch (err) {
    if (attempt >= 2) throw err;
    try {
      console.warn("importV failed, retrying once", { url, attempt, err });
    } catch (_) {}
    return importV(path, attempt + 1);
  }
}

export const BUILD_ID = getBuildId();
