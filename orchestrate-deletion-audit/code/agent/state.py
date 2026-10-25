"""Interpret a single observation as a resource state reading.

Each reading answers, where the observation allows it:
    present   – does the record (still) exist?
    exposed   – does it still carry direct identifiers?
    active    – is a data flow enabled / an archive restorable?
plus the data category it most likely belongs to.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .evidence import Observation

TRUE = {"true", "1", "yes", "enabled", "allowed", "active"}
FALSE = {"false", "0", "no", "disabled", "blocked", "stopped"}

IDENT_FIELDS = ("email", "customer_email", "owner_email", "requester_email", "phone", "owner_phone",
                "requester_phone", "email_hash", "full_name", "display_name", "requester_name", "name",
                "uploaded_by")
REF_FIELDS = ("user_id", "customer_reference", "subject_reference")

SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "integration_service": "DATA_FLOW",
    "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD",
    "marketing_platform": "MARKETING_PROFILE",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "object_storage": "SUPPORT_ATTACHMENT",
    "telemetry_service": "PRODUCT_TELEMETRY",
}


def _b(v) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in TRUE:
        return True
    if s in FALSE:
        return False
    return None


def _has_value(v) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and s.lower() not in ("null", "none", "[redacted]", "redacted", "—", "-")


def category_of(o: Observation) -> str:
    rt = str(o.get("record_type") or "").upper()
    if rt and rt not in ("NONE",):
        return rt
    cont = (o.container or o.get("_container") or o.get("collection_name") or o.get("_table") or "").lower()
    ev = str(o.get("event") or "").lower()
    if o.system == "support_platform":
        if "contact_query" in ev or "contact_index" in str(o.get("source") or ""):
            # legacy contact index rows only map an identifier to the person
            return "SECONDARY_IDENTIFIER"
        if "attachment" in cont or "attachment" in ev or o.get("file") or o.get("file_name") \
                or o.get("storage_class") == "ticket_attachment":
            return "SUPPORT_ATTACHMENT"
        return "SUPPORT_MESSAGE"
    if o.system == "analytics_platform" and ("contact_lookup" in ev or _b(o.get("email_opt_in")) is not None):
        # contact lookups keyed by an identifier act as a secondary identifier store
        return "SECONDARY_IDENTIFIER"
    return SYSTEM_CATEGORY.get(o.system, "UNKNOWN")


def reading(o: Observation) -> Dict[str, Any]:
    """Return {'present','exposed','active'} (None when the observation is silent)."""
    f = o.fields
    r: Dict[str, Any] = {"present": None, "exposed": None, "active": None}
    if o.kind == "activity":
        et = f.get("event_type")
        d = f.get("details", "")
        if et == "CHECK_DATA_FLOW" and "remains enabled" in d:
            r.update(present=True, active=True)
        elif et == "CHECK_RESTORE_ACCESS" and "can still be restored" in d:
            r.update(present=True, active=True)
        elif et == "SYNC_RECORD" and "Active connector repopulated" in d:
            # the connector itself was demonstrably running at that moment
            r.update(present=True, active=True)
        elif et == "RESTORE_BACKUP" and "repopulated" in d:
            r.update(present=True, active=True)
        return r
    result = str(f.get("result") or "").upper()
    http = str(f.get("http_status") or f.get("status_code") or "")
    rows = str(f.get("rows") or "")

    # existence
    if result in ("NOT_FOUND", "ABSENT", "DELETED", "MISS") or http == "404" or rows == "0" \
            or str(f.get("hit_total")) == "0" or str(f.get("total_hits")) == "0" or result == "NONE":
        r["present"] = False
    elif result in ("FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "BLOCKED", "DISABLED", "PRESENT",
                    "HIT") or http == "200" or rows not in ("", "0") or o.kind in ("sql", "json", "media", "text"):
        r["present"] = True
    elif o.kind == "log" and re.search(r"(completed|_read$|_scan$|heartbeat|evaluated|loaded)", str(f.get("event", ""))):
        r["present"] = True
    if str(f.get("result", "")).lower() == "hit":
        r["present"] = True
    if str(f.get("result", "")).lower() == "miss":
        r["present"] = False
    if f.get("profile__present") is None and "profile" in f and f.get("profile") is None and o.kind == "json":
        r["present"] = False
    if f.get("archive_present") is not None and _b(f.get("archive_present")) is False:
        r["present"] = False
    if o.system == "account_service" and str(f.get("account_status", "")).upper() == "DELETED":
        r["present"] = False

    # exposure
    if r["present"]:
        if result in ("IDENTIFIABLE",):
            r["exposed"] = True
        elif result in ("ANONYMIZED", "REDACTED"):
            r["exposed"] = False
        else:
            idents = [k for k in IDENT_FIELDS if _has_value(f.get(k))]
            text = str(f.get("message_text") or f.get("message_excerpt") or "")
            if re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text):
                idents.append("message_text")
            if f.get("matched_value") and "pii_scan" in str(f.get("event", "")):
                idents.append("matched_value")
            if str(f.get("direct_identifiers", "")) not in ("", "0"):
                idents.append("direct_identifiers")
            if o.system == "analytics_platform":
                idents += [k for k in ("user_id", "customer_reference") if _has_value(f.get(k))]
            if o.system == "analytics_platform" and "contact_lookup" in str(f.get("event", "")):
                idents.append("correlation_key")
            r["exposed"] = bool(idents)
            r["exposed_fields"] = idents

    # activity of flows / restorability
    if o.system == "integration_service":
        en = _b(f.get("enabled"))
        if en is None and result in ("ACTIVE", "DISABLED"):
            en = result == "ACTIVE"
        if en is None and f.get("last_delivery_status"):
            en = str(f["last_delivery_status"]).lower() not in ("stopped", "disabled")
        if en is None and _b(f.get("scheduler_registered")) is not None:
            en = _b(f.get("scheduler_registered"))
        r["active"] = en
        if en is not None:
            r["present"] = True
    if o.system == "backup_service":
        ra = _b(f.get("restore_allowed"))
        if ra is None and result in ("ACTIVE", "BLOCKED"):
            ra = result == "ACTIVE"
        if ra is None and f.get("eligible") is not None:
            ra = _b(f.get("eligible")) and not _b(f.get("deletion_block"))
        if ra is None and f.get("decision") is not None:
            ra = str(f.get("decision")).lower() in ("allowed", "allow", "approved")
        r["active"] = ra
    return r
