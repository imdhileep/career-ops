export const DEFAULT_SERVER = "http://127.0.0.1:3000";

/** Only a loopback http(s) URL is accepted: the extension talks to your own machine. */
export function normalizeServer(raw) {
  try {
    const u = new URL(String(raw || "").trim());
    if (!/^https?:$/.test(u.protocol)) return null;
    if (!["127.0.0.1", "localhost"].includes(u.hostname)) return null;
    return u.origin;
  } catch {
    return null;
  }
}

export async function getServer() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return normalizeServer(server) || DEFAULT_SERVER;
}
