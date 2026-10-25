# Usage report — final full-dataset run

Run: `python3 code/main.py` over `dataset/deletion_requests.csv` (518 requests),
producing the submitted `output.csv`. Raw counters written by the run:
`code/evaluation/usage_run.json`.

## Final run (produced `output.csv`)

| Model provider | Model | Model calls | Input tokens | Output tokens | Total tokens | Cost (USD) |
|---|---|---|---|---|---|---|
| Anthropic | `claude-sonnet-5-5` (vision, screenshots/PDFs) | 0 (15 cache hits) | 0 | 0 | 0 | 0.00 |
| Anthropic | `claude-haiku-5-5` (intent fallback) | 0 | 0 | 0 | 0 | 0.00 |
| **Overall** | | **0** | **0** | **0** | **0** | **0.00** |

* Requests: 518 · average tokens per request: **0** · estimated cost per request: **$0.00**.
* Runtime ≈ 18 s on one CPU core, no network access needed.

Why zero: the reasoning (identity resolution, linking, state reconstruction,
policy and planning) is deterministic code. The only places where a model adds
value are (a) reading screenshots and PDFs and (b) classifying ambiguous request
text. For (a) the 11 screenshots and 4 PDFs were transcribed **once** into
`code/agent/media_cache.json` (keyed by file hash) with Claude vision using
`code/prompts/media_extraction.md`; the run reuses those transcripts (15 cache
hits). For (b) all 518 texts were decided by the multilingual rules, so the
LLM fallback was never triggered.

## One-time development cost (not part of the run)

The screenshot/PDF transcription and the whole build were done inside a Claude
Code session (model `claude-opus-5-5`). Session totals reported by the harness at
the time of writing: ~29 K uncached input tokens, ~39.1 M cache-read tokens,
~0.47 M cache-write tokens, ~127 K output tokens, ≈ $14.09 — this covers coding,
data exploration and evaluation, of which the 15 media transcriptions are a
small part (≈ 15 × ~2 K image/document input tokens and ~0.4 K output tokens).

## Projected cost if the cache is cold

If every media file were re-read live with `claude-sonnet-5-5`
($3 / M input, $15 / M output): ~15 calls × (~2,000 input + ~400 output tokens)
≈ 30 K input + 6 K output ≈ **$0.18 total ≈ $0.00035 per request**. The intent
fallback (`claude-haiku-5-5`, ~250 input + ~40 output tokens per call) would add
≈ $0.0005 per ambiguous request.
