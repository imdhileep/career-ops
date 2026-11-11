#!/usr/bin/env node

/**
 * verify-cv-claims.mjs — Deterministic claim checker (zero tokens, no LLM).
 *
 * The model writes tailored CVs and reports; the same model reviewing its own
 * output tends to agree with itself. This script moves the check out of the
 * model: it compares generated files against the user's source-of-truth files
 * (cv.md, article-digest.md, config/profile.yml) using plain string matching.
 *
 * Checks:
 *   1. METRIC NOT IN SOURCE — every number/metric in a tailored CV
 *      ("40%", "$2M", "3x", "170+", "1,200", "2021") must appear in a source
 *      file. Catches invented or inflated achievements.
 *   2. QUOTE NOT IN SOURCE — every citation of the form
 *         "exact text" — cv.md
 *      must exist word for word (whitespace-insensitive) in the cited file.
 *      Used by evaluation reports (Block B) to anchor claims to real CV lines.
 *
 * Usage:
 *   node verify-cv-claims.mjs <file> [<file>...]          # CV: metrics + quotes
 *   node verify-cv-claims.mjs --quotes-only reports/001-acme-2026-01-01.md
 *   node verify-cv-claims.mjs --source extra.md <file>    # add a source file
 *   node verify-cv-claims.mjs --json <file>               # machine-readable
 *
 * Supported inputs: .html, .tex, .md (anything else is read as plain text).
 * Exit codes: 0 = clean, 2 = problems found, 1 = usage / missing files.
 */

import { readFileSync, existsSync } from 'fs';
import { join, dirname, resolve, relative, isAbsolute, extname } from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;

export const DEFAULT_SOURCES = ['cv.md', 'article-digest.md', 'config/profile.yml'];

// ── Text extraction ─────────────────────────────────────────────

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', ndash: '–', mdash: '—', times: '×', euro: '€', pound: '£', percnt: '%' };

export function htmlToText(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(style|script|head)\b[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>|<\/(p|div|li|h[1-6]|tr|section)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&([a-z]+);/gi, (m, n) => ENTITIES[n.toLowerCase()] ?? m);
}

export function texToText(tex) {
  const start = tex.indexOf('\\begin{document}');
  let body = start >= 0 ? tex.slice(start + '\\begin{document}'.length) : tex;
  return body
    .replace(/(^|[^\\])%.*$/gm, '$1')                      // comments
    .replace(/\{,\}/g, ',')                                 // 1{,}200 thousands separator
    .replace(/\\(?:[vh]space|raisebox|scalebox|resizebox|setlength|addtolength)\*?(\{[^}]*\})+/g, ' ') // layout args
    .replace(/\\href\{[^}]*\}/g, ' ')                       // link targets (keep link text)
    .replace(/\\(%|\$|&|#|_)/g, '$1')                       // escaped chars
    .replace(/\\[a-zA-Z]+\*?(\[[^\]]*\])?/g, ' ')           // commands
    .replace(/[{}]/g, ' ')
    .replace(/~/g, ' ')
    .replace(/--/g, '–');
}

export function extractText(content, filename = '') {
  const ext = extname(filename).toLowerCase();
  if (ext === '.html' || ext === '.htm') return htmlToText(content);
  if (ext === '.tex') return texToText(content);
  return content;
}

// ── Metric tokens ───────────────────────────────────────────────

// number, optional unit; boundaries checked manually so "S3", "EC2", "GPT-4o" are skipped
const NUM_RE = /(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?(?:%|percent\b|×|x\b|k\b|K\b|thousand\b|mm\b|MM\b|m\b|M\b|million\b|bn\b|B\b|billion\b|\+))?/g;
const LAYOUT_UNIT = /^\s?(pt|px|em|ex|in|mm|cm|bp|rem|vh|vw)\b/;

const MULT = { k: 1e3, thousand: 1e3, m: 1e6, mm: 1e6, million: 1e6, bn: 1e9, b: 1e9, billion: 1e9 };

/**
 * Returns [{ raw, key, scalar }] where key encodes value + kind ('%' | 'x' | '')
 * so "$2M", "$2 million" and "2,000,000" all share key "2000000".
 */
export function extractMetrics(text) {
  const out = [];
  NUM_RE.lastIndex = 0;
  let m;
  while ((m = NUM_RE.exec(text)) !== null) {
    const before = text[m.index - 1] ?? '';
    const afterIdx = m.index + m[0].length;
    const after = text[afterIdx] ?? '';
    if (/[A-Za-z0-9_.]/.test(before)) continue;                 // S3, v1.2 tails, EC2
    if (!m[2] && /[A-Za-z]/.test(after)) continue;               // 4o, 10am, 3D
    if (!m[2] && LAYOUT_UNIT.test(text.slice(afterIdx))) continue;
    if (before === '-' && /[A-Za-z]/.test(text[m.index - 2] ?? '')) continue; // GPT-4, ISO-27001 style ids
    const numStr = m[1].replace(/,/g, '');
    let value = Number(numStr);
    if (!Number.isFinite(value)) continue;
    const unitRaw = (m[2] || '').trim();
    const unit = unitRaw.toLowerCase();
    let kind = '';
    if (unit === '%' || unit === 'percent') kind = '%';
    else if (unit === 'x' || unit === '×') kind = 'x';
    else if (MULT[unit]) value *= MULT[unit];
    // bare single digits ("Python 3", "Top 5", list numbers) are too noisy to police
    if (!unitRaw && /^\d$/.test(numStr)) continue;
    const scalar = String(+value.toPrecision(12));
    out.push({ raw: m[0].trim(), key: scalar + kind, scalar });
  }
  return out;
}

// ── Quotes ──────────────────────────────────────────────────────

// "exact text" — cv.md   (straight or curly quotes; -, –, — or "--" separator)
const QUOTE_RE = /["“]([^"”\n]{4,}?)["”]\s*(?:[—–]|--?)\s*`?([\w./-]+\.(?:md|ya?ml|txt))`?/g;

export function extractQuotes(text) {
  const out = [];
  QUOTE_RE.lastIndex = 0;
  let m;
  while ((m = QUOTE_RE.exec(text)) !== null) out.push({ quote: m[1], src: m[2] });
  return out;
}

const norm = s => s.replace(/[*_`]/g, '').replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();

// ── Core check ──────────────────────────────────────────────────

/**
 * @param {{name:string, content:string}} target
 * @param {{name:string, content:string}[]} sources
 * @param {{quotesOnly?:boolean, root?:string, readSource?:(p:string)=>string|null}} opts
 */
export function checkClaims(target, sources, opts = {}) {
  const problems = [];
  const warnings = [];
  const text = extractText(target.content, target.name);

  if (!opts.quotesOnly) {
    const keys = new Set();
    const scalars = new Set();
    for (const s of sources) {
      for (const t of extractMetrics(extractText(s.content, s.name))) {
        keys.add(t.key);
        scalars.add(t.scalar);
      }
    }
    const seen = new Set();
    for (const t of extractMetrics(text)) {
      if (keys.has(t.key) || seen.has(t.raw)) continue;
      seen.add(t.raw);
      if (scalars.has(t.scalar)) warnings.push(`UNIT DIFFERS FROM SOURCE -> "${t.raw}"`);
      else problems.push(`METRIC NOT IN SOURCE -> "${t.raw}"`);
    }
  }

  const root = opts.root ?? ROOT;
  const read = opts.readSource ?? (p => {
    const abs = resolve(root, p);
    const rel = relative(root, abs);
    if (rel.startsWith('..') || isAbsolute(rel) || !existsSync(abs)) return null;
    return readFileSync(abs, 'utf-8');
  });
  const cache = new Map();
  for (const { quote, src } of extractQuotes(text)) {
    if (!cache.has(src)) cache.set(src, read(src));
    const body = cache.get(src);
    if (body == null) problems.push(`QUOTE CITES MISSING FILE -> ${src}`);
    else if (!norm(body).includes(norm(quote))) {
      problems.push(`QUOTE NOT IN SOURCE -> "${quote.length > 70 ? quote.slice(0, 67) + '...' : quote}" (${src})`);
    }
  }

  return { problems, warnings };
}

// ── CLI ─────────────────────────────────────────────────────────

function main(argv) {
  const files = [];
  const extraSources = [];
  let quotesOnly = false;
  let json = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--quotes-only') quotesOnly = true;
    else if (a === '--json') json = true;
    else if (a === '--source') extraSources.push(argv[++i]);
    else if (a === '-h' || a === '--help') { console.log(readFileSync(fileURLToPath(import.meta.url), 'utf-8').split('*/')[0]); return 0; }
    else files.push(a);
  }
  if (files.length === 0) {
    console.error('Usage: node verify-cv-claims.mjs [--quotes-only] [--json] [--source file] <file> [<file>...]');
    return 1;
  }

  const sources = [];
  for (const p of [...DEFAULT_SOURCES, ...extraSources]) {
    const abs = isAbsolute(p) ? p : join(ROOT, p);
    if (existsSync(abs)) sources.push({ name: p, content: readFileSync(abs, 'utf-8') });
  }
  if (!quotesOnly && !sources.some(s => s.name === 'cv.md')) {
    console.error('cv.md not found — nothing to verify metrics against. Run onboarding first.');
    return 1;
  }

  const results = [];
  for (const f of files) {
    if (!existsSync(f)) { console.error(`no such file: ${f}`); return 1; }
    const r = checkClaims({ name: f, content: readFileSync(f, 'utf-8') }, sources, { quotesOnly });
    results.push({ file: f, ...r });
  }

  const total = results.reduce((n, r) => n + r.problems.length, 0);
  if (json) {
    console.log(JSON.stringify({ ok: total === 0, problems: total, results }, null, 2));
  } else {
    for (const r of results) {
      for (const p of r.problems) console.error(`${r.file}: ${p}`);
      for (const w of r.warnings) console.error(`${r.file}: warn: ${w}`);
    }
    console.error(`verify-cv-claims: ${total} problem(s) in ${results.length} file(s)`);
  }
  return total > 0 ? 2 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exit(main(process.argv.slice(2)));
}
