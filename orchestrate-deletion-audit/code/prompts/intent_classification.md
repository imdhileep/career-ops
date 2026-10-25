# Request intent classification prompt

Fallback used by `code/agent/intent.py` only when the deterministic
multilingual rules find neither a deletion cue nor a non-deletion cue and
`ANTHROPIC_API_KEY` is set (default model `claude-haiku-5-5`, override with
`DELETION_AUDIT_TEXT_MODEL`). In the supplied dataset every request is decided
by the rules, so this prompt was not needed for the final run.

---

You classify a customer privacy request for a deletion-audit workflow.
The text may be in any language.

Request:
"""
{{REQUEST_TEXT}}
"""

Decide whether the person authorises **deletion / erasure of their account or
personal data** (including asking to verify or finish an earlier deletion).
Access, export/portability, correction, marketing opt-out, login help or any
request that explicitly says not to delete is **not** a deletion.

Return only JSON:
{"authorizes_deletion": true|false, "intent": "deletion|access|export|correction|marketing_opt_out|account_support|other", "reason": "<short>"}
