#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent.

Usage (from the repository root):
    python3 code/main.py                       # audits dataset/deletion_requests.csv -> output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --out /tmp/sample_pred.csv
    python3 code/main.py --trace traces.jsonl  # also write per-case reasoning traces

Pipeline
    1. evidence.extract_all   - parse every artifact into normalised observations
    2. Auditor.prepare        - global workflow linking across all requests
    3. Auditor.audit_case     - intent -> identity -> linking -> state at audit_at
                                -> deletion_rules.csv -> ordered steps
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from agent import evidence  # noqa: E402
from agent.auditor import Auditor  # noqa: E402
from agent.usage import Usage  # noqa: E402


def read_requests(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_auditor(dataset_dir: str, usage: Usage) -> Auditor:
    obs, files = evidence.extract_all(dataset_dir, usage=usage)
    auditor = Auditor(dataset_dir, obs, files, usage=usage)
    # the workflow linker needs every request that shares deletion-worker batches
    all_requests = []
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        p = os.path.join(dataset_dir, name)
        if os.path.exists(p):
            all_requests.extend(read_requests(p))
    auditor.prepare(all_requests)
    return auditor


def audit_rows(auditor: Auditor, requests):
    rows, traces = [], []
    for req in requests:
        res = auditor.audit_case(req)
        rows.append((req["case_id"], res["status"],
                     json.dumps(res["steps"], separators=(",", ":"), ensure_ascii=False)))
        traces.append(res["trace"])
    rows.sort(key=lambda r: r[0])
    return rows, traces


def write_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        w.writerows(rows)


def run(requests_path: str, out_path: str, dataset_dir: str, trace_path: str = None, usage_path: str = None):
    t0 = time.time()
    usage = Usage()
    auditor = build_auditor(dataset_dir, usage)
    requests = read_requests(requests_path)
    rows, traces = audit_rows(auditor, requests)
    write_csv(rows, out_path)
    if trace_path:
        with open(trace_path, "w", encoding="utf-8") as fh:
            for t in traces:
                fh.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    if usage_path:
        usage.dump(usage_path, len(requests))
    counts = {}
    for _, st, _ in rows:
        counts[st] = counts.get(st, 0) + 1
    print(f"wrote {len(rows)} rows to {out_path} in {time.time() - t0:.1f}s  {counts}")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(ROOT, "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: dataset/deletion_requests.csv)")
    ap.add_argument("--out", default=os.path.join(ROOT, "output.csv"))
    ap.add_argument("--trace", default=None, help="optional JSONL file with per-case reasoning traces")
    ap.add_argument("--usage", default=os.path.join(HERE, "evaluation", "usage_run.json"))
    args = ap.parse_args()
    req = args.requests or os.path.join(args.dataset, "deletion_requests.csv")
    run(req, args.out, args.dataset, args.trace, args.usage)


if __name__ == "__main__":
    main()
