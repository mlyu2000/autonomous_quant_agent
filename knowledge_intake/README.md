# knowledge_intake — continuous trading-knowledge intake

The application **continuously** acquires new trading knowledge from two
independent sources and stores it in the Neo4j knowledge graph, feeding the
same 6-Layer Strategy Schema the YouTube pipeline uses:

| Source | What it does |
|---|---|
| **A) Internet (RSS)** | Polls RSS feeds for new articles, fetches the body, asks the LLM to extract a tradeable strategy. Non-strategic content (news/regulation) is skipped — never faked. |
| **B) LLM model** | Asks the LLM to synthesize a fresh, concrete, backtestable strategy for a rotating topic (gold trend-following, RSI mean-reversion, momentum breakouts, ...). |

Pipeline: `source -> LLM (6-layer extraction) -> StrategyV6 validation -> 768d embedding -> StrategyGraphIngestor -> Neo4j`.

Everything reuses existing code — `knowledge_base/core/smb_schema_v6.py`,
`knowledge_base/orchestration/neo4j_ingestor.py` — so graph nodes are
identical in shape to the YouTube-ingested ones and are immediately
available to `pipeline.py generate-kb`.

## Run it

```bash
cd ~/projects/autonomous_quant_agent

# one cycle (internet or LLM), then exit
./backtest_engine/venv/bin/python knowledge_intake/intake.py --once --source internet
./backtest_engine/venv/bin/python knowledge_intake/intake.py --once --source llm --count 3

# CONTINUOUS — loop every 30 minutes (both sources, alternating)
./backtest_engine/venv/bin/python knowledge_intake/intake.py --loop --source internet --interval 1800

# via the pipeline / control plane (one cycle as a job)
./backtest_engine/venv/bin/python backtest_engine/pipeline.py intake --source llm --count 3
# or in the browser: Generate & Run tab -> "Intake knowledge (internet/LLM)"
```

A single combined loop (alternating sources) is also possible by running two
daemon processes, one per source.

## Configuration

- **Feeds**: `knowledge_intake/feeds.yaml` — add any RSS feed URL. Without
  `pyyaml` in the venv the built-in defaults (cointelegraph, cryptopotato) are used.
- **LLM / embedding / Neo4j**: from `knowledge_base/.env` (`LLM_API_BASE`,
  `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_API_BASE`, `NEO4J_*`). Never committed.
- **State / log**: `intake_state.json` (seen URLs, ingested ids, totals),
  `intake_log.jsonl` (one line per cycle) — both gitignored.

## Honesty rules (MVP)

- Only strategies that **parse** the 6-Layer schema are stored; everything else
  is counted as skipped. No placeholder/fake conditions.
- **Idempotent**: stable strategy id (`hash(name+indicators+direction)`), so
  re-running the same article never duplicates a Strategy node.
- If a feed, the LLM, or Neo4j is unreachable the cycle reports the failure —
  nothing is invented.

## Control-plane visibility

The Knowledge Base tab shows a "Continuous knowledge intake" panel (last run,
strategies ingested, per-source totals, last 20 cycles). The intake cycle is
triggerable as a job from the Generate & Run tab.
