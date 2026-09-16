# Autonomous Quant Agent

Self-evolving quantitative trading strategy pipeline. Strategies are generated
— from a genome/spec model **and from the Neo4j knowledge base** — compiled to
runnable Backtrader code, backtested against frozen XAUUSD market data with an
**honest execution model** (real TP/SL, commission, spread, slippage, swap),
and validated through hard **PASS / FAIL / INCONCLUSIVE** gates before
promotion.

## Objective

Build a system that *proposes, tests, and keeps only strategies that survive
skeptical validation* — and that improves its own framework over time, not just
the strategies it produces. Money-risk discipline is the core invariant: a
verdict of "profitable" must be backed by a real backtest whose accounting
reconciles, and unknown/guessable inputs fail closed rather than silently
passing.

## Architecture

```
autonomous_quant_agent/
├── backtest_engine/                # authoritative MVP backtester
│   ├── pipeline.py                 # single entrypoint:
│   │                               #   validate-data | generate | generate-kb |
│   │                               #   evolve | backtest | audit-gen | audit-json
│   ├── engine/runner.py           # Backtrader harness: broker, spread pricing,
│   │                               #   analyzers, equity safety guard
│   ├── engine/custom_analyzers.py # CriticAnalyzer (per-trade MAFE/MFE log)
│   ├── compiler/dynamic_loader.py # spec -> runnable bt.Strategy (grammar eval,
│   │                               #   real TP/SL pending orders, swap)
│   ├── synthesis_layer/
│   │   ├── grammar.py             # SHARED condition grammar (single source of
│   │   │                           #   truth for generator + compiler + validator)
│   │   ├── strategy_schema.py     # spec dataclass + validation + dedupe
│   │   │                           #   (mechanism + indicator core + direction)
│   │   ├── strategy_generator.py  # template theses + mutation + dedupe
│   │   └── neo4j_bridge.py        # Neo4j graph -> valid specs (KB wiring)
│   ├── evolution/                 # SELF-EVOLUTION: the system evolves its own
│   │   │                           #   framework ingredients (EA over a genome)
│   │   ├── genome.py              # bounded genome (search-distribution genes)
│   │   ├── genome_generator.py    # genome -> distinct specs (fail-closed)
│   │   ├── fitness.py             # mechanism-agnostic fitness (real audit PASSes)
│   │   ├── governance.py          # harmlessness gate + rejected_proposals store
│   │   ├── evolve.py              # the EA loop (real backtests + real audit)
│   │   └── evolution_state.json   # auto-applied baseline genome (committed)
│   ├── audit/gates.py             # 8 gates (sample, min/yr, time-stability,
│   │                               #   pnl-invariant, outlier, DD, cost-ratio,
│   │                               #   open-position)
│   ├── audit/writer.py
│   ├── data/manifest.json         # frozen data contract (costs, spread, swap,
│   │                               #   leverage, periods)
│   ├── data_lake/                 # FROZEN XAUUSD M1/M15/H1/H4/D1 parquet
│   ├── tests/                     # 48 tests (truth baseline, generator, gates,
│   │                               #   indicator compilability, evolution)
│   ├── results/rejected_proposals.jsonl  # HIGH-risk framework proposals
│   │                               #   (never auto-applied; re-reviewed every 5 gens)
│   └── requirements.txt
└── knowledge_base/                 # YouTube -> Neo4j -> synthesis (first-class)
    ├── core/                      # smb_schema_v6.py (pydantic schema)
    ├── orchestration/             # neo4j_ingestor.py, workflows.py, activities.py
    ├── scripts/                   # ingestion + build helpers
    ├── docs/                      # technical specification
    ├── data/sample_transcripts/   # 8 sample transcripts
    ├── .env                       # NEO4J_URI / USER / PASSWORD (gitignored)
    └── docker-compose.neo4j.yml   # neo4j:5.23-enterprise (bolt :7687)
```

### Data flow

```
manifest.json (frozen costs/periods)
        │
   pipeline.py
        │
   ┌────┴───────────────────────────────────────┐
   generate              generate-kb            │
 (template theses)      (Neo4j graph)           │
   └────┬───────────────────────┘
        │  spec_*.json (deduped, grammar-validated)
   backtest  (per spec: validate_spec -> compile_spec -> run_backtest
              on the spec's timeframe, spread-aware, costs from manifest)
        │  run_<ts>/*_metrics.json + run_manifest.json
   audit   (8 gates -> PASS / FAIL / INCONCLUSIVE)
        │  results/audit/audit_gen_<N>.json
        │
   evolve  (EA over the genome: re-runs generate->backtest->audit for
            several candidate genomes, fitness = # genuinely-PASS survivors;
            the fittest LOW-risk genome is auto-applied as the new baseline
            (committed); HIGH-risk framework proposals it observes go to
            results/rejected_proposals.jsonl for human re-review)
```

## Self-evolution (the system improves its own framework)

The stated objective is a system that *improves its own framework over time,
not just the strategies it produces*. The `evolution/` package implements this
as an evolutionary algorithm over a **genome** — a bounded, JSON,
fail-closed representation of the builder's *search distribution* (the
framework's ingredients): indicator-lookback range, TP/SL magnitude bands,
Bollinger dev, mutation rate, and a seed.

- **What evolves**: the genome (WHERE the search looks) — not the skeptical
  auditor. `pipeline.py evolve --generations N` runs the EA: each candidate
  genome shapes a batch of distinct specs, which are backtested on the frozen
  data lake and audited by the real gates. Fitness is mechanism-agnostic
  (count of genuinely-PASS survivors + a conservative quality tiebreak), so a
  genome is only "fitter" if it produces more validated survivors.
- **Governance (the safety model)**:
  - **LOW-risk** = a genome/search-distribution change, strictly within
    declared bounds, touching only the `evolution/` surface → **auto-applied**
    (becomes the new committed baseline genome). A no-regression guard means a
    genome is only applied if it is not worse than the incumbent baseline.
  - **HIGH-risk** = anything touching the skeptical gates, the frozen manifest,
    the shared grammar, the compiler/runner/schema, or credentials → **NEVER
    auto-applied**. The EA *observes its own failure modes* (e.g. a gate
    binding on N specs, zero PASS across the population) and records such
    framework changes to `results/rejected_proposals.jsonl`, where they are
    **re-reviewed every 5 generations** by a human. Rejected proposals are
    kept, never discarded.
  - **Harmlessness gate**: a hard check that no proposal — regardless of its
    declared risk — may touch a protected surface. The EA can tune its search,
    it cannot rewrite the auditor.
- **No manual menu edits**: the only way to change the builder's ingredients is
  through this EA loop (or a human explicitly accepting a HIGH-risk proposal).

## Honest execution model (MVP)

- **Pricing**: `SpreadPandasData` — buys fill at Ask, sells at Bid (mid ±
  half-spread), so spread cost is real, not cosmetic.
- **Commission**: `COMM_FIXED` per oz per leg (XAUUSD: $7/lot round turn =
  $0.035/oz per leg), from the manifest — never a percentage mis-set as fixed.
- **TP/SL**: *real* pending Limit/Stop orders placed on entry fill (next-bar
  fills, no lookahead). Time-exit and end-of-data cancel pendings and market-
  close, so a position **always** closes.
- **Slippage**: a small fraction on market fills.
- **Swap/rollover**: charged by the strategy once per trading day held
  (Wed/Fri triple), at manifest rates, attributed to the trade's PnL.
- **Leverage**: 100:1 (retail XAUUSD) per manifest.
- **Equity safety guard**: final value is computed from the (net-of-cost)
  trade log so the PnL invariant holds; broker equity is a cross-check.
  Non-finite / below -initial equity is flagged, never reported.

## Prerequisites

- Python 3.11+
- `uv` (recommended) or `venv`
- Neo4j 5.x (for `generate-kb`); the local container is `smb-neo4j`
  (bolt `localhost:7687`). Backtest does **not** need Neo4j.

## Setup

```bash
cd backtest_engine
python3.11 -m venv venv      # or: uv venv --python 3.11 venv
./venv/bin/pip install -r requirements.txt
```

## Usage (single repeatable pipeline)

```bash
cd backtest_engine
# 1. verify frozen data matches manifest
./venv/bin/python pipeline.py validate-data

# 2. generate distinct strategy specs (template theses, deduped)
./venv/bin/python pipeline.py generate --count 8
#    (optional) shape the search with the auto-applied evolved genome:
./venv/bin/python pipeline.py generate --count 8 --use-genome

# 2b. (optional) pull mappable strategies from the Neo4j knowledge base
NEO4J_PASSWORD=*** ./venv/bin/python pipeline.py generate-kb --count 8

# 3. backtest all specs in strategies/
./venv/bin/python pipeline.py backtest --spec-dir strategies --out results/raw

# 4. run the validation gates over the run
./venv/bin/python pipeline.py audit-gen
./venv/bin/python pipeline.py audit-json

# 5. (optional) run the self-evolution EA: evolve the builder's genome,
#    auto-apply the LOW-risk improvement (committed), and record the
#    HIGH-risk framework proposals it observes for human re-review
./venv/bin/python pipeline.py evolve --generations 2 --pop-size 2 --specs-per-genome 3
```

Gates emit **PASS / FAIL / INCONCLUSIVE** only. A strategy that loses money,
has <12 trades, a PnL/commission mismatch, a single trade dominating profit,
concentrates all profit in one year, or whose edge does not survive
commission+swap is **FAIL** — never silently "profitable". A run that simply
lacks evidence is **INCONCLUSIVE**, never a silent pass.

### Neo4j knowledge-base wiring

The knowledge base ingests YouTube strategy transcripts into a Neo4j graph
(`Strategy` / `Fragment` / `Indicator` / `Risk` / `Management` / `Trigger`
nodes). `generate-kb` (via `synthesis_layer/neo4j_bridge.py`) is the single
code path that turns a graph `Strategy` into a runnable spec:

- Free-text indicators are **normalized** to the shared grammar
  ("200 EMA" → `ema(200)`, "RSI" → `rsi(14)`, "Bollinger Bands" → `bbands`);
  names that cannot be faithfully mapped (Price Action, VIX, S/R levels,
  options, delta…) are **skipped**, never guessed.
- Mechanism / direction / timeframe are classified deterministically from the
  strategy's text; TP:SL ratio is parsed when explicit.
- **Fail-closed**: a spec is only emitted if it passes `validate_spec` AND
  compiles; anything else is counted and dropped. If the graph is unreachable
  the command raises — it does not fabricate specs.
- Credentials come from env / `knowledge_base/.env` (never hard-coded, never
  printed).

## Data contract (frozen)

- Symbol: XAUUSD (gold spot, per oz)
- Periods: M1, M15, H1, H4, D1 (Dukascopy-derived)
- 2006-01-01 .. 2025-12-31
- Commission: fixed $/oz (COMM_FIXED), from manifest
- Spread: static total spread, from manifest
- Slippage: small pct, from manifest
- Leverage: 100:1
- Swap/rollover: charged, from manifest rates

## Testing

```bash
cd backtest_engine
./venv/bin/python -m pytest tests/ -q   # 48 tests
```

Covers: truth-baseline equity math, generator dedupe (distinct theses only),
audit gates (the PnL-invariant now reconciles two INDEPENDENT sources — the
broker balance-sheet value vs the per-trade log — and is a real check, not a
tautology; outlier resistance blocks single-trade results; time-stability uses
real trade dates), indicator compilability (every grammar-whitelisted
indicator compiles AND runs — the regression guard for grammar/compiler drift),
and the self-evolution subsystem (genome bounds / fail-closed mutation,
mechanism-agnostic fitness, the harmlessness gate + rejected-proposal store
with its 5-generation re-review cadence, and the genome->spec bridge).

The real end-to-end `pipeline.py evolve` (real backtests + real audit) is an
integration run, exercised separately rather than in the fast unit suite.

## Known limitations / honesty notes

- **Fixed 1-oz lots**: sizing is fixed-lot only; no dynamic risk-based sizing.
- **Condition grammar is explicit & safe**: a shared whitelist is evaluated;
  unknown conditions fail closed (spec rejected) rather than silently passing.
- **Single instrument/timeframe per spec**: each spec runs on its declared
  timeframe; the data lake has exactly M1/M15/H1/H4/D1.
- **KB strategies are heuristic-normalized**: the bridge maps free-text KB
  strategies to the grammar conservatively; it will not backtest a KB
  strategy it cannot faithfully express.
- **MT5/MT4 legacy paths** are excluded from the active pipeline (see
  .gitignore).

## License

Internal research project.
