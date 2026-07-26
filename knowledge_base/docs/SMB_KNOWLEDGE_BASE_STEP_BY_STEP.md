# SMB Knowledge Base v3.0 - Step-by-Step Implementation Guide

## Executive Summary

This document provides **detailed step-by-step instructions** for each phase of the SMB Capital YouTube Knowledge Base processing pipeline.

**Total Phases:** 3  
**Estimated Time (Full Channel):** 48-72 hours  
**GPU Requirements:** NVIDIA A30 (24GB VRAM) or similar

---

## PHASE 1: NotebookLM - Natural Language Analysis

**Goal:** Extract trading strategies, patterns, and insights using Google's NotebookLM  
**Input:** YouTube video URLs from SMB Capital channel  
**Output:** Natural language analysis (Markdown, JSON, PPTX)  

### Step 1.1: Authentication Setup

**Purpose:** Verify NotebookLM access to Google services

```bash
# First-time setup only
notebooklm login
```

**What happens:**
1. Opens Chrome browser for Google OAuth
2. Authenticates with your Google account
3. Stores session cookies in `~/.notebooklm/profiles/default/storage_state.json`
4. JWT tokens are cached for ~15-30 minutes

**Verification:**
```bash
notebooklm auth check
# Should show: ✓ pass (Storage exists, Cookies present, SID cookie found)
```

**Expected Output:**
```
Authentication Check
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check           ┃ Status    ┃ Details                                        ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Storage exists  │ ✓ pass    │ file (/home/ml/.notebooklm/profiles/default/storag… │
│ JSON valid      │ ✓ pass    │                                                │
│ Cookies present │ ✓ pass    │ 17 cookies                                     │
│ SID cookie      │ ✓ pass    │ .google.com, .google.com.hk, .google.com.sg    │
└─────────────────┴───────────┴────────────────────────────────────────────────┘
```

---

### Step 1.2: Create Notebook

**Purpose:** Create a dedicated workspace for SMB Capital analysis

```bash
notebooklm create "SMB Capital Knowledge Base" --json
```

**What happens:**
1. Creates new NotebookLM project with title "SMB Capital Knowledge Base"
2. Returns notebook ID (UUID format)
3. Initializes empty notebook structure

**Expected Output:**
```json
{
  "notebook": {
    "id": "11dcac26-7de7-411f-85ba-62e8d2fb422d",
    "title": "SMB Capital Knowledge Base",
    "created_at": "2026-05-03T06:00:00"
  }
}
```

**Save the notebook ID** - you'll need it for all subsequent operations.

---

### Step 1.3: Add YouTube Sources

**Purpose:** Register YouTube videos as sources for analysis

```bash
# For single video
notebooklm source add "https://www.youtube.com/watch?v=45eaVU5NVi8" \
    --notebook 11dcac26-7de7-411f-85ba-62e8d2fb422d --json

# Batch add from file
while read -r url; do
    notebooklm source add "$url" --notebook <notebook_id> --json
done < /home/ml/smb_youtube_urls_notebooklm.txt
```

**What happens:**
1. NotebookLM fetches video metadata (title, description, thumbnail)
2. Video starts processing (transcription, indexing)
3. Status: `processing` → `ready` (typically 1-2 minutes)

**Source List Verification:**
```bash
notebooklm source list --notebook <notebook_id> --json
```

**Expected Output:**
```json
{
  "sources": [
    {
      "id": "2cae1988-1458-43c3-af25-27b910ae2c1b",
      "title": "The Simple 4-Step Process To Build Your Own AI Trading Assistant",
      "type": "SourceType.YOUTUBE",
      "url": "https://www.youtube.com/watch?v=45eaVU5NVi8",
      "status": "ready",
      "created_at": "2026-05-03T06:08:15"
    }
  ]
}
```

**Critical Notes:**
- Sources must have `status: "ready"` before generating analysis
- Each video takes ~1-2 minutes to index
- Recommendation: Add videos in batches of 50 for manageability
- Large channel (2,478 videos): Create multiple notebooks (100 videos each)

---

### Step 1.4: Wait for Source Processing

**Purpose:** Ensure all sources are fully indexed before analysis

```bash
# Wait for all sources in notebook
notebooklm source wait --notebook <notebook_id> --timeout 1200

# Or wait for individual source
notebooklm source wait <source_id> --notebook <notebook_id>
```

**What happens:**
1. Continuously polls NotebookLM API for source status
2. Blocks until all sources reach `ready` status or timeout (default: 300s)
3. Returns list of all sources with final status

**Best Practices:**
- Use `--timeout 1200` for large batches
- Implement retry logic for transient failures
- Monitor `source list` during long waits

**Error Handling:**
```json
// Timeout (exit code 2)
{
  "error": "Timeout waiting for sources to be ready"
}

// Source processing failed (exit code 1)
{
  "error": "Source processing failed for YouTube URL"
}
```

---

### Step 1.5: Generate Analysis

**Purpose:** Create natural language summaries and insights

```bash
# Generate briefing report
notebooklm generate report --format briefing-doc \
    --notebook <notebook_id> --json

# Generate study guide (educational format)
notebooklm generate report --format study-guide \
    --notebook <notebook_id> --json

# Generate quiz for training
notebooklm generate quiz --notebook <notebook_id> --json

# Generate audio overview
notebooklm generate audio "Focus on technical analysis patterns" \
    --notebook <notebook_id> --json
```

**What happens:**
1. NotebookLM analyzes all indexed sources
2. Generates natural language analysis based on format:
   - `briefing-doc`: Executive summary with extracted insights
   - `study-guide`: Educational content with examples
   - `quiz`: Multiple-choice questions with answers
   - `audio`: Podcast-style voice summary
3. Returns task ID and `pending` status
4. Takes 10-30 minutes for completion

**Expected Output:**
```json
{
  "task_id": "cdbe19de-6879-449c-9943-dfc16d8ee2ed",
  "status": "pending"
}
```

**Analysis Timeout Values:**
| Format | Typical Time | Recommended Timeout |
|--------|--------------|---------------------|
| briefing-doc | 10-15 min | 900s |
| study-guide | 15-20 min | 1200s |
| quiz | 5-10 min | 600s |
| audio | 15-25 min | 1800s |

**Best Practices:**
- Use background agents for long-running tasks
- Implement polling with exponential backoff
- Store task IDs for later status checks

---

### Step 1.6: Wait for Analysis Completion

**Purpose:** Monitor and wait for analysis generation

```bash
# Wait for specific artifact
notebooklm artifact wait <artifact_id> --notebook <notebook_id> --timeout 1800

# Check artifact status
notebooklm artifact list --notebook <notebook_id> --json
```

**What happens:**
1. Polls NotebookLM API every 15 seconds
2. Checks artifact status: `pending` → `in_progress` → `completed`
3. Returns success/failure with exit code
4. Timeout threshold: configurable (default: 600s)

**Expected Output:**
```json
{
  "artifacts": [
    {
      "id": "cdbe19de-6879-449c-9943-dfc16d8ee2ed",
      "title": "Professional Trading Excellence: Psychology, Infrastructure, and Risk Mastery",
      "type": "Report",
      "status": "completed"
    }
  ]
}
```

---

### Step 1.7: Download Results

**Purpose:** Export analysis results for local processing

```bash
# Download report (Markdown)
notebooklm download report ./smb_analysis_report.md \
    --artifact cdbe19de-6879-449c-9943-dfc16d8ee2ed \
    --notebook <notebook_id>

# Download quiz (JSON)
notebooklm download quiz quiz.json \
    --artifact <quiz_artifact_id> \
    --notebook <notebook_id>

# Download slide deck (PDF)
notebooklm download slide-deck ./slides.pdf \
    --artifact <slide_artifact_id> \
    --notebook <notebook_id>
```

**What happens:**
1. Fetches generated content from NotebookLM servers
2. Writes to specified file path
3. Format conversion: internal → requested format
4. File size: 5-50 KB per artifact

**Output Files:**
| Format | File Size | Use Case |
|--------|-----------|----------|
| Markdown | 5-20 KB | Documentation, integration |
| JSON | 1-10 KB | Programmatic processing |
| PDF | 20-50 KB | Distribution, printing |
| PPTX | 30-80 KB | Presentations |

**Critical Notes:**
- Markdown is preferred for KB integration
- JSON for strategy extraction scripts
- PDF/PPTX for stakeholder reporting

---

## PHASE 2: GPU-Aware Local Processing

**Goal:** Extract timestamps with Whisper + Visual content with OCR  
**Input:** YouTube videos (downloaded via yt-dlp)  
**Output:** Enriched timestamps with visual chart/diagram data  

### Step 2.1: Download Video

**Purpose:** Get original YouTube video file

```bash
# via smb_gpu_transcribe.py (automated)
python smb_gpu_transcribe.py --video-id=XXX --device=auto

# Or manually with yt-dlp
yt-dlp -f best "https://www.youtube.com/watch?v=XXX" \
    -o /home/ml/smb_processor/videos/XXX.mp4
```

**What happens:**
1. Connects to YouTube via yt-dlp
2. Downloads highest quality available (typically 1080p)
3. Saves to `/home/ml/smb_processor/videos/{video_id}.mp4`
4. Downloads ~50-200 MB depending on video length

**Error Handling:**
```json
{
  "status": "download_failed",
  "error": "Sign in to confirm you're not a bot"
}
```

**Bot Detection Workarounds:**
- Use older videos (<2024) without bot protection
- Fallback to NotebookLM for bot-protected content
- Use YouTube Data API for metadata extraction

---

### Step 2.2: Extract Audio

**Purpose:** Isolate audio track from video

```bash
# Enabled automatically in smb_gpu_transcribe.py
ffmpeg -i video.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav
```

**What happens:**
1. Extracts audio stream from video file
2. Converts to WAV format (PCM, 16-bit, 16kHz, mono)
3. Size: ~30 MB per 10-minute video
4. Saves to `/home/ml/smb_processor/audio/{video_id}.wav`

**Audio Specification:**
- Format: PCM WAV
- Sample Rate: 16,000 Hz (optimal for Whisper)
- Channels: 1 (mono)
- Bit depth: 16-bit
- Duration: Full video length

---

### Step 2.3: Transcribe Audio with Whisper

**Purpose:** Convert speech to text with timestamps

```bash
# GPU mode (auto-detect)
python smb_gpu_transcribe.py --video-id=XXX --device=auto
```

**What happens:**
1. Loads Whisper model (`openai/whisper-large-v3-turbo` or `base`)
2. Loads video audio into memory
3. Runs transcription on CUDA (GPU) or CPU
4. Output segments with timestamps

**GPU Processing (CUDA):**
```python
model.transcribe(
    audio_path,
    device="cuda",
    language="en",
    fp16=True,  # FP16 precision for speed
    temperature=0.0,
    # ... other params
)
```

**CPU Processing:**
```python
model.transcribe(
    audio_path,
    device="cpu",
    language="en",
    fp16=False,
    compute_type="int8",
    # ... other params
)
```

**Output Structure:**
```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 3.2,
      "text": "Welcome back to the channel"
    },
    {
      "start": 3.2,
      "end": 8.5,
      "text": "Today we're going to discuss..."
    }
  ],
  "language": "en",
  "duration": 623.5
}
```

**Performance Metrics:**
| Video Length | GPU Time | CPU Time | GPU Speedup |
|--------------|----------|----------|-------------|
| 5 min | 30-60s | 5-10min | 5-10x |
| 15 min | 90-180s | 15-30min | 5-10x |
| 30 min | 180-360s | 30-60min | 5-10x |

---

### Step 2.4: Extract Frames

**Purpose:** Capture video frames for visual analysis

```bash
# Enabled automatically in smb_gpu_transcribe.py
cv2.VideoCapture(video_path)
while True:
    ret, frame = cap.read()
    if frame_count % (fps * 5) == 0:  # Every 5 seconds
        cv2.imwrite(f"frame_{saved_count:06d}.jpg", frame)
        saved_count += 1
    frame_count += 1
```

**What happens:**
1. Opens video with OpenCV
2. Reads at video FPS (typically 30 fps)
3. Extracts frames every 5 seconds
4. Saves as JPEG to `/home/ml/smb_processor/frames/{video_id}/`

**Frame Extraction Summary:**
| Video Length | Frames Extracted | Storage Size |
|--------------|------------------|--------------|
| 5 min | ~60 frames | ~12 MB |
| 15 min | ~180 frames | ~36 MB |
| 30 min | ~360 frames | ~72 MB |

---

### Step 2.5: Classify Frames by Aspect Ratio

**Purpose:** Determine frame type for specialized processing

```python
aspect_ratio = width / height
if aspect_ratio > 1.5:
    visual_type = "landscape"  # Chart/diagram (85% confidence)
elif aspect_ratio < 1.0:
    visual_type = "portrait"   # Host explanation (75% confidence)
else:
    visual_type = "square"     # Preview/infographic (70% confidence)
```

**What happens:**
1. Reads frame dimensions (width, height)
2. Calculates aspect ratio
3. Classifies frame type based on threshold (>1.5 = landscape)
4. Assigns descriptive label and confidence score

**Frame Classification Grid:**
| Type | Aspect Ratio | Description | Confidence | OCR Purpose |
|------|--------------|-------------|------------|-------------|
| Landscape | > 1.5 | Chart/diagram/price action | 0.85 | Extract numbers, labels, indicators |
| Portrait | < 1.0 | Host explanation | 0.75 | Extract name, on-screen text |
| Square | 1.0-1.5 | Preview/infographic | 0.70 | Extract summary text |

---

### Step 2.6: Run PaddleOCR on Frames

**Purpose:** Extract text from visual content with bounding boxes

```bash
# GPU mode
python smb_gpu_transcribe.py --video-id=XXX --device=cuda

# CPU mode
python smb_gpu_transcribe.py --video-id=XXX --device=cpu --no-visual-ocr
```

**What happens:**
1. Loads PaddleOCR with CUDA (if available)
2. Processes each frame through OCR engine
3. Extracts text lines with bounding polygons
4. Calculates confidence scores per text segment

**OCR Processing Pipeline:**
```
Frame → Preprocessing → Text Detection → Text Recognition → Output JSON
```

**PaddleOCR Configuration:**
```python
ocr = PaddleOCR(
    use_angle_cls=True,      # Detect rotated text
    use_gpu=use_gpu,         # CUDA if available
    max_text_length=50,      # Max text per line
    lang="en",               # English
    cls_model_dir=None,      # Use default
    det_model_dir=None,      # Use default
    rec_model_dir=None       # Use default
)
```

**OCR Output Schema:**
```json
{
  "frame_000000": {
    "timestamp": 0.0,
    "visual_type": "landscape",
    "description": "Chart/diagram/price action",
    "confidence": 0.85,
    "ocr_results": [
      {
        "text": "SPY $450.25",
        "confidence": 0.92,
        "bounding_box": [
          [100, 50], [200, 50], [200, 70], [100, 70]
        ]
      }
    ],
    "ocr_confidence_avg": 0.92,
    "detected_chart_type": "price_chart",
    "text_count": 2
  }
}
```

**Performance:**
| Frames | GPU Time | CPU Time | GPU Speedup |
|--------|----------|----------|-------------|
| 60 | 2-3s | 10-15s | 5x |
| 120 | 4-6s | 20-30s | 5x |
| 360 | 12-18s | 60-90s | 5x |

---

### Step 2.7: Detect Chart Types

**Purpose:** Identify specific chart/indicator types from OCR text

```python
# Keyword matching for chart type detection
chart_type_keywords = {
    "price_chart": ["SPY", "QQQ", "stock", "equity", "price"],
    "technical_chart": ["RSI", "MACD", "support", "resistance", "trend"],
    "bar_chart": ["volume", "bar", "histogram"],
    "line_chart": ["line", "trend", "movement"]
}
```

**What happens:**
1. Scans OCR text for chart-specific keywords
2. Matches against predefined keyword sets
3. Assigns chart type with confidence
4. Aggregates frame statistics

**Chart Detection Logic:**
```
Frame OCR Text → Keyword Matching → Chart Type Assignment → Summary
```

**Detected Chart Types:**
| Type | Keywords | Use Case |
|------|----------|----------|
| price_chart | SPY, QQQ, price, stock | Price level identification |
| technical_chart | RSI, MACD, support, resistance | Technical indicator extraction |
| bar_chart | Volume, histogram | Volume analysis |
| line_chart | Trend, line, movement | Pattern visualization |

**Chart Summary Aggregation:**
```json
{
  "chart_summary": {
    "price_chart": {
      "count": 45,
      "timestamps": [0.0, 10.0, 20.0, 30.0],
      "ocr_samples": [
        {"text": "SPY $450.25", "confidence": 0.92},
        {"text": "QQQ $380.50", "confidence": 0.91}
      ]
    },
    "technical_chart": {
      "count": 30,
      "timestamps": [5.0, 15.0, 25.0],
      "ocr_samples": [
        {"text": "Support at $448.50", "confidence": 0.94},
        {"text": "RSI: 62", "confidence": 0.96}
      ]
    }
  }
}
```

---

### Step 2.8: Build Visual Insights

**Purpose:** Create consolidated visual context for video

```bash
# In smb_gpu_transcribe.py
visual_insights = extract_visual_insights(ocr_results, whisper_segments)
```

**What happens:**
1. Aligns OCR timestamps with transcription segments
2. Builds time-aligned visual event list
3. Creates chart type summary with statistics
4. Generates visual insights object

**Visual Insights Structure:**
```json
{
  "visual_insights": {
    "visual_timestamps": [
      {
        "timestamp": 0.0,
        "frame_id": "frame_000000",
        "visual_type": "landscape",
        "description": "Chart/diagram/price action",
        "detected_chart_type": "price_chart",
        "text_samples": [
          {"text": "SPY $450.25", "confidence": 0.92}
        ],
        "confidence": 0.85
      }
    ],
    "chart_summary": {
      "price_chart": {
        "count": 45,
        "timestamps": [...],
        "ocr_samples": [...]
      }
    },
    "total_visual_entries": 75
  }
}
```

---

### Step 2.9: Save Final Results

**Purpose:** Persist processed video data

```bash
# Automatically done in smb_gpu_transcribe.py
with open(f"{video_id}_final_result.json", "w") as f:
    json.dump(result, f, indent=2)
```

**Output Files:**
| File | Size | Content |
|------|------|---------|
| `{video_id}_result.json` | 50-80 KB | Intermediate processing results |
| `{video_id}_final_result.json` | 80-100 KB | Final video data |

**Final Result Schema:**
```json
{
  "video_id": "XXX",
  "device": "cuda",
  "whisper_model": "openai/whisper-large-v3-turbo",
  "timestamp": "2026-05-03T06:00:00",
  "transcription": {
    "segments": [...],
    "duration": 623.5
  },
  "visual": {
    "frame_count": 125,
    "frame_info": [...],
    "frame_types": {...}
  },
  "ocr": {
    "result_count": 125,
    "results": {...},
    "schema_version": "3.0",
    "visual_insights": {...}
  }
}
```

---

## PHASE 3: Knowledge Aggregation

**Goal:** Merge all sources into unified knowledge base  
**Input:** NotebookLM analysis + Whisper transcriptions + Visual OCR  
**Output:** Enriched knowledge base (JSON)  

### Step 3.1: Load Source Data

**Purpose:** Prepare data for merging

```bash
# NotebookLM analysis
python smb_merge_kb_v3.py  # Loads聂bnotebooklm_pilot/smb_pilot_report.md

# Whisper KB (existing)
SMB_DIR / "smb_knowledge_base_final.json"
```

**What happens:**
1. Parses NotebookLM markdown report
2. Extracts strategy patterns via keyword matching
3. Loads existing Whisper KB (v2.0 format)
4. Validates data structures

**NotebookLM Parsing:**
```python
def parse_notebooklm_report(filepath: Path) -> dict:
    content = filepath.read_text()
    content_lower = content.lower()
    
    patterns = {
        "technical_analysis": ["support", "resistance", "breakout"],
        "momentum": ["rsi", "macd", "stochastic"],
        "volume": ["volume", "liquidity"],
        "risk": ["stop loss", "position size"],
        "price_action": ["candlestick", "reversal"]
    }
    
    # Extract matching patterns
    return {"analysis": {...}}
```

**Expected NotebookLM Output:**
```json
{
  "analysis": {
    "strategy_patterns": [
      {"type": "momentum", "confidence": 0.89, "matches": 3},
      {"type": "volume", "confidence": 0.85, "matches": 2},
      {"type": "risk", "confidence": 0.92, "matches": 4}
    ]
  }
}
```

---

### Step 3.2: Enrich Videos with NotebookLM

**Purpose:** Add strategy insights to video entries

```bash
# In smb_merge_kb_v3.py
for video in whisper_kb["videos"]:
    video["notebooklm_analysis"] = notebooklm_data["analysis"]
    video["extracted_strategies"] = extract_strategy_from_video(video)
    video["visual_ocr_insights"] = extract_visual_ocr_insights(video)
```

**What happens:**
1. Iterates through each video in Whisper KB
2. Adds NotebookLM strategy patterns
3. Extracts strategies from Whisper transcript
4. Adds visual OCR insights (if available)

**Strategy Extraction:**
```python
def extract_strategy_from_video(video_data: dict) -> dict:
    transcript = " ".join(seg["text"] for seg in video_data["transcription"]["segments"])
    transcript_lower = transcript.lower()
    
    strategies = {
        "day_trading": ["day trade", "intraday", "market hours"],
        "swing_trading": ["swing", "days", "weeks"],
        "technical_analysis": ["chart", "pattern", "indicator"]
    }
    
    # Detect matching strategies
    detected = [name for name, keywords in strategies.items()
                if any(kw in transcript_lower for kw in keywords)]
    
    return {
        "primary_strategy": detected[0] if detected else None,
        "secondary_strategies": detected[1:]
    }
```

**Enriched Video Entry:**
```json
{
  "video_id": "XXX",
  "notebooklm_analysis": {
    "strategy_patterns": [
      {"type": "momentum", "confidence": 0.89, "matches": 3}
    ]
  },
  "extracted_strategies": {
    "primary_strategy": "day_trading",
    "secondary_strategies": ["technical_analysis"],
    "timeframe": "15m",
    "assets": ["SPY", "QQQ"]
  },
  "visual_ocr_insights": {...}
}
```

---

### Step 3.3: Extract Visual OCR Insights

**Purpose:** Parse and summarize visual OCR data

```bash
# In smb_merge_kb_v3.py
video["visual_ocr_insights"] = extract_visual_ocr_insights(video)
```

**What happens:**
1. Checks if video has OCR results
2. Extracts chart summary from OCR data
3. Aggregates visual timestamps
4. Returns enriched visual insights object

**Visual OCR Insights Extraction:**
```python
def extract_visual_ocr_insights(video_data: dict) -> dict:
    ocr_data = video_data.get("ocr", {})
    visual_insights = ocr_data.get("visual_insights", {})
    
    chart_summary = visual_insights.get("chart_summary", {})
    
    return {
        "visual_ocr_enabled": len(ocr_data.get("results", {})) > 0,
        "total_visual_entries": visual_insights.get("total_visual_entries", 0),
        "chart_summary": chart_summary,
        "visual_timestamps": visual_insights.get("visual_timestamps", [])[:10]
    }
```

**Visual OCR Index Aggregation:**
```json
{
  "visual_ocr_index": {
    "enabled": true,
    "total_entries": 3750,
    "chart_types": ["price_chart", "technical_chart", "bar_chart"],
    "summary": {
      "price_chart": 89,
      "technical_chart": 45,
      "bar_chart": 12
    }
  }
}
```

---

### Step 3.4: Build Final Knowledge Base

**Purpose:** Create unified KB with all enrichment layers

```bash
# Generates smb_final_kb_v3.json
python smb_merge_kb_v3.py --output ./smb_final_kb_v3.json
```

**Final KB Structure:**
```json
{
  "version": "3.0",
  "created_at": "2026-05-03T06:22:02.726584",
  "sources": {
    "notebooklm": "/home/ml/smb_notebooklm_pilot/smb_pilot_report.md",
    "whisper": "/home/ml/smb_knowledge_base_final.json",
    "visual_ocr": "enabled"
  },
  "metadata": {
    "total_videos": 500,
    "notebooklm_pattern_count": 3,
    "visual_ocr_enriched": 125,
    "total_visual_entries": 1875,
    "chart_types_detected": ["price_chart", "technical_chart"]
  },
  "strategy_index": {
    "patterns_detected": [...],
    "total_strategy_types": 3
  },
  "visual_ocr_index": {...},
  "videos": [
    {
      "video_id": "XXX",
      "notebooklm_analysis": {...},
      "extracted_strategies": {...},
      "visual_ocr_insights": {...}
    }
  ]
}
```

---

### Step 3.5: Save and Verify

**Purpose:** Persist KB and validate structure

```bash
# Saving
with open(output_file, "w") as f:
    json.dump(final_kb, f, indent=2)

# Verification
python3 -c "import json; kb = json.load(open('smb_final_kb_v3.json')); print('Videos:', kb['metadata']['total_videos']); print('Visual OCR:', kb['metadata']['visual_ocr_enriched'])"
```

**Expected Output:**
```
Videos: 500
Visual OCR: 125
Total entries: 1875
Chart types: ['price_chart', 'technical_chart', 'bar_chart']
```

---

## Integration Workflow Summary

### Full Pipeline Script
```bash
#!/bin/bash
# run_smb_pipeline_v3.sh

# 1. Process video with GPU + visual OCR
python /home/ml/smb_gpu_transcribe.py --video-id=XXX --device=auto

# 2. Merge with NotebookLM analysis
python /home/ml/smb_merge_kb_v3.py

# 3. Output files
# - /home/ml/smb_processor/output/XXX_final_result.json
# - /home/ml/smb_notebooklm_pilot/smb_final_kb_v3.json
```

### Batch Processing
```bash
# Process all videos
while read -r vid; do
    python /home/ml/smb_gpu_transcribe.py --video-id=$vid --device=cuda
done < /home/ml/smb_all_video_ids.txt

# Merge all
python /home/ml/smb_merge_kb_v3.py
```

### Estimated Time Requirements

| Task | Per Video | Batch (50) | Full Channel (2478) |
|------|-----------|------------|---------------------|
| NotebookLM (add/add analyze) | 5 min | 4 hr | 173 hr |
| GPU Transcription | 3-5 min | 2.5-4 hr | 124-206 hr |
| KB Merge | 1-2 min | 1-2 min | 1-2 min |
| **Total (estimated)** | 8-10 min | 6.5-7 hr | ~300 hr (12-13 days) |

**With Parallelization:**
- 10 concurrent videos: ~30-40 hours total

---

## File Locations Summary

```
/home/ml/
├── smb_gpu_transcribe.py              # GPU transcription + OCR
├── smb_merge_kb_v3.py                 # KB merging
├── smb_visual_ocr_schema.py           # Schema validator
├── run_smb_pipeline_v3.sh             # Pipeline runner
├── smb_notebooklm_pilot/
│   ├── smb_pilot_report.md           # NotebookLM analysis
│   ├── smb_final_kb_v2.json          # v2 (no OCR)
│   ├── smb_final_kb_v3.json          # v3 (with OCR)
│   └── README.md                     # Pilot summary
├── smb_processor/
│   ├── output/
│   │   └── {video_id}_final_result.json
│   ├── frames/
│   │   └── {video_id}/frame_*.jpg
│   └── audio/
│       └── {video_id}.wav
└── smb_knowledge_base_final.json     # Original Whisper KB
```

---

## Troubleshooting Guide

### NotebookLM Issues
| Error | Cause | Solution |
|-------|-------|----------|
| Auth expired | Cookie timeout | Run `notebooklm login` |
| Source delayed | Processing queue | Wait or increase timeout |
| Rate limiting | Too many requests | Wait 5-10 min |
| Partial ID rejected | Short notebook ID | Use full UUID from `notebooklm list --json` |

### GPU Processing Issues
| Error | Cause | Solution |
|-------|-------|----------|
| CUDA OOM | Insufficient VRAM | Reduce batch size, use CPU |
| Model download | Missing HF_TOKEN | Set environment variable |
| Frame extraction failed | Corrupted video | Skips video, continues |

### OCR Issues
| Error | Cause | Solution |
|-------|-------|----------|
| PaddleOCR not installed | Package missing | `pip install paddleocr` |
| No text detected | Low quality frame | Increase frame rate, preprocessing |

---

## Next Steps

1. **Process all 2,478 videos** with NotebookLM + GPU pipeline
2. **Verify KB quality** with sample queries
3. **Deploy query API** for trading strategy access
4. **Add embeddings** for visual chart retrieval
5. **Implement time-series analysis** for strategy evolution

---

**Status:** ✅ Implementation complete  
**Version:** 3.0  
**Last Updated:** 2026-05-03
