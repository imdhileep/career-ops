import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as yaml from "js-yaml";
import { careerOpsRoot, rootScript } from "@/lib/career-ops";
import { resolveCodeRoot } from "@/lib/core/code-root.mjs";
import { getProfilePath } from "@/lib/core/profile-path.mjs";
import { titleFit } from "@/lib/title-fit.mjs";
import { readJsonObject } from "@/lib/json-body";
import { contactFields, normalizeJob, skillCoverage, targetRoles } from "@/lib/companion.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Companion API for the career-ops browser extension (extension/).
//
// Reached only through the proxy's origin guard: a chrome-extension:// caller is
// refused unless the user lists its origin in CAREER_OPS_ALLOWED_ORIGINS. Zero
// tokens: the match score is the core's regex skill-gap check plus the title
// band, never an AI run. Saving a job goes through /api/explore/add, the same
// writer the Explore page uses, so this route writes nothing.

function readProfile(): unknown {
  try {
    return yaml.load(fs.readFileSync(getProfilePath(careerOpsRoot()), "utf8"));
  } catch {
    return null;
  }
}

/** GET: who the extension is talking to, plus contact fields for autofill. */
export async function GET() {
  const profile = readProfile();
  return Response.json({
    ok: true,
    profileFound: profile != null,
    contact: contactFields(profile),
    targets: targetRoles(profile),
  });
}

function runSkillGap(jdFile: string): Promise<unknown | null> {
  const script = rootScript("jd-skill-gap");
  if (!fs.existsSync(script)) return Promise.resolve(null);
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      [script, jdFile],
      { cwd: resolveCodeRoot(process.cwd(), process.env), timeout: 20_000, maxBuffer: 4 << 20, windowsHide: true },
      (_err, out) => {
        try {
          const start = (out || "").indexOf("{");
          resolve(start < 0 ? null : JSON.parse(out.slice(start)));
        } catch {
          resolve(null);
        }
      },
    );
  });
}

/** POST {job}: zero-token match for the job on the current page. */
export async function POST(req: Request) {
  const body = await readJsonObject<{ job?: unknown }>(req);
  if (!body) return Response.json({ error: "bad json" }, { status: 400 });
  const parsed = normalizeJob(body.job);
  if (!parsed.ok) return Response.json({ error: parsed.error }, { status: 400 });
  const { job } = parsed;

  const fit = titleFit(job.title, targetRoles(readProfile()));

  let coverage = skillCoverage(null);
  const cvExists = fs.existsSync(path.join(careerOpsRoot(), "cv.md"));
  if (cvExists && job.jdText.trim()) {
    // The JD is page text: written to a private temp file and read as data only.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "co-companion-"));
    try {
      const file = path.join(dir, "jd.md");
      fs.writeFileSync(file, `# ${job.title}\n\n${job.jdText}\n`, { mode: 0o600 });
      coverage = skillCoverage((await runSkillGap(file)) as Parameters<typeof skillCoverage>[0]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }

  return Response.json({ ok: true, job: { url: job.url, title: job.title, company: job.company }, fit, coverage, cvFound: cvExists });
}
