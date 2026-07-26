"""
Temporal activities for v5.0 - Real NotebookLM integration and direct LLM
"""

from temporalio import activity
from datetime import timedelta
from typing import Dict, Any, List, Optional
import sys
from pathlib import Path
import json
import re
import os
import random
import time
import subprocess
import requests
from bs4 import BeautifulSoup
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import GenericProxyConfig

# Add project roots to path for shared schemas + knowledge_extraction
_project_root = Path(__file__).parent.parent.parent  # autonomous_quant_agent/
sys.path.insert(0, str(_project_root / "shared"))
sys.path.insert(0, str(_project_root / "knowledge_extraction"))

# Also keep v4 core on path for backward compat
v4_path = Path(__file__).parent.parent / "smb_knowledge_base_v4"
if v4_path.exists():
    sys.path.insert(0, str(v4_path / "core"))
else:
    sys.path.insert(0, "/home/ml/smb_knowledge_base_v4/core")

# OpenAI client for direct LLM
try:
    from openai import OpenAI
    HAVE_OPENAI = True
except ImportError:
    HAVE_OPENAI = False

# Transcript settings
LANGUAGE_CODES = ['en']
MAX_RETRIES_PER_VIDEO = 10

# Proxy support (optional)
def get_free_proxies():
    print("🔄 Fetching fresh proxy list...")
    url = "https://free-proxy-list.net/en"
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = []
        for row in soup.select("table.table tbody tr"):
            tds = row.find_all("td")
            if len(tds) >= 7 and tds[6].text.strip() == "yes":
                ip = tds[0].text.strip()
                port = tds[1].text.strip()
                proxies.append(f"http://{ip}:{port}")
        print(f"✅ Found {len(proxies)} HTTPS proxies")
        return proxies
    except Exception as e:
        print(f"⚠️ Failed to fetch proxies: {e}")
        return []

def _fetch_transcript(video_id: str, proxies: List[str]):
    if not proxies:
        try:
            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(video_id, languages=LANGUAGE_CODES)
            text = TextFormatter().format_transcript(transcript)
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                title = info.get('title', video_id)
            return True, text, title
        except Exception as e:
            return False, None, None
    attempted = set()
    max_attempts = min(MAX_RETRIES_PER_VIDEO, len(proxies))
    for attempt in range(1, max_attempts + 1):
        available = [p for p in proxies if p not in attempted]
        if not available:
            break
        proxy = random.choice(available)
        attempted.add(proxy)
        try:
            proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy)
            ytt_api = YouTubeTranscriptApi(proxy_config=proxy_config)
            transcript = ytt_api.fetch(video_id, languages=LANGUAGE_CODES)
            text = TextFormatter().format_transcript(transcript)
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                title = info.get('title', video_id)
            return True, text, title
        except Exception as e:
            print(f"⚠️ Attempt {attempt}/{max_attempts} failed for {video_id}")
            time.sleep(1.2)
    return False, None, None

# Extraction prompt (6-layer schema with name/description)
STRATEGY_EXTRACTION_PROMPT = """You are an expert Quantitative Analyst and Data Engineer building a Trading Strategy Knowledge Graph.
Your task is to read transcripts from YouTube trading videos and extract the atomic elements of the trading strategies discussed.

You must extract the data into a strict JSON format based on a 6-Layer Strategy Schema.

DEFINITIONS FOR EXTRACTION:
To ensure accurate extraction, you must understand the following structural framework:
- Layer 2 (Indicators/Tools): The mathematical or visual tools applied to the chart.
- Layer 3 (Context/Regime): The overarching market environment required for the strategy to be valid (e.g., "Uptrend", "High volatility", "Earnings gap").
- Layer 4 (Mechanics/Execution): The strict "If/Then" rules.
    - "Setup Conditions" are the environment that must exist right before entry.
    - "Entry Trigger" is the exact atomic moment the trader clicks buy/sell.
- Layer 5 (Risk Management): The initial mathematical rules protecting capital (Initial Stop Loss, Position Sizing).
- Layer 6 (Trade Management): What happens AFTER the trade is active (Trailing stops, scaling out, time-based exits).

RULES:
1. DO NOT GUESS. If a specific layer, indicator, or rule is not explicitly mentioned, you MUST output `null` for that field.
2. Standardize terminology (e.g., "8 period exponential moving average" -> "8 EMA").
3. Output ONLY valid JSON. Do not include markdown formatting or explanations outside the JSON object.

JSON SCHEMA:
{
  "strategy_name": "String",
  "short_description": "String (10-200 characters)",
  "video_metadata": {
    "creator": "String",
    "asset_class": "String (e.g., Forex, Crypto, Stocks) or null",
    "strategy_direction": "String (Long, Short, Both) or null"
  },
  "layer_2_indicators": [
    {
      "name": "String",
      "parameters": "String or null",
      "timeframe": "String (e.g., 4H, 5m) or null"
    }
  ],
  "layer_3_context": {
    "trend_requirement": "String or null",
    "market_regime": "String or null",
    "catalyst_or_macro": "String or null"
  },
  "layer_4_mechanics": {
    "setup_conditions": ["Array of Strings"],
    "candlestick_pattern": "String (e.g., Bullish Engulfing, Pin Bar) or null",
    "entry_trigger": "String or null"
  },
  "layer_5_risk_management": {
    "initial_stop_loss": "String or null",
    "position_sizing": "String or null"
  },
  "layer_6_trade_management": {
    "take_profit_target": "String or null",
    "trailing_stop_logic": "String or null",
    "scaling_logic": "String (e.g., scale out 50% at 1R) or null"
  }
}

=== EXAMPLE 1 ===
Video: BEST and Proper Way to use MACD Alligator Trading Strategy
[Transcript text about MACD, Alligator, 30m/4H timeframes, and 200 EMA...]

EXAMPLE OUTPUT:
{
  "strategy_name": "MACD Alligator Strategy",
  "short_description": "Trend following strategy using MACD crossover combined with Alligator indicator to determine trend direction and entry signals.",
  "video_metadata": {
    "creator": "Trading Rush",
    "asset_class": null,
    "strategy_direction": "Both"
  },
  "layer_2_indicators": [
    {"name": "MACD", "parameters": "Zero line rule", "timeframe": "30m"},
    {"name": "Alligator", "parameters": null, "timeframe": "4H"},
    {"name": "200 EMA", "parameters": "length: 200", "timeframe": "30m"}
  ],
  "layer_3_context": {
    "trend_requirement": "Uptrend or Downtrend required. 200 EMA must not be flat.",
    "market_regime": "Trending. Avoid range and slow-moving markets.",
    "catalyst_or_macro": null
  },
  "layer_4_mechanics": {
    "setup_conditions": [
      "Price must be above 200 EMA for longs",
      "Alligator indicator on 4H timeframe must have mouth open indicating trend"
    ],
    "candlestick_pattern": null,
    "entry_trigger": "MACD gives a crossover signal on the entry timeframe"
  },
  "layer_5_risk_management": {
    "initial_stop_loss": null,
    "position_sizing": null
  },
  "layer_6_trade_management": {
    "take_profit_target": null,
    "trailing_stop_logic": null,
    "scaling_logic": null
  }
}

=== EXAMPLE 2 ===
Video: A technical analysis trade that requires RISK ON
[Transcript text about NIO, EV sector, pre-market breakouts, ATR, VWAP...]

EXAMPLE OUTPUT:
{
  "strategy_name": "Risk On Momentum Breakout",
  "short_description": "Intraday momentum strategy focusing on pre-market breakouts with high relative volume and ATR-based targets.",
  "video_metadata": {
    "creator": "SMB Capital",
    "asset_class": "Equities (EV Sector)",
    "strategy_direction": "Long"
  },
  "layer_2_indicators": [
    {"name": "RVOL (Relative Volume)", "parameters": "> 5", "timeframe": "Intraday"},
    {"name": "ATR (Average True Range)", "parameters": "1.15", "timeframe": "Daily"},
    {"name": "VWAP", "parameters": null, "timeframe": "Intraday"}
  ],
  "layer_3_context": {
    "trend_requirement": "Strong stock consolidating for multiple weeks at high timeframe resistance.",
    "market_regime": "High momentum, sector trading independently of overall market.",
    "catalyst_or_macro": "News catalyst (Bank upgrade, price target increase)."
  },
  "layer_4_mechanics": {
    "setup_conditions": [
      "Stock gaps up above high timeframe resistance",
      "Holds above pre-market highs or opening range without falling back in"
    ],
    "candlestick_pattern": null,
    "entry_trigger": "Opening Drive (breaks resistance on open) OR Dip and Rip (dips, stalls, rips to upside)"
  },
  "layer_5_risk_management": {
    "initial_stop_loss": "Below wick low or fallback into the morning range",
    "position_sizing": "Risk On. 30% to 50% of daily stop limit. Add size on higher lows."
  },
  "layer_6_trade_management": {
    "take_profit_target": "At least 2 ATR move from the lows",
    "trailing_stop_logic": "Hold partial position against a wider stop for intraday swing",
    "scaling_logic": "Scale out small pieces on the way up into spikes, cut half for breakeven if price action stalls"
  }
}

Extract strategy data from this video transcript following the 6-Layer Schema above. Return ONLY the JSON object with no additional text."""

# Helper to save extracted JSON for debugging
def _save_strategy_json(data: Dict[str, Any], video_id: str):
    processed_dir = Path(__file__).parent.parent.parent / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"{video_id}_extracted.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def _get_default_strategy(video_id: str, transcript: str) -> Dict[str, Any]:
    """Fallback strategy when extraction fails - returns v6 schema."""
    return {
        "strategy_name": f"Strategy from {video_id}",
        "short_description": "Fallback strategy with limited information; extracted directly from transcript with low confidence.",
        "video_metadata": {
            "creator": "Unknown",
            "asset_class": None,
            "strategy_direction": "Both"
        },
        "layer_2_indicators": [],
        "layer_3_context": {
            "trend_requirement": None,
            "market_regime": None,
            "catalyst_or_macro": None
        },
        "layer_4_mechanics": {
            "setup_conditions": [],
            "candlestick_pattern": None,
            "entry_trigger": None
        },
        "layer_5_risk_management": {
            "initial_stop_loss": None,
            "position_sizing": None
        },
        "layer_6_trade_management": {
            "take_profit_target": None,
            "trailing_stop_logic": None,
            "scaling_logic": None
        },
        "confidence": 0.3,
        "edge_score": 30,
    }


# Embedding generation using OpenAI-compatible endpoint
_EMBEDDING_MODEL_CACHE = None

def _get_embedding_client_and_model():
    global _EMBEDDING_MODEL_CACHE
    if _EMBEDDING_MODEL_CACHE is not None:
        return _EMBEDDING_MODEL_CACHE
    embedding_api_base = os.getenv("EMBEDDING_API_BASE", "http://localhost:9001/v1")
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", "dummy")
    try:
        client = OpenAI(base_url=embedding_api_base, api_key=embedding_api_key)
        model = os.getenv("EMBEDDING_MODEL")
        if not model:
            try:
                models = client.models.list()
                if models.data:
                    model = models.data[0].id
                else:
                    model = "text-embedding-ada-002"
            except Exception as e:
                print(f"Warning: could not list models from embedding endpoint: {e}. Using fallback.")
                model = "text-embedding-ada-002"
        _EMBEDDING_MODEL_CACHE = (client, model)
        return client, model
    except Exception as e:
        print(f"Failed to initialize embedding client: {e}")
        return None, None

def _compute_strategy_embedding(strategy: dict) -> Optional[List[float]]:
    client, model = _get_embedding_client_and_model()
    if not client or not model:
        return None
    try:
        name = strategy.get("strategy_name", "")
        desc = strategy.get("short_description", "")
        l2 = strategy.get("layer_2_indicators", [])
        l2_text = " ".join(ind.get("name", "") for ind in l2)
        l4 = strategy.get("layer_4_mechanics", {})
        l4_setup = " ".join(l4.get("setup_conditions", []))
        text = f"{name} {desc} {l2_text} {l4_setup}".strip()
        if not text:
            return None
        resp = client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except Exception as e:
        print(f"Embedding compute error: {e}")
        return None

@activity.defn(name="download_video")
def download_video(video_url: str) -> Dict[str, Any]:
    video_id = video_url.split("=")[-1] if "=" in video_url else video_url
    proxies = []
    if os.getenv("USE_PROXIES", "0") == "1":
        proxies = get_free_proxies()
    success, transcript_text, title = _fetch_transcript(video_id, proxies)
    if not success:
        transcript_text = ""
        title = video_id
    return {
        "video_id": video_id,
        "transcript": transcript_text,
        "transcript_length": len(transcript_text),
        "status": "transcribed" if success else "failed",
        "title": title,
        # No notebook_id or source_id
    }

@activity.defn(name="extract_strategy")
async def extract_strategy(video_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract strategy from transcript using NotebookLM or direct LLM."""
    video_id = video_data["video_id"]
    notebook_id = video_data.get("notebook_id")
    source_id = video_data.get("source_id")
    transcript = video_data.get("transcript", "")

    # If we have a notebook_id, try NotebookLM (legacy)
    if notebook_id and source_id:
        try:
            result = subprocess.run(
                ["notebooklm", "ask", STRATEGY_EXTRACTION_PROMPT, "-n", notebook_id, "-s", source_id, "--json"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                response = json.loads(result.stdout)
                answer = response.get("answer", "")
                if isinstance(answer, str):
                    json_match = re.search(r"```json\s*(.*?)\s*```", answer, re.DOTALL)
                    if json_match:
                        strategy_json = json.loads(json_match.group(1))
                    else:
                        strategy_json = json.loads(answer)
                else:
                    strategy_json = answer
                if not isinstance(strategy_json, dict):
                    raise ValueError("NotebookLM didn't return a dictionary")
                strategy_json["video_id"] = video_id
                _save_strategy_json(strategy_json, video_id)
                return strategy_json
            else:
                raise subprocess.CalledProcessError(result.returncode, result.stderr)
        except Exception as e:
            print(f"NotebookLM extraction error: {e}")
            # fallthrough to direct LLM if transcript available

    # If no notebook or NotebookLM failed, try direct LLM if transcript present and OPENAI available
    if transcript and HAVE_OPENAI:
        try:
            client = OpenAI(
                base_url=os.getenv("LLM_API_BASE", "http://localhost:9000/v1"),
                api_key=os.getenv("LLM_API_KEY", "dummy")
            )
            resp = client.chat.completions.create(
                model=os.getenv("LLM_MODEL", "qwen3.5-4b"),
                messages=[
                    {"role": "system", "content": STRATEGY_EXTRACTION_PROMPT},
                    {"role": "user", "content": transcript}
                ],
                temperature=0.0,
                max_tokens=2000
            )
            answer = resp.choices[0].message.content
            json_match = re.search(r"```json\s*(.*?)\s*```", answer, re.DOTALL)
            json_str = json_match.group(1) if json_match else answer
            strategy_json = json.loads(json_str)
            strategy_json["video_id"] = video_id
            _save_strategy_json(strategy_json, video_id)
            return strategy_json
        except Exception as e:
            print(f"Direct LLM extraction failed: {e}")

    # Fallback
    fallback = _get_default_strategy(video_id, transcript)
    _save_strategy_json(fallback, video_id)
    return fallback

# Import schema for validation (optional)
try:
    from smb_schema_v6 import StrategyV6
except ImportError:
    StrategyV6 = None

@activity.defn(name="validate_strategy")
async def validate_strategy(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Validate strategy extraction correctness for 6-layer schema."""
    errors = []
    warnings = []

    if StrategyV6 is not None:
        try:
            StrategyV6(**strategy)
        except Exception as e:
            errors.append(f"Schema validation error: {e}")
    else:
        # Fallback manual validation
        required_top = ["strategy_name", "short_description", "video_metadata", "layer_2_indicators", "layer_3_context",
                        "layer_4_mechanics", "layer_5_risk_management", "layer_6_trade_management"]
        for key in required_top:
            if key not in strategy:
                errors.append(f"Missing required top-level key: {key}")

        vm = strategy.get("video_metadata", {})
        for subkey in ["creator", "asset_class", "strategy_direction"]:
            if subkey not in vm:
                errors.append(f"Missing video_metadata.{subkey}")

        l4 = strategy.get("layer_4_mechanics", {})
        if "setup_conditions" not in l4:
            errors.append("Missing layer_4_mechanics.setup_conditions")
        if "entry_trigger" not in l4:
            errors.append("Missing layer_4_mechanics.entry_trigger")

    # Quality gates (optional)
    confidence = strategy.get("confidence")
    if confidence is not None and confidence < 0.75:
        warnings.append(f"confidence {confidence} < 0.75 (recommended)")

    edge_score = strategy.get("edge_score")
    if edge_score is not None and edge_score < 65:
        warnings.append(f"edge_score {edge_score} < 65 (recommended)")

    valid = len(errors) == 0
    return {
        **strategy,
        "validation_status": "validated" if valid else "failed",
        "validation_errors": errors,
        "validation_warnings": warnings,
        "quality_gates_passed": valid
    }

@activity.defn(name="store_strategy")
async def store_strategy(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Store strategy into Neo4j graph database if valid."""
    validation_status = strategy.get("validation_status")
    if validation_status != "validated":
        vid = strategy.get("id") or strategy.get("video_id")
        print(f"Skipping storage for {vid}: validation status {validation_status}")
        return {"status": "skipped_validation_failed", "video_id": vid}

    video_id = strategy.get("video_id") or strategy.get("id")
    if not video_id:
        raise ValueError("Strategy missing video_id")

    # Compute embedding (mandatory)
    embedding = _compute_strategy_embedding(strategy)
    if not embedding:
        raise ValueError(f"Failed to generate embedding for strategy {video_id}")
    strategy["embedding"] = embedding

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        from .neo4j_ingestor import StrategyGraphIngestor
    except ImportError:
        from neo4j_ingestor import StrategyGraphIngestor

    ingestor = StrategyGraphIngestor(uri, user, password)
    try:
        ingestor.ingest_strategy(strategy, video_id)
        return {"status": "stored_neo4j", "video_id": video_id}
    finally:
        ingestor.close()

# --- Fragment support ---
try:
    from smb_knowledge_base.core.knowledge_extraction import KnowledgeExtraction, StrategyFragment
except ImportError:
    KnowledgeExtraction = None
    StrategyFragment = None

KNOWLEDGE_EXTRACTION_PROMPT = """You are an expert Quantitative Analyst and Data Engineer building a Trading Strategy Knowledge Graph.
Your task is to read transcripts from YouTube trading videos and extract the atomic elements of the trading strategies discussed.

You must output JSON with two top-level arrays: "full_strategies" (complete 6-layer strategies) and "fragments" (isolated concepts). Follow these rules:

1. A full strategy includes all 6 layers (Layer 2: Indicators/Tools; Layer 3: Context/Regime; Layer 4: Mechanics/Execution; Layer 5: Risk Management; Layer 6: Trade Management). Fill missing fields explicitly as null.
2. A fragment is any partial insight that does NOT constitute a full strategy. Set target_layer (1-6) indicating which layer it belongs to:
   - Layer 1: Core Philosophy / Edge (overarching principle)
   - Layer 2: Indicators/Tools
   - Layer 3: Context/Regime
   - Layer 4: Mechanics/Execution
   - Layer 5: Risk Management
   - Layer 6: Trade Management
3. For fragments, provide:
   - concept_name: concise name
   - description: detailed explanation
   - pseudo_code: logical rules/pseudo-code
   - indicators_mentioned: list of any indicators used
4. DO NOT GUESS. Use null for missing data.
5. Output ONLY JSON, no extra text.

JSON schema:
{
  "source_title": "string",
  "full_strategies": [ { /* same 6-layer object as before */ } ],
  "fragments": [ { "target_layer": number, "concept_name": string, "description": string, "pseudo_code": string, "indicators_mentioned": [string] } ]
}

If nothing meaningful is found, return empty arrays.
"""

def _compute_fragment_embedding(fragment: dict) -> Optional[List[float]]:
    client, model = _get_embedding_client_and_model()
    if not client or not model:
        return None
    try:
        name = fragment.get("concept_name", "")
        desc = fragment.get("description", "")
        pseudo = fragment.get("pseudo_code", "")
        indicators = " ".join(fragment.get("indicators_mentioned", []))
        text = f"{name} {desc} {pseudo} {indicators}".strip()
        if not text:
            return None
        resp = client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except Exception as e:
        print(f"Fragment embedding error: {e}")
        return None

@activity.defn(name="extract_knowledge")
async def extract_knowledge(video_data: Dict[str, Any]) -> Dict[str, Any]:
    video_id = video_data["video_id"]
    transcript = video_data.get("transcript", "")
    source_title = video_data.get("title", video_id)

    if not transcript:
        raise ValueError(f"No transcript for {video_id}")

    if not HAVE_OPENAI:
        raise RuntimeError("OpenAI client not available")

    try:
        client = OpenAI(
            base_url=os.getenv("LLM_API_BASE", "http://localhost:9000/v1"),
            api_key=os.getenv("LLM_API_KEY", "dummy")
        )
        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "qwen3.5-4b"),
            messages=[
                {"role": "system", "content": KNOWLEDGE_EXTRACTION_PROMPT},
                {"role": "user", "content": transcript[:30000]}
            ],
            temperature=0.0,
            max_tokens=4000
        )
        answer = resp.choices[0].message.content
        json_match = re.search(r"```json\s*(.*?)\s*```", answer, re.DOTALL)
        json_str = json_match.group(1) if json_match else answer
        data = json.loads(json_str)
        data["source_title"] = data.get("source_title", source_title)
    except Exception as e:
        print(f"extract_knowledge LLM error: {e}")
        raise

    # Save raw extraction
    processed_dir = Path(__file__).parent.parent.parent / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"{video_id}_knowledge.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    # Validate with KnowledgeExtraction model
    if KnowledgeExtraction is not None:
        try:
            knowledge = KnowledgeExtraction.model_validate(data)
            return knowledge.model_dump()
        except Exception as e:
            print(f"KnowledgeExtraction validation failed: {e}")
            raise
    else:
        if "full_strategies" not in data or "fragments" not in data:
            raise ValueError("Missing full_strategies or fragments in extraction")
        return data

@activity.defn(name="validate_fragment")
async def validate_fragment(fragment: Dict[str, Any]) -> Dict[str, Any]:
    errors = []
    warnings = []
    try:
        frag = StrategyFragment.model_validate(fragment)
    except Exception as e:
        errors.append(f"Schema validation error: {e}")
        valid = False
    else:
        if not frag.concept_name.strip():
            errors.append("Concept name is empty")
        if len(frag.description) < 10:
            errors.append("Description too short (<10 characters)")
        if not (1 <= frag.target_layer <= 6):
            errors.append("target_layer must be between 1 and 6")
        if not frag.pseudo_code.strip():
            warnings.append("Pseudo-code is empty")
        valid = len(errors) == 0
    return {
        **fragment,
        "validation_status": "validated" if valid else "failed",
        "validation_errors": errors,
        "validation_warnings": warnings,
        "quality_gates_passed": valid
    }

@activity.defn(name="store_fragment")
async def store_fragment(fragment: Dict[str, Any], video_id: str) -> Dict[str, Any]:
    embedding = _compute_fragment_embedding(fragment)
    if not embedding:
        raise ValueError(f"Failed to generate embedding for fragment {fragment.get('concept_name')}")

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        from .neo4j_ingestor import StrategyGraphIngestor
    except ImportError:
        from neo4j_ingestor import StrategyGraphIngestor

    ingestor = StrategyGraphIngestor(uri, user, password)
    try:
        ingestor.create_fragment(fragment, embedding, source_video_id=video_id)
        return {"status": "stored_fragment", "concept_name": fragment.get("concept_name"), "video_id": video_id}
    finally:
        ingestor.close()
