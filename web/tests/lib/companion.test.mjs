import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeJob, skillCoverage, contactFields, targetRoles, MAX_JD_CHARS } from "../../src/lib/companion.mjs";

test("normalizeJob accepts a job and strips the URL fragment", () => {
  const r = normalizeJob({ url: "https://boards.greenhouse.io/acme/jobs/1#app", title: "  Senior  Engineer ", company: "Acme", jdText: "x" });
  assert.equal(r.ok, true);
  assert.equal(r.job.url, "https://boards.greenhouse.io/acme/jobs/1");
  assert.equal(r.job.title, "Senior Engineer");
});

test("normalizeJob rejects bad input", () => {
  assert.equal(normalizeJob(null).ok, false);
  assert.equal(normalizeJob({ url: "javascript:alert(1)", title: "x" }).ok, false);
  assert.equal(normalizeJob({ url: "file:///etc/passwd", title: "x" }).ok, false);
  assert.equal(normalizeJob({ url: "https://a.com", title: "" }).ok, false);
});

test("normalizeJob defaults company to ? and caps JD text", () => {
  const r = normalizeJob({ url: "https://a.com/j", title: "T", jdText: "a".repeat(MAX_JD_CHARS + 50) });
  assert.equal(r.job.company, "?");
  assert.equal(r.job.jdText.length, MAX_JD_CHARS);
});

test("skillCoverage weights named skills fully and prose mentions half", () => {
  const c = skillCoverage({ existing: ["Python", "Go"], supportedByResume: ["LLMs"], gap: ["Terraform"] });
  assert.equal(c.pct, 63); // (2 + 0.5) / 4
  assert.equal(c.total, 4);
  assert.deepEqual(c.missing, ["Terraform"]);
});

test("skillCoverage returns null pct when nothing was extracted", () => {
  assert.equal(skillCoverage(null).pct, null);
  assert.equal(skillCoverage({ existing: [], gap: [] }).pct, null);
});

test("contactFields splits names and normalizes profile URLs", () => {
  const f = contactFields({ candidate: { full_name: "Mary Ann Lee", email: "m@x.dev", linkedin: "linkedin.com/in/m", portfolio_url: "https://m.dev" } });
  assert.equal(f.firstName, "Mary Ann");
  assert.equal(f.lastName, "Lee");
  assert.equal(f.linkedin, "https://linkedin.com/in/m");
  assert.equal(f.portfolio, "https://m.dev");
  assert.equal(f.phone, "");
});

test("contactFields and targetRoles tolerate a missing profile", () => {
  assert.equal(contactFields(null).fullName, "");
  assert.deepEqual(targetRoles(undefined), []);
  assert.deepEqual(targetRoles({ target_roles: { primary: ["ML Engineer", "", 5] } }), ["ML Engineer"]);
});
