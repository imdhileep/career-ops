"""Identity resolution: current snapshot + historical audit log."""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


def norm_key(value: str) -> Optional[str]:
    """Canonical form used for identifier matching."""
    if value is None:
        return None
    s = str(value).strip().strip('"').strip()
    if not s or s.lower() in ("null", "none"):
        return None
    if "@" in s:
        return "email:" + s.lower()
    if re.fullmatch(r"user_[0-9]+", s):
        return "user:" + s
    if re.fullmatch(r"[0-9a-fA-F]{64}", s):
        return "hash:" + s.lower()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+") and len(digits) >= 8:
        return "phone:" + digits
    return None


@dataclass
class Identity:
    user_id: str
    name: str
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    history: List[dict] = field(default_factory=list)

    def keys(self) -> Set[str]:
        out = {"user:" + self.user_id}
        out |= {"email:" + e.lower() for e in self.emails}
        out |= {"hash:" + h.lower() for h in self.hashes}
        out |= {"phone:" + re.sub(r"\D", "", p) for p in self.phones}
        return out


class IdentityStore:
    def __init__(self, dataset_dir: str):
        with open(os.path.join(dataset_dir, "user_identities.csv"), newline="", encoding="utf-8") as fh:
            self.current = {r["user_id"]: r for r in csv.DictReader(fh)}
        path = os.path.join(dataset_dir, "user_audit_log.csv")
        self.audit = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                self.audit = list(csv.DictReader(fh))
        self.by_key: Dict[str, Set[str]] = {}
        for uid in self.current:
            for k in self.identity(uid).keys():
                self.by_key.setdefault(k, set()).add(uid)

    def identity(self, uid: str) -> Identity:
        cur = self.current.get(uid, {})
        ident = Identity(uid, cur.get("name", ""))
        if cur:
            ident.emails.add(cur["email"])
            ident.phones.add(cur["phone"])
            ident.hashes.add(cur["email_hash"])
        for a in self.audit:
            if a["user_id"] != uid:
                continue
            ident.history.append(a)
            target = {"email": ident.emails, "phone": ident.phones, "email_hash": ident.hashes}.get(a["field_name"])
            if target is not None:
                for v in (a["old_value"], a["new_value"]):
                    if v:
                        target.add(v)
        ident.emails = {e for e in ident.emails if e}
        return ident

    def resolve_email(self, email: str) -> Optional[str]:
        uids = self.by_key.get("email:" + email.strip().lower(), set())
        if len(uids) == 1:
            return next(iter(uids))
        if len(uids) > 1:
            # prefer the user whose *current* email matches
            for uid in sorted(uids):
                if self.current[uid]["email"].lower() == email.strip().lower():
                    return uid
            return sorted(uids)[0]
        return None

    def owners(self, key: str) -> Set[str]:
        return self.by_key.get(key, set())
