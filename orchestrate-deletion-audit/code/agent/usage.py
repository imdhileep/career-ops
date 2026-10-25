"""Model-usage accounting for usage_report.md."""
from __future__ import annotations

import json
from collections import defaultdict

# USD per million tokens (input, output) for the cost estimate; set
# DELETION_AUDIT_PRICES='{"model-id": [in, out]}' to override per model.
import os as _os
PRICES = json.loads(_os.environ.get("DELETION_AUDIT_PRICES", "{}"))


class Usage:
    def __init__(self):
        self.calls = defaultdict(int)
        self.inp = defaultdict(int)
        self.out = defaultdict(int)
        self.cache_hits = defaultdict(int)

    def record(self, model: str, input_tokens: int, output_tokens: int):
        self.calls[model] += 1
        self.inp[model] += int(input_tokens)
        self.out[model] += int(output_tokens)

    def cache_hit(self, kind: str):
        self.cache_hits[kind] += 1

    def summary(self, n_requests: int) -> dict:
        models = {}
        total_cost = 0.0
        for m in sorted(self.calls):
            pi, po = PRICES.get(m, (3.0, 15.0))
            cost = self.inp[m] / 1e6 * pi + self.out[m] / 1e6 * po
            total_cost += cost
            models[m] = {"calls": self.calls[m], "input_tokens": self.inp[m], "output_tokens": self.out[m],
                         "cost_usd": round(cost, 6)}
        tin, tout = sum(self.inp.values()), sum(self.out.values())
        return {
            "requests": n_requests,
            "models": models,
            "total_calls": sum(self.calls.values()),
            "total_input_tokens": tin,
            "total_output_tokens": tout,
            "total_tokens": tin + tout,
            "avg_tokens_per_request": round((tin + tout) / n_requests, 3) if n_requests else 0,
            "total_cost_usd": round(total_cost, 6),
            "cost_per_request_usd": round(total_cost / n_requests, 8) if n_requests else 0,
            "cache_hits": dict(self.cache_hits),
        }

    def dump(self, path: str, n_requests: int):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.summary(n_requests), fh, indent=2)
