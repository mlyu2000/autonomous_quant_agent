#!/usr/bin/env python3
import os
from neo4j import GraphDatabase
from openai import OpenAI

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text-v2-moe")
EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "http://localhost:9001/v1")
EMBED_DIM = os.getenv("EMBED_DIM")

def get_embedding_dimension():
    client = OpenAI(api_key="dummy", base_url=EMBEDDING_API_BASE)
    try:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input="test")
        return len(resp.data[0].embedding)
    except Exception as e:
        print(f"Could not auto-detect dimension: {e}")
        return None

# Connect to Neo4j
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
dim = EMBED_DIM
if not dim:
    dim = get_embedding_dimension()
    if not dim:
        dim = "384"
        print("Falling back to dimension 384")
print(f"Using embedding dimension: {dim}")

# Create vector indexes
INDEX_QUERIES = [
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
    f"""
    CREATE VECTOR INDEX fragment_embedding IF NOT EXISTS
    FOR (f:Fragment) ON (f.embedding)
    OPTIONS {{
      indexConfig: {{
        `vector.dimensions`: {dim},
        `vector.similarity_function`: 'cosine'
      }}
    }}
    """
]

with driver.session() as session:
    for q in INDEX_QUERIES:
        session.run(q)
        if "strategy_embedding" in q:
            print("Vector index strategy_embedding created or already exists.")
        elif "fragment_embedding" in q:
            print("Vector index fragment_embedding created or already exists.")

driver.close()
