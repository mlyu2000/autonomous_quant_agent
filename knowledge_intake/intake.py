"""
knowledge_intake/intake.py — CONTINUOUS trading-knowledge intake.

Two source types, both feeding the EXISTING Neo4j knowledge graph through the
EXISTING StrategyGraphIngestor + StrategyV6 6-layer schema (the same path the
YouTube pipeline uses — no re-invention):

  A) INTERNET  — pull new articles from RSS feeds, extract the tradeable
     strategy via the LLM, embed, ingest.
  B) LLM MODEL — ask the LLM to synthesize a fresh, concrete strategy
     (optionally grounded on existing graph knowledge), embed, ingest.

Honesty rules (MVP):
  - Only ingest strategies that PARSE the 6-layer schema. Non-strategic
    content (news/regulation) is SKIPPED (counted), never faked.
  - Idempotent: a stable strategy id (hash of name+indicators) means re-running
    the same article won't create duplicate Strategy nodes.
  - No network access is fabricated; if a feed or the LLM is unreachable we
    report it, we do not invent strategies.

Run once:        ./backtest_engine/venv/bin/python knowledge_intake/intake.py --once --source internet
Run continuously: ./backtest_engine/venv/bin/python knowledge_intake/intake.py --loop --interval 1800
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge_base"))

logger = logging.getLogger("knowledge_intake")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Config (from knowledge_base/.env — gitignored, never committed)
# ---------------------------------------------------------------------------
def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    p = ROOT / "knowledge_base" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
LLM_URL = ENV.get("LLM_API_BASE", "http://127.0.0.1:18081/v1") + "/chat/completions"
LLM_KEY = ENV.get("LLM_API_KEY", "")
LLM_MODEL = ENV.get("LLM_MODEL", "qwen3-8-27b-int4-dflash2-r2")
EMB_URL = ENV.get("EMBEDDING_API_BASE", "http://localhost:9001/v1")
NEO4J_URI = ENV.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = ENV.get("NEO4J_USER", "neo4j")
NEO4J_PW = ENV.get("NEO4J_PASSWORD", "")
assert LLM_KEY, "LLM_API_KEY missing from knowledge_base/.env"
assert NEO4J_PW, "NEO4J_PASSWORD missing from knowledge_base/.env"

FEEDS_FILE = Path(__file__).parent / "feeds.yaml"
STATE_FILE = Path(__file__).parent / "intake_state.json"
LOG_FILE = Path(__file__).parent / "intake_log.jsonl"


DEFAULT_FEEDS = [
    {"url": "https://cointelegraph.com/rss", "topic": "crypto trading"},
    {"url": "https://cryptopotato.com/feed/", "topic": "crypto trading"},
]


def load_feeds() -> List[Dict[str, str]]:
    """Load feeds from feeds.yaml if present (needs pyyaml); otherwise use
    the built-in defaults. Keeps the venv dependency-free."""
    if FEEDS_FILE.exists():
        try:
            import yaml
            data = yaml.safe_load(FEEDS_FILE.read_text()) or {}
            return data.get("feeds", DEFAULT_FEEDS)
        except ImportError:
            pass
    return DEFAULT_FEEDS


# ---------------------------------------------------------------------------
# State (which article URLs / strategy ids we've already ingested)
# ---------------------------------------------------------------------------
def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_urls": [], "ingested_ids": [], "last_run": None, "totals": {}}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _log_event(record: Dict[str, Any]) -> None:
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ---------------------------------------------------------------------------
# Embedding (768-dim local server)
# ---------------------------------------------------------------------------
def embed(text: str) -> Optional[List[float]]:
    try:
        r = httpx.post(f"{EMB_URL}/embeddings",
                       json={"model": "/model", "input": text[:4000]}, timeout=30)
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception as e:
        logger.warning("embed failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# LLM extraction — 6-Layer Strategy Schema (reuses the proven prompt shape)
# ---------------------------------------------------------------------------
SCHEMA_HINT = (
    'Return ONLY a JSON object, no prose, matching the 6-Layer Strategy Schema:\n'
    '{"strategy_name":"str","short_description":"str(10-200 chars)",'
    '"video_metadata":{"creator":"str|null","asset_class":"Forex|Crypto|Commodities|Stocks|Index|null",'
    '"strategy_direction":"Long|Short|Both|null"},'
    '"layer_2_indicators":[{"name":"str","parameters":"str|null","timeframe":"str|null"}],'
    '"layer_3_context":{"trend_requirement":"str|null","market_regime":"str|null","catalyst_or_macro":"str|null"},'
    '"layer_4_mechanics":{"setup_conditions":["str"],"candlestick_pattern":"str|null","entry_trigger":"str|null"},'
    '"layer_5_risk_management":{"initial_stop_loss":"str|null","position_sizing":"str|null"},'
    '"layer_6_trade_management":{"take_profit_target":"str|null","trailing_stop_logic":"str|null","scaling_logic":"str|null"},'
    '"confidence":0.0}'
)

EXTRACT_SYSTEM = (
    "You are an expert Quantitative Analyst building a Trading Strategy Knowledge Graph. "
    "Extract concrete tradeable strategies from the text into the 6-Layer Strategy Schema. "
    "RULES: 1) DO NOT GUESS — if a layer/indicator/rule is not explicitly in the text, output null. "
    "2) Standardize terms ('8 period EMA' -> '8 EMA'). 3) If the text is NOT a tradeable strategy "
    "(news, regulation, price commentary, opinion), return an empty object {}. "
    "4) Output ONLY valid JSON."
)

GEN_SYSTEM = (
    "You are an expert Quantitative Strategist. Invent a concrete, testable trading strategy "
    "for the given topic. It must be specific enough to backtest: name the exact indicators, "
    "parameters, timeframes, entry trigger, stop loss, take profit. Fill the 6-Layer Strategy "
    "Schema. Output ONLY valid JSON."
)


def _llm(system: str, user: str, max_tokens: int = 2500) -> Optional[str]:
    try:
        r = httpx.post(
            LLM_URL,
            headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
            timeout=180,
        )
        r.raise_for_status()
        m = r.json()["choices"][0]["message"]
        # Reasoning models may put the answer in content; be defensive.
        return m.get("content") or ""
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return None


def _parse_json(obj: Optional[str]) -> Optional[Dict[str, Any]]:
    if not obj:
        return None
    m = re.search(r"\{.*\}", obj, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def strategy_id_for(name: str, indicators: List[str], direction: str) -> str:
    """Stable id -> idempotent MERGE in Neo4j (re-run = no duplicate)."""
    key = f"{name.strip().lower()}|{','.join(sorted(i.lower() for i in indicators))}|{direction}"
    return "web-" + hashlib.sha1(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Source A: INTERNET (RSS) -> article -> LLM -> 6-layer -> embed -> ingest
# ---------------------------------------------------------------------------
def fetch_rss_items(url: str, n: int = 15) -> List[Dict[str, str]]:
    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    out = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:n]:
        def g(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S)
            if not m:
                return ""
            v = m.group(1)
            c = re.match(r"<!\[CDATA\[(.*?)\]\]>", v, re.S)
            return html.unescape((c.group(1) if c else v)).strip()
        out.append({"title": g("title"), "link": g("link"), "desc": g("description")})
    return out


def clean_body(html_text: str, cap: int = 6000) -> str:
    t = re.sub(r"<script.*?</script>", "", html_text, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()[:cap]


def extract_from_article(title: str, body: str) -> Optional[Dict[str, Any]]:
    user = f"{SCHEMA_HINT}\n\nARTICLE TITLE: {title}\n\nARTICLE TEXT:\n{body}"
    return _parse_json(_llm(EXTRACT_SYSTEM, user))


# ---------------------------------------------------------------------------
# Source B: LLM MODEL -> synthetic strategy -> embed -> ingest
# ---------------------------------------------------------------------------
TOPICS = [
    "gold (XAUUSD) trend-following on the H4 timeframe",
    "EUR/USD mean-reversion using RSI on the H1",
    "S&P 500 momentum breakout with ATR stops on the daily",
    "bitcoin swing trading using EMA 20/50 cross on the 4H",
    "oil (WTI) volatility squeeze breakout on the 1H",
]


def generate_from_llm(topic: str) -> Optional[Dict[str, Any]]:
    user = f"{SCHEMA_HINT}\n\nGenerate a concrete, testable strategy for: {topic}"
    return _parse_json(_llm(GEN_SYSTEM, user, max_tokens=3000))


# ---------------------------------------------------------------------------
# Convert a raw 6-layer dict -> validated StrategyV6 with embedding
# ---------------------------------------------------------------------------
def to_strategy_v6(raw: Dict[str, Any], source: str, source_url: str) -> Optional[Any]:
    from core.smb_schema_v6 import StrategyV6
    if not raw or not raw.get("strategy_name"):
        return None
    raw = dict(raw)
    # Default every 6-layer field so pydantic never fails on a partial LLM
    # answer (the schema requires the layer objects to be present).
    raw.setdefault("video_metadata", {})
    raw["video_metadata"].setdefault("asset_class", None)
    raw["video_metadata"].setdefault("strategy_direction", "Both")
    raw["video_metadata"]["creator"] = source
    raw.setdefault("layer_2_indicators", [])
    for k in ("layer_3_context", "layer_4_mechanics", "layer_5_risk_management",
              "layer_6_trade_management"):
        raw.setdefault(k, {})
    raw["layer_4_mechanics"].setdefault("setup_conditions", [])
    raw.setdefault("short_description", raw["strategy_name"])
    try:
        s = StrategyV6(**raw)
    except Exception as e:
        logger.info("schema reject: %s", e)
        return None
    emb = embed(f"{s.strategy_name} {s.short_description}")
    if emb:
        s.embedding = emb
    return s


def ingest_strategy(s, source: str, source_url: str, creator: str) -> str:
    """Write to Neo4j via the EXISTING StrategyGraphIngestor. Returns the id."""
    from orchestration.neo4j_ingestor import StrategyGraphIngestor
    name = s.strategy_name
    inds = [i.name for i in s.layer_2_indicators if i.name]
    direction = (s.video_metadata.strategy_direction or "Both") if s.video_metadata else "Both"
    sid = strategy_id_for(name, inds, direction)
    ing = StrategyGraphIngestor(NEO4J_URI, NEO4J_USER, NEO4J_PW)
    try:
        payload = s.model_dump()
        payload["embedding"] = s.embedding
        # source provenance on the creator node
        creator_name = f"{creator} [{source}] {source_url[:60]}"
        payload["video_metadata"]["creator"] = creator_name
        ing.ingest_strategy(payload, sid)
    finally:
        ing.close()
    return sid


# ---------------------------------------------------------------------------
# One cycle over a source
# ---------------------------------------------------------------------------
def run_internet_cycle(state: Dict[str, Any], limit_per_feed: int = 4) -> Dict[str, Any]:
    stats = {"feeds_ok": 0, "feeds_failed": 0, "articles": 0,
             "strategies": 0, "skipped_nonstrat": 0, "ingested": []}
    seen = set(state["seen_urls"])
    for feed in load_feeds():
        url, topic = feed["url"], feed.get("topic", "trading")
        try:
            items = fetch_rss_items(url)
            stats["feeds_ok"] += 1
        except Exception as e:
            logger.warning("feed failed %s: %s", url, e)
            stats["feeds_failed"] += 1
            continue
        for art in items:
            if len(state["ingested_ids"]) and not art["link"]:
                continue
            if art["link"] in seen:
                continue
            if stats["strategies"] >= limit_per_feed * 2:
                break
            try:
                r = httpx.get(art["link"], headers={"User-Agent": "Mozilla/5.0"},
                              timeout=25, follow_redirects=True)
                body = clean_body(r.text) or art["desc"]
                if len(body) < 300:
                    continue
            except Exception:
                body = art["desc"]
                if len(body) < 300:
                    continue
            raw = extract_from_article(art["title"], body)
            if not raw or not raw.get("strategy_name"):
                stats["skipped_nonstrat"] += 1
                seen.add(art["link"])
                continue
            stats["strategies"] += 1
            s = to_strategy_v6(raw, source="internet", source_url=art["link"])
            if s is None:
                seen.add(art["link"])
                continue
            try:
                sid = ingest_strategy(s, "internet", art["link"], art["title"][:40])
                stats["ingested"].append({"id": sid, "name": s.strategy_name,
                                           "source": "internet", "url": art["link"]})
                state["ingested_ids"].append(sid)
            except Exception as e:
                logger.warning("ingest failed: %s", e)
            seen.add(art["link"])
    state["seen_urls"] = list(seen)[-500:]
    return stats


def run_llm_cycle(state: Dict[str, Any], count: int = 3) -> Dict[str, Any]:
    stats = {"generated": 0, "valid": 0, "ingested": []}
    for i in range(count):
        topic = TOPICS[(i + int(time.time() // 3600)) % len(TOPICS)]
        raw = generate_from_llm(topic)
        stats["generated"] += 1
        if not raw or not raw.get("strategy_name"):
            continue
        stats["valid"] += 1
        s = to_strategy_v6(raw, source="llm-model", source_url=topic)
        if s is None:
            continue
        try:
            sid = ingest_strategy(s, "llm-model", topic, f"LLM {topic[:30]}")
            stats["ingested"].append({"id": sid, "name": s.strategy_name,
                                       "source": "llm-model", "topic": topic})
            state["ingested_ids"].append(sid)
        except Exception as e:
            logger.warning("llm ingest failed: %s", e)
    return stats


def run_cycle(source: str, **kw) -> Dict[str, Any]:
    state = _load_state()
    t0 = time.time()
    if source == "all":
        # alternate internet / llm each cycle (persist the phase)
        phase = state.get("next_phase", "internet")
        stats = (run_internet_cycle(state, limit_per_feed=kw.get("limit_per_feed", 4))
                 if phase == "internet"
                 else run_llm_cycle(state, count=kw.get("count", 3)))
        state["next_phase"] = "llm" if phase == "internet" else "internet"
        source = phase
    elif source == "internet":
        stats = run_internet_cycle(state, limit_per_feed=kw.get("limit_per_feed", 4))
    elif source == "llm":
        stats = run_llm_cycle(state, count=kw.get("count", 3))
    else:
        raise ValueError(f"unknown source: {source}")
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["totals"].setdefault("internet", 0)
    state["totals"].setdefault("llm", 0)
    state["totals"][source if source != "llm" else "llm"] += len(stats.get("ingested", []))
    _save_state(state)
    stats["elapsed_s"] = round(time.time() - t0, 1)
    stats["source"] = source
    _log_event({"at": state["last_run"], "source": source, "stats": stats})
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--loop", action="store_true", help="run continuously")
    ap.add_argument("--source", default="all", choices=["internet", "llm", "all"],
                    help="all = alternate internet/llm each cycle (continuous mode)")
    ap.add_argument("--interval", type=int, default=1800, help="seconds between cycles (loop)")
    ap.add_argument("--limit-per-feed", type=int, default=4)
    ap.add_argument("--count", type=int, default=3, help="llm strategies per cycle")
    args = ap.parse_args(argv)

    if not (args.once or args.loop):
        ap.error("pass --once or --loop")

    if args.once:
        stats = run_cycle(args.source, limit_per_feed=args.limit_per_feed, count=args.count)
        print(json.dumps(stats, indent=2))
        return 0

    logger.info("continuous intake: source=%s interval=%ds", args.source, args.interval)
    while True:
        try:
            stats = run_cycle(args.source, limit_per_feed=args.limit_per_feed, count=args.count)
            logger.info("cycle done: %s ingested in %ss",
                        len(stats.get("ingested", [])), stats.get("elapsed_s"))
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.warning("cycle error: %s", e)
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
