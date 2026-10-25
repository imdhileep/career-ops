"""Evidence extraction.

Every participant-visible artifact (activity stream, logs, JSON records, plain
text handovers, SQLite snapshot exports, PDFs and screenshots) is normalised
into a flat list of ``Observation`` objects:

    Observation(source_id, captured_at, observed_at, system, resource_id,
                fields, keys, kind)

``fields`` keeps the raw attributes that describe the resource state
(e.g. ``http_status``, ``enabled``, ``restore_allowed``, ``email``) and
``keys`` holds every identifier string that links the resource to a person
(emails, hashes, user ids, lookup keys ...).  Downstream stages never look at
raw files again, which keeps the reasoning deterministic and auditable.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from . import media

RESOURCE_RE = re.compile(
    r"\b(account|search|analytics|support|export|data_flow|backup|audit_record|"
    r"tombstone|cache|marketing|email_delivery|telemetry|object|legal_hold|"
    r"secondary|identifier)_[0-9]+\b"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_[0-9]+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")

PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "cache": "cache_service",
    "marketing": "marketing_platform",
    "email_delivery": "email_delivery_service",
    "object": "object_storage",
    "legal_hold": "legal_service",
    "telemetry": "telemetry_service",
}

# attribute names whose values identify a person
KEY_FIELDS = {
    "email", "customer_email", "owner_email", "requester_email", "uploaded_by",
    "lookup_key", "subject_key", "match_key", "scan_key", "subject_ref",
    "subject_reference", "customer_reference", "user_id", "correlation_key",
    "subject_selector", "matched_value", "email_hash", "phone", "owner_phone",
    "requester_phone", "full_name", "display_name", "requester_name",
    "recipient", "recipient_email", "to", "contact_email", "lookup_value", "name", "query", "owner_key",
}
NAME_FIELDS = {"full_name", "display_name", "requester_name", "customer_name", "name"}


def system_for(resource_id: str) -> str:
    for prefix in sorted(PREFIX_SYSTEM, key=len, reverse=True):
        if resource_id.startswith(prefix + "_"):
            return PREFIX_SYSTEM[prefix]
    return "unknown"


@dataclass
class Observation:
    source_id: str              # file_id / activity_id / audit_id
    captured_at: str            # when the source artifact was captured
    observed_at: str            # when this specific fact was observed
    system: str
    resource_id: str
    fields: Dict[str, Any] = field(default_factory=dict)
    keys: List[str] = field(default_factory=list)
    kind: str = ""              # format family, e.g. "sql:replica", "log", "activity"
    container: str = ""         # table / collection / data store name

    def get(self, name, default=None):
        return self.fields.get(name, default)


def _norm_time(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    t = str(t).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(:\d{2})?)", t)
    if not m:
        return None
    hhmmss = m.group(2) if m.group(3) else m.group(2) + ":00"
    return f"{m.group(1)}T{hhmmss}Z"


def _collect_keys(fields: Dict[str, Any]) -> List[str]:
    keys = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if not s or s.lower() in ("null", "none", "[redacted]", "redacted"):
            continue
        if k in KEY_FIELDS:
            keys.append(s)
        # free text (messages, details) can still carry identifiers
        if k in ("message_text", "message_excerpt", "details", "message", "text", "note", "body"):
            keys.extend(EMAIL_RE.findall(s))
            keys.extend(USER_RE.findall(s))
    return keys


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            # nested documents (search hits, account profiles) carry identifiers
            inner = _flatten(v)
            for ik, iv in inner.items():
                out.setdefault(ik, iv)
            out[k + "__present"] = True
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# Activity stream
# --------------------------------------------------------------------------- #

def load_activity(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def activity_observations(rows: List[Dict[str, str]]) -> List[Observation]:
    obs = []
    for r in rows:
        rid = r["resource_id"]
        keys = EMAIL_RE.findall(r["details"]) + USER_RE.findall(r["details"])
        f = dict(r)
        obs.append(Observation(r["activity_id"], r["event_time"], r["event_time"],
                               r["target_system"] if r["target_system"] in PREFIX_SYSTEM.values() else system_for(rid),
                               rid, f, keys, "activity", r["event_type"]))
    return obs


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #

KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')


def _parse_kv(text: str) -> Dict[str, str]:
    out = {}
    for k, v in KV_RE.findall(text):
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1].replace('\\"', '"')
        out[k] = v
    return out


def _parse_log_line(line: str) -> Optional[Dict[str, str]]:
    line = line.rstrip("\n")
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$", line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2)
    rec: Dict[str, str] = {}
    if rest.startswith("{"):                                    # JSON structured
        try:
            rec = {k: str(v) for k, v in json.loads(rest).items()}
        except json.JSONDecodeError:
            return None
    elif rest.startswith("<"):                                  # RFC5424 syslog
        parts = rest.split(" ", 6)
        if len(parts) >= 7:
            rec["service"], rec["trace_id"] = parts[2], parts[3]
            rec.update(_parse_kv(parts[6]))
    elif rest.startswith("["):                                  # bracket trace
        mm = re.match(r"\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+([\w.]+):\s*(.*)$", rest)
        if mm:
            rec["level"], rec["service"], rec["trace_id"], rec["event"] = mm.group(1, 2, 3, 4)
            rec.update(_parse_kv(mm.group(5)))
    elif " | " in rest:                                         # pipe separated
        parts = [p.strip() for p in rest.split(" | ")]
        if len(parts) >= 5:
            rec["level"], rec["service"], rec["trace_id"], rec["event"] = parts[:4]
            rec.update(_parse_kv(" | ".join(parts[4:])))
    else:                                                       # key=value (+ nested message)
        rec = _parse_kv(rest)
        if "message" in rec:
            rec.update({k: v for k, v in _parse_kv(rec["message"]).items() if k not in rec or k == "event"})
        if "requestId" in rec:
            rec.setdefault("trace_id", rec["requestId"])
        if "function" in rec:
            rec.setdefault("service", rec["function"])
    rec["_ts"] = ts
    return rec


LOOKUP_KEYS = ("lookup_key", "subject_key", "match_key", "scan_key", "subject_ref", "query", "owner_key")


def log_observations(file_id: str, captured_at: str, text: str) -> List[Observation]:
    recs = [r for r in (_parse_log_line(l) for l in text.splitlines()) if r]
    by_trace: Dict[str, List[Dict[str, str]]] = {}
    for r in recs:
        if r.get("trace_id"):
            by_trace.setdefault(r["trace_id"], []).append(r)
    obs = []
    for r in recs:
        rid = r.get("resource_id")
        if not rid or not RESOURCE_RE.fullmatch(rid):
            continue
        fields = {k: v for k, v in r.items() if not k.startswith("_")}
        # a completion line inherits the lookup key from its trace's start line
        for sib in by_trace.get(r.get("trace_id", ""), []):
            for lk in LOOKUP_KEYS:
                if lk in sib and lk not in fields:
                    fields[lk] = sib[lk]
            if sib.get("resource_id") == rid and sib is not r:
                for k, v in sib.items():
                    if k.startswith("_") or k in fields:
                        continue
                    if k in ("enabled", "restore_allowed", "eligible", "deletion_block",
                             "archive_present", "marker_found", "result", "rows", "http_status"):
                        fields[k] = v
        system = r.get("system") or r.get("service") or system_for(rid)
        if system not in PREFIX_SYSTEM.values():
            system = system_for(rid)
        keys = _collect_keys(fields)
        obs.append(Observation(file_id, captured_at, r["_ts"], system, rid, fields, keys, "log",
                               r.get("event", "")))
    return obs


# --------------------------------------------------------------------------- #
# JSON records
# --------------------------------------------------------------------------- #

def _walk_json(node: Any, ctx: Dict[str, Any], out: List[Dict[str, Any]]):
    if isinstance(node, dict):
        local = dict(ctx)
        for k in ("captured_at", "exported_at", "data_store", "collection", "system", "source", "origin"):
            if k in node and not isinstance(node[k], (dict, list)):
                local["_" + k] = node[k]
        if "resource_id" in node and isinstance(node["resource_id"], str):
            out.append({"node": node, "ctx": local})
            # nested search hits carry their own resource ids
            hits = node.get("hits")
            if isinstance(hits, dict):
                for doc in hits.get("documents", []) or []:
                    if not isinstance(doc, dict) or not doc.get("resource_id"):
                        continue
                    if doc.get("resource_id") != node["resource_id"]:
                        out.append({"node": dict(doc, observed_at=node.get("observed_at")), "ctx": local})
                    else:
                        out[-1] = {"node": dict(node, **{k: v for k, v in doc.items() if k not in node}), "ctx": local}
            return
        for k, v in node.items():
            sub = dict(local)
            if isinstance(v, (dict, list)) and k not in ("records", "snapshots", "service_exports"):
                sub["_group"] = k
                if k in PREFIX_SYSTEM.values():
                    sub["_data_store"] = k
            _walk_json(v, sub, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, ctx, out)


def _json_scopes(file_id: str, captured_at: str, data: Any) -> List[Observation]:
    """A snapshot of a data store lists every record it holds at that instant."""
    out = []
    snaps = data.get("snapshots") if isinstance(data, dict) else None
    for snap in snaps or []:
        if not isinstance(snap, dict) or not isinstance(snap.get("records"), list):
            continue
        system = snap.get("data_store")
        if system not in PREFIX_SYSTEM.values():
            continue
        listed = sorted(r.get("resource_id") for r in snap["records"] if isinstance(r, dict) and r.get("resource_id"))
        when = _norm_time(snap.get("captured_at")) or captured_at
        out.append(Observation(file_id, captured_at, when, system, "__scope__",
                               {"listed": listed, "collection": snap.get("collection")}, [], "scope", "json"))
    return out


def json_observations(file_id: str, captured_at: str, data: Any) -> List[Observation]:
    found: List[Dict[str, Any]] = []
    _walk_json(data, {}, found)
    obs = _json_scopes(file_id, captured_at, data)
    for item in found:
        node, ctx = item["node"], item["ctx"]
        rid = node["resource_id"]
        if not RESOURCE_RE.fullmatch(rid):
            continue
        fields = _flatten(node)
        if "hits" in node and isinstance(node["hits"], dict):
            fields["hit_total"] = node["hits"].get("total")
            fields.pop("hits__present", None)
        observed = (_norm_time(node.get("observed_at")) or _norm_time(node.get("recorded_at"))
                    or _norm_time(ctx.get("_captured_at")) or _norm_time(ctx.get("_exported_at")) or captured_at)
        system = node.get("system") or ctx.get("_data_store") or ctx.get("_system")
        if system not in PREFIX_SYSTEM.values():
            system = system_for(rid)
        container = ctx.get("_collection") or ctx.get("_group") or ""
        fields["_container"] = container
        obs.append(Observation(file_id, captured_at, observed, system, rid, fields,
                               _collect_keys(fields), "json", container))
    return obs


# --------------------------------------------------------------------------- #
# Plain text records (shift handovers etc.)
# --------------------------------------------------------------------------- #

def text_observations(file_id: str, captured_at: str, text: str) -> List[Observation]:
    obs = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = [p.strip(" -\t") for p in line.split("|")]
        rid = next((p for p in parts if RESOURCE_RE.fullmatch(p)), None)
        if not rid:
            continue
        ts = next((_norm_time(p) for p in parts if _norm_time(p)), captured_at)
        i = parts.index(rid)
        fields = {"system": parts[i - 1] if i > 0 else system_for(rid), "resource_id": rid}
        rest = parts[i + 1:]
        if len(rest) >= 1:
            fields["record_type"] = rest[0]
        if len(rest) >= 2:
            fields["result"] = rest[1]
        if len(rest) >= 3:
            fields["subject_reference"] = rest[2]
        system = fields["system"] if fields["system"] in PREFIX_SYSTEM.values() else system_for(rid)
        obs.append(Observation(file_id, captured_at, ts, system, rid, fields,
                               _collect_keys(fields), "text", fields.get("record_type", "")))
    return obs


# --------------------------------------------------------------------------- #
# SQL snapshots
# --------------------------------------------------------------------------- #

def sql_observations(file_id: str, captured_at: str, text: str) -> List[Observation]:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
    except sqlite3.Error:
        # tolerate dialect issues by stripping CHECK constraints
        con = sqlite3.connect(":memory:")
        con.executescript(re.sub(r"CHECK\s*\((?:[^()]|\([^()]*\))*\)", "", text))
    topology = (re.search(r"Database topology:\s*(\S+)", text) or [None, ""])[1]
    snap_completed = _norm_time((re.search(r"snapshot_completed_at=(\S+)", text) or [None, None])[1])
    header_cap = _norm_time((re.search(r"Captured at:\s*(\S+)", text) or [None, None])[1])
    scope = {}
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if "snapshot_scope" in tables:
        scope = {a: _norm_time(b) for a, b in con.execute("SELECT origin_system, captured_at FROM snapshot_scope")}
    obs = []
    for t in tables:
        if t == "snapshot_scope":
            continue
        cur = con.execute(f'SELECT * FROM "{t}"')
        cols = [c[0] for c in cur.description]
        if "resource_id" not in cols:
            continue
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            if rec.get("record_json"):
                try:
                    inner = json.loads(rec["record_json"])
                    rec.update(_flatten(inner))
                except json.JSONDecodeError:
                    pass
                rec.pop("record_json", None)
            rid = rec["resource_id"]
            if not RESOURCE_RE.fullmatch(str(rid)):
                continue
            system = rec.get("origin_system") or system_for(rid)
            if system not in PREFIX_SYSTEM.values():
                system = system_for(rid)
            observed = (_norm_time(rec.get("captured_at")) or scope.get(system) or snap_completed
                        or header_cap or _norm_time(rec.get("updated_at")) or _norm_time(rec.get("indexed_at"))
                        or captured_at)
            rec["_table"] = t
            rec["_topology"] = topology or "inventory"
            obs.append(Observation(file_id, captured_at, observed, system, rid, rec,
                                   _collect_keys(rec), "sql", t))
    con.close()
    for o in obs:
        o.fields["_sql_tables"] = tables
    # Scope records: a full point-in-time capture of a system proves that any
    # record it does not list was absent at that instant.
    scoped = dict(scope)
    full_capture = topology in ("failover_replica", "read_replica", "regional_replicas", "reporting_replica")
    if (full_capture or any(t.startswith("customer_profiles_") for t in tables)) and "account_service" not in scoped:
        scoped["account_service"] = header_cap or captured_at
    for system, when in scoped.items():
        listed = sorted({o.resource_id for o in obs if o.system == system})
        obs.append(Observation(file_id, captured_at, when or captured_at, system, "__scope__",
                               {"listed": listed}, [], "scope", "sql"))
    return obs


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

@dataclass
class EvidenceFile:
    file_id: str
    path: str
    origin: str
    captured_at: str
    text: str = ""


def load_manifest(dataset_dir: str) -> List[EvidenceFile]:
    with open(os.path.join(dataset_dir, "evidence_files.csv"), newline="", encoding="utf-8") as fh:
        return [EvidenceFile(r["file_id"], os.path.join(dataset_dir, r["file_path"]), r["origin"],
                             _norm_time(r["captured_at"])) for r in csv.DictReader(fh)]


def extract_all(dataset_dir: str, usage=None) -> (List[Observation], Dict[str, EvidenceFile]):
    files = load_manifest(dataset_dir)
    out: List[Observation] = []
    by_id = {}
    for ef in files:
        by_id[ef.file_id] = ef
        ext = os.path.splitext(ef.path)[1].lower()
        if not os.path.exists(ef.path):
            continue
        if ext in (".log",):
            ef.text = open(ef.path, encoding="utf-8", errors="replace").read()
            out.extend(log_observations(ef.file_id, ef.captured_at, ef.text))
        elif ext == ".json":
            ef.text = open(ef.path, encoding="utf-8").read()
            out.extend(json_observations(ef.file_id, ef.captured_at, json.loads(ef.text)))
        elif ext == ".sql":
            ef.text = open(ef.path, encoding="utf-8").read()
            out.extend(sql_observations(ef.file_id, ef.captured_at, ef.text))
        elif ext in (".txt", ".md", ".csv"):
            ef.text = open(ef.path, encoding="utf-8", errors="replace").read()
            out.extend(text_observations(ef.file_id, ef.captured_at, ef.text))
        elif ext in (".pdf", ".png", ".jpg", ".jpeg"):
            facts = media.extract_media(ef, usage=usage)
            ef.text = facts.get("text", "")
            out.extend(media_observations(ef, facts))
    activity = load_activity(os.path.join(dataset_dir, "system_activity.csv"))
    out.extend(activity_observations(activity))
    out.extend(alias_observations(activity, by_id, out))
    return out, by_id


def alias_observations(activity, by_id, existing) -> List[Observation]:
    """Data-catalog links name an alias for a resource (statement number, file
    name, customer key).  A screenshot / PDF that shows the alias is evidence
    about that resource even when the internal id is not printed on it."""
    have = {(o.source_id, o.resource_id) for o in existing}
    out = []
    for r in activity:
        if r["event_type"] != "LINK_RESOURCE_IDENTITY":
            continue
        m = re.search(r"linked (\S+) \(([^)]+)\)|confirmed (\S+) \(([^)]+)\)", r["details"])
        if not m:
            continue
        rid, alias = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if alias == rid or USER_RE.fullmatch(alias):
            continue
        for ef in by_id.values():
            if not ef.text or (ef.file_id, rid) in have or not ef.path.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
                continue
            if re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", ef.text):
                keys = EMAIL_RE.findall(r["details"]) + USER_RE.findall(r["details"])
                out.append(Observation(ef.file_id, ef.captured_at, ef.captured_at, system_for(rid), rid,
                                       {"resource_id": rid, "alias": alias, "result": "FOUND"},
                                       [k for k in keys if k in ef.text], "media", "alias"))
    return out


def media_observations(ef: EvidenceFile, facts: Dict[str, Any]) -> List[Observation]:
    """Media extraction returns structured ``records`` (one per resource shown)."""
    obs = []
    for rec in facts.get("records", []):
        rid = rec.get("resource_id")
        if not rid or not RESOURCE_RE.fullmatch(rid):
            continue
        fields = dict(rec)
        system = rec.get("system") if rec.get("system") in PREFIX_SYSTEM.values() else system_for(rid)
        observed = _norm_time(rec.get("observed_at")) or ef.captured_at
        obs.append(Observation(ef.file_id, ef.captured_at, observed, system, rid, fields,
                               _collect_keys(fields), "media", rec.get("record_type", "")))
    return obs
