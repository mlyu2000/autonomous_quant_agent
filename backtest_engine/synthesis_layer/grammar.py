"""Shared condition grammar for the strategy spec model.

Single source of truth used by BOTH the generator (emission) and the
compiler (evaluation), plus spec validation (parsing). Unknown conditions
fail closed: validation rejects them and evaluation returns None/False.

A condition is one or more primitives joined by the literal keyword ` and `.
Each primitive is `LHS OP RHS` where OP is one of `>=`, `<=`, `>`, `<` and
LHS/RHS are *value tokens*:

    close              # current bar close
    <number>           # numeric literal, e.g. 30, -5, 1500.5
    volume_spike       # shorthand for volume_ratio(20) > 2.0 (see below)
    <indicator>        # e.g. rsi, sma, ema, macd_hist, atr, adx, cci, mfi, obv, vwap, volume_ratio
    <indicator>(N)     # indicator with an explicit period, e.g. sma(50)
    bbands.top         # upper Bollinger band line
    bbands.bot         # lower Bollinger band line
    donchian.mid       # Donchian channel middle

Examples of valid primitives:
    close > sma(50)      close < ema(20)
    rsi(14) < 30         rsi > 70
    macd_hist > 0        macd_hist < 0
    close > bbands.top   close < bbands.bot
    close > donchian.mid
    atr(14) > 0.002      adx(14) > 25      cci(20) > 100
    close > 1500         volume_ratio(20) > 2.0

Rules enforced (fail-closed):
  - Only whitelisted indicator names are allowed.
  - An indicator with an explicit period in a condition must match the period
    declared in the spec's `indicators` list (checked at compile time).
  - `bbands.top/bot` only pair with the bbands indicator; `donchian.mid` only
    with the donchian indicator.
  - `volume_spike` is syntactic sugar for `volume_ratio(20) > 2.0`.
  - Anything else is a ConditionParseError -> the spec is rejected.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

INDICATOR_NAMES = [
    "sma", "ema", "wma", "rsi", "macd", "macd_hist", "stoch", "stoch_k",
    "stoch_rsi", "adx", "atr", "bbands", "donchian", "psar", "ao", "cci",
    "mfi", "obv", "vwap", "volume_ratio",
]

# Indicators that are plain lines (current value via [0]).
LINE_INDICATORS = {
    "sma", "ema", "wma", "rsi", "macd", "macd_hist", "stoch", "stoch_k",
    "stoch_rsi", "adx", "atr", "cci", "mfi", "obv", "vwap", "volume_ratio",
    "psar", "ao",
}

# Indicators that expose band lines rather than a single line.
BAND_INDICATORS = {"bbands": ("top", "bot"), "donchian": ("mid", "upper", "lower")}

DEFAULT_PERIOD = {
    "sma": 20, "ema": 20, "wma": 20, "rsi": 14, "macd": 26, "macd_hist": 26,
    "stoch": 14, "stoch_k": 14, "stoch_rsi": 14, "adx": 14, "atr": 14,
    "bbands": 20, "donchian": 20, "psar": 10, "ao": 34, "cci": 20, "mfi": 14,
    "obv": 0, "vwap": 20, "volume_ratio": 20,
}

_OP_RE = re.compile(r"^(?P<op>>=|<=|>|<)$")


class ConditionParseError(ValueError):
    pass


def _parse_value(tok: str) -> Dict[str, Any]:
    """Parse a single value token. Raises ConditionParseError if unknown."""
    tok = tok.strip()
    if not tok:
        raise ConditionParseError("empty value token")
    if tok == "close":
        return {"kind": "close"}
    if tok == "volume_spike":
        return {"kind": "volume_spike"}
    m = re.match(r"^bbands\.(top|bot)$", tok)
    if m:
        return {"kind": "band", "host": "bbands", "line": m.group(1)}
    m = re.match(r"^donchian\.(mid|upper|lower)$", tok)
    if m:
        return {"kind": "band", "host": "donchian", "line": m.group(1)}
    m = re.match(r"^(?P<name>[a-z_][a-z0-9_]*)\((?P<period>\d+)\)$", tok)
    if m:
        name = m.group("name")
        if name not in INDICATOR_NAMES:
            raise ConditionParseError(f"unknown indicator '{name}'")
        return {"kind": "indicator", "name": name, "period": int(m.group("period"))}
    m = re.match(r"^[a-z_][a-z0-9_]*$", tok)
    if m:
        if tok not in INDICATOR_NAMES:
            raise ConditionParseError(f"unknown indicator '{tok}'")
        return {"kind": "indicator", "name": tok, "period": None}
    try:
        return {"kind": "number", "value": float(tok)}
    except ValueError as exc:
        raise ConditionParseError(f"unrecognized value token {tok!r}") from exc


def _split_primitive(part: str) -> Dict[str, Any]:
    """Split one primitive string into lhs/op/rhs value tokens."""
    # Find the operator by scanning for a token that is a valid operator and
    # whose neighbors are value tokens.
    tokens = part.split()
    if len(tokens) != 3:
        raise ConditionParseError(f"primitive must be 'LHS OP RHS', got {part!r}")
    lhs_tok, op_tok, rhs_tok = tokens
    if not _OP_RE.match(op_tok):
        raise ConditionParseError(f"invalid operator {op_tok!r} in {part!r}")
    return {
        "lhs": _parse_value(lhs_tok),
        "op": op_tok,
        "rhs": _parse_value(rhs_tok),
    }


def parse_condition(text: str) -> List[Dict[str, Any]]:
    """Parse a condition string into a list of primitive dicts (AND-composed).

    Raises ConditionParseError on anything outside the whitelist (fail-closed).
    """
    if not text or not isinstance(text, str):
        raise ConditionParseError(f"empty condition: {text!r}")
    primitives: List[Dict[str, Any]] = []
    for part in text.split(" and "):
        part = part.strip()
        if not part:
            raise ConditionParseError(f"empty part in condition: {text!r}")
        prim = _split_primitive(part)
        # `volume_spike` shorthand: expand to volume_ratio(20) > 2.0
        if prim["lhs"]["kind"] == "volume_spike":
            if prim["op"] != ">":
                raise ConditionParseError("volume_spike only supports '>'")
            prim = {
                "lhs": {"kind": "indicator", "name": "volume_ratio", "period": 20},
                "op": ">",
                "rhs": {"kind": "number", "value": float(prim["rhs"]["value"])},
            }
        primitives.append(prim)
    if not primitives:
        raise ConditionParseError(f"no primitives in condition: {text!r}")
    return primitives


def evaluate_primitive(prim: Dict[str, Any], close: float, values: Dict[str, Any]) -> Optional[bool]:
    """Evaluate one primitive. Returns True/False, or None if it cannot be
    evaluated (indicator missing / warmup) — callers treat None as False."""
    if close is None:
        return None

    def resolve(v: Dict[str, Any]):
        kind = v["kind"]
        if kind == "close":
            return float(close)
        if kind == "number":
            return v["value"]
        if kind == "indicator":
            name = v["name"]
            if name not in values or values[name] is None:
                return None
            ind = values[name]
            if name in LINE_INDICATORS:
                try:
                    return float(ind[0])
                except (TypeError, ValueError):
                    return None
            # band host indicators are only reached via band tokens; if
            # referenced bare, treat as unavailable.
            return None
        if kind == "band":
            host = v["host"]
            line = v["line"]
            if host not in values or values[host] is None:
                return None
            ind = values[host]
            try:
                return float(getattr(ind.lines, line)[0])
            except (AttributeError, IndexError, TypeError, ValueError):
                return None
        return None

    lv = resolve(prim["lhs"])
    rv = resolve(prim["rhs"])
    if lv is None or rv is None:
        return None
    op = prim["op"]
    if op == ">":
        return lv > rv
    if op == "<":
        return lv < rv
    if op == ">=":
        return lv >= rv
    if op == "<=":
        return lv <= rv
    return None


def evaluate_condition(primitives: List[Dict[str, Any]], close: float, values: Dict[str, Any]) -> bool:
    """AND-combined evaluation. Missing data (None) => False (fail-closed)."""
    for prim in primitives:
        result = evaluate_primitive(prim, close, values)
        if result is None or not result:
            return False
    return True


def condition_refs(primitives: List[Dict[str, Any]]) -> set:
    """Set of (indicator_name, period_or_None) referenced by a condition."""
    refs = set()
    for prim in primitives:
        for side in ("lhs", "rhs"):
            v = prim[side]
            if v["kind"] == "indicator":
                refs.add((v["name"], v["period"]))
            elif v["kind"] == "band":
                refs.add((v["host"], None))
    return refs


def validate_spec_conditions(spec: Dict[str, Any]) -> None:
    """Fail-closed validation: every entry rule condition must parse."""
    for i, rule in enumerate(spec.get("entry_rules", [])):
        text = rule.get("condition", "")
        try:
            parse_condition(text)
        except ConditionParseError as exc:
            raise ConditionParseError(f"entry_rules[{i}]: {exc}") from exc


def refs_match_declared(condition_refs, declared: Dict[str, int]) -> bool:
    """Check every condition indicator reference is covered by spec
    declarations: name must be declared; if the condition pins a period it
    must equal the declared period."""
    for name, period in condition_refs:
        if name not in declared:
            return False
        if period is not None and declared[name] != period:
            return False
    return True
