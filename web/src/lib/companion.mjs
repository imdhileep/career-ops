// Pure helpers for the browser-extension companion API (/api/companion).
// No I/O here, so `node --test` covers every rule the route relies on.

export const MAX_JD_CHARS = 60_000;
const MAX_FIELD = 300;

function str(v, max = MAX_FIELD) {
  return typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

/**
 * Validate and normalize a job captured by the extension. Page text is
 * untrusted: it is only ever written to a temp file and read as data.
 * @param {unknown} raw
 * @returns {{ok: true, job: {url: string, title: string, company: string, location: string, jdText: string}} | {ok: false, error: string}}
 */
export function normalizeJob(raw) {
  if (!raw || typeof raw !== "object") return { ok: false, error: "job must be an object" };
  const r = /** @type {Record<string, unknown>} */ (raw);
  let url;
  try {
    url = new URL(String(r.url ?? ""));
  } catch {
    return { ok: false, error: "job.url must be a valid URL" };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return { ok: false, error: "job.url must be http(s)" };
  url.hash = "";
  const title = str(r.title);
  if (!title) return { ok: false, error: "job.title is required" };
  const jdText = typeof r.jdText === "string" ? r.jdText.slice(0, MAX_JD_CHARS) : "";
  return {
    ok: true,
    job: { url: url.toString(), title, company: str(r.company) || "?", location: str(r.location), jdText },
  };
}

/**
 * Turn jd-skill-gap.mjs output into a coverage summary. A skill the CV names
 * counts fully, one only mentioned in CV prose counts half; gaps count zero.
 * @param {{existing?: string[], supportedByResume?: string[], gap?: string[]} | null} gap
 */
export function skillCoverage(gap) {
  const existing = Array.isArray(gap?.existing) ? gap.existing : [];
  const supported = Array.isArray(gap?.supportedByResume) ? gap.supportedByResume : [];
  const missing = Array.isArray(gap?.gap) ? gap.gap : [];
  const total = existing.length + supported.length + missing.length;
  const pct = total === 0 ? null : Math.round(((existing.length + supported.length * 0.5) / total) * 100);
  return { pct, total, existing, supported, missing };
}

function cleanUrl(v) {
  const s = str(v);
  if (!s) return "";
  return /^https?:\/\//i.test(s) ? s : `https://${s}`;
}

/**
 * Contact fields for form autofill, from a parsed config/profile.yml.
 * Only fields a job application form asks for; nothing from the narrative.
 * @param {unknown} profile
 */
export function contactFields(profile) {
  const c = profile && typeof profile === "object" && /** @type {any} */ (profile).candidate;
  const cand = c && typeof c === "object" ? c : {};
  const fullName = str(cand.full_name);
  const parts = fullName.split(" ").filter(Boolean);
  return {
    fullName,
    firstName: parts.length > 1 ? parts.slice(0, -1).join(" ") : fullName,
    lastName: parts.length > 1 ? parts[parts.length - 1] : "",
    email: str(cand.email),
    phone: str(cand.phone),
    location: str(cand.location),
    linkedin: cleanUrl(cand.linkedin),
    github: cleanUrl(cand.github),
    portfolio: cleanUrl(cand.portfolio_url),
  };
}

/** Target role phrases from a parsed profile, for the title-fit band. */
export function targetRoles(profile) {
  const p = /** @type {any} */ (profile);
  const primary = p?.target_roles?.primary;
  return Array.isArray(primary) ? primary.filter((x) => typeof x === "string" && x.trim()) : [];
}
