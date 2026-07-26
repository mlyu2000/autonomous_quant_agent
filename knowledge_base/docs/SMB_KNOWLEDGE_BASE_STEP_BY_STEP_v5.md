# SMB Knowledge Base v5.0 - Step-by-Step Migration Guide

## Overview

This guide documents the transition from v4.0 to v5.0 with Temporal.io orchestration and ClawTeam-OpenClaw multi-agent swarm integration.

## Architecture Changes

```
v4.0:
YouTube → Python Scripts → v4 Schema → v4 Graph

v5.0:
YouTube → YouTube Monitor → Temporal Client → Temporal Workflows → ClawTeam Swarm → v5 Graph
                                     ↓
                                    (Observability)
                                     ↓
                                Prometheus + Grafana
```

## Prerequisites

### 1. Temporal Server

**Option A: Temporal Cloud (Recommended)**
```bash
curl nonprofit.temporal.io/temporal -o temporal
chmod +x temporal
sudo mv temporal /usr/local/bin/

temporal config create-profile --address <your-namespace.temporal.io:7233>
temporal config set-profile --profile default
```

**Option B: Local Temporal**
```bash
docker run -p 7233:7233 -p 8233:8233 temporalio/auto-setup:latest
```

### 2. Python Dependencies

```bash
# Temporal SDK
pip install temporalio==1.4
pip install temporal-sdk-core==1.4

# YouTube API
pip install google-api-python-client
pip install google-auth-httplib2
pip install google-auth-oauthlib

# ClawTeam (already installed from v4.0)
pip install -e ~/ClawTeam-OpenClaw
```

## Validation Requirements

Same as v4.0:
- **Quality Thresholds:**
  - `edge_score >= 65` (minimum viable)
  - `confidence >= 0.75` (high confidence)
  - `p-value < 0.05` (statistical significance)

## v5.0 Implementation Checklist

### ✅ Task 13: Temporal Workflows (COMPLETED)

**Files Created:**
- `orchestration/workflows.py` - ProcessNewVideo, ExtractAndValidateStrategy, RunBacktest, UpdateKnowledgeGraph, RunSwarmAnalysis
- `orchestration/worker.py` - Temporal worker configuration
- `orchestration/client.py` - CLI and programmatic workflow management

**Test Results:**
- [x] Temporal SDK installed
- [x] Workflows defined with proper state management
- [x] Activities registered for async execution
- [x] Retry policies configured
- [ ] Test with 5 pilot videos

### ✅ Task 14: YouTube Monitor (COMPLETED)

**Files Created:**
- `ingestion/youtube_monitor.py` - Auto-detection and Temporal workflow triggering

**Features:**
- YouTube API authentication
- Video deduplication (seenVideos tracking)
- 30-minute check interval (configurable)
- Auto-start workflow on new uploads

**Test Results:**
- [x] YouTube API authentication working
- [x] Deduplication implemented
- [x] Workflow triggering functional
- [ ] Integration with 5 pilot videos

### ✅ Task 15: ClawTeam Integration (IN PROGRESS)

**Files Created:**
- `skills/trading_skills.py` - Custom trading skills for ClawTeam
- `teams/smb_trading_team.yaml` - Team configuration for 4 agents
- `ClawTeam-OpenClaw/` - Already cloned

**Agent Roles:**
1. **Analyst**: Extract strategies from videos (NotebookLM + Claude fallback)
2. **Validator**: Run backtests, calculate edge scores
3. **Portfolio**: Analyze regime dependency and portfolio correlation
4. **Executor**: Generate signals, store in graph

**Test Results:**
- [x] ClawTeam cloned and installed
- [x] Trading skills implemented
- [x] Team template created
- [ ] Full swarm execution on 5 videos

### ❌ Task 16: Self-Improvement Loop (PENDING)

**Files Created:**
- `orchestration/self_improvement.py` - DSPy-based optimization loop

**Features:**
- Metrics collection across strategies
- DSPy teleprompter with MIPROv2
- Prompt optimization for low performers
- Learning history in Neo4j

**Test Results:**
- [x] DSPy optimizer configured
- [x] Metrics collection working
- [ ] Integration with swarm
- [ ] Performance on 5 videos

### ❌ Task 17: Real-Time Features (PENDING)

**Planned Features:**
- Whisper live transcription
- Real-time chart overlay UI
- Paper-trading connector (IBKR/Tradovate)
- Real-time alert system

### ❌ Task 18: Multi-Trader Support (PENDING)

**Planned Features:**
- Extended schema for multiple traders
- Graph relationships for trader-strategy mapping
- Translation layer for non-English content
- Ensemble queries across multiple traders

### ❌ Task 19: Production Hardening (PENDING)

**Planned Features:**
- Prometheus + Grafana monitoring
- Structured logging
- Alerting (Slack, Telegram, Email)
- A/B testing for swarms
- Production documentation

## Testing Workflow

### Test with 5 Pilot Videos (Task 13-15)

```bash
# 1. Start Temporal server
docker run -p 7233:7233 -p 8233:8233 temporalio/auto-setup:latest

# 2. Start worker in background
cd ~/smb_knowledge_base_v5
python orchestration/worker.py &
WORKER_PID=$!

# 3. Test video processing
python -c "
import asyncio
from orchestration.client import TemporalClient

async def main():
    client = TemporalClient()
    await client.connect()
    
    videos = [
        '45eaVU5NVi8',
        'sh5h0GJzjNk',
        'Rqmdw4xyIMM',
        'WXBxLHdYFi8',
        '_cQnMSU5yGk'
    ]
    
    for v in videos:
        url = f'https://youtube.com/watch?v={v}'
        wf_id = await client.start_video_processing(url)
        print(f'Video {v}: {wf_id}')

asyncio.run(main())
"

# 4. Monitor workflows
python orchestration/client.py list

# 5. Check results
for v in videos; do
    python orchestration/client.py result --id "process-$v"
done

# 6. Stop worker
kill $WORKER_PID
```

### Expected Success Metrics

| Video ID | Edge Score | Confidence | Status |
|----------|------------|------------|--------|
| 45eaVU5NVi8 | >= 65 | >= 0.75 | ✅ |
| sh5h0GJzjNk | >= 65 | >= 0.75 | ✅ |
| Rqmdw4xyIMM | >= 65 | >= 0.75 | ✅ |
| WXBxLHdYFi8 | >= 65 | >= 0.75 | ✅ |
| _cQnMSU5yGk | >= 65 | >= 0.75 | ✅ |

## Troubleshooting

### Temporal Worker Not Starting

```bash
# Check if server is running
docker ps | grep temporal

# Check ports
netstat -tlnp | grep 7233

# Restart server
docker restart <temporal_container_id>
```

### YouTube Monitor Authtication Error

```bash
# Delete old credentials
rm -f ~/smb_knowledge_base_v5/token.pickle

# Re-run monitor to re-authenticate
python ingestion/youtube_monitor.py
```

### Swarm Agents Not Responding

```bash
# Check ClawTeam state
clawteam team status --team smb-trading-swarm

# View agent inboxes
clawteam inbox peek --team smb-trading-swarm --agent analyst

# Re-spawn team
clawteam team cleanup --team smb-trading-swarm
clawteam team spawn-team --team smb-trading-swarm --template smb_trading_team
```

## Next Steps After Validation

1. **Deploy YouTube Monitor** as systemd service
2. **Enable Prometheus metrics** endpoint
3. **Set up alerting** for low-quality strategies
4. **Deploy Grafana dashboard** for workflow monitoring

## Migration Path from v4.0

```
v4.0 (existing) → Export JSON → v5.0wkflows ingest → v5.0 graph

No data loss during migration - v4.0 JSON is compatible with v5.0 schema.
```

## Support

- Temporal docs: https://docs.temporal.io
- ClawTeam docs: https://github.com/win4r/ClawTeam-OpenClaw
- YouTube API: https://developers.google.com/youtube/v3
