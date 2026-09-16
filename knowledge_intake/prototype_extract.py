"""
PROTOTYPE: prove the internet -> LLM -> 6-layer schema -> embedding path
works on a REAL article with the REAL LLM, before we build the continuous loop.

Stages (each prints real output):
  1. fetch a real RSS feed, pick one article, extract clean text
  2. send to the LLM (CS1 litellm qwen3) with a 6-layer strategy-schema prompt
  3. parse the JSON into the existing pydantic StrategyV6
  4. compute the 768-dim embedding from the local embedding server
Run:  ./backtest_engine/venv/bin/python knowledge_intake/prototype_extract.py
"""
from __future__ import annotations
import json, re, html, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge_base"))

# --- load .env (NEO4J, EMBEDDING, and we add LLM here) ---
def load_env() -> dict:
    env = {}
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
assert LLM_KEY, "LLM_API_KEY missing from knowledge_base/.env"

# --- 1. fetch RSS + extract text ---
def fetch_articles(feed_url: str, n: int = 8):
    r = httpx.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
    out = []
    for it in items:
        def g(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S)
            if not m:
                return ""
            val = m.group(1)
            c = re.match(r"<!\[CDATA\[(.*?)\]\]>", val, re.S)  # RSS CDATA wrappers
            val = c.group(1) if c else val
            return html.unescape(val).strip()
        out.append({"title": g("title"), "link": g("link"), "desc": g("description")})
    return out[:n]

def clean_body(html_text: str, cap: int = 6000) -> str:
    t = re.sub(r"<script.*?</script>", "", html_text, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()[:cap]

# --- 2. LLM extraction with 6-layer prompt ---
PROMPT = """You are a quant knowledge extractor. Read the article text below and extract
ANY concrete trading strategy or technique it describes into the 6-Layer Strategy
Schema. Be faithful: only include what the text actually says. If it is not a
tradeable strategy (e.g. pure news/finance/finance-regulation), return an empty
full_strategies list and 0 fragments.

Return ONLY a JSON object, no prose, with this exact shape:
{"full_strategies":[{"strategy_name":"short name","short_description":"one sentence","video_metadata":{"creator":"source name or null","asset_class":"Crypto|Forex|Commodities|Stocks|Index|null","strategy_direction":"Long|Short|Both"},"layer_2_indicators":[{"name":"e.g. RSI","parameters":"14","timeframe":"H1"}],"layer_3_context":{"trend_requirement":"...","market_regime":"...","catalyst_or_macro":"..."},"layer_4_mechanics":{"setup_conditions":["..."],"candlestick_pattern":"null or name","entry_trigger":"..."},"layer_5_risk_management":{"initial_stop_loss":"...","position_sizing":"..."},"layer_6_trade_management":{"take_profit_target":"...","trailing_stop_logic":"...","scaling_logic":"..."},"confidence":0.0}],"fragments":[{"target_layer":2,"concept_name":"...","description":"...","pseudo_code":"...","indicators_mentioned":["..."]}]}

ARTICLE TITLE: @@TITLE@@

ARTICLE TEXT:
@@BODY@@
"""

def extract_via_llm(title: str, body: str) -> dict:
    prompt = PROMPT.replace("@@TITLE@@", title).replace("@@BODY@@", body[:6000])
    resp = httpx.post(
        LLM_URL,
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3000,
            "temperature": 0.1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"] or ""
    # the reasoning model puts the real answer in content (verified); be defensive
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0)) if m else {"full_strategies": [], "fragments": []}

# --- 3. parse into existing pydantic StrategyV6 ---
def parse_v6(data: dict):
    from core.smb_schema_v6 import KnowledgeExtraction
    return KnowledgeExtraction(**data)

# --- 4. embed ---
def embed(text: str) -> list:
    r = httpx.post(f"{EMB_URL}/embeddings",
                   json={"model": "/model", "input": text}, timeout=30)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]

# ============ main ============
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--feed", default="https://www.investing.com/rss/news.rss",
                help="RSS feed URL to pull trading knowledge from")
ap.add_argument("--n", type=int, default=10, help="how many recent items to consider")
args = ap.parse_args()
FEED = args.feed
print("== stage 1: fetch RSS ==")
arts = fetch_articles(FEED, n=args.n)
print(f"  {len(arts)} items")
# Prefer items that already carry a description; otherwise fall back to the
# first item and we'll pull the body from its link.
art = next((a for a in arts if len(a["desc"]) > 200), arts[0])
print(f"  chosen: {art['title'][:70]}")
r = httpx.get(art["link"], headers={"User-Agent": "Mozilla/5.0"}, timeout=25,
              follow_redirects=True)
body = clean_body(r.text) or art["desc"]
print(f"  body chars: {len(body)}")

print("== stage 2: LLM extract (real qwen3 @18081) ==")
t0 = time.time()
data = extract_via_llm(art["title"], body)
print(f"  {time.time()-t0:.1f}s  strategies={len(data.get('full_strategies',[]))} fragments={len(data.get('fragments',[]))}")

print("== stage 3: parse into pydantic StrategyV6 ==")
ke = parse_v6(data)
print(f"  validated full_strategies={len(ke.full_strategies)} fragments={len(ke.fragments)}")
if ke.full_strategies:
    s = ke.full_strategies[0]
    print(f"  strategy_name={s.strategy_name!r}")
    print(f"  indicators={[i.name for i in s.layer_2_indicators]}")
    print(f"  entry_trigger={s.layer_4_mechanics.entry_trigger!r}")
    print(f"  stop={s.layer_5_risk_management.initial_stop_loss!r}")

print("== stage 4: embed (real 768-dim @9001) ==")
if ke.full_strategies:
    s = ke.full_strategies[0]
    emb = embed(f"{s.strategy_name} {s.short_description}")
    print(f"  embedding dim={len(emb)}  head={emb[:4]}")
else:
    print("  (no strategy -> skip embed; article was non-strategic news)")

print("\nDONE — path works end-to-end." if ke.full_strategies or ke.fragments else "\nDONE — path works; this article had no extractable strategy.")
