"""Deletion audit reasoning: link evidence to the subject, reconstruct state at
``audit_at``, apply ``deletion_rules.csv`` and plan ordered steps."""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import intent as intent_mod
from .evidence import Observation, RESOURCE_RE, system_for
from .identity import IdentityStore, norm_key
from .state import category_of, reading

OP_ORDER = [
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
OP_RANK = {op: i for i, (_, op, _) in enumerate(OP_ORDER)}
OP_STATE = {op: st for _, op, st in OP_ORDER}
OP_PHASE = {op: ph for ph, op, _ in OP_ORDER}

# treatment -> (finding op, remediation op, verification op)
TREATMENT_OPS = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "RETAIN": (None, None, "VERIFY_RECORD_PRESENT"),
    "DISABLE": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCK": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}

REDACT_LIKE = {"SUPPORT_MESSAGE", "ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "EMAIL_DELIVERY_LOG", "ACCOUNT_TOMBSTONE"}

STATUS_TO_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def load_rules(dataset_dir: str) -> Dict[str, dict]:
    with open(os.path.join(dataset_dir, "deletion_rules.csv"), newline="", encoding="utf-8") as fh:
        return {r["data_category"]: r for r in csv.DictReader(fh)}


@dataclass
class ResourceView:
    system: str
    resource_id: str
    category: str = ""
    link_reasons: List[str] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    state_source: Optional[Observation] = None
    cite: Optional[Observation] = None


class Auditor:
    def __init__(self, dataset_dir: str, observations: List[Observation], files: dict, usage=None):
        self.dataset_dir = dataset_dir
        self.obs = observations
        self.files = files
        self.usage = usage
        self.ids = IdentityStore(dataset_dir)
        self.rules = load_rules(dataset_dir)
        self.by_resource: Dict[str, List[Observation]] = defaultdict(list)
        for o in observations:
            self.by_resource[o.resource_id].append(o)
        self.activity = [o for o in observations if o.kind == "activity"]
        self.scopes = [o for o in observations if o.kind == "scope"]
        self.relations = self._resource_relations()
        self.workflow_links: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    WINDOW_FROM_REQUEST = True

    def visible(self, o: Observation, audit_at: str, requested_at: str = "") -> bool:
        """Facts observed in the request window (requested_at, audit_at]."""
        if o.observed_at > audit_at:
            return False
        if self.WINDOW_FROM_REQUEST and requested_at and o.observed_at < requested_at:
            return False
        return True

    @staticmethod
    def citable(o: Observation, audit_at: str) -> bool:
        return (o.captured_at or o.observed_at) <= audit_at and o.observed_at <= audit_at

    def prepare(self, requests: List[dict]):
        """Global pass: workflow links need every request at once (shared batches)."""
        from . import workflow
        cases = []
        for r in requests:
            if not intent_mod.classify(r["request_text"])["authorizes_deletion"]:
                continue
            uid = self.ids.resolve_email(r["email"])
            if uid:
                cases.append(dict(r, user_id=uid))
        self.workflow_links = workflow.build_links(cases, self.ids, self.obs)

    def _resource_relations(self) -> Dict[str, Set[Tuple[str, str]]]:
        """Resource-to-resource edges (downstream copies, source records)."""
        rel: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        for o in self.obs:
            src = o.get("source_resource_id")
            if src and RESOURCE_RE.fullmatch(str(src)):
                rel[o.resource_id].add((src, o.observed_at))
                rel[src].add((o.resource_id, o.observed_at))
            if o.kind == "activity":
                m = re.search(r"repopulated downstream resource (\S+?)\.?$", o.get("details", ""))
                if m:
                    rel[o.resource_id].add((m.group(1), o.observed_at))
                    rel[m.group(1)].add((o.resource_id, o.observed_at))
        return rel

    def obs_owner_keys(self, o: Observation) -> Set[str]:
        return {k for k in (norm_key(x) for x in o.keys) if k}

    # ------------------------------------------------------------------ #
    # linking
    # ------------------------------------------------------------------ #
    def link_resources(self, uid: str, audit_at: str, requested_at: str = "") -> Dict[str, ResourceView]:
        ident = self.ids.identity(uid)
        mine = ident.keys()
        views: Dict[str, ResourceView] = {}
        for o in self.obs:
            if not self.visible(o, audit_at, requested_at):
                continue
            ks = self.obs_owner_keys(o)
            hit = ks & mine
            if not hit:
                continue
            # a record naming *another* known user and none of ours is a decoy
            v = views.setdefault(o.resource_id, ResourceView(o.system, o.resource_id))
            v.link_reasons.append(f"{o.source_id}:{sorted(hit)[0]}")
        # workflow links (deletion-worker batches) computed globally
        for rid, why in self.workflow_links.get(uid, {}).items():
            v = views.setdefault(rid, ResourceView(system_for(rid), rid))
            v.link_reasons.append(why)
        # downstream propagation via explicit relations (search doc <- account, connector -> search)
        for rid in list(views):
            for other, t in self.relations.get(rid, ()):
                if t <= audit_at and other not in views:
                    v = views.setdefault(other, ResourceView(system_for(other), other))
                    v.link_reasons.append(f"relation:{rid}")
        return views

    # ------------------------------------------------------------------ #
    # state reconstruction
    # ------------------------------------------------------------------ #
    def reconstruct(self, view: ResourceView, audit_at: str, requested_at: str = ""):
        obs = [o for o in self.by_resource.get(view.resource_id, []) if self.visible(o, audit_at, requested_at)]
        view.observations = obs
        cats = [category_of(o) for o in obs if o.kind != "activity"]
        cats = [c for c in cats if c and c != "UNKNOWN"]
        view.category = _majority(cats) or category_of(obs[0]) if obs else _default_cat(view.system)
        if not view.category or view.category == "UNKNOWN":
            view.category = _default_cat(view.system)
        readings = []
        for o in obs:
            r = reading(o)
            if r["present"] is None and r["active"] is None and r["exposed"] is None:
                continue
            readings.append((o.observed_at, _source_rank(o), o, r))
        if not readings:
            view.state = {"present": None}
            self._apply_scopes(view, audit_at, requested_at)
            return
        readings.sort(key=lambda x: (x[0], x[1]))
        latest_t = readings[-1][0]
        # several readings at the same instant (e.g. primary + replica tables of
        # one snapshot): the record exists if *any* copy still exists
        same = [x for x in readings if x[0] == latest_t]
        state = {"present": None, "exposed": None, "active": None}
        src = same[-1][2]
        for _, _, o, r in same:
            for k in ("present", "exposed", "active"):
                if r.get(k) is True:
                    if state[k] is not True:
                        state[k] = True
                        if k == "present" or k == "exposed":
                            src = o
                elif r.get(k) is False and state[k] is None:
                    state[k] = False
        # carry forward attributes the latest reading is silent about
        for _, _, o, r in reversed(readings):
            for k in ("exposed", "active"):
                if state[k] is None and r.get(k) is not None and state["present"] is not False:
                    state[k] = r[k]
        view.state = state
        view.state_source = src
        # cite the most recent artifact that (a) was captured by audit_at and
        # (b) agrees with the reconstructed state
        attrs = {"DATA_FLOW": ("active",), "BACKUP_ARCHIVE": ("active",)}.get(
            view.category, ("present", "exposed") if view.category in REDACT_LIKE else ("present",))

        def agrees(r):
            if state["present"] is False:
                return r.get("present") is False
            for k in attrs:
                if state.get(k) is None or r.get(k) is None or r[k] != state[k]:
                    return False
            return True
        cites = [x for x in readings if self.citable(x[2], audit_at) and agrees(x[3])]
        view.cite = cites[-1][2] if cites else None
        self._apply_scopes(view, audit_at, requested_at)

    def _apply_scopes(self, view: ResourceView, audit_at: str, requested_at: str):
        """Profiles missing from a full account-store capture are absent.

        Only used to *prove absence* of a profile that no reading shows as
        present; partial captures never override an explicit present reading.
        """
        if view.system != "account_service" or view.state.get("present") is True:
            return
        if view.cite is not None and view.state.get("present") is False:
            return
        omitted = [s for s in self.scopes if s.system == "account_service"
                   and self.visible(s, audit_at, requested_at) and self.citable(s, audit_at)
                   and view.resource_id not in s.get("listed", [])]
        if omitted:
            omitted.sort(key=lambda s: s.observed_at)
            view.state = dict(view.state, present=False)
            view.cite = omitted[-1]

    # ------------------------------------------------------------------ #
    # policy
    # ------------------------------------------------------------------ #
    def treatment(self, view: ResourceView, audit_at: str) -> Optional[str]:
        cat = view.category
        rule = self.rules.get(cat)
        if cat == "DATA_FLOW":
            return "DISABLE"
        if cat == "BACKUP_ARCHIVE":
            return "BLOCK"
        if not rule:
            return "DELETE"
        action = rule["required_action"].upper()
        if cat == "SECURITY_AUDIT_LOG":
            until = _latest_attr(view.observations, ("retention_until", "retained_until"))
            if until and until < audit_at:
                return "DELETE"
        if cat == "LEGAL_HOLD_RECORD":
            until = _latest_attr(view.observations, ("retained_until", "retention_until"))
            if until and until < audit_at:
                return "DELETE"
        return action

    # ------------------------------------------------------------------ #
    # per-case audit
    # ------------------------------------------------------------------ #
    def audit_case(self, req: dict) -> dict:
        intent = intent_mod.classify(req["request_text"], usage=self.usage)
        trace = {"case_id": req["case_id"], "intent": intent}
        if not intent["authorizes_deletion"]:
            return {"status": "NOT_APPLICABLE", "steps": [], "trace": trace}
        uid = self.ids.resolve_email(req["email"])
        trace["user_id"] = uid
        if not uid:
            return {"status": "INCOMPLETE", "steps": [], "trace": trace}
        audit_at = req["audit_at"]
        views = self.link_resources(uid, audit_at, req["requested_at"])
        items = []
        for rid, v in sorted(views.items()):
            self.reconstruct(v, audit_at, req["requested_at"])
            t = self.treatment(v, audit_at)
            items.append(self.assess(v, t, audit_at))
        items = [i for i in items if i]
        violations = [i for i in items if i["violation"]]
        status = "INCOMPLETE" if violations else "COMPLETE"
        steps = plan_steps(items, status)
        trace["resources"] = [{k: i[k] for k in ("system", "resource_id", "category", "treatment", "violation",
                                                 "final_op", "evidence", "why")} for i in items]
        return {"status": status, "steps": steps, "trace": trace}

    def assess(self, v: ResourceView, treatment: str, audit_at: str) -> Optional[dict]:
        st = v.state
        present, exposed, active = st.get("present"), st.get("exposed"), st.get("active")
        f_op, r_op, v_op = TREATMENT_OPS[treatment]
        ev = v.cite.source_id if v.cite else ""
        item = {"system": v.system, "resource_id": v.resource_id, "category": v.category,
                "treatment": treatment, "violation": False, "final_op": v_op, "evidence": ev,
                "finding_op": None, "remediation_op": None, "why": v.link_reasons[:3]}
        if present is None and active is None:
            # linked but never observed in a state-bearing way
            if treatment in ("RETAIN",):
                return item
            if treatment == "DELETE" and v.system == "account_service":
                return item
            return None
        if treatment == "DELETE":
            if present:
                item.update(violation=True, finding_op=f_op, remediation_op=r_op)
        elif treatment in ("REDACT", "ANONYMIZE"):
            if present is False:
                item["final_op"] = "VERIFY_RECORD_ABSENT"
            elif exposed:
                item.update(violation=True, finding_op=f_op, remediation_op=r_op)
        elif treatment == "RETAIN":
            if present is False:
                return None
        elif treatment in ("DISABLE", "BLOCK"):
            if active:
                item.update(violation=True, finding_op=f_op, remediation_op=r_op)
            elif present is False and treatment == "BLOCK":
                return None
        return item


def plan_steps(items: List[dict], status: str) -> List[dict]:
    raw = []
    for i in items:
        if status == "INCOMPLETE":
            if i["violation"]:
                raw.append((i["finding_op"], i["system"], i["resource_id"], i["evidence"]))
                raw.append((i["remediation_op"], i["system"], i["resource_id"], ""))
            raw.append((i["final_op"], i["system"], i["resource_id"], ""))
        else:
            raw.append((i["final_op"], i["system"], i["resource_id"], i["evidence"]))
    raw = sorted(set(raw), key=lambda x: (OP_RANK[x[0]], x[1], x[2]))
    steps = []
    for n, (op, sysid, rid, ev) in enumerate(raw, 1):
        steps.append({"step_no": n, "phase": OP_PHASE[op], "system_id": sysid, "resource_id": rid,
                      "operation": op, "state": OP_STATE[op], "evidence_id": ev})
    return steps


def _majority(xs):
    if not xs:
        return None
    counts = defaultdict(int)
    for x in xs:
        counts[x] += 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _default_cat(system):
    from .state import SYSTEM_CATEGORY
    return SYSTEM_CATEGORY.get(system, "UNKNOWN")


def _source_rank(o: Observation) -> int:
    return {"activity": 0, "log": 1, "text": 2, "json": 2, "sql": 2, "media": 2}.get(o.kind, 1)


def _latest_attr(obs, names):
    val = None
    for o in sorted(obs, key=lambda x: x.observed_at):
        for n in names:
            if o.get(n):
                val = str(o.get(n))
    return val
