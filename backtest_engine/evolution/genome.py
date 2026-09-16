"""Genome: the evolvable, bounded representation of the builder's SEARCH
DISTRIBUTION (the framework's ingredients), not a single strategy.

The genome is pure JSON data. It is validated fail-closed against a strict
schema and mutated only within declared bounds. The evolutionary loop
(evolve.py) treats the genome as a chromosome: a genome that produces more
genuinely-PASS strategies (via the real backtest + skeptical audit) is a fitter
genome and propagates.

Mechanism-agnostic by construction: no mechanism class name or indicator is
hard-coded into the genome. It only tunes the *distribution* the existing,
already-validated generator/compiler/audit operate over.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


class GenomeValidationError(Exception):
    pass


# Hard bounds on every tunable gene. Mutations are clamped into these ranges,
# so a genome can never encode an out-of-range (uncompilable / unrealistic)
# ingredient. These are DATA bounds, not code changes.
BOUNDS: Dict[str, Any] = {
    "lookback_min": (5, 10),
    "lookback_max": (20, 200),
    "tp_min": (0.001, 0.02),
    "tp_max": (0.01, 0.06),
    "sl_min": (0.001, 0.015),
    "sl_max": (0.004, 0.03),
    "tp_delta": (0.0, 0.004),
    "sl_delta": (0.0, 0.003),
    "bbands_dev_min": (1.2, 1.8),
    "bbands_dev_max": (1.8, 2.8),
    "mutation_rate": (0.0, 1.0),
}

# Candidate lookback delta magnitudes a genome can carry (it picks a subset).
LOOKBACK_DELTA_POOL = [1, 2, 3, 5, 8, 13]

# Default genome (the current, hand-tuned baseline the EA starts from).
DEFAULT_GENOME: Dict[str, Any] = {
    "genome_id": "baseline",
    "lookback_min": 8,
    "lookback_max": 120,
    "lookback_deltas": [1, 2, 5],
    "tp_min": 0.008,
    "tp_max": 0.02,
    "sl_min": 0.004,
    "sl_max": 0.01,
    "tp_delta": 0.002,
    "sl_delta": 0.001,
    "bbands_dev_min": 1.5,
    "bbands_dev_max": 2.3,
    "mutation_rate": 0.7,
    "seed": 20260915,
}


@dataclass
class Genome:
    genome_id: str = "baseline"
    lookback_min: int = DEFAULT_GENOME["lookback_min"]
    lookback_max: int = DEFAULT_GENOME["lookback_max"]
    lookback_deltas: List[int] = field(default_factory=lambda: list(DEFAULT_GENOME["lookback_deltas"]))
    tp_min: float = DEFAULT_GENOME["tp_min"]
    tp_max: float = DEFAULT_GENOME["tp_max"]
    sl_min: float = DEFAULT_GENOME["sl_min"]
    sl_max: float = DEFAULT_GENOME["sl_max"]
    tp_delta: float = DEFAULT_GENOME["tp_delta"]
    sl_delta: float = DEFAULT_GENOME["sl_delta"]
    bbands_dev_min: float = DEFAULT_GENOME["bbands_dev_min"]
    bbands_dev_max: float = DEFAULT_GENOME["bbands_dev_max"]
    mutation_rate: float = DEFAULT_GENOME["mutation_rate"]
    seed: int = DEFAULT_GENOME["seed"]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def clone(self) -> "Genome":
        return Genome(**copy.deepcopy(self.to_dict()))

    def validate(self) -> "Genome":
        """Fail-closed schema + range validation. Raises GenomeValidationError."""
        for key, (lo, hi) in BOUNDS.items():
            val = getattr(self, key, None)
            if val is None:
                raise GenomeValidationError(f"missing gene '{key}'")
            if not (lo <= val <= hi):
                raise GenomeValidationError(f"gene '{key}'={val} out of bounds [{lo}, {hi}]")
        # lookback consistency
        if self.lookback_min > self.lookback_max:
            raise GenomeValidationError("lookback_min must be <= lookback_max")
        if self.tp_min > self.tp_max:
            raise GenomeValidationError("tp_min must be <= tp_max")
        if self.sl_min > self.sl_max:
            raise GenomeValidationError("sl_min must be <= sl_max")
        if self.bbands_dev_min > self.bbands_dev_max:
            raise GenomeValidationError("bbands_dev_min must be <= bbands_dev_max")
        # lookback_deltas: non-empty list of positive ints from the pool
        if not self.lookback_deltas or not isinstance(self.lookback_deltas, list):
            raise GenomeValidationError("lookback_deltas must be a non-empty list")
        for d in self.lookback_deltas:
            if not isinstance(d, int) or d <= 0 or d not in LOOKBACK_DELTA_POOL:
                raise GenomeValidationError(f"lookback_deltas entry {d!r} not in pool {LOOKBACK_DELTA_POOL}")
        # seed
        if not isinstance(self.seed, int):
            raise GenomeValidationError("seed must be an int")
        return self

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Genome":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(raw) - known
        if unknown:
            raise GenomeValidationError(f"unknown genome fields: {sorted(unknown)}")
        g = cls(**{k: v for k, v in raw.items() if k in known})
        return g.validate()


def _jitter(value: float, mag: float, rng: random.Random) -> float:
    return value + rng.uniform(-mag, mag)


def mutate(genome: Genome, rng: random.Random) -> Genome:
    """Produce a child genome by perturbing genes within bounds, then clamp +
    re-validate (fail-closed). Deterministic given rng."""
    child = genome.clone()
    child.genome_id = f"{genome.genome_id}_m{rng.randint(0, 99999)}"

    def clamp(key: str, val: float) -> float:
        lo, hi = BOUNDS[key]
        return max(lo, min(hi, val))

    # integer lookback genes
    child.lookback_min = int(clamp("lookback_min", _jitter(child.lookback_min, 2.0, rng)))
    child.lookback_max = int(clamp("lookback_max", _jitter(child.lookback_max, 20.0, rng)))

    # lookback_deltas: swap in / out one delta
    if rng.random() < 0.6:
        pool = list(LOOKBACK_DELTA_POOL)
        child.lookback_deltas = sorted(set(child.lookback_deltas) | {rng.choice(pool)})
        if len(child.lookback_deltas) > 5:
            child.lookback_deltas = child.lookback_deltas[:5]
        if rng.random() < 0.5 and len(child.lookback_deltas) > 1:
            drop = rng.choice(child.lookback_deltas)
            child.lookback_deltas = [d for d in child.lookback_deltas if d != drop]
    if not child.lookback_deltas:
        child.lookback_deltas = [2]

    # float genes
    child.tp_min = clamp("tp_min", _jitter(child.tp_min, 0.003, rng))
    child.tp_max = clamp("tp_max", _jitter(child.tp_max, 0.006, rng))
    child.sl_min = clamp("sl_min", _jitter(child.sl_min, 0.002, rng))
    child.sl_max = clamp("sl_max", _jitter(child.sl_max, 0.004, rng))
    child.tp_delta = clamp("tp_delta", _jitter(child.tp_delta, 0.001, rng))
    child.sl_delta = clamp("sl_delta", _jitter(child.sl_delta, 0.001, rng))
    child.bbands_dev_min = clamp("bbands_dev_min", _jitter(child.bbands_dev_min, 0.2, rng))
    child.bbands_dev_max = clamp("bbands_dev_max", _jitter(child.bbands_dev_max, 0.3, rng))
    child.mutation_rate = clamp("mutation_rate", _jitter(child.mutation_rate, 0.15, rng))
    child.seed = rng.randint(0, 2**31 - 1)

    # maintain monotone constraints after clamping
    child.lookback_min = min(child.lookback_min, child.lookback_max)
    child.tp_min = min(child.tp_min, child.tp_max)
    child.sl_min = min(child.sl_min, child.sl_max)
    child.bbands_dev_min = min(child.bbands_dev_min, child.bbands_dev_max)

    return child.validate()
