#!/usr/bin/env python3
"""Evaluation workflow for the deletion audit agent.

    python3 code/evaluation/main.py            # metrics + custom cases + ablations
    python3 code/evaluation/main.py --write    # also regenerate evaluation_report.md

Metrics (computed against dataset/sample_output.csv):
  * case-level  : deletion_status accuracy (+ confusion matrix)
  * step-level  : precision / recall / F1 over full step tuples
                  (phase, system_id, resource_id, operation, state, evidence_id)
  * field-level : for steps matched on (phase, system_id, resource_id, operation),
                  accuracy of state and evidence_id; resource-level identity recall
  * sequence    : exact steps_json equality and mean normalised LCS similarity
Custom cases are synthetic requests derived from the public dataset that probe a
single behaviour each (intent, identity history, decoys, audit cutoff, ...).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
ROOT = os.path.dirname(CODE)
sys.path.insert(0, CODE)

from main import build_auditor, read_requests  # noqa: E402
from agent.usage import Usage  # noqa: E402

DATASET = os.path.join(ROOT, "dataset")
FULL = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
ALIGN = ("phase", "system_id", "resource_id", "operation")


def tup(step, keys):
    return tuple(step[k] for k in keys)


def lcs(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def score(pred: dict, gold: dict) -> dict:
    status_ok = conf = 0
    confusion = Counter()
    tp = fp = fn = 0
    field_hits = Counter()
    field_total = 0
    exact = 0
    lcs_sum = 0.0
    res_tp = res_fp = res_fn = 0
    per_case = {}
    for cid, g in gold.items():
        p = pred.get(cid, {"status": "MISSING", "steps": []})
        confusion[(g["status"], p["status"])] += 1
        ok = p["status"] == g["status"]
        status_ok += ok
        gs, ps = [tup(s, FULL) for s in g["steps"]], [tup(s, FULL) for s in p["steps"]]
        gset, pset = set(gs), set(ps)
        tp += len(gset & pset)
        fp += len(pset - gset)
        fn += len(gset - pset)
        galign = {tup(s, ALIGN): s for s in g["steps"]}
        for s in p["steps"]:
            k = tup(s, ALIGN)
            if k in galign:
                field_total += 1
                for f in ("state", "evidence_id"):
                    field_hits[f] += s[f] == galign[k][f]
        is_exact = ok and gs == ps
        exact += is_exact
        denom = max(len(gs), len(ps))
        sim = 1.0 if denom == 0 else lcs(gs, ps) / denom
        lcs_sum += sim
        gres = {(s["system_id"], s["resource_id"]) for s in g["steps"]}
        pres = {(s["system_id"], s["resource_id"]) for s in p["steps"]}
        res_tp += len(gres & pres)
        res_fp += len(pres - gres)
        res_fn += len(gres - pres)
        per_case[cid] = {"status_ok": ok, "exact": is_exact, "lcs": round(sim, 3),
                         "missing": sorted(gset - pset), "extra": sorted(pset - gset)}
    n = len(gold)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "confusion": {f"{a}->{b}": c for (a, b), c in sorted(confusion.items())},
        "step_precision": prec, "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "field_state_accuracy": field_hits["state"] / field_total if field_total else 0,
        "field_evidence_accuracy": field_hits["evidence_id"] / field_total if field_total else 0,
        "aligned_steps": field_total,
        "resource_precision": res_tp / (res_tp + res_fp) if res_tp + res_fp else 1.0,
        "resource_recall": res_tp / (res_tp + res_fn) if res_tp + res_fn else 1.0,
        "sequence_exact": exact / n,
        "sequence_lcs": lcs_sum / n,
        "per_case": per_case,
    }


def load_gold(path):
    out = {}
    for r in read_requests(path):
        out[r["case_id"]] = {"status": r["deletion_status"], "steps": json.loads(r["steps_json"])}
    return out


def predict(auditor, requests):
    return {r["case_id"]: auditor.audit_case(r) for r in requests}


# --------------------------------------------------------------------------- #
# Custom evaluation cases (built from public data; each probes one behaviour)
# --------------------------------------------------------------------------- #

def custom_cases(samples):
    by = {r["case_id"]: r for r in samples}
    cases = []

    c = copy.deepcopy(by["case_21"])
    c.update(case_id="custom_01_access_request",
             request_text="Could you send me a copy of the personal data you hold? I still use the account, please do not delete anything.")
    cases.append((c, "NOT_APPLICABLE with [] - access request must not authorise deletion",
                  lambda r: r["status"] == "NOT_APPLICABLE" and r["steps"] == []))

    c = copy.deepcopy(by["case_29"])
    c.update(case_id="custom_02_marketing_only",
             request_text="Please remove me from marketing lists only. I want to keep my account and orders.")
    cases.append((c, "NOT_APPLICABLE - marketing opt-out scoped by 'only'",
                  lambda r: r["status"] == "NOT_APPLICABLE"))

    c = copy.deepcopy(by["case_29"])
    c.update(case_id="custom_03_historical_email", email="NOAH.F4380@OUTLOOK.COM")
    cases.append((c, "historical, differently-cased email resolves to the same subject (user_29) and finds search_1993",
                  lambda r: r["trace"].get("user_id") == "user_29"
                  and any(s["resource_id"] == "search_1993" for s in r["steps"])))

    c = copy.deepcopy(by["case_28"])
    c.update(case_id="custom_04_decoy_email")
    cases.append((c, "decoy search document for smartinez6461.jr@gmail.com is never attributed to the subject",
                  lambda r: not any(s["resource_id"] == "search_8168" for s in r["steps"])))

    c = copy.deepcopy(by["case_24"])
    c.update(case_id="custom_05_audit_cutoff", audit_at="2026-09-03T00:00:00Z")
    cases.append((c, "evidence captured after audit_at is never cited",
                  lambda r: all(not s["evidence_id"].startswith("evidence_") or s["evidence_id"] not in ("evidence_92", "evidence_02")
                                for s in r["steps"])))

    c = copy.deepcopy(by["case_30"])
    c.update(case_id="custom_06_ordering")
    def ordering(r):
        ops = [s["operation"] for s in r["steps"]]
        phases = [s["phase"] for s in r["steps"]]
        order = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
        mono = all(order[a] <= order[b] for a, b in zip(phases, phases[1:]))
        flow_first = ops.index("DISABLE_DATA_FLOW") < ops.index("DELETE_RECORD") if "DISABLE_DATA_FLOW" in ops else True
        numbered = [s["step_no"] for s in r["steps"]] == list(range(1, len(r["steps"]) + 1))
        empty_ev = all(s["evidence_id"] == "" for s in r["steps"] if s["phase"] != "FINDING")
        return mono and flow_first and numbered and empty_ev and r["status"] == "INCOMPLETE"
    cases.append((c, "INCOMPLETE plan: phase order, data flow disabled before deletes, consecutive step_no, empty evidence for future work",
                  ordering))

    c = copy.deepcopy(by["case_31"])
    c.update(case_id="custom_07_tombstone")
    cases.append((c, "redacted account tombstone is retained and verified as REDACTED (never deleted)",
                  lambda r: any(s["resource_id"].startswith("tombstone_") and s["operation"] == "VERIFY_RECORD_REDACTED"
                                for s in r["steps"]) and not any(s["operation"] == "DELETE_RECORD" for s in r["steps"])))

    c = copy.deepcopy(by["case_09"])
    c.update(case_id="custom_08_retention")
    cases.append((c, "security audit record inside its retention period is kept (VERIFY_RECORD_PRESENT, no DELETE)",
                  lambda r: any(s["system_id"] == "security_service" and s["operation"] == "VERIFY_RECORD_PRESENT" for s in r["steps"])
                  and not any(s["system_id"] == "security_service" and s["operation"] == "DELETE_RECORD" for s in r["steps"])))
    return cases


def fmt(x):
    return f"{x * 100:.1f}%" if isinstance(x, float) else str(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="regenerate evaluation_metrics.md")
    args = ap.parse_args()
    samples = read_requests(os.path.join(DATASET, "sample_deletion_requests.csv"))
    gold = load_gold(os.path.join(DATASET, "sample_output.csv"))

    usage = Usage()
    auditor = build_auditor(DATASET, usage)
    pred = predict(auditor, samples)
    m = score(pred, gold)

    # ablations: switch off individual reasoning components
    ablations = {}
    saved = (auditor.WINDOW_FROM_REQUEST, auditor.workflow_links)
    auditor.WINDOW_FROM_REQUEST = False
    ablations["no request-window filter"] = score(predict(auditor, samples), gold)
    auditor.WINDOW_FROM_REQUEST = saved[0]
    auditor.workflow_links = {}
    ablations["no workflow linking"] = score(predict(auditor, samples), gold)
    auditor.workflow_links = saved[1]

    customs = []
    for req, desc, check in custom_cases(samples):
        res = auditor.audit_case(req)
        customs.append((req["case_id"], desc, bool(check(res)), res["status"], len(res["steps"])))

    keys = ["status_accuracy", "step_precision", "step_recall", "step_f1", "field_state_accuracy",
            "field_evidence_accuracy", "resource_precision", "resource_recall", "sequence_exact", "sequence_lcs"]
    print("== sample set ==")
    for k in keys:
        print(f"{k:26s} {fmt(m[k])}")
    print("confusion", m["confusion"])
    for name, a in ablations.items():
        print(f"-- ablation: {name}: status {fmt(a['status_accuracy'])} f1 {fmt(a['step_f1'])} exact {fmt(a['sequence_exact'])}")
    print("== custom cases ==")
    for cid, desc, ok, st, n in customs:
        print(("PASS" if ok else "FAIL"), cid, st, n, "-", desc)
    failing = [(cid, v) for cid, v in m["per_case"].items() if not v["exact"]]
    print("== non-exact sample cases ==")
    for cid, v in failing:
        print(cid, "status_ok" if v["status_ok"] else "STATUS", "lcs", v["lcs"], "missing", v["missing"], "extra", v["extra"])

    if args.write:
        out = os.path.join(HERE, "evaluation_metrics.md")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("# Auto-generated evaluation metrics\n\n")
            fh.write("Generated by `python3 code/evaluation/main.py --write` on the 32 public sample cases.\n\n")
            fh.write("| Metric | Value |\n|---|---|\n")
            for k in keys:
                fh.write(f"| {k} | {fmt(m[k])} |\n")
            fh.write(f"\nConfusion (gold->pred): `{m['confusion']}`\n\n## Ablations\n\n| Variant | Status acc | Step F1 | Exact sequence |\n|---|---|---|---|\n")
            fh.write(f"| full system | {fmt(m['status_accuracy'])} | {fmt(m['step_f1'])} | {fmt(m['sequence_exact'])} |\n")
            for name, a in ablations.items():
                fh.write(f"| {name} | {fmt(a['status_accuracy'])} | {fmt(a['step_f1'])} | {fmt(a['sequence_exact'])} |\n")
            fh.write("\n## Custom cases\n\n| Case | Expectation | Result |\n|---|---|---|\n")
            for cid, desc, ok, st, n in customs:
                fh.write(f"| {cid} | {desc} | {'PASS' if ok else 'FAIL'} ({st}, {n} steps) |\n")
            fh.write("\n## Non-exact sample cases\n\n")
            for cid, v in failing:
                fh.write(f"- **{cid}** (status {'ok' if v['status_ok'] else 'WRONG'}, LCS {v['lcs']}): missing {v['missing']}; extra {v['extra']}\n")
        print("wrote", out)


if __name__ == "__main__":
    main()
