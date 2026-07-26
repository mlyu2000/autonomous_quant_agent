"""
Temporal.io Workflows for SMB Knowledge Base v5.0

For Temporal SDK 1.27.x - synchronous workflow pattern.
Orchestrates multi-agent swarm processing using ClawTeam-OpenClaw.
All workflows are observable in Temporal UI.
"""

from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from typing import List, Optional, Dict, Any


@workflow.defn
class ProcessNewVideo:
    """Process a single YouTube video through the full pipeline."""
    
    @workflow.run
    async def run(self, video_url: str) -> dict:
        """
        Main entry point for video processing.
        
        Args:
            video_url: YouTube URL to process
            
        Returns:
            dict: Processing results with strategy and metrics
        """
        # Import activities
        from .activities import download_video, extract_strategy, validate_strategy, store_strategy
        from clawteam import spawn
        
        # Step 1: Download video
        video_path = await workflow.execute_activity(
            download_video,
            video_url,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3)
        )
        
        # Step 2: Run swarm analysis (activity wraps temp functionality)
        result = await workflow.execute_activity(
            self._run_swarm_analysis,
            video_path,
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=RetryPolicy(maximum_attempts=2)
        )
        
        # Step 3: Store in graph
        graph_result = await workflow.execute_activity(
            store_strategy,
            result['strategy'],
            start_to_close_timeout=timedelta(minutes=3)
        )
        
        result['graph_status'] = graph_result
        return result
    
    @workflow.signal
    async def cancel_processing(self):
        """Signal to cancel ongoing processing."""
        self._cancelled = True
    
    @workflow.query
    def processing_status(self) -> str:
        """Query current processing status."""
        return getattr(self, '_status', 'idle')
    
    async def _run_swarm_analysis(self, video_path: str) -> dict:
        """Internal method for swarm analysis - will be wrapped as activity."""
        from clawteam import spawn, board
        
        team_name = f"smb_analysis_{int(self.now().timestamp())}"
        
        # Spawn agents
        spawn(team=team_name, agent_name="analyst", 
              task=f"Extract strategies from {video_path} using structured schema", 
              backend="hermes")
        spawn(team=team_name, agent_name="validator", 
              task="Validate chart data, run backtests, compute edge score", 
              backend="hermes")
        spawn(team=team_name, agent_name="portfolio", 
              task="Analyze regime dependency and portfolio correlation", 
              backend="hermes")
        spawn(team=team_name, agent_name="executor", 
              task="Generate final signal, risk rules and store in graph", 
              backend="hermes")
        
        return {"video_path": video_path, "team_name": team_name, "status": "spawning"}


@workflow.defn
class ExtractAndValidateStrategy:
    """Extract strategy and validate with backtesting."""
    
    @workflow.run
    async def run(self, video_path: str) -> dict:
        """Extract strategy from video, validate, return backtest results."""
        from ..ingestion.structured_extractor import extract_strategy
        # Using stub from activities module
        
        strategy = await workflow.execute_activity(
            extract_strategy,
            video_path,
            start_to_close_timeout=timedelta(hours=1)
        )
        
        validated = await workflow.execute_activity(
            validate_strategy,
            strategy,
            start_to_close_timeout=timedelta(hours=1)
        )
        
        return {
            'strategy': validated.model_dump(),
            'edge_score': validated.edge_score,
            'confidence': validated.confidence
        }


@workflow.defn
class RunBacktest:
    """Run backtest on existing strategy."""
    
    @workflow.run
    async def run(self, strategy_dict: Dict[str, Any]) -> dict:
        """
        Run vectorbt backtest on a stored strategy.
        
        Args:
            strategy_dict: Serialized TradingStrategy from v4 schema
            
        Returns:
            dict: Backtest metrics including expectancy, Sharpe ratio, max drawdown
        """
        from ..core.smb_schema_v4 import TradingStrategy
        from ..validation.backtest_engine import run_backtest
        
        strategy = TradingStrategy.model_validate(strategy_dict)
        
        result = await workflow.execute_activity(
            self._run_backtest,
            strategy,
            start_to_close_timeout=timedelta(hours=2)
        )
        
        return result
    
    async def _run_backtest(self, strategy: 'TradingStrategy') -> dict:
        """Internal backtest method."""
        from ..validation.backtest_engine import run_backtest
        result = run_backtest(strategy)
        return result.model_dump()


@workflow.defn
class UpdateKnowledgeGraph:
    """Update Neo4j graph with new strategy."""
    
    @workflow.run
    async def run(self, strategy_dict: Dict[str, Any]) -> dict:
        """
        Store strategy in Neo4j and Qdrant.
        
        Args:
            strategy_dict: TradingStrategy model dump
            
        Returns:
            dict: Node ID and store results
        """
        store_result = await workflow.execute_activity(
            self._store_strategy,
            strategy_dict,
            start_to_close_timeout=timedelta(minutes=2)
        )
        return store_result
    
    async def _store_strategy(self, strategy_dict: dict) -> dict:
        """Internal store method."""
        from ..core.smb_schema_v4 import TradingStrategy
        # Using stub from activities module
        strategy = TradingStrategy.model_validate(strategy_dict)
        return store_strategy(strategy)


@workflow.defn
class RunSwarmAnalysis:
    """Execute multi-agent ClawTeam swarm for strategy extraction and validation."""
    
    @workflow.run
    async def run(self, video_path: str) -> dict:
        """
        Spawn and coordinate ClawTeam swarm.
        
        Four agents:
        - Analyst: Extract strategies from video
        - Validator: Run backtests and compute edge scores
        - Portfolio: Analyze regime dependency
        - Executor: Generate final signals
        
        Returns:
            dict: Swarm output with validated strategy
        """
        team_name = f"smb_analysis_{int(self.now().timestamp())}"
        
        # Spawn agents
        from clawteam import spawn
        
        spawn(team=team_name, agent_name="analyst", 
              task=f"Extract strategies from {video_path} using structured schema", 
              backend="hermes")
        spawn(team=team_name, agent_name="validator", 
              task="Validate chart data, run backtests, compute edge score", 
              backend="hermes")
        spawn(team=team_name, agent_name="portfolio", 
              task="Analyze regime dependency and portfolio correlation", 
              backend="hermes")
        spawn(team=team_name, agent_name="executor", 
              task="Generate final signal, risk rules and store in graph", 
              backend="hermes")
        
        return {
            "team_name": team_name,
            "video_path": video_path,
            "status": "agents_spawned"
        }
