# smb_schema_v6.py
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional


class VideoMetadata(BaseModel):
    creator: Optional[str] = None
    asset_class: Optional[str] = None
    strategy_direction: Optional[str] = None  # "Long", "Short", "Both"


class Layer2Indicator(BaseModel):
    name: Optional[str] = None
    parameters: Optional[str] = None
    timeframe: Optional[str] = None


class Layer3Context(BaseModel):
    trend_requirement: Optional[str] = None
    market_regime: Optional[str] = None
    catalyst_or_macro: Optional[str] = None


class Layer4Mechanics(BaseModel):
    setup_conditions: List[str] = []
    candlestick_pattern: Optional[str] = None
    entry_trigger: Optional[str] = None


class Layer5RiskManagement(BaseModel):
    initial_stop_loss: Optional[str] = None
    position_sizing: Optional[str] = None


class Layer6TradeManagement(BaseModel):
    take_profit_target: Optional[str] = None
    trailing_stop_logic: Optional[str] = None
    scaling_logic: Optional[str] = None


class StrategyV6(BaseModel):
    strategy_name: str
    short_description: str
    video_metadata: VideoMetadata
    layer_2_indicators: List[Layer2Indicator] = []
    layer_3_context: Layer3Context
    layer_4_mechanics: Layer4Mechanics
    layer_5_risk_management: Layer5RiskManagement
    layer_6_trade_management: Layer6TradeManagement
    confidence: Optional[float] = None
    edge_score: Optional[int] = None
    validation_status: Optional[str] = None
    validation_errors: Optional[List[str]] = None
    validation_warnings: Optional[List[str]] = None
    quality_gates_passed: Optional[bool] = None
    embedding: Optional[List[float]] = Field(
        default=None,
        description="Vector embedding of the strategy's name, description, and key mechanics"
    )

    model_config = ConfigDict(extra='ignore')


class StrategyFragment(BaseModel):
    """
    A partial trading concept (Lego block) extracted from a transcript.
    """
    target_layer: int = Field(ge=1, le=6)
    concept_name: str
    description: str
    pseudo_code: str
    indicators_mentioned: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = Field(
        default=None,
        description="Vector embedding of the fragment's concept_name + description"
    )

    model_config = ConfigDict(extra='ignore')


class KnowledgeExtraction(BaseModel):
    """Top-level container for LLM knowledge extraction output."""
    source_title: Optional[str] = None
    video_id: Optional[str] = None
    full_strategies: List[StrategyV6] = Field(default_factory=list)
    fragments: List[StrategyFragment] = Field(default_factory=list)

    model_config = ConfigDict(extra='ignore')
