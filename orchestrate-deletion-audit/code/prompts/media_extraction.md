# Media evidence extraction prompt

Used by `code/agent/media.py` for screenshots and PDFs that are not yet in
`media_cache.json` (live call only when `ANTHROPIC_API_KEY` and
`DELETION_AUDIT_VISION_MODEL` are set).
The cached transcriptions shipped in `media_cache.json` were produced with
this same prompt with a vision-language model during development.

---

You are reading one evidence artifact (a screenshot of an internal tool, or a
business PDF) from an e-commerce platform's privacy audit. Extract facts only;
do not decide whether a deletion is complete.

Return **only** JSON of this shape:

```json
{
  "summary": "one or two sentences describing what the artifact shows",
  "records": [
    {
      "system": "account_service | search_service | analytics_platform | support_platform | export_service | integration_service | backup_service | security_service | identity_service | marketing_platform | object_storage | legal_service",
      "resource_id": "the exact internal id shown (e.g. account_5762, data_flow_1121)",
      "record_type": "USER_PROFILE | SEARCH_INDEX_ENTRY | ANALYTICS_PROFILE | SUPPORT_MESSAGE | SUPPORT_ATTACHMENT | USER_EXPORT | DATA_FLOW | BACKUP_ARCHIVE | SECURITY_AUDIT_LOG | ACCOUNT_TOMBSTONE | MARKETING_PROFILE | LEGAL_HOLD_RECORD",
      "result": "FOUND | NOT_FOUND | IDENTIFIABLE | ANONYMIZED | REDACTED | ACTIVE | BLOCKED | DISABLED",
      "customer_reference": "user_NN if shown, else null",
      "full_name": "name shown for this record, else null",
      "email": "email shown for this record, else null",
      "phone": "phone shown for this record, else null",
      "enabled": "true/false for connectors if determinable",
      "restore_allowed": "true/false for backup archives if determinable",
      "observed_at": "ISO-8601 time the screen/document says the data was captured, if shown"
    }
  ]
}
```

Rules:
- One record per resource that is visible. Include rows that belong to other
  people too (the auditor needs them to reject decoys); copy identifiers exactly.
- Never invent ids. If a resource has no visible internal id, omit it.
- A connector shown as healthy / continuous / delivering is `enabled: true`.
- A backup with no restore block shown is `restore_allowed: true`.
- A record that shows only a stable `user_NN` reference and no name/email/phone
  is `REDACTED`.
