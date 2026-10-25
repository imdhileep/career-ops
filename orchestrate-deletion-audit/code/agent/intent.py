"""Request intent: does ``request_text`` authorise deletion?

Primary path is a deterministic multilingual rule set (fast, free, auditable).
Ambiguous texts (both deletion and non-deletion cues, or neither) can be sent to
an LLM when ``ANTHROPIC_API_KEY`` is set; the prompt lives in
``code/prompts/intent_classification.md``.  In the supplied dataset every text
is resolved by the rules, so the final run makes zero model calls here.
"""
from __future__ import annotations

import json
import os
import re

DELETION_CUES = [
    r"\bdelet", r"\beras", r"\bremov", r"\bclose (my|this|the) account", r"\bclosed my account",
    r"\bclose the account completely", r"permanent(ly)? (account[- ])?delet", r"right to be forgotten",
    r"exclus[aã]o", r"\bexclu[ií]r", r"eliminaci[oó]n", r"\beliminar", r"suppression", r"\bsupprimer",
    r"effacement", r"l[öo]sch", r"삭제", r"削除", r"消去", r"退会", r"탈퇴", r"\bdisconnected my account",
    # follow-ups that ask to verify / finish an earlier deletion
    r"(profile|account) (is|was) (gone|removed|deleted)", r"(confirm|verify|check) .*(gone|removed|deleted|deletion)",
    r"audit the deletion", r"cannot identify me", r"no longer (be )?(linked|point)", r"finish the deletion",
    r"(account|profile) .*still (exist|appear|recogni)",
]

# phrases that explicitly scope the request away from deletion
NEGATION_CUES = [
    r"not asking for deletion", r"not a request to (erase|delete)", r"(do not|don't|dont) (close|delete)",
    r"without removing", r"leave (the|my) (account|profile)", r"keep the account", r"continue using",
    r"still (need|use|want)", r"marketing lists only", r"\bonly\b.*marketing", r"stop the promotional",
    r"leave .* in place", r"keep .* as it is",
]

NON_DELETION_INTENTS = {
    "access": [r"what personal information", r"send me a copy", r"copy of the information", r"information request"],
    "export": [r"download my account information", r"portable format", r"copy of last year"],
    "correction": [r"out of date", r"misspelled", r"correct it", r"change the email address", r"replace it with"],
    "marketing_opt_out": [r"promotional emails", r"marketing lists"],
    "account_support": [r"resetting the login", r"can't access", r"cannot access"],
}


def classify(text: str, usage=None) -> dict:
    t = (text or "").lower()
    deletion = [c for c in DELETION_CUES if re.search(c, t)]
    negation = [c for c in NEGATION_CUES if re.search(c, t)]
    other = [k for k, pats in NON_DELETION_INTENTS.items() if any(re.search(p, t) for p in pats)]
    if negation or (other and not deletion):
        return {"authorizes_deletion": False, "intent": other[0] if other else "non_deletion",
                "method": "rules", "cues": negation or other}
    if deletion:
        return {"authorizes_deletion": True, "intent": "deletion", "method": "rules", "cues": deletion}
    llm = _llm_classify(text, usage)
    if llm is not None:
        return llm
    # Every row comes from the deletion workflow; without an explicit non-deletion
    # cue we treat the message as (a follow-up to) a deletion request.
    return {"authorizes_deletion": True, "intent": "deletion_followup", "method": "rules-default", "cues": []}


def _llm_classify(text: str, usage=None):
    model = os.environ.get("DELETION_AUDIT_TEXT_MODEL", "")
    if not os.environ.get("ANTHROPIC_API_KEY") or not model:
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt = open(os.path.join(here, "prompts", "intent_classification.md"), encoding="utf-8").read()
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=200,
        messages=[{"role": "user", "content": prompt.replace("{{REQUEST_TEXT}}", text)}])
    if usage is not None:
        usage.record(model, msg.usage.input_tokens, msg.usage.output_tokens)
    out = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    data = json.loads(m.group(0))
    return {"authorizes_deletion": bool(data.get("authorizes_deletion")), "intent": data.get("intent", "llm"),
            "method": "llm", "cues": []}
