"""Every tunable number and every model ID in the project. One place, on purpose.

Live model IDs come from `done models`, which writes `models.json` at the repo root.
The hardcoded tables below are the fallback when that file is absent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_JSON = REPO_ROOT / "models.json"

BASE_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1")

# --- model tiers ------------------------------------------------------------
# depth 0 = root planner (strong), depth 1 = mid (cheap+fast), depth 2+ = leaves (tiny).
# Confirmed live on Crusoe 2026-10-17 via `done models` (see DECISIONS.md).
# depth 0 plans; every node that does actual work runs on Nemotron-3-Super, and a cheaper
# model from a different family verifies it. Measured, not assumed: Nemotron Nano at the leaves
# burned 23-39k tokens per node and failed most checks, while Super finished the same work in
# 14k and 3 tool steps. (DECISIONS 15:25)
DEFAULT_TIERS: dict[int, str] = {
    0: "deepseek-ai/DeepSeek-V4-Pro",
    1: "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
    2: "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
}

# A node's verifier must be a DIFFERENT FAMILY than the node's own model.
# Families as the live IDs actually spell them: "deepseek-ai", "zai-org", not "deepseek"/"zai".
DEFAULT_VERIFIER_FOR_FAMILY: dict[str, str] = {
    "nvidia": "openai/gpt-oss-120b",
    "openai": "google/gemma-4-31b-it",
    "deepseek-ai": "zai-org/GLM-5.3",
    "zai-org": "deepseek-ai/DeepSeek-V4.1-Flash",
    "google": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
    "qwen": "openai/gpt-oss-120b",
}

# No meta-llama on Crusoe; gpt-oss-120b is the cheap always-there model.
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-120b"

# Cheap model used only by the drift judge (monitor), never for real work.
DEFAULT_DRIFT_JUDGE = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

# USD per 1M tokens, (prompt, completion). Used for the cost line in the report.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.05, 0.20),
    "google/gemma-4-31b-it": (0.14, 0.40),
    "deepseek-ai/DeepSeek-V4-Pro": (0.50, 2.00),
    "deepseek-ai/DeepSeek-V4.1-Flash": (0.27, 1.10),
    "deepseek-ai/Deepseek-V4-Flash": (0.27, 1.10),
    "zai-org/GLM-5.3": (1.00, 4.00),
    "zai-org/GLM-5.3-Flash": (0.20, 0.80),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.05, 0.20),
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B": (0.10, 0.40),
    "nvidia/Nemotron-3.5-Lightning-30B-A3B": (0.05, 0.20),
    "Qwen/Qwen3.8-27B": (0.10, 0.30),
}
UNKNOWN_PRICE = (0.30, 1.20)  # conservative guess so cost is never silently 0


def family(model_id: str) -> str:
    """'nvidia/Nemotron-3-Nano' -> 'nvidia'. Family is the verifier-independence unit."""
    return model_id.split("/")[0].lower()


@dataclass
class Config:
    # recursion limits — each one independently guarantees termination (README §what stops it)
    MAX_DEPTH: int = 3
    MIN_SPLIT_BUDGET: int = 8_000
    MAX_CHILDREN: int = 5
    MAX_ATTEMPTS: int = 2
    MAX_TOOL_STEPS_PER_LEAF: int = 25

    # budget
    RESERVE_FRACTION: float = 0.15
    MIN_LEAF_BUDGET: int = 3_000
    # A worker may spend at most this share of its NODE's allocation; the rest belongs to
    # writing the checks, verifying them, and one retry. Verification cost scales with the
    # number of nodes, so a single global reserve is the wrong shape. (DECISIONS 14:15)
    WORKER_SHARE: float = 0.60
    MIN_VERIFY_RESERVE: int = 6_000

    # llm
    MAX_CONCURRENT_LLM: int = 6
    RETRIES: int = 3
    SHELL_TIMEOUT: int = 30
    OUTPUT_TRUNCATE: int = 4096

    # monitor
    LOOP_REPEAT_THRESHOLD: int = 3
    NO_PROGRESS_STEPS: int = 5
    DRIFT_EVERY_N_STEPS: int = 6
    DRIFT_THRESHOLD: int = 7
    BUDGET_WARN_FRACTION: float = 0.55   # warn just before the worker's hard ceiling

    tiers: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_TIERS))
    verifier_for_family: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_VERIFIER_FOR_FAMILY)
    )
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    drift_judge_model: str = DEFAULT_DRIFT_JUDGE

    def model_for_depth(self, depth: int) -> str:
        """Tier lookup that clamps: depth 5 gets the deepest tier we defined."""
        if depth in self.tiers:
            return self.tiers[depth]
        deepest = max(self.tiers)
        return self.tiers[deepest if depth > deepest else min(self.tiers)]

    def verifier_for(self, worker_model: str) -> str:
        """Pick a verifier from a different family. Never returns the worker's own family."""
        fam = family(worker_model)
        chosen = self.verifier_for_family.get(fam)
        if chosen and family(chosen) != fam:
            return chosen
        # last resort: any configured verifier of another family
        for candidate in list(self.verifier_for_family.values()) + [self.fallback_model]:
            if family(candidate) != fam:
                return candidate
        raise ValueError(f"no cross-family verifier available for {worker_model!r}")

    def cross_family_fallbacks(self, worker_model: str) -> list[str]:
        """Models of a different family than the worker, for llm.py to fall back onto."""
        fam = family(worker_model)
        pool = list(dict.fromkeys(list(self.verifier_for_family.values())
                                  + list(self.tiers.values()) + [self.fallback_model]))
        return [m for m in pool if family(m) != fam]

    def price_for(self, model_id: str) -> tuple[float, float]:
        return self.prices.get(model_id, UNKNOWN_PRICE)


def load_config() -> Config:
    """Config from defaults, overlaid with models.json if `done models` has run."""
    cfg = Config()
    if MODELS_JSON.exists():
        try:
            data = json.loads(MODELS_JSON.read_text())
        except json.JSONDecodeError:
            return cfg
        if tiers := data.get("tiers"):
            cfg.tiers = {int(k): v for k, v in tiers.items()}
        if vf := data.get("verifier_for_family"):
            cfg.verifier_for_family = vf
        if prices := data.get("prices"):
            cfg.prices.update({k: tuple(v) for k, v in prices.items()})
        cfg.fallback_model = data.get("fallback_model", cfg.fallback_model)
        cfg.drift_judge_model = data.get("drift_judge_model", cfg.drift_judge_model)
    return cfg


def save_models_json(payload: dict) -> Path:
    MODELS_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    return MODELS_JSON
