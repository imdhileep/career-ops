# Usage report — final full-dataset run

Run: `python3 code/main.py` over `dataset/deletion_requests.csv` (518 requests),
producing the submitted `output.csv`. Raw counters written by the run:
`code/evaluation/usage_run.json`.

## Final run (produced `output.csv`)

| Model provider | Model | Model calls | Input tokens | Output tokens | Total tokens | Cost (USD) |
|---|---|---|---|---|---|---|
| Anthropic API (optional) | vision model for screenshots/PDFs (`DELETION_AUDIT_VISION_MODEL`) | 0 (15 cache hits) | 0 | 0 | 0 | 0.00 |
| Anthropic API (optional) | text model for the intent fallback (`DELETION_AUDIT_TEXT_MODEL`) | 0 | 0 | 0 | 0 | 0.00 |
| **Overall** | | **0** | **0** | **0** | **0** | **0.00** |

* Requests: 518 · average tokens per request: **0** · estimated cost per request: **$0.00**.
* Runtime ≈ 18 s on one CPU core, no network access needed.

Why zero: the reasoning (identity resolution, linking, state reconstruction,
policy and planning) is deterministic code. The only places where a model adds
value are (a) reading screenshots and PDFs and (b) classifying ambiguous request
text. For (a) the 11 screenshots and 4 PDFs were transcribed **once** into
`code/agent/media_cache.json` (keyed by file hash) with a vision-language model
using `code/prompts/media_extraction.md`; the run reuses those transcripts
(15 cache hits). For (b) all 518 texts were decided by the multilingual rules,
so the LLM fallback was never triggered.

## One-time development cost (not part of the run)

The media transcription was done during development in an AI-assisted coding
session (see the chat transcript). It is a one-off: ≈ 15 calls ×
(~2,000 input + ~400 output tokens) ≈ 30 K input and 6 K output tokens.

## Projected cost if the cache is cold

Re-reading every media file live at a mid-tier vision price ($3 / M input,
$15 / M output): ~30 K input + 6 K output ≈ **$0.18 total ≈ $0.00035 per
request**. The intent fallback at a small-model price ($1 / M input, $5 / M
output; ~250 input + ~40 output tokens per call) would add ≈ $0.0005 per
ambiguous request.
