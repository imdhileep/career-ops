#!/usr/bin/env python3
"""Convert a CV markdown file to a clean, ATS-friendly resume HTML.

Usage: md_to_html.py [cv.md] [out.html]
Pairs with generate-pdf.mjs (HTML -> PDF). Same structure as generate_docx.py:
# name, ## section, ### subhead, - bullets, **bold**, plain paragraphs.
"""
import os, re, sys, html as _html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

CSS = """
body{font-family:Calibri,'Helvetica Neue',Arial,sans-serif;font-size:10.5pt;
color:#222;max-width:760px;margin:32px auto;line-height:1.4;padding:0 24px}
h1{font-size:22pt;color:#1F2A44;text-align:center;margin:0 0 4px}
h2{font-size:12pt;color:#1F2A44;border-bottom:1.5px solid #1F2A44;
text-transform:uppercase;letter-spacing:.5px;margin:18px 0 6px;padding-bottom:2px}
.sub{font-weight:700;font-size:11pt;margin:8px 0 2px}
ul{margin:4px 0 8px;padding-left:20px}li{margin:2px 0}
p{margin:4px 0}
"""


def inline(text):
    # escape HTML first (leaves ** intact), then render **bold**
    return BOLD_RE.sub(r"<strong>\1</strong>", _html.escape(text))


def build(md_path, out_path):
    lines = open(md_path, encoding="utf-8").read().splitlines()
    body, in_ul = [], False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            body.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            close_ul(); body.append(f"<h1>{inline(line[2:].strip())}</h1>")
        elif line.startswith("### "):
            close_ul(); body.append(f'<p class="sub">{inline(line[4:].strip())}</p>')
        elif line.startswith("## "):
            close_ul(); body.append(f"<h2>{inline(line[3:].strip())}</h2>")
        elif line.lstrip().startswith(("- ", "* ")):
            if not in_ul:
                body.append("<ul>"); in_ul = True
            body.append(f"<li>{inline(line.lstrip()[2:].strip())}</li>")
        else:
            close_ul(); body.append(f"<p>{inline(line.strip())}</p>")
    close_ul()

    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(body)}</body></html>"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(doc)
    return out_path


def main():
    md = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "cv.md")
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/resume.html"
    print("wrote", build(md, out))


if __name__ == "__main__":
    main()
