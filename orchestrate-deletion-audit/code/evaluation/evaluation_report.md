# Evaluation report — Ghost Data Still Exists deletion audit agent

Reproduce with `python3 code/evaluation/main.py --write` (writes the raw tables to
`evaluation_metrics.md`). Gold reference: `dataset/sample_output.csv` (32 public
cases). The 518 evaluation requests have no public answers; their outputs were
checked structurally (see "Full-dataset checks").

## 1. Metrics on the 32 public sample cases (final system)

| Metric | Definition | Value |
|---|---|---|
| Case-level status accuracy | `deletion_status` equal to gold | **100.0 %** (32/32) |
| Step precision | predicted step tuples `(phase, system_id, resource_id, operation, state, evidence_id)` that are in gold | **99.5 %** |
| Step recall | gold step tuples that were predicted | **97.9 %** |
| Step F1 | harmonic mean | **98.7 %** |
| Field accuracy – `state` | on steps aligned by `(phase, system_id, resource_id, operation)` | **100.0 %** |
| Field accuracy – `evidence_id` | same alignment | **100.0 %** |
| Resource precision / recall | identity resolution: `(system_id, resource_id)` sets | **99.1 % / 96.4 %** |
| Sequence accuracy (exact) | whole `steps_json` array identical (order included) | **90.6 %** (29/32) |
| Sequence similarity (LCS) | mean LCS(gold, pred) / max length | **96.3 %** |

Confusion (gold → predicted): COMPLETE→COMPLETE 10, INCOMPLETE→INCOMPLETE 22.
The public samples contain no NOT_APPLICABLE case; that path is covered by
custom cases 1–2 and by the 10 non-deletion requests in the evaluation set.

### Ablations (same metrics, one component switched off)

| Variant | Status acc | Step F1 | Exact sequence |
|---|---|---|---|
| Full system | 100.0 % | 98.7 % | 90.6 % |
| No request-window filter (use all evidence before `audit_at`) | 100.0 % | 97.4 % | 84.4 % |
| No workflow linking (identifier keys only) | 100.0 % | 92.5 % | 37.5 % |

Workflow linking is the single most valuable component: most account
profiles and every already-anonymised record appear in evidence **without** any
identifier, so they can only be tied to the request through the deletion
worker's activity.

## 2. Custom evaluation cases (created for this project)

Each case is a synthetic request derived from public data that isolates one
behaviour; the check is executed by `code/evaluation/main.py`.

| # | Case | What it probes | Expected | Result |
|---|---|---|---|---|
| 1 | `custom_01_access_request` | case_21 identity, text rewritten as an access request that says "do not delete" | `NOT_APPLICABLE`, `[]` | PASS |
| 2 | `custom_02_marketing_only` | marketing opt-out scoped with "only" (contains the word *remove*) | `NOT_APPLICABLE` | PASS |
| 3 | `custom_03_historical_email` | case_29 submitted with the subject's *old* email in upper case | resolves to `user_29`, still finds `search_1993` | PASS |
| 4 | `custom_04_decoy_email` | case_28: a search document for the look-alike `smartinez6461.jr@gmail.com` exists | decoy never attributed | PASS |
| 5 | `custom_05_audit_cutoff` | case_24 with `audit_at` moved before the evidence capture | evidence captured after `audit_at` never cited | PASS |
| 6 | `custom_06_ordering` | case_30 INCOMPLETE plan | phase order, `DISABLE_DATA_FLOW` before any delete, consecutive `step_no`, empty evidence for future steps | PASS |
| 7 | `custom_07_tombstone` | case_31 account tombstone | retained and verified `REDACTED`, never deleted | PASS |
| 8 | `custom_08_retention` | case_09 security audit record within retention | `VERIFY_RECORD_PRESENT`, no delete | PASS |

## 3. Remaining sample mismatches (3 cases)

| Case | Gap | Why it is not fixed |
|---|---|---|
| case_18, case_20 | gold verifies a retained `audit_record_*` (`audit_record_6645`, `audit_record_5569`) | these ids occur in **no** participant-visible file or activity row, so there is nothing to link; inventing ids would be guessing |
| case_04 | gold uses `account_004` / `analytics_901` | `account_004` appears nowhere in the evidence; the workflow linker picks the only plausible worker submission in the window (`account_631`). `analytics_901` has no identifiers and no worker activity |

## 4. Failures found during development and the fix for each

Sample exact-match progression: 7/32 → 19 → 23 → 28 → **29/32**.

| # | Failure observed | Root cause | Fix |
|---|---|---|---|
| 1 | Accounts, audit records missing in most cases | evidence only references them by resource id; the identifier sits on the trace's *start* log line | join log lines on `trace_id`; completion lines inherit the lookup key |
| 2 | Audit record judged absent | a sibling `cache_lookup result=miss` line was inherited as the record's result | inherit state fields only from lines about the same resource |
| 3 | Search hits not linked | JSON export nests the hit document under `hits.documents` with the same id | merge nested document fields into the parent observation |
| 4 | Follow-up message marked NOT_APPLICABLE ("confirm the profile is gone") | intent rules only knew imperative deletion verbs | added verification / follow-up cues; explicit negation cues still win |
| 5 | Accounts mentioned only by the deletion worker never linked | no identifier anywhere | workflow linker: submission result mirrors reported status, global one-to-one assignment, seconds-offset scoring of follow-up actions |
| 6 | Anonymised analytics / redacted tickets missing in COMPLETE cases | records carry no identifiers by design | attach worker policy actions to the request whose submission they are scheduled from (unique offset fit only) |
| 7 | Old, unrelated evidence about the same person (earlier request) leaked in | linking used every artifact before `audit_at` | restrict facts to the request window `requested_at … audit_at` |
| 8 | Records known only from a bundle exported after `audit_at` were lost | captured-after-audit files were dropped entirely | separate *visibility* (fact observed by `audit_at`) from *citability* (artifact captured by `audit_at`) |
| 9 | COMPLETE verifications had no evidence for deleted profiles | absence was never stated explicitly | full captures emit *scope* records; a profile missing from a capture is proven absent (never overrides an explicit present reading) |
| 10 | Wrong evidence cited for connectors | agreement check compared irrelevant attributes (exposure of a connector) | compare only the attribute the treatment depends on |
| 11 | Legacy contact-index rows redacted instead of deleted | treated as support conversations | contact-index / secondary-identifier category, specific categories beat system defaults |
| 12 | Every syslog account linked to every user (full run) | RFC5424 fields off by one → all lines shared trace id `-` | fixed field positions; caught by a full-run sanity check (cases with >1 account) |
| 13 | Workflow links leaked between two requests of the same person | links keyed by user | links keyed by case |
| 14 | Catalog link timestamps outranked the artifact itself | data-catalog activity treated as a state reading | catalog links only link; aliases (statement numbers, file names) map the PDF / screenshot to the resource |

## 5. Full-dataset checks (518 evaluation requests)

* 518 rows, header `case_id,deletion_status,steps_json`, sorted, valid JSON,
  consecutive `step_no` from 1, phase order respected (asserted in a check script).
* Status distribution: INCOMPLETE 356, COMPLETE 152, NOT_APPLICABLE 10 (exactly
  the ten requests whose text asks for access, export, correction, opt-out or login help).
* Every deletion case has at least one step; 497/508 COMPLETE/INCOMPLETE cases
  include an account-profile step (the rest are one-off cases whose evidence
  never names an account).
* All COMPLETE verification steps and INCOMPLETE findings cite a `file_id` or
  `activity_id` captured at or before `audit_at`, except 1 step with no
  citable artifact.

## 6. Known limitations

* Records that exist only in the organisers' truth (no visible id) cannot be
  reported; this caps recall for some INCOMPLETE cases (as in case_18/20).
* Workflow linking relies on scheduling regularities of the deletion worker; when
  several requests share a batch and no content evidence separates them, the
  assignment can be wrong.
* The public samples were also used during development, so the sample metrics
  are optimistic; the ablations and custom cases are the more honest signal.
