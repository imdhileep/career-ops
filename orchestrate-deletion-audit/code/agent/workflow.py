"""Workflow linking: attach deletion-worker activity to the request it served.

Many resources carry no direct identifier in any evidence (an account that is
only mentioned by the deletion worker, an analytics profile that was already
anonymised, a support thread already redacted).  They can still be tied to a
request through the deletion workflow itself:

1. The worker submits the subject's *primary profile* shortly before the
   workflow reports its status.  The submission result mirrors the reported
   status (COMPLETE -> SUCCESS, PARTIALLY_COMPLETE -> PARTIAL,
   NOT_STARTED -> FAILED).
2. Every follow-up action for the same request (policy actions, final results,
   connector / restore checks) is scheduled from that submission, so its
   timestamp keeps a characteristic seconds offset from the account event.

Candidate accounts that are linked to *another* person by content are
excluded, content-linked accounts win outright, and remaining ties are
resolved globally (one account per request) using the offset evidence.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from .evidence import Observation
from .identity import IdentityStore, norm_key

STATUS_TO_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}

# seconds-offset families observed between a request's profile submission and
# its follow-up actions (policy actions, final-result callbacks, checks)
POLICY_OFFSETS = {
    ("ANONYMIZE_RECORD", "analytics_platform"): {40, 41},
    ("REDACT_RECORD", "support_platform"): {10, 11},
    ("REDACT_RECORD", "identity_service"): {40, 41},
    ("DISABLE_DATA_FLOW", "integration_service"): {39, 40},
    ("BLOCK_RESTORE", "backup_service"): {8, 9},
    ("RETAIN_RECORD", "legal_service"): {8, 9, 10, 11, 39, 40, 41},
}
CALLBACK_OFFSETS = {20, 21, 22, 23, 24, 51, 52}
MAX_FOLLOWUP_S = 4 * 3600


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def content_owners(obs: List[Observation], ids: IdentityStore) -> Dict[str, Set[str]]:
    owners: Dict[str, Set[str]] = defaultdict(set)
    for o in obs:
        for k in o.keys:
            nk = norm_key(k)
            if nk:
                owners[o.resource_id] |= ids.owners(nk)
    return owners


def _offset_fits(ev: Observation, t0: datetime) -> bool:
    d = (_t(ev.observed_at) - t0).total_seconds()
    if d <= 0 or d > MAX_FOLLOWUP_S:
        return False
    sec = int(d) % 60
    et, tgt = ev.get("event_type"), ev.get("target_system")
    details = ev.get("details", "")
    if details.startswith("Policy action completed"):
        return sec in POLICY_OFFSETS.get((et, tgt), set())
    return sec in CALLBACK_OFFSETS


def build_links(cases: List[dict], ids: IdentityStore, obs: List[Observation]) -> Dict[str, Dict[str, str]]:
    """Return {user_id: {resource_id: reason}} for workflow-derived links."""
    owners = content_owners([o for o in obs if o.kind != "activity" or o.get("event_type") == "LINK_RESOURCE_IDENTITY"], ids)
    dw = [o for o in obs if o.kind == "activity" and o.get("source_system") == "deletion_worker"]
    acct = [o for o in dw if o.get("event_type") == "DELETE_RECORD" and o.get("target_system") == "account_service"]
    followups = [o for o in dw if o not in acct]

    # ---- candidate accounts per case ------------------------------------
    cand: Dict[str, List[Observation]] = {}
    case_user: Dict[str, str] = {}
    for c in cases:
        uid = c["user_id"]
        case_user[c["case_id"]] = uid
        want = STATUS_TO_RESULT.get(c["reported_status"])
        lo, hi = c["requested_at"], c["reported_at"]
        cs = []
        rep = _t(hi)
        for o in acct:
            own = owners.get(o.resource_id, set())
            if own and uid not in own:
                continue
            if "submitted primary profile" in o.get("details", ""):
                # batch submission: inside the request window, result mirrors the status
                if not (lo < o.observed_at <= hi):
                    continue
                if want and o.get("reported_result") != want and uid not in own:
                    continue
            else:
                # one-off worker report: logged right around the reported status
                if abs((_t(o.observed_at) - rep).total_seconds()) > 30 * 60:
                    continue
            cs.append(o)
        cand[c["case_id"]] = cs

    # content-linked resources per user (used to score offsets)
    user_res: Dict[str, Set[str]] = defaultdict(set)
    for rid, us in owners.items():
        for u in us:
            user_res[u].add(rid)

    rep_at = {c["case_id"]: c["reported_at"] for c in cases}

    def score(cid: str, a: Observation) -> Tuple[int, int]:
        uid = case_user[cid]
        if uid in owners.get(a.resource_id, set()):
            return (100, 0)
        if "submitted primary profile" not in a.get("details", ""):
            gap = abs((_t(a.observed_at) - _t(rep_at[cid])).total_seconds())
            return (50 - int(gap // 60), 0)
        t0 = _t(a.observed_at)
        s = 0
        for ev in followups:
            if ev.resource_id in user_res[uid] and _offset_fits(ev, t0):
                s += 1
        return (s, 0)

    # ---- global one-to-one assignment ------------------------------------
    assigned: Dict[str, Observation] = {}
    used: Set[str] = set()
    pending = {cid for cid, cs in cand.items() if cs}
    while pending:
        best = None
        for cid in pending:
            cs = [a for a in cand[cid] if a.resource_id not in used]
            if not cs:
                continue
            scored = sorted(((score(cid, a), a) for a in cs), key=lambda x: (-x[0][0], x[1].observed_at))
            top = scored[0]
            margin = top[0][0] - (scored[1][0][0] if len(scored) > 1 else -1)
            key = (-(top[0][0] >= 100), len(cs), -margin, -top[0][0], cid)
            if best is None or key < best[0]:
                best = (key, cid, top[1])
        if best is None:
            break
        _, cid, a = best
        assigned[cid] = a
        used.add(a.resource_id)
        pending.discard(cid)

    links: Dict[str, Dict[str, str]] = defaultdict(dict)
    for cid, a in assigned.items():
        links[case_user[cid]][a.resource_id] = f"workflow:{a.source_id}"

    # ---- follow-up actions without identifiers --------------------------
    starts = [(cid, _t(a.observed_at)) for cid, a in assigned.items()]
    for ev in followups:
        if owners.get(ev.resource_id):
            continue  # already attributable by content
        fits = [cid for cid, t0 in starts if _offset_fits(ev, t0)]
        if len(fits) == 1:
            cid = fits[0]
            links[case_user[cid]].setdefault(ev.resource_id, f"workflow-followup:{ev.source_id}")
    return links
