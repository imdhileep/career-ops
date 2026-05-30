#!/usr/bin/env python3
"""Build an Excel tracker (data/applications.xlsx) from reports/ + applications.md.

Columns: #, Date, Company, Role, Score, Decision, Legitimacy, Status,
Job URL, Report (JD details), Resume (PDF), Resume (.docx), Notes.

URL / report / PDF / docx cells are clickable hyperlinks. Re-runnable: safe to
call after every batch/merge to refresh. Zero LLM cost — pure parsing.
"""
import os, re, glob, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
OUT = os.path.join(ROOT, "data", "applications.xlsx")

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

HEADER_RE = lambda k: re.compile(r"^\*\*%s:\*\*\s*(.+?)\s*$" % k, re.M)
H1_RE = re.compile(r"^#\s*Evaluation:\s*(.+?)\s*$", re.M)
MS_RE = re.compile(r"```yaml\s*(.*?)```", re.S)


def ms_value(ms, key):
    m = re.search(r'^%s:\s*"?(.*?)"?\s*$' % re.escape(key), ms, re.M)
    return m.group(1).strip() if m else ""


def ms_first_list_item(ms, key):
    # grab the first "- item" line under a "key:" block
    m = re.search(r'^%s:\s*\n((?:\s*-\s*.+\n?)+)' % re.escape(key), ms, re.M)
    if not m:
        return ""
    first = m.group(1).splitlines()[0]
    return re.sub(r'^\s*-\s*"?(.*?)"?\s*$', r"\1", first)


def rel(path):
    """Normalize a report's stored path to repo-relative (strip leading career-ops/)."""
    path = path.strip().lstrip("/")
    if path.startswith("career-ops/"):
        path = path[len("career-ops/"):]
    return path


def parse_report(fp):
    txt = open(fp, encoding="utf-8").read()
    base = os.path.basename(fp)
    num = int(base.split("-", 1)[0])
    h1 = H1_RE.search(txt)
    company, role = "", ""
    if h1:
        parts = re.split(r"\s+[—-]\s+", h1.group(1), maxsplit=1)
        company = parts[0].strip()
        role = parts[1].strip() if len(parts) > 1 else ""

    def hdr(k):
        m = HEADER_RE(k).search(txt)
        return m.group(1).strip() if m else ""

    ms_m = MS_RE.search(txt)
    ms = ms_m.group(1) if ms_m else ""
    if ms:
        company = ms_value(ms, "company") or company
        role = ms_value(ms, "role") or role

    score = (hdr("Score") or (ms_value(ms, "score") + "/5")).replace("/5/5", "/5")
    decision = ms_value(ms, "final_decision")
    note = ms_value(ms, "next_action") or ms_first_list_item(ms, "hard_stops")
    pdf = rel(hdr("PDF"))
    docx = pdf[:-4] + ".docx" if pdf.endswith(".pdf") else ""
    return {
        "num": num,
        "date": hdr("Date"),
        "company": company,
        "role": role,
        "score": score,
        "decision": decision,
        "legitimacy": hdr("Legitimacy"),
        "url": hdr("URL"),
        "report": os.path.relpath(fp, ROOT),
        "pdf": pdf,
        "docx": docx,
        "note": note,
    }


def main():
    rows = [parse_report(fp) for fp in glob.glob(os.path.join(REPORTS, "*.md"))]
    rows.sort(key=lambda r: r["num"])

    # Fallback for the .docx column: if no per-job Word file exists yet, link the
    # editable base resume so the column is always usable.
    base_docx = ""
    for cand in sorted(glob.glob(os.path.join(ROOT, "output", "resume-*.docx"))):
        base_docx = os.path.relpath(cand, ROOT)
        break
    for r in rows:
        if not (r["docx"] and os.path.exists(os.path.join(ROOT, r["docx"]))):
            r["docx"] = base_docx

    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    cols = ["#", "Date", "Company", "Role", "Score", "Decision", "Legitimacy",
            "Status", "Job URL", "Report (JD details)", "Resume (PDF)",
            "Resume (.docx)", "Notes"]
    ws.append(cols)
    head_fill = PatternFill("solid", fgColor="1F2A44")
    for c in range(1, len(cols) + 1):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    def link(ws, r, c, target, text, is_url=False):
        cell = ws.cell(r, c, text or "")
        if target:
            cell.hyperlink = target if is_url else os.path.join(ROOT, target)
            cell.font = Font(color="0563C1", underline="single")

    for i, row in enumerate(rows, start=2):
        ws.cell(i, 1, row["num"])
        ws.cell(i, 2, row["date"])
        ws.cell(i, 3, row["company"])
        ws.cell(i, 4, row["role"])
        ws.cell(i, 5, row["score"])
        ws.cell(i, 6, row["decision"])
        ws.cell(i, 7, row["legitimacy"])
        ws.cell(i, 8, "Evaluated")
        link(ws, i, 9, row["url"], row["url"], is_url=True)
        link(ws, i, 10, row["report"], "report")
        link(ws, i, 11, row["pdf"] if os.path.exists(os.path.join(ROOT, row["pdf"])) else "", "PDF")
        link(ws, i, 12, row["docx"] if os.path.exists(os.path.join(ROOT, row["docx"])) else "", ".docx")
        ws.cell(i, 13, row["note"])

    widths = [5, 11, 18, 34, 8, 9, 16, 11, 46, 16, 13, 13, 60]
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT} — {len(rows)} rows")


if __name__ == "__main__":
    main()
