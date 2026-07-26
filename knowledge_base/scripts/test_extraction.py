#!/usr/bin/env python3
"""
Extract strategies and fragments from 5 random transcripts.
Uses the KNOWLEDGE_EXTRACTION_PROMPT from activities.py.
"""
import json
import os
import random
import re
from pathlib import Path
from openai import OpenAI

# Load .env
env_path = Path("/home/ml/projects/autonomous_quant_agent/knowledge_base/.env")
for line in env_path.read_text().strip().split("\n"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

os.environ["LLM_API_BASE"] = "http://localhost:9000/v1"
os.environ["LLM_API_KEY"] = "dummy"
os.environ["LLM_MODEL"] = "qwen3.5-4b"

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
  "full_strategies": [ {
    "strategy_name": "String",
    "short_description": "String (10-200 characters)",
    "video_metadata": {
      "creator": "String",
      "asset_class": "String or null",
      "strategy_direction": "String (Long, Short, Both) or null"
    },
    "layer_2_indicators": [
      {"name": "String", "parameters": "String or null", "timeframe": "String or null"}
    ],
    "layer_3_context": {
      "trend_requirement": "String or null",
      "market_regime": "String or null",
      "catalyst_or_macro": "String or null"
    },
    "layer_4_mechanics": {
      "setup_conditions": ["Array of Strings"],
      "candlestick_pattern": "String or null",
      "entry_trigger": "String or null"
    },
    "layer_5_risk_management": {
      "initial_stop_loss": "String or null",
      "position_sizing": "String or null"
    },
    "layer_6_trade_management": {
      "take_profit_target": "String or null",
      "trailing_stop_logic": "String or null",
      "scaling_logic": "String or null"
    }
  } ],
  "fragments": [ { "target_layer": number, "concept_name": string, "description": string, "pseudo_code": string, "indicators_mentioned": [string] } ]
}

If nothing meaningful is found, return empty arrays.
"""

client = OpenAI(
    base_url=os.environ["LLM_API_BASE"],
    api_key=os.environ["LLM_API_KEY"]
)

raw_dir = Path("/home/ml/projects/autonomous_quant_agent/knowledge_base/data/raw")
txt_files = [f for f in raw_dir.rglob("*.txt") if f.stat().st_size > 5000]
random.seed(42)
selected = random.sample(txt_files, min(5, len(txt_files)))

output_dir = Path("/home/ml/projects/autonomous_quant_agent/knowledge_base/data/extraction_test")
output_dir.mkdir(parents=True, exist_ok=True)

total_full = 0
total_fragments = 0

for i, fpath in enumerate(selected, 1):
    transcript = fpath.read_text(encoding="utf-8")
    # Truncate to 30000 chars like activities.py does
    transcript_chunk = transcript[:30000]

    print(f"\n{'='*70}")
    print(f"Transcript {i}/5: {fpath.name}")
    print(f"Size: {fpath.stat().st_size / 1024:.1f} KB, Chunk: {len(transcript_chunk)} chars")
    print(f"{'='*70}")

    try:
        resp = client.chat.completions.create(
            model=os.environ["LLM_MODEL"],
            messages=[
                {"role": "system", "content": KNOWLEDGE_EXTRACTION_PROMPT},
                {"role": "user", "content": transcript_chunk}
            ],
            temperature=0.0,
            max_tokens=4000
        )
        raw = resp.choices[0].message.content

        # Extract JSON from markdown code blocks
        json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        json_str = json_match.group(1) if json_match else raw

        data = json.loads(json_str)
        data["source_file"] = fpath.name
        data["source_title"] = data.get("source_title", fpath.stem)

        full_strategies = data.get("full_strategies", [])
        fragments = data.get("fragments", [])

        total_full += len(full_strategies)
        total_fragments += len(fragments)

        print(f"\n--- Extraction Result ---")
        print(f"Full strategies: {len(full_strategies)}")
        print(f"Fragments (Lego blocks): {len(fragments)}")

        # Print full strategies
        for si, strat in enumerate(full_strategies):
            print(f"\n  [Full Strategy {si+1}]: {strat.get('strategy_name', 'N/A')}")
            print(f"    Description: {strat.get('short_description', 'N/A')[:120]}")
            print(f"    Direction: {strat.get('video_metadata', {}).get('strategy_direction', 'N/A')}")
            indicators = strat.get('layer_2_indicators', [])
            if indicators:
                ind_names = [ind.get('name') for ind in indicators if ind.get('name')]
                print(f"    Indicators: {', '.join(ind_names)}")
            mechanics = strat.get('layer_4_mechanics', {})
            entry = mechanics.get('entry_trigger')
            if entry:
                print(f"    Entry trigger: {entry[:100]}")
            risk = strat.get('layer_5_risk_management', {})
            stop = risk.get('initial_stop_loss')
            if stop:
                print(f"    Stop loss: {stop[:100]}")

        # Print fragments (Lego blocks)
        for fi, frag in enumerate(fragments):
            layer = frag.get('target_layer', '?')
            name = frag.get('concept_name', 'N/A')
            desc = frag.get('description', '')[:120]
            indicators = frag.get('indicators_mentioned', [])
            pseudo = frag.get('pseudo_code', '')[:100]
            print(f"\n  [Fragment {fi+1}]: Layer {layer} - {name}")
            print(f"    Description: {desc}")
            if indicators:
                print(f"    Indicators: {', '.join(indicators)}")
            if pseudo:
                print(f"    Pseudo-code: {pseudo}")

        # Save full output
        out_path = output_dir / f"extraction_{i}_{fpath.stem}.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"\n  Saved to: {out_path}")

    except json.JSONDecodeError as e:
        print(f"  ERROR: Failed to parse JSON: {e}")
        print(f"  Raw output (last 500 chars): {raw[-500:]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print(f"\n{'='*70}")
print(f"SUMMARY")
print(f"{'='*70}")
print(f"Transcripts processed: 5")
print(f"Total full strategies extracted: {total_full}")
print(f"Total fragments (Lego blocks) extracted: {total_fragments}")
print(f"Output directory: {output_dir}")
