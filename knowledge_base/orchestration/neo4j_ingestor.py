from neo4j import GraphDatabase
from typing import Dict, Any, List, Optional
import uuid


class StrategyGraphIngestor:
    """
    Ingests strategies and fragments into Neo4j.

    - Strategies are stored with optional vector embeddings.
    - Fragments are stored with concept_name, target_layer, pseudo_code,
      and vector embeddings for semantic search.
    """

    def __init__(self, uri: str, user: str, password: str):
        """Initialize the Neo4j driver."""
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        """Close the driver."""
        self.driver.close()

    # ------------------------------------------------------------------
    # Strategy ingestion (unchanged, plus embedding)
    # ------------------------------------------------------------------
    def ingest_strategy(self, json_data: Dict[str, Any], strategy_id: str):
        """
        Ingest a single strategy into Neo4j.

        Args:
            json_data: The strategy JSON following the 6-layer schema.
            strategy_id: The YouTube video ID used as strategy identifier.
        """
        with self.driver.session() as session:
            session.execute_write(self._ingest_transaction, json_data, strategy_id)

    def _ingest_transaction(self, tx, json_data: Dict[str, Any], strategy_id: str):
        """Transaction function that performs all graph updates."""

        video_metadata = json_data.get("video_metadata", {})
        creator = video_metadata.get("creator")
        asset_class = video_metadata.get("asset_class")
        direction = video_metadata.get("strategy_direction")

        layer2 = json_data.get("layer_2_indicators", [])
        layer3 = json_data.get("layer_3_context", {})
        layer4 = json_data.get("layer_4_mechanics", {})
        layer5 = json_data.get("layer_5_risk_management", {})
        layer6 = json_data.get("layer_6_trade_management", {})

        strategy_name = json_data.get("strategy_name", f"Strategy {strategy_id}")
        short_description = json_data.get("short_description", "")
        embedding = json_data.get("embedding")

        # 1. Strategy node (idempotent) - include name, description, embedding
        tx.run(
            """
            MERGE (s:Strategy {id: $strategy_id})
            ON CREATE SET s.name = $name, s.short_description = $desc,
                          s.direction = $direction, s.embedding = $embedding
            ON MATCH SET s.name = $name, s.short_description = $desc,
                         s.direction = $direction, s.embedding = $embedding
            """,
            **{
                "strategy_id": strategy_id,
                "name": strategy_name,
                "desc": short_description,
                "direction": direction,
                "embedding": embedding,
            }
        )

        # 2. Creator and PUBLISHED relationship
        if creator:
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (c:Creator {name: $creator})
                MERGE (c)-[:PUBLISHED]->(s)
                """,
                **{
                    "strategy_id": strategy_id,
                    "creator": creator,
                }
            )

        # 3. AssetClass and TRADES_ASSET relationship
        if asset_class:
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (a:AssetClass {name: $asset_class})
                MERGE (s)-[:TRADES_ASSET]->(a)
                """,
                **{
                    "strategy_id": strategy_id,
                    "asset_class": asset_class,
                }
            )

        # 4. Indicators (layer 2)
        for ind in layer2:
            ind_name = ind.get("name")
            if not ind_name:
                continue
            parameters = ind.get("parameters")
            timeframe = ind.get("timeframe")
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (i:Indicator {name: $ind_name})
                MERGE (s)-[r:USES_INDICATOR]->(i)
                ON CREATE SET r.parameters = $ind_parameters, r.timeframe = $ind_timeframe
                ON MATCH SET r.parameters = $ind_parameters, r.timeframe = $ind_timeframe
                """,
                **{
                    "strategy_id": strategy_id,
                    "ind_name": ind_name,
                    "ind_parameters": parameters,
                    "ind_timeframe": timeframe,
                }
            )

        # 5. Pattern (candlestick pattern)
        pattern_name = layer4.get("candlestick_pattern")
        if pattern_name:
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (p:Pattern {name: $pattern_name})
                MERGE (s)-[:USES_PATTERN]->(p)
                """,
                **{
                    "strategy_id": strategy_id,
                    "pattern_name": pattern_name,
                }
            )

        # 6. Context
        trend = layer3.get("trend_requirement")
        regime = layer3.get("market_regime")
        macro = layer3.get("catalyst_or_macro")
        tx.run(
            """
            MERGE (s:Strategy {id: $strategy_id})
            MERGE (s)-[:HAS_CONTEXT]->(ctx:Context)
            ON CREATE SET ctx.trend = $trend, ctx.regime = $regime, ctx.macro = $macro
            ON MATCH SET ctx.trend = $trend, ctx.regime = $regime, ctx.macro = $macro
            """,
            **{
                "strategy_id": strategy_id,
                "trend": trend,
                "regime": regime,
                "macro": macro,
            }
        )

        # 7. Risk
        stop_loss = layer5.get("initial_stop_loss")
        sizing = layer5.get("position_sizing")
        tx.run(
            """
            MERGE (s:Strategy {id: $strategy_id})
            MERGE (s)-[:HAS_RISK_RULE]->(risk:Risk)
            ON CREATE SET risk.stop_loss = $stop_loss, risk.sizing = $sizing
            ON MATCH SET risk.stop_loss = $stop_loss, risk.sizing = $sizing
            """,
            **{
                "strategy_id": strategy_id,
                "stop_loss": stop_loss,
                "sizing": sizing,
            }
        )

        # 8. Management
        take_profit = layer6.get("take_profit_target")
        trailing = layer6.get("trailing_stop_logic")
        scaling = layer6.get("scaling_logic")
        tx.run(
            """
            MERGE (s:Strategy {id: $strategy_id})
            MERGE (s)-[:HAS_TRADE_MANAGEMENT]->(mgmt:Management)
            ON CREATE SET mgmt.take_profit = $take_profit, mgmt.trailing = $trailing, mgmt.scaling = $scaling
            ON MATCH SET mgmt.take_profit = $take_profit, mgmt.trailing = $trailing, mgmt.scaling = $scaling
            """,
            **{
                "strategy_id": strategy_id,
                "take_profit": take_profit,
                "trailing": trailing,
                "scaling": scaling,
            }
        )

        # 9. Setup conditions
        setup_conditions = layer4.get("setup_conditions", [])
        for cond_desc in setup_conditions:
            if not cond_desc:
                continue
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (cond:SetupCondition {description: $cond_desc})
                MERGE (s)-[:REQUIRES_SETUP]->(cond)
                """,
                **{
                    "strategy_id": strategy_id,
                    "cond_desc": cond_desc,
                }
            )

        # 10. Trigger
        entry_trigger = layer4.get("entry_trigger")
        if entry_trigger:
            tx.run(
                """
                MERGE (s:Strategy {id: $strategy_id})
                MERGE (trig:Trigger {description: $trigger_desc})
                MERGE (s)-[:HAS_TRIGGER]->(trig)
                """,
                **{
                    "strategy_id": strategy_id,
                    "trigger_desc": entry_trigger,
                }
            )

    # ------------------------------------------------------------------
    # Fragment ingestion (updated with fragment_id + embedding)
    # ------------------------------------------------------------------
    def create_fragment(
        self,
        fragment: Dict[str, Any],
        embedding: Optional[List[float]] = None,
        source_video_id: Optional[str] = None,
    ):
        """
        Ingest a strategy fragment into Neo4j.

        Args:
            fragment: Dict with concept_name, target_layer, description,
                      pseudo_code, indicators_mentioned.
            embedding: 768-dim vector embedding (from nomic model).
            source_video_id: Originating YouTube video ID.
        """
        with self.driver.session() as session:
            session.execute_write(
                self._create_fragment_tx, fragment, embedding, source_video_id
            )

    def _create_fragment_tx(self, tx, fragment, embedding, source_video_id):
        concept_name = fragment.get("concept_name")
        target_layer = fragment.get("target_layer")
        if not concept_name or target_layer is None:
            raise ValueError("Fragment must have concept_name and target_layer")

        # Use a stable fragment_id: hash of concept_name + target_layer
        fragment_id = fragment.get(
            "fragment_id",
            f"frag-{concept_name}-{target_layer}",
        )

        tx.run(
            """
            MERGE (f:Fragment {fragment_id: $fragment_id})
            SET f.concept_name = $concept_name,
                f.target_layer = $target_layer,
                f.description = $description,
                f.pseudo_code = $pseudo_code,
                f.indicators_mentioned = $indicators,
                f.embedding = $embedding,
                f.source_video_id = $source_video_id,
                f.base_weight = COALESCE(f.base_weight, 1.0)
            """,
            **{
                "fragment_id": fragment_id,
                "concept_name": concept_name,
                "target_layer": target_layer,
                "description": fragment.get("description", ""),
                "pseudo_code": fragment.get("pseudo_code", ""),
                "indicators": fragment.get("indicators_mentioned", []),
                "embedding": embedding,
                "source_video_id": source_video_id,
            }
        )

    # ------------------------------------------------------------------
    # Vector index setup
    # ------------------------------------------------------------------
    def create_vector_index(self, dim: int = 768):
        """
        Create Neo4j vector indexes for Fragment and Strategy embeddings.

        Args:
            dim: Embedding dimension (default 768 for nomic-embed-text-v2-moe).
        """
        with self.driver.session() as session:
            queries = [
                f"""
                CREATE VECTOR INDEX fragment_embedding IF NOT EXISTS
                FOR (f:Fragment) ON (f.embedding)
                OPTIONS {{
                  indexConfig: {{
                    `vector.dimensions`: {dim},
                    `vector.similarity_function`: 'cosine'
                  }}
                }}
                """,
                f"""
                CREATE VECTOR INDEX strategy_embedding IF NOT EXISTS
                FOR (s:Strategy) ON (s.embedding)
                OPTIONS {{
                  indexConfig: {{
                    `vector.dimensions`: {dim},
                    `vector.similarity_function`: 'cosine'
                  }}
                }}
                """,
            ]
            for q in queries:
                session.run(q)
