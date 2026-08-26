"""
ingest.py
Reads all 14 knowledge-base markdown files, parses frontmatter metadata,
splits into chunks, embeds with sentence-transformers, and stores in ChromaDB.

Run once before starting the agent:
    python ingest.py
"""

import os
import re
import yaml
import chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer

# Paths - works whether run from aster-row-agent/ or repo root
KB_DIR = Path(__file__).parent.parent / "knowledge-base"
CHROMA_DIR = Path(__file__).parent / "chroma_db"

# Fields that must never leave the system (defense in depth)
INTERNAL_AUDIENCES = {"internal"}
INTERNAL_STATUSES = {"draft"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2].strip()
            except yaml.YAMLError:
                pass
    return {}, text.strip()


def split_into_chunks(body: str, filename: str, meta: dict) -> list[dict]:
    """
    Split document body by headings (## or ###).
    Each chunk carries the heading as context so retrieval stays precise.
    """
    chunks = []
    # Split on markdown headings
    sections = re.split(r'\n(#{1,3} .+)\n', body)

    current_heading = meta.get("title", filename)
    buffer = []

    for part in sections:
        if re.match(r'^#{1,3} ', part):
            # Save previous buffer as a chunk
            if buffer:
                chunks.append({
                    "heading": current_heading,
                    "text": "\n".join(buffer).strip()
                })
                buffer = []
            current_heading = part.strip("# ").strip()
        else:
            if part.strip():
                buffer.append(part.strip())

    # Don't forget the last section
    if buffer:
        chunks.append({
            "heading": current_heading,
            "text": "\n".join(buffer).strip()
        })

    return [c for c in chunks if len(c["text"]) > 30]


def build_chunk_id(filename: str, heading: str, idx: int) -> str:
    """Stable unique ID for each chunk."""
    safe = re.sub(r'[^a-z0-9]', '-', f"{filename}-{heading}-{idx}".lower())
    return re.sub(r'-+', '-', safe)[:64]


def ingest():
    print("=== Aster & Row Knowledge Base Ingestion ===\n")

    if not KB_DIR.exists():
        raise FileNotFoundError(f"knowledge-base directory not found at {KB_DIR}")

    # Load embedding model (downloads once, then cached locally)
    print("Loading embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    # Set up ChromaDB
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Fresh collection each ingest so stale chunks don't linger
    try:
        client.delete_collection("knowledge_base")
    except Exception:
        pass
    collection = client.create_collection(
        name="knowledge_base",
        metadata={"hnsw:space": "cosine"}
    )

    md_files = sorted(KB_DIR.glob("*.md"))
    total_chunks = 0

    for md_file in md_files:
        raw = md_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)

        filename = md_file.name
        status = meta.get("status", "unknown")
        audience = meta.get("audience", "customer")
        policy_authority = meta.get("policy_authority", "none")
        doc_id = meta.get("document_id", filename)
        title = meta.get("title", filename)

        # Flag internal/draft docs — we still index them so the agent
        # can recognize injection attempts, but we tag them clearly
        is_internal = (audience in INTERNAL_AUDIENCES or
                       status in INTERNAL_STATUSES or
                       meta.get("customer_answering") is False)

        chunks = split_into_chunks(body, filename, meta)

        for idx, chunk in enumerate(chunks):
            chunk_id = build_chunk_id(filename, chunk["heading"], idx)
            text = chunk["text"]
            heading = chunk["heading"]

            # Embed the text
            embedding = model.encode(text).tolist()

            # Store with rich metadata for filtering and ranking
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[{
                    "filename": filename,
                    "document_id": doc_id,
                    "title": title,
                    "heading": heading,
                    "status": status,
                    "audience": audience,
                    "policy_authority": policy_authority,
                    "is_internal": str(is_internal),
                    "supersedes": str(meta.get("supersedes", "")),
                    "superseded_by": str(meta.get("superseded_by", "")),
                }]
            )
            total_chunks += 1

        flag = " [INTERNAL/DRAFT]" if is_internal else ""
        print(f"  ✓ {filename} ({status}) — {len(chunks)} chunks{flag}")

    print(f"\nIngestion complete: {len(md_files)} files → {total_chunks} chunks stored in ChromaDB")
    print(f"Index location: {CHROMA_DIR}\n")


if __name__ == "__main__":
    ingest()
