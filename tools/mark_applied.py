#!/usr/bin/env python3
"""Mark a job's application status and refresh the tracker (live updates).

Usage:
    .venv/bin/python tools/mark_applied.py <num> <status> [--no-refresh]

<status>: applied | blocked | skipped | error | <anything>
Writes data/applied-status.tsv (num, status, ISO timestamp), updates the
status column in data/apply-worklist.csv, and (unless --no-refresh) rebuilds
the xlsx/csv/html tracker so applications.html reflects it immediately.
"""
import os, sys, csv, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(ROOT, "data", "applied-status.tsv")
WORKLIST = os.path.join(ROOT, "data", "apply-worklist.csv")


def upsert_status(num, status):
    rows = {}
    if os.path.exists(STATUS_FILE):
        for line in open(STATUS_FILE, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0].isdigit():
                rows[p[0]] = p
    rows[str(num)] = [str(num), status, datetime.datetime.now().isoformat(timespec="seconds")]
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        for k in sorted(rows, key=int):
            f.write("\t".join(rows[k]) + "\n")


def update_worklist(num, status):
    if not os.path.exists(WORKLIST):
        return
    rows = list(csv.DictReader(open(WORKLIST, encoding="utf-8")))
    if not rows:
        return
    fields = list(rows[0].keys())
    for r in rows:
        if r.get("num") == str(num):
            r["status"] = status
    with open(WORKLIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    args = [a for a in sys.argv[1:] if a != "--no-refresh"]
    refresh = "--no-refresh" not in sys.argv[1:]
    if len(args) < 2 or not args[0].isdigit():
        print(__doc__)
        sys.exit(1)
    num, status = args[0], args[1]
    upsert_status(num, status)
    update_worklist(num, status)
    print(f"marked #{num} -> {status}")
    if refresh:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import make_tracker_xlsx
        make_tracker_xlsx.main()


if __name__ == "__main__":
    main()
