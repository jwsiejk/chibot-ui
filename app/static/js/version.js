// Stable, guaranteed exports for dynamic module loader.

// Always export a getBuildId function
export function getBuildId() {
  try {
    return window?.ASKCHIP_BUILD_ID || null;
  } catch (_) {
    return null;
  }
}

// Always export withVersion — appends ?v=BUILD_ID or returns raw URL
export function withVersion(url) {
  try {
    const id = getBuildId();
    if (!id) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}v=${id}`;
  } catch (_) {
    return url;
  }
}

// Always export importV — reliable dynamic import wrapper
export async function importV(path) {
  try {
    const vpath = withVersion(path);
    return await import(/* @vite-ignore */ vpath);
  } catch (err) {
    console.warn("version.importV fallback", { path, err: String(err) });
    return await import(/* @vite-ignore */ path);
  }
}

// Default export for compatibility
export default {
  getBuildId,
  withVersion,
  importV
};
