export const BUILD_ID =
  (typeof window !== 'undefined' && window.BUILD_ID) ||
  (typeof document !== 'undefined'
    ? document
        .querySelector('script')
        ?.textContent?.match(/BUILD_ID.+?["'](.+?)["']/)?.[1] || null
    : null);

export function withV(url) {
  if (!url) return url;
  try {
    const u = new URL(url, typeof window !== 'undefined' ? window.location.origin : undefined);
    u.searchParams.set('v', BUILD_ID || Date.now().toString());
    return u.toString();
  } catch (err) {
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}v=${encodeURIComponent(BUILD_ID || Date.now().toString())}`;
  }
}

export async function importV(url) {
  return import(/* @vite-ignore */ withV(url));
}
