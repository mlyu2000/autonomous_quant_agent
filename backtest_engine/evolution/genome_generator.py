"""Genome -> spec bridge.

Given a genome (a distribution over the builder's ingredients), generate
distinct-thesis specs the way the existing generator does, but clamp every
tunable gene (indicator lookbacks, TP/SL magnitudes, Bollinger dev) INTO the
genome's declared bounds before validation. The shared grammar and schema still
validate fail-closed, and dedupe is by the project's mechanism-class + indicator
core + direction key — so the genome tunes WHERE the search looks, never WHAT
is valid.
"""
from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from synthesis_layer.strategy_generator import (
    MECHANISM_CLASSES,
    build_spec_raw,
)
from synthesis_layer.strategy_schema import (
    validate_spec,
    dedupe_key,
    write_spec,
    StrategySpec,
)


def _clamp_lookback(lb: int, genome) -> int:
    return max(int(genome.lookback_min), min(int(genome.lookback_max), lb))


def _clamp_exit_percent(mode: str, value: float, genome) -> float:
    if mode == "percent":
        # TP and SL live in different bands; decide by relative size against
        # the genome's tp/sl ranges (TP is the wider target).
        if value >= genome.sl_max or value >= genome.tp_min:
            return max(genome.tp_min, min(genome.tp_max, value))
        return max(genome.sl_min, min(genome.sl_max, value))
    return value


def _clamp_bbands_dev(dev: float, genome) -> float:
    return max(genome.bbands_dev_min, min(genome.bbands_dev_max, dev))


def apply_genome(raw: Dict[str, Any], genome) -> Dict[str, Any]:
    """Clamp a raw spec's genes into the genome's bounds (in place copy)."""
    out = copy.deepcopy(raw)
    for ind in out.get("indicators", []):
        if "lookback" in ind:
            ind["lookback"] = _clamp_lookback(int(ind["lookback"]), genome)
        if ind.get("name") == "bbands" and "dev" in ind.get("params", {}):
            ind["params"]["dev"] = _clamp_bbands_dev(float(ind["params"]["dev"]), genome)
    for rule in out.get("exit_rules", []):
        params = rule.get("params", {})
        if params.get("mode") == "percent" and "value" in params:
            params["value"] = _clamp_exit_percent("percent", float(params["value"]), genome)
        if params.get("mode") == "atr" and "value" in params:
            # leave ATR multipliers alone (not a genome gene in MVP)
            pass
    return out


def genome_generate_specs(genome, count: int, out_dir: Path,
                          rng: Optional[random.Random] = None) -> List[StrategySpec]:
    """Generate `count` distinct specs shaped by `genome`. Fail-closed: any
    spec that does not validate is dropped (not forced through)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if rng is None:
        rng = random.Random(genome.seed)
    specs: List[StrategySpec] = []
    seen = set()
    attempts = 0
    max_attempts = count * 60
    while len(specs) < count and attempts < max_attempts:
        attempts += 1
        mechanism_class = rng.choice(sorted(MECHANISM_CLASSES))
        mutate = len(specs) > 0  # first spec = clean baseline template
        raw = build_spec_raw(mechanism_class, len(specs), mutate=mutate, rng=rng)
        raw = apply_genome(raw, genome)
        try:
            spec = validate_spec(raw)
        except Exception:
            continue  # fail-closed: drop invalid, do not force
        key = dedupe_key(spec)
        if key in seen:
            continue
        seen.add(key)
        write_spec(spec, out_dir)
        specs.append(spec)
    return specs
