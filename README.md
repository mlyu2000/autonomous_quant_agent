# Autonomous Quant Agent

Self-evolving quantitative trading strategy pipeline. Strategies are generated
from a genome/spec model, compiled to runnable Backtrader code, backtested
against frozen XAUUSD market data, and validated through hard PASS/FAIL/INCONCLUSIVE
gates before promotion.

## Architecture

```
autonomous_quant_agent/
├── backtest_engine/        # MVP simulation core (the authoritative backtester)
│   ├── pipeline.py         # single repeatable entrypoint: validate-data | generate | backtest | audit-*
│   ├── engine/runner.py    # Backtrader harness: broker, analyzers, equity safety guard
│   ├── engine/custom_analyzers.py  # CriticAnalyzer (MAFE/MFE per-trade log)
│   ├── compiler/dynamic_loader.py # spec -> runnable bt.Strategy (safe condition eval)
│   ├── synthesis_layer/    # strategy spec schema + generator (dedup, distinct theses)
│   ├── audit/gates.py      # validation gates (sample_size, pnl_invariant, outlier_resistance...)
│   ├── audit/writer.py     # audit JSON writer
│   ├── data_lake/          # FROZEN market data (XAUUSD M1/H1/H4/M15/D1 parquet)
│   ├── data/manifest.json  # data contract (period, symbol, commission model)
│   ├── tests/              # 14 tests (truth baseline, generator, gates)
│   └── requirements.txt
└── knowledge_base/         # strategy knowledge system (YouTube -> Neo4j -> synthesis)
    ├── core/               # smb_schema_v6.py (Neo4j schema)
    ├── orchestration/      # neo4j_ingestor.py, workflows.py, activities.py
    ├── scripts/            # ingestion + build helpers
    ├── docs/               # technical specification
    └── (neo4j/, archive/, data/ ignored — runtime/legacy, not in git)
```

## Prerequisites

- Python 3.11+
- `uv` (recommended) or `venv`
- Network: none required for backtest (data is frozen in `data_lake/`)

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

# 2. generate distinct strategy specs (dedup enforces distinct theses)
./venv/bin/python pipeline.py generate --count 8

# 3. backtest all specs in strategies/
./venv/bin/python pipeline.py backtest --spec-dir strategies --out results/raw

# 4. run the validation gates over the run
./venv/bin/python pipeline.py audit-gen
./venv/bin/python pipeline.py audit-json
```

Gates emit PASS / FAIL / INCONCLUSIVE only. A strategy that loses money, has <12
trades, a PnL/commission mismatch, or a single trade dominating profit is FAIL —
never silently "profitable".

## Data contract (frozen)

- Symbol: XAUUSD (gold spot, per oz)
- Periods: M1, M15, H1, H4, D1 (Dukascopy-derived)
- 2006-01-01 .. 2025-12-31
- Commission: fixed $/oz (COMM_FIXED), configurable
- Leverage: 100:1 (retail XAUUSD, per manifest)
- Swap/rollover: disabled in MVP (documented divergence)

## Known limitations / honesty notes

- **No leverage/margin realism beyond 100:1**: sizer uses fixed 1-oz lots.
- **Swap/rollover disabled** in the MVP backtest (Backtrader/MT4 divergence).
- **Condition grammar is explicit & safe**: only `close>sma`, `close<ema`,
  `rsi<30`, `rsi>70`, `macd_hist>0`, `macd_hist<0` are evaluated. Unknown
  conditions fail closed (0 trades) rather than silently passing.
- **Exit model**: exits use market orders (self.close) so positions ALWAYS
  close; TP/SL percentages are recorded for reporting but execution is market.
- **MT5/MT4 legacy paths** are excluded from the active pipeline (see .gitignore).
- **Time-stability gate** is a coarse placeholder (window count) until per-window
  reruns are implemented.

## Testing

```bash
cd backtest_engine
./venv/bin/python -m pytest tests/ -q   # 14 tests, ~4s
```

Tests cover: truth-baseline equity math, generator dedup (distinct theses only),
and audit gates (pnl_invariant catches real discrepancies, outlier resistance
blocks single-trade-dominated results).

## License

Internal research project.
