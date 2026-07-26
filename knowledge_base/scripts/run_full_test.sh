#!/usr/bin/env bash
set -e

# Load .env (expects .env in current working directory)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Ensure Python dependencies
pip install -q neo4j openai

# Start Neo4j if not already running
if ! docker ps --format '{{.Names}}' | grep -q '^smb-neo4j$'; then
  echo "Starting Neo4j container..."
  docker compose -f docker-compose.neo4j.yml up -d
  echo "Waiting 25 seconds for Neo4j to initialize..."
  sleep 25
else
  echo "Neo4j container already running."
fi

# Create vector index
echo "Creating vector index..."
python scripts/create_vector_index.py

# Run end-to-end test
echo "Running end-to-end test..."
python scripts/run_end_to_end_test.py
