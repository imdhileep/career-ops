# Ghost Data Still Exists — Deletion Audit Agent

HackerRank Orchestrate (October 2026). For every request in
`dataset/deletion_requests.csv` the agent decides whether deletion was
requested, reconstructs what happened to the subject's data across every
system **as of `audit_at`**, applies `dataset/deletion_rules.csv`, and emits
`COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` plus the ordered findings,
remediation and verification steps (`output.csv`).

## Quick start

```bash
# Python 3.9+; standard library only (pdfplumber / anthropic are optional)
python3 code/main.py                                   # -> output.csv (518 rows)
python3 code/evaluation/main.py                        # metrics on the 32 public samples + custom cases
python3 code/evaluation/main.py --write                # also refresh code/evaluation/evaluation_metrics.md
python3 code/main.py --trace traces.jsonl              # per-case reasoning trace (links, states, evidence)
```

The full run is deterministic, offline, takes a few seconds and makes **zero
paid model calls** (see `code/evaluation/usage_report.md`). Optional live model
paths switch on automatically when `ANTHROPIC_API_KEY` is set:

| Env var | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | enables a live vision-model call for screenshots/PDFs missing from the cache, and the LLM fallback for ambiguous request intent |
| `DELETION_AUDIT_VISION_MODEL` | vision model id (required for live vision calls) |
| `DELETION_AUDIT_TEXT_MODEL` | intent fallback model id (required for the LLM fallback) |

## Architecture

```
dataset/ ──► evidence.py ──► Observation[] ──► workflow.py (global) ──► auditor.py (per case) ──► output.csv
              │  logs (6 formats, trace-id join)                │  intent.py   request authorises deletion?
              │  JSON exports / snapshot bundles               │  identity.py current + historical identifiers
              │  SQLite snapshots (primary + replicas, scopes) │  link        content keys + workflow + relations
              │  text handovers, activity stream               │  state.py    state at audit_at, evidence to cite
              └─ media.py: PDFs (text) + screenshots (VLM       │  policy      deletion_rules.csv -> treatment
                 transcripts cached in media_cache.json) └─ planner     ordered FINDING/REMEDIATION/VERIFICATION
```

1. **Evidence normalisation (`code/agent/evidence.py`, `media.py`)** – every
   artifact becomes `Observation(source_id, captured_at, observed_at, system,
   resource_id, fields, keys)`. Logs in six different layouts are parsed and
   joined on `trace_id`, so a completion line inherits the lookup key of its
   start line. SQL captures are executed in in-memory SQLite; every table
   (primary, regional/reporting/failover replicas) yields observations, and a
   full capture of a store also yields a *scope* record so absence-by-omission
   can be proven. Screenshots were transcribed once with a vision-language model using
   `code/prompts/media_extraction.md`; transcripts are cached by file hash.
2. **Intent (`intent.py`)** – multilingual rule set (EN/ES/PT/FR/JA/KO …) with
   explicit non-deletion scoping cues (access, export, correction, marketing
   "only", "do not close"). LLM fallback only for texts with no cue at all.
3. **Identity (`identity.py`)** – the request email (any case, current or
   historical) resolves to a `user_id`; the identifier set includes every
   email, phone and email hash from `user_audit_log.csv`. Matching is exact
   after normalisation, so look-alike decoys (`x.jr@…`) never link.
4. **Linking** – a resource belongs to the subject when an observation inside
   the request window (`requested_at` … `audit_at`) carries one of the
   subject's identifiers, when the data catalog links it, when it is a
   downstream copy of a linked record (`source_resource_id`, connector
   repopulation, restore jobs), or through **workflow linking**
   (`workflow.py`): the deletion worker's *primary profile submission* is
   matched to the request (result mirrors the reported status), solved as a
   global one-to-one assignment across all 550 requests, and follow-up actions
   with no identifiers (already-anonymised analytics, redacted tickets) are
   attached through their scheduling offset from that submission.
5. **State at `audit_at` (`state.py`, `Auditor.reconstruct`)** – the latest
   reading wins; readings taken at the same instant are OR-ed (a replica copy
   keeps a record alive). Workflow statuses and activity `reported_result`
   values are treated as claims, never as state; only readings (lookups, scans,
   snapshots, connector checks) count. The cited evidence is the most recent
   artifact captured by `audit_at` that agrees with the reconstructed state.
6. **Policy** – data category from record type / collection / event (support
   *attachment* vs *conversation*, legacy contact indexes → secondary
   identifier, …) mapped through `deletion_rules.csv`: DELETE, REDACT,
   ANONYMIZE, RETAIN (security log within retention, active legal hold,
   tombstone keeps only `user_id`), DISABLE flows, BLOCK restores.
7. **Planner** – findings only for violations; INCOMPLETE cases get the full
   remediation plan (flows disabled and restores blocked before records are
   changed) and verification of every in-scope record; COMPLETE cases get only
   evidence-backed verification. Order: phase → operation table order →
   `system_id` → `resource_id`; consecutive `step_no`; future work has
   `evidence_id = ""`.

## Evaluation

`code/evaluation/main.py` scores predictions against `dataset/sample_output.csv`
(case status accuracy, step precision/recall/F1, field accuracy, resource-level
identity recall, exact-sequence and LCS sequence similarity), runs ablations
(no request window, no workflow linking) and eight custom behavioural cases.
Results and the failure analysis are in `code/evaluation/evaluation_report.md`;
token/cost accounting in `code/evaluation/usage_report.md`.

## Repository layout

```
code/main.py                    entry point (writes output.csv)
code/agent/evidence.py          artifact parsers -> observations (+ scopes)
code/agent/media.py             PDF text + screenshot transcripts (cache / live vision)
code/agent/media_cache.json     cached vision-model transcripts keyed by sha256
code/agent/identity.py          identity resolution (current + history)
code/agent/intent.py            request intent classification
code/agent/workflow.py          deletion-worker workflow linking (global assignment)
code/agent/state.py             observation -> state reading, data category
code/agent/auditor.py           linking, state at audit_at, policy, step planning
code/agent/usage.py             model usage / cost accounting
code/prompts/*.md               prompts for the optional model calls
code/evaluation/main.py         evaluation workflow
code/evaluation/*.md            evaluation and usage reports
```

Challenge page: https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
