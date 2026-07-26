#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import asyncio
import json
from smb_knowledge_base.orchestration.activities import extract_knowledge, validate_strategy, store_strategy, validate_fragment, store_fragment
from neo4j import GraphDatabase

BASE = Path(__file__).parent.parent

# Load .env variables
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except ImportError:
    env_path = BASE / ".env"
    if env_path.exists():
        with open(env_path) as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()
TRANSCRIPTS_DIR = BASE / "data" / "sample_transcripts"
PROCESSED_DIR = BASE / "data" / "processed"
REVIEW_DIR = BASE / "data" / "review"

for d in [PROCESSED_DIR, REVIEW_DIR]:
    d.mkdir(parents=True, exist_ok=True)

async def process_file(filepath: Path):
    video_id = filepath.stem
    print(f"\n=== Processing {video_id} ===")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            transcript = f.read()
        knowledge = await extract_knowledge({"video_id": video_id, "transcript": transcript, "title": video_id})
        full_strategies = knowledge.get("full_strategies", [])
        fragments = knowledge.get("fragments", [])
        print(f"Extracted {len(full_strategies)} full strategies, {len(fragments)} fragments")
        full_stored = 0
        frag_stored = 0
        for idx, strat in enumerate(full_strategies):
            if not strat:
                continue
            strat["video_id"] = video_id
            val = await validate_strategy(strat)
            if val.get("quality_gates_passed"):
                try:
                    await store_strategy(val)
                    full_stored += 1
                    print(f"  Stored full strategy {idx}")
                except Exception as e:
                    print(f"  Error storing full strategy {idx}: {e}")
            else:
                review_file = REVIEW_DIR / f"{video_id}_full_{idx}_invalid.txt"
                with open(review_file, 'w', encoding='utf-8') as rf:
                    rf.write(f"Validation errors: {val.get('validation_errors')}\n\nStrategy: {json.dumps(strat, indent=2)}")
                print(f"  Invalid full strategy {idx} written to {review_file}")
        for idx, frag in enumerate(fragments):
            if not frag:
                continue
            frag["source_video_id"] = video_id
            val = await validate_fragment(frag)
            if val.get("quality_gates_passed"):
                try:
                    await store_fragment(val, video_id)
                    frag_stored += 1
                    print(f"  Stored fragment {idx}")
                except Exception as e:
                    print(f"  Error storing fragment {idx}: {e}")
            else:
                review_file = REVIEW_DIR / f"{video_id}_frag_{idx}_invalid.txt"
                with open(review_file, 'w', encoding='utf-8') as rf:
                    rf.write(f"Validation errors: {val.get('validation_errors')}\n\nFragment: {json.dumps(frag, indent=2)}")
                print(f"  Invalid fragment {idx} written to {review_file}")
        return full_stored, frag_stored
    except Exception as e:
        print(f"Error processing {video_id}: {e}")
        review_file = REVIEW_DIR / f"{video_id}_error.txt"
        with open(review_file, 'w', encoding='utf-8') as rf:
            rf.write(f"Error: {e}\n")
        raise

async def main():
    files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    total_full_stored = 0
    total_frag_stored = 0
    for fp in files:
        fs, ff = await process_file(fp)
        total_full_stored += fs
        total_frag_stored += ff
    print("\n--- Summary ---")
    print(f"Files processed: {len(files)}")
    print(f"Full strategies stored: {total_full_stored}")
    print(f"Fragments stored: {total_frag_stored}")

    # Neo4j verification
    try:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            strat_count = session.run("MATCH (s:Strategy) RETURN count(s) as cnt").single()["cnt"]
            strat_emb = session.run("MATCH (s:Strategy) WHERE exists(s.embedding) RETURN count(s) as cnt").single()["cnt"]
            frag_count = session.run("MATCH (f:Fragment) RETURN count(f) as cnt").single()["cnt"]
            frag_emb = session.run("MATCH (f:Fragment) WHERE exists(f.embedding) RETURN count(f) as cnt").single()["cnt"]
            print(f"Neo4j: Strategies total = {strat_count}, with embedding = {strat_emb}")
            print(f"Neo4j: Fragments total = {frag_count}, with embedding = {frag_emb}")
        driver.close()
    except Exception as e:
        print(f"Neo4j verification failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())