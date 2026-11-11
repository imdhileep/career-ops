// End-to-end test: real Chromium with the unpacked extension, a real career-ops
// web server, and a local fixture job page.
//
//   cd web && npm run build && cd ..
//   node extension/tests/e2e.mjs
//
// Uses a throwaway data root (example CV + profile), so your own data is never read.
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const { getProfilePath } = await import(new URL("../../path-resolver.mjs", import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = path.dirname(fileURLToPath(import.meta.url));
const extDir = fs.realpathSync(path.join(here, ".."));
const repo = path.dirname(extDir);

// Chrome derives an unpacked extension's id from its absolute path.
const extId = [...createHash("sha256").update(extDir).digest("hex").slice(0, 32)]
  .map((h) => String.fromCharCode(97 + parseInt(h, 16)))
  .join("");
const origin = `chrome-extension://${extId}`;

const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), "co-ext-e2e-"));
fs.mkdirSync(path.join(dataRoot, "config"));
fs.mkdirSync(path.join(dataRoot, "data"));
fs.copyFileSync(path.join(repo, "examples/cv-example.md"), path.join(dataRoot, "cv.md"));
fs.copyFileSync(path.join(repo, "config/profile.example.yml"), getProfilePath(dataRoot));
fs.copyFileSync(path.join(repo, "templates/portals.example.yml"), path.join(dataRoot, "portals.yml"));

const JOB = `<!doctype html><html><head><title>Senior AI Engineer - Acme</title>
<script type="application/ld+json">${JSON.stringify({
  "@context": "https://schema.org", "@type": "JobPosting", title: "Senior AI Engineer",
  hiringOrganization: { "@type": "Organization", name: "Acme AI" },
  jobLocation: { "@type": "Place", address: { addressLocality: "Austin", addressRegion: "TX", addressCountry: "US" } },
  description: "<h2>Requirements</h2><ul><li>Python and PyTorch</li><li>Kubernetes, Terraform, AWS</li><li>LLMs and RAG</li></ul>",
})}</script></head><body>
<h1>Senior AI Engineer</h1>
<form onsubmit="window.__submitted = true; return false;">
  <label for="fn">First Name</label><input id="fn" name="job_application[first_name]">
  <label for="ln">Last Name</label><input id="ln" name="job_application[last_name]">
  <label>Email <input type="email" name="email"></label>
  <input name="phone" placeholder="Phone">
  <label for="li">LinkedIn Profile</label><input id="li" name="urls[LinkedIn]">
  <label for="why">Why us?</label><textarea id="why" name="why">Already typed by me</textarea>
  <input type="file" name="resume">
  <button type="submit">Submit application</button>
</form></body></html>`;

const fixture = http.createServer((_q, r) => { r.setHeader("content-type", "text/html"); r.end(JOB); });
await new Promise((ok) => fixture.listen(4555, "127.0.0.1", ok));

const PORT = 3124;
const web = spawn(process.execPath, ["server.mjs", "start"], {
  cwd: path.join(repo, "web"),
  env: { ...process.env, PORT: String(PORT), CAREER_OPS_ROOT: dataRoot, CAREER_OPS_ALLOWED_ORIGINS: origin },
  stdio: ["ignore", "pipe", "pipe"],
  detached: true, // own process group, so the Next.js child dies with it
});
const stopWeb = () => { try { process.kill(-web.pid, "SIGTERM"); } catch {} };
// Never leave a server behind, even when a step below throws.
process.on("exit", () => { stopWeb(); fixture.close(); });
let webLog = "";
await new Promise((ok, fail) => {
  const t = setTimeout(() => fail(new Error(`web server did not start:\n${webLog}`)), 30000);
  const watch = (d) => { webLog += d; if (/Ready/.test(String(d))) { clearTimeout(t); ok(); } };
  web.stdout.on("data", watch);
  web.stderr.on("data", watch);
  web.on("exit", (code) => { clearTimeout(t); fail(new Error(`web server exited (${code}):\n${webLog}`)); });
});

const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "co-ext-profile-"));
const ctx = await chromium.launchPersistentContext(profileDir, {
  headless: true,
  // Extensions need full Chromium (the headless shell can't load them).
  // CHROMIUM_PATH overrides; otherwise Playwright's own Chromium is used.
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : { channel: "chromium" }),
  args: [`--disable-extensions-except=${extDir}`, `--load-extension=${extDir}`],
});
let failed = false;
let popup = null;
try {
  // Point the extension at the test server.
  const opts = await ctx.newPage();
  await opts.goto(`${origin}/src/options.html`);
  await opts.evaluate((s) => chrome.storage.sync.set({ server: s }), `http://127.0.0.1:${PORT}`);

  const jobPage = await ctx.newPage();
  await jobPage.goto("http://127.0.0.1:4555/jobs/1");
  const tabId = await opts.evaluate(async () => (await chrome.tabs.query({ url: "http://127.0.0.1:4555/*" }))[0].id);

  popup = await ctx.newPage();
  popup.on("console", (m) => console.log("[popup]", m.text()));
  popup.on("pageerror", (e) => console.log("[popup error]", e.message));
  await popup.goto(`${origin}/src/popup.html?tab=${tabId}`);
  await popup.waitForFunction(() => document.getElementById("coverage").textContent !== "–", null, { timeout: 20000 });
  const shown = await popup.evaluate(() => ({
    title: document.getElementById("title").textContent,
    meta: document.getElementById("meta").textContent,
    coverage: document.getElementById("coverage").textContent,
    fit: document.getElementById("fit").textContent,
    missing: document.getElementById("missing").textContent,
  }));
  console.log("popup:", shown);
  assert.equal(shown.title, "Senior AI Engineer");
  assert.match(shown.meta, /Acme AI · Austin, TX, US/);
  assert.match(shown.coverage, /^\d+%$/);
  assert.equal(shown.fit, "Strong"); // profile.example.yml targets "Senior AI Engineer"

  await popup.click("#fill");
  await popup.waitForFunction(() => /Filled/.test(document.getElementById("status").textContent), null, { timeout: 10000 });
  const form = await jobPage.evaluate(() => ({
    fn: document.querySelector("#fn").value,
    ln: document.querySelector("#ln").value,
    email: document.querySelector('[name="email"]').value,
    phone: document.querySelector('[name="phone"]').value,
    li: document.querySelector("#li").value,
    why: document.querySelector("#why").value,
    submitted: Boolean(window.__submitted),
  }));
  console.log("form:", form);
  assert.equal(form.fn, "Jane");
  assert.equal(form.ln, "Smith");
  assert.equal(form.email, "jane@example.com");
  assert.equal(form.phone, "+1-555-0123");
  assert.equal(form.li, "https://linkedin.com/in/janesmith");
  assert.equal(form.why, "Already typed by me", "never overwrites what the user typed");
  assert.equal(form.submitted, false, "never submits");

  await popup.click("#save");
  await popup.waitForFunction(() => /Saved|Not saved/.test(document.getElementById("status").textContent), null, { timeout: 20000 });
  const status = await popup.textContent("#status");
  console.log("save:", status);
  assert.match(status, /^Saved/);
  const pipeline = fs.readFileSync(path.join(dataRoot, "data/pipeline.md"), "utf8");
  assert.match(pipeline, /127\.0\.0\.1:4555\/jobs\/1/);
  if (process.env.E2E_SCREENSHOT) {
    await popup.setViewportSize({ width: 340, height: 420 });
    await popup.locator("body").screenshot({ path: process.env.E2E_SCREENSHOT });
    await jobPage.screenshot({ path: process.env.E2E_SCREENSHOT.replace(/\.png$/, "-form.png") });
  }

  // A random website must still be refused by the app's origin guard.
  const res = await fetch(`http://127.0.0.1:${PORT}/api/companion`, { headers: { origin: "https://evil.example", "sec-fetch-site": "cross-site" } });
  assert.equal(res.status, 403);
  console.log("PASS: popup match, autofill (no submit, no overwrite), save to pipeline, foreign origin refused");
} catch (e) {
  failed = true;
  console.error("FAIL:", e.message);
  if (popup) console.error("popup showed:", (await popup.evaluate(() => document.body.innerText).catch(() => "?")).replace(/\s+/g, " "));
} finally {
  await ctx.close();
  stopWeb();
  fixture.close();
  fs.rmSync(dataRoot, { recursive: true, force: true });
  fs.rmSync(profileDir, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
