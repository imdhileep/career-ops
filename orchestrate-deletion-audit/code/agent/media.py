"""Multimodal evidence (screenshots and PDFs).

Resolution order for each media file:
1. ``media_cache.json`` – structured transcription keyed by the file's sha256
   prefix (produced with a vision-language model using the prompt in
   ``code/prompts/media_extraction.md``).  This keeps the full run
   deterministic, offline and free.
2. Live vision call to the Anthropic API when ``ANTHROPIC_API_KEY`` and
   ``DELETION_AUDIT_VISION_MODEL`` are set and
   the file is not cached (new or modified evidence).
3. Text-only fallback: ``pdftotext``/``pdfplumber`` text for PDFs, from which
   resource ids and identifiers are pulled with regular expressions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "media_cache.json")
PROMPT_PATH = os.path.join(os.path.dirname(HERE), "prompts", "media_extraction.md")
VISION_MODEL = os.environ.get("DELETION_AUDIT_VISION_MODEL", "")

_cache = None


def _load_cache() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except FileNotFoundError:
            _cache = {}
    return _cache


def file_digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def pdf_text(path: str) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True,
                              text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:  # pragma: no cover - optional dependency
        return ""


RID = re.compile(r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|marketing|object)_[0-9]+\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER = re.compile(r"\buser_[0-9]+\b")


def _regex_records(text: str) -> list:
    """Fallback: associate every identifier in the document with every resource id."""
    rids = sorted(set(RID.findall(text)))
    emails = sorted(set(EMAIL.findall(text)))
    users = sorted(set(USER.findall(text)))
    recs = []
    for rid in rids:
        rec = {"resource_id": rid, "result": "FOUND"}
        if emails:
            rec["email"] = emails[0]
        if users:
            rec["customer_reference"] = users[0]
        recs.append(rec)
    return recs


def _live_vision(path: str, usage=None) -> Dict[str, Any]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not VISION_MODEL:
        return {}
    try:
        import anthropic  # type: ignore
    except ImportError:
        return {}
    prompt = open(PROMPT_PATH, encoding="utf-8").read() if os.path.exists(PROMPT_PATH) else \
        "Extract every resource id and identifier shown as JSON {summary, records:[...]}."
    data = base64.standard_b64encode(open(path, "rb").read()).decode()
    media_type = "application/pdf" if path.endswith(".pdf") else "image/png"
    block = ({"type": "document", "source": {"type": "base64", "media_type": media_type, "data": data}}
             if media_type == "application/pdf" else
             {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    client = anthropic.Anthropic()
    msg = client.messages.create(model=VISION_MODEL, max_tokens=2000,
                                 messages=[{"role": "user", "content": [block, {"type": "text", "text": prompt}]}])
    if usage is not None:
        usage.record(VISION_MODEL, msg.usage.input_tokens, msg.usage.output_tokens)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def extract_media(ef, usage=None) -> Dict[str, Any]:
    digest = file_digest(ef.path)
    cache = _load_cache()
    facts: Dict[str, Any] = {}
    if digest in cache:
        facts = dict(cache[digest])
        if usage is not None:
            usage.cache_hit("media")
    else:
        try:
            facts = _live_vision(ef.path, usage=usage)
        except Exception:
            facts = {}
    text = pdf_text(ef.path) if ef.path.endswith(".pdf") else ""
    if not facts.get("records") and text and not facts:
        facts["records"] = _regex_records(text)
    facts["text"] = (facts.get("summary", "") + "\n" + text).strip()
    return facts
