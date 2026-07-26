# Knowledge Base

## Purpose
- Ingest trading knowledge from YouTube videos / transcripts.
- Store structured strategy fragments in Neo4j.
- Provide graph-backed strategy elements for the synthesis layer.

## Structure
- `core/` — schema and data models
- `orchestration/` — workflows and Neo4j ingestion
- `scripts/` — extraction, validation, indexing, test runners
- `data/` — raw transcripts, processed JSON, embeddings, review logs
- `docs/` — knowledge-base documentation
- `archive/` — legacy/pilot artifacts
- `neo4j/` — local DB runtime data/config

## Integration
- `knowledge_base` → Neo4j (`Strategy`, `Indicator`, `RiskRule`, `TradeRule`, `Fragment`)
- `synthesis_layer/` queries Neo4j and emits `backtest_engine/strategies/spec_*.json`
- `backtest_engine/` compiles specs, runs local simulation, writes `results/raw/` and `results/audit/`

## Environment
- See `docker-compose.neo4j.yml` for Neo4j/Qdrant
- Env vars in `.env`
- Scripts live in `scripts/`
