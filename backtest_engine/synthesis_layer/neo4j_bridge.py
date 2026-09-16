"""
Neo4j Knowledge-Base bridge: turn graph-extracted strategies into VALID
backtest specs.

The knowledge base (knowledge_base/) ingests YouTube strategy transcripts
into a Neo4j graph (Strategy / Fragment / Indicator / Risk / Management
nodes). Those strategies are free-text and often reference indicators that
do not map to the backtest grammar (e.g. "Price Action", "VIX",
"Support/Resistance Levels").

This bridge is the single code path that converts a graph Strategy into a
spec the compiler can actually run. Honesty rules (MVP):
  - Only emit a spec if it PARSES the shared grammar AND compiles.
    Anything that cannot be faithfully mapped is SKIPPED (counted), never
    faked with a placeholder condition.
  - No network access is required beyond the local bolt endpoint; if the
    graph is unreachable we raise, we do NOT fabricate specs.
  - Credentials come from the environment or knowledge_base/.env — never
    hard-coded, never printed.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def _load_kb_env() -> Dict[str, str]:
    """Load NEO4J_* credentials from env, falling back to knowledge_base/.env."""
    env: Dict[str, str] = {
        "NEO4J_URI": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "NEO4J_USER": os.environ.get("NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD", ""),
    }
    if not env["NEO4J_PASSWORD"]:
        env_path = PROJECT_ROOT / "knowledge_base" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key in env and val.strip():
                        env[key] = val.strip()
    return env


def _driver(env: Dict[str, str]):
    from neo4j import GraphDatabase  # imported lazily so the module loads
    return GraphDatabase.driver(
        env["NEO4J_URI"],
        auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]),
        connection_timeout=15,
    )


# ---------------------------------------------------------------------------
# Indicator name -> grammar indicator mapping
# ---------------------------------------------------------------------------
# Returns (grammar_name, lookback) or None if the free-text indicator cannot
# be faithfully mapped. Lookback is parsed when the name encodes a period.
def _parse_period(text: str) -> Optional[int]:
    # "20 period moving average", "5m", "14 rsi", "200 ema"
    m = re.search(r"(\d{1,4})\s*(?:period|bar|day|days|bars|m|min|minute)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"^(\d{1,4})\s*(ema|sma|ma|wma|rsi|bbands|bb|atr|adx|cci|mfi)$", text, re.I)
    if m:
        return int(m.group(1))
    return None


def _map_indicator(name: Any) -> Optional[Tuple[str, int]]:
    n = str(name).strip() if name is not None else ""
    low = n.lower()
    if not low:
        return None
    p = _parse_period(low)

    def pick(default: int) -> int:
        return p if (p and 2 <= p <= 400) else default

    # Moving averages
    if "moving average" in low or low in ("sma", "ma", "mavg") or "period moving" in low:
        return ("sma", pick(20))
    if re.search(r"\bema\b", low):
        return ("ema", pick(20))
    if re.search(r"\bwma\b", low):
        return ("wma", pick(20))
    if low == "macd" or low.startswith("macd"):
        return ("macd_hist", 26)
    if "rsi" in low and "stoch" not in low:
        return ("rsi", pick(14))
    if "bollinger" in low or low == "bbands" or low == "bb":
        return ("bbands", pick(20))
    if "atr" in low:
        return ("atr", pick(14))
    if "adx" in low:
        return ("adx", pick(14))
    if "stoch" in low:
        return ("stoch_k", pick(14))
    if "cci" in low:
        return ("cci", pick(20))
    if "mfi" in low:
        return ("mfi", pick(14))
    if "obv" in low:
        return ("obv", 20)
    if "vwap" in low:
        return ("vwap", 20)
    if "volume" in low:
        return ("volume_ratio", pick(20))
    if "donchian" in low:
        return ("donchian", pick(20))
    # Non-mappable free-text (price action, S/R levels, VIX, options, delta,
    # candlesticks) are intentionally NOT mapped — we refuse to guess.
    return None


# ---------------------------------------------------------------------------
# Mechanism + direction + timeframe classification (deterministic, keyword)
# ---------------------------------------------------------------------------
_MECHANISM_PATTERNS = [
    ("reversion", r"\b(reversion|mean.?revert|overbought|oversold|fade|retrace|pullback to (rsi|level))\b"),
    ("vol_snapback", r"\b(volatili|vol squeeze|squeeze|snapback|expansion|spike)\b"),
    ("time_decay", r"\b(time.?based|hold for|let it run|time stop|time frame exit)\b"),
    ("trend_momentum", r"\b(trend|momentum|crossover|breakout|alligator|continuation|follow)\b"),
]


def _classify_mechanism(text: str) -> str:
    low = (text or "").lower()
    for cls, pat in _MECHANISM_PATTERNS:
        if re.search(pat, low):
            return cls
    return "trend_momentum"


def _classify_direction(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"\blong\b|bullish|buy", low):
        if re.search(r"\bshort\b|bearish|sell", low):
            return "long"  # bias toward long when both mentioned
        return "long"
    if re.search(r"\bshort\b|bearish|sell", low):
        return "short"
    return "long"  # default when "Both"/ambiguous


_TIMEFRAME_MAP = {
    "m1": "M1", "1m": "M1", "1 min": "M1", "1 minute": "M1",
    "m5": "M5", "5m": "M5",
    "m15": "M15", "15m": "M15", "15 min": "M15", "15 minute": "M15",
    "h1": "H1", "1h": "H1", "1 hour": "H1", "hourly": "H1",
    "h4": "H4", "4h": "H4", "4 hour": "H4", "4h timeframe": "H4",
    "d1": "D1", "daily": "D1", "day": "D1", "daily chart": "D1",
    "weekly": "D1",
}
# The frozen data lake has exactly these timeframes.
_ALLOWED_TF = {"M1", "M15", "H1", "H4", "D1"}


def _classify_timeframe(indicator_timeframes: List[str], text: str) -> str:
    candidates = list(indicator_timeframes or []) + [text]
    for cand in candidates:
        key = (cand or "").strip().lower()
        if key in _TIMEFRAME_MAP:
            tf = _TIMEFRAME_MAP[key]
            if tf in _ALLOWED_TF:
                return tf
    return "H4"  # default: H4 (present in the data lake)


def _parse_tp_sl_ratio(tp_text: Any) -> Optional[float]:
    """Extract a TP:SL multiplier (e.g. '1.5:1', '1.5 times the risk')."""
    if tp_text is None:
        return None
    if isinstance(tp_text, (list, tuple)):
        tp_text = " ".join(str(x) for x in tp_text)
    low = str(tp_text).lower()
    if not low or low in ("none", "null", "not specified"):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[:x]\s*1\b", low)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*times", low)
    if m:
        return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Spec construction from a graph strategy
# ---------------------------------------------------------------------------
def _build_spec_from_strategy(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert one Neo4j Strategy row into a raw spec dict, or None if it
    cannot be faithfully mapped to the grammar."""
    name = s.get("name") or ""
    desc = s.get("desc") or ""
    combined_text = " ".join(
        str(x) for x in [
            name, desc, s.get("trend"), s.get("trig"),
            s.get("stop"), s.get("tp"),
        ] if x is not None
    )

    # Map indicators
    indicators: List[Dict[str, Any]] = []
    seen: set = set()
    for ind_name in s.get("inds") or []:
        mapped = _map_indicator(ind_name)
        if mapped is None:
            continue
        gname, lb = mapped
        key = (gname, lb)
        if key in seen:
            continue
        seen.add(key)
        params = {}
        if gname == "bbands":
            params = {"dev": 2.0}
        indicators.append({"name": gname, "lookback": lb, "shift": 1, "params": params})
    if not indicators:
        return None  # nothing mappable -> refuse to guess

    mechanism = _classify_mechanism(combined_text)
    direction = _classify_direction(combined_text)
    timeframe = _classify_timeframe(s.get("tf_list") or [], combined_text)

    # Build entry condition(s) from the actually-mapped indicator set.
    ind_names = {i["name"] for i in indicators}
    ind_lb = {i["name"]: i["lookback"] for i in indicators}

    def ref(ind: str) -> str:
        return f"{ind}({ind_lb[ind]})" if ind in ind_lb else ind

    conditions: List[str] = []
    if mechanism == "reversion":
        if "rsi" in ind_names:
            t = "rsi(14) < 35" if direction == "long" else "rsi(14) > 65"
            conditions.append(t)
        elif "bbands" in ind_names:
            t = "close < bbands.bot" if direction == "long" else "close > bbands.top"
            conditions.append(t)
        elif "sma" in ind_names or "ema" in ind_names:
            ma = "sma" if "sma" in ind_names else "ema"
            t = f"close < {ref(ma)}" if direction == "long" else f"close > {ref(ma)}"
            conditions.append(t)
        else:
            return None
    else:  # trend_momentum / vol_snapback / time_decay
        built = False
        if "macd_hist" in ind_names:
            conditions.append("macd_hist > 0" if direction == "long" else "macd_hist < 0")
            built = True
        if "sma" in ind_names:
            conditions.append(f"close > {ref('sma')}" if direction == "long"
                              else f"close < {ref('sma')}")
            built = True
        elif "ema" in ind_names:
            conditions.append(f"close > {ref('ema')}" if direction == "long"
                              else f"close < {ref('ema')}")
            built = True
        elif "bbands" in ind_names:
            conditions.append("close > bbands.top" if direction == "long"
                              else "close < bbands.bot")
            built = True
        elif "rsi" in ind_names:
            conditions.append("rsi(14) > 55" if direction == "long" else "rsi(14) < 45")
            built = True
        if not built:
            return None

    entry_rules = [{"direction": direction, "condition": " and ".join(conditions)}]

    # Exits: derive TP from an explicit ratio if present, else defaults.
    stop_pct = 0.008
    tp_ratio = _parse_tp_sl_ratio(s.get("tp") or "")
    tp_pct = round(stop_pct * tp_ratio, 4) if tp_ratio else 0.015
    exit_rules = [
        {"exit_type": "stop", "params": {"mode": "percent", "value": stop_pct}},
        {"exit_type": "tp", "params": {"mode": "percent", "value": tp_pct}},
        {"exit_type": "time", "params": {"bars": 48}},
    ]

    return {
        "spec_id": (s.get("id") or name)[:40] or None,  # normalize_spec regenerates
        "thesis_id": f"kb-{s.get('id') or 'anon'}",
        "mechanism_class": mechanism,
        "timeframe": timeframe,
        "indicators": indicators,
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "risk": {"max_positions": 1, "cooldown_bars": 0, "trailing_stop": False},
        "sizing": {"mode": "fixed_lot", "lots": 0.01},
        "source_file": f"neo4j:{s.get('id')}",
        "extra": {
            "generated_from": f"neo4j:{s.get('id')}",
            "kb_strategy_name": name,
            "kb_direction": s.get("dir"),
            "kb_assets": s.get("assets") or [],
        },
    }


_MATCH_BLOCK = """
MATCH (s:Strategy)-[:USES_INDICATOR]->(i:Indicator)
OPTIONAL MATCH (s)-[:HAS_CONTEXT]->(c:Context)
OPTIONAL MATCH (s)-[:HAS_RISK_RULE]->(r:Risk)
OPTIONAL MATCH (s)-[:HAS_TRADE_MANAGEMENT]->(m:Management)
OPTIONAL MATCH (s)-[:HAS_TRIGGER]->(t:Trigger)
OPTIONAL MATCH (s)-[:TRADES_ASSET]->(a:AssetClass)
"""

_AGG_RETURN = """
WITH s,
     s.name AS name, s.short_description AS desc, s.direction AS dir,
     s.id AS id,
     collect(DISTINCT i.name) AS inds,
     collect(DISTINCT i.timeframe) AS tf_list,
     collect(DISTINCT c.trend) AS trend_l,
     collect(DISTINCT r.stop_loss) AS stop_l,
     collect(DISTINCT m.take_profit) AS tp_l,
     collect(DISTINCT t.description) AS trig_l,
     collect(DISTINCT a.name) AS assets
RETURN name, desc, dir, id, inds, tf_list,
       trend_l[0] AS trend, stop_l[0] AS stop, tp_l[0] AS tp,
       trig_l[0] AS trig, assets
 ORDER BY rand()
 LIMIT $lim
"""


def _run_strategies(session, *, assets: Optional[List[str]], lim: int) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"lim": lim}
    if assets:
        # WHERE must sit between the MATCH block and the WITH aggregation.
        q = _MATCH_BLOCK + " WHERE a.name IN $assets\n" + _AGG_RETURN
        params["assets"] = assets
    else:
        q = _MATCH_BLOCK + _AGG_RETURN
    return session.run(q, **params).data()


def fetch_and_build_specs(
    count: int,
    *,
    prefer_assets: Optional[List[str]] = None,
    seed: int = 20260915,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Query the knowledge graph and return (valid_raw_specs, stats).

    `prefer_assets` (e.g. ["Gold (XAUUSD)", "Forex", "Commodities"]) biases the
    pull toward strategies that actually trade the backtest instrument; when
    not enough are mappable it falls back to any mappable strategy.
    Returns only specs that were successfully mapped (pre-validation).
    """
    import random
    from .strategy_schema import validate_spec, dedupe_key  # noqa: E402

    env = _load_kb_env()
    driver = _driver(env)
    stats = {"connected": 0, "pulled": 0, "mapped": 0, "skipped_unmappable": 0,
             "rejected_validation": 0, "deduped": 0}
    specs: List[Dict[str, Any]] = []
    seen_keys = set()

    try:
        driver.verify_connectivity()
        stats["connected"] = 1
        with driver.session() as session:
            rows: List[Dict[str, Any]] = []
            # First pass: strategies that trade a preferred asset (gold/FX/commodity)
            if prefer_assets:
                try:
                    rows = _run_strategies(session, assets=prefer_assets, lim=count * 8)
                except Exception:
                    rows = []
            # Fill remainder (or full) with any mappable strategy
            if len(rows) < count * 8:
                try:
                    rows += _run_strategies(session, assets=None, lim=count * 8)
                except Exception:
                    pass
            stats["pulled"] = len(rows)

            rng = random.Random(seed)
            rng.shuffle(rows)
            for row in rows:
                if len(specs) >= count:
                    break
                raw = _build_spec_from_strategy(row)
                if raw is None:
                    stats["skipped_unmappable"] += 1
                    continue
                try:
                    spec = validate_spec(raw)
                except Exception:
                    stats["rejected_validation"] += 1
                    continue
                key = dedupe_key(spec)
                if key in seen_keys:
                    stats["deduped"] += 1
                    continue
                seen_keys.add(key)
                specs.append(spec.to_dict())
                stats["mapped"] += 1
    finally:
        driver.close()
    return specs, stats


def write_kb_specs(specs: List[Dict[str, Any]], out_dir: Path) -> List[Path]:
    from .strategy_schema import write_spec, validate_spec
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for raw in specs:
        spec = validate_spec(raw)
        paths.append(write_spec(spec, out_dir))
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="neo4j_bridge")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "backtest_engine/strategies"))
    parser.add_argument("--prefer", default="Gold (XAUUSD),Forex,Commodities,Commodities (Gold)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    prefer = [p.strip() for p in args.prefer.split(",") if p.strip()]
    specs, stats = fetch_and_build_specs(args.count, prefer_assets=prefer)
    print("bridge stats:", json.dumps(stats, indent=2))
    for s in specs:
        print(f"  {s['mechanism_class']:<14} {s['timeframe']:<4} {s['thesis_id']:<32} "
              f"{s['entry_rules'][0]['condition']}")
    if args.dry_run:
        return 0
    if specs:
        paths = write_kb_specs(specs, Path(args.out))
        print(f"wrote {len(paths)} KB specs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
