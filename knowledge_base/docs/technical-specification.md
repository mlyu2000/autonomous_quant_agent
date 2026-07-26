# Master Technical Specification: Autonomous Quant Agent

## 1. Executive Summary
The Autonomous Quant Agent is a self-improving, decoupled algorithmic trading research system. It continuously discovers, generates, backtests, and refines trading strategies without human intervention. 

It achieves this by extracting trading knowledge from unstructured data (transcripts, articles) as **Strategy Fragments (Lego Blocks)**. It uses a **Neo4j Knowledge Graph** to store these fragments, a **Genetic Algorithm** to assemble and mutate them into complete 6-Layer Strategy JSONs, an **LLM Critic** for logical debugging, and a **Decoupled Backtest Engine** for simulation. Crucially, it employs strict **Walk-Forward Validation** (In-Sample vs. Out-of-Sample) to prevent curve-fitting.

---

## 2. Design Philosophy & Architecture
*   **Fragment-Based Synthesis (Lego Blocks):** Trading edge rarely comes as a complete package. The system extracts isolated concepts (e.g., a specific Layer 3 Support/Resistance logic, or a Layer 5 Risk rule) and organically combines them.
*   **Decoupled Microservices:** The "Brain" (Synthesis Layer) and the "Simulator" (Backtest Engine) are strictly separated via REST APIs.
*   **LLM-as-a-Compiler:** An LLM translates the assembled 6-Layer Strategy JSON into executable Python (`Backtrader`) code dynamically.
*   **Anti-Overfitting:** A strategy is only considered "Robust" if it survives Out-of-Sample (OOS) testing. The Knowledge Graph is *only* rewarded based on OOS performance.

---

## 3. System Components & Directory Structure

### Master Directory Tree
```text
quant_system/
├── knowledge_extraction/   # Parses unstructured text into Fragments & Strategies
├── synthesis_layer/        # The "Brain" (Graph, GA, LLM Critic, Orchestrator)
├── backtest_engine/        # The "Simulator" (FastAPI, LLM Compiler, Backtrader)
├── data_lake/              # Centralized Parquet storage
└── shared/                 # Shared Pydantic schemas (Fragments, Strategy JSON, API)
```

### 3.1. Component 1: Knowledge Extraction Pipeline
**Purpose:** Read transcripts/articles and extract actionable knowledge.
*   **LLM Extractor:** Uses structured outputs to parse text. If it finds a full strategy, it outputs a complete 6-Layer JSON. If it finds isolated concepts, it outputs `StrategyFragment` objects (tagged with `target_layer`, `pseudo_code`, and `indicators_mentioned`).
*   **Graph Ingester:** Pushes the extracted JSONs and Fragments into Neo4j.

### 3.2. Component 2: The Knowledge Graph (Neo4j)
**Purpose:** Long-term memory of indicators, fragments, and their synergies.
*   **Nodes:** `Indicator`, `Concept`, `Fragment` (The Lego blocks), `LayerCategory` (1 through 6).
*   **Edges:** 
    *   `[:SYNERGIZES_WITH {weight: float}]` (Between indicators/fragments).
    *   `[:BELONGS_TO_LAYER]` (Links a Fragment to its target layer).
*   **Logic:** 
    *   *Read:* Query top fragments and synergies to seed the Genetic Algorithm.
    *   *Write:* After an OOS backtest, update the edge weights of the fragments/indicators used. $$W_{new} = W_{old} + (\alpha \times \text{OOS\_Sharpe})$$.

### 3.3. Component 3: The Synthesis Layer
**Purpose:** Assemble Lego blocks and orchestrate the evolution loop.
*   **`genetic_engine.py`:** Generates 6-Layer JSON blueprints. 
    *   *Crossover:* Swaps entire layers between two parent strategies.
    *   *Mutate (Parameters):* Tweaks numerical values (e.g., SMA 20 -> 25).
    *   *Mutate (Fragments):* Queries Neo4j for a random `Fragment` belonging to a specific layer (e.g., Layer 5) and replaces the existing layer logic with the Fragment's pseudo-code.
*   **`llm_critic.py`:** Acts as a logical debugger for high-potential, flawed strategies.
*   **`orchestrator.py`:** The master loop handling the IS/OOS data split and API calls.

### 3.4. Component 4: The Backtest Engine
**Purpose:** A stateless execution environment.
*   **API (`FastAPI`):** Receives `BacktestPayload` (Strategy JSON + Date Config).
*   **LLM Compiler (`compiler/`):** Prompts an LLM to convert the JSON (which may contain text from Fragments) into a `bt.Strategy` class. Uses `importlib` to dynamically load it.
*   **Execution (`engine/runner.py`):** Loads Parquet data, slices it by the requested dates (IS or OOS), runs `Backtrader`, and returns standardized metrics.

### 3.5. Component 5: Data Pipeline
**Purpose:** Fast, standardized data access.
*   **Format:** Parquet (via `Polars`/`Pandas`). Strict Rule: NO lookahead bias.

---

## 4. The Core Process Flow (The Unified Loop)

**Phase 0: Knowledge Ingestion (Continuous)**
1. System ingests a YouTube transcript.
2. Extractor identifies a novel Trailing Stop logic. It saves it to Neo4j as a `Fragment` linked to `Layer 6`.

**Phase 1: Setup**
3. Orchestrator defines In-Sample (IS: 2020-2022) and Out-of-Sample (OOS: 2023) periods.
4. Orchestrator queries Neo4j for top indicator synergies and high-weight Fragments.

**Phase 2: In-Sample Evolution**
5. GA generates a Gen 1 population of 10 Strategy JSONs by assembling Fragments.
6. Orchestrator sends all 10 to the Backtest Engine API concurrently (using IS dates).
7. Top 3 "Elite" strategies are sent to the LLM Critic to fix logical flaws.
8. GA creates Gen 2 via crossover, parameter mutation, and **Fragment mutation** (swapping out a layer for a new Fragment from the Graph).
9. Repeat for N generations. Extract final Top 5 Elite strategies.

**Phase 3: Out-of-Sample Validation (The Filter)**
10. Orchestrator sends the Top 5 Elite to the Backtest Engine using OOS dates.
11. Compare IS vs. OOS metrics. Reject if `OOS_Sharpe < (IS_Sharpe * 0.5)`.
12. For strategies that pass/fail, update Neo4j edge weights for the specific **Fragments** and **Indicators** used. The AI learns which Lego blocks survive in unseen markets.

---

## 5. Strict Coding Rules & Considerations for Agents

*   **Fragment Integration:** When the GA uses a Fragment, the LLM Compiler in the Backtest Engine must be robust enough to translate the Fragment's `pseudo_code` into valid Backtrader logic.
*   **No Data Leakage:** The Backtest Engine MUST slice the Parquet dataframe by `start_date` and `end_date` *before* passing it to Backtrader.
*   **Sandboxing & Timeouts:** LLM-generated Python code execution must be wrapped in `asyncio.wait_for(..., timeout=60)`.
*   **JSON Validation:** All JSONs and Fragments moving between modules MUST be validated via Pydantic schemas (`shared/schemas.py`).
*   **Concurrency:** The Synthesis Layer must use `httpx` or `aiohttp` to trigger backtests concurrently.

---

## 6. Future Roadmap (To-Do List)
1.  **Macro Risk Manager (The Global Warden):** A portfolio-level risk module to prevent simultaneous over-leveraging across multiple deployed strategies.
2.  **Live Execution Bridge:** Integration with `CCXT` to deploy winning strategies to live WebSocket feeds and real Broker APIs.
3.  **Continuous Data Fetcher:** Automated ingestion of latest market candles to the Parquet Data Lake.
