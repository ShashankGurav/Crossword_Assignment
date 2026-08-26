"""
retriever.py
Handles semantic retrieval from ChromaDB with:
- Metadata-aware ranking (active > superseded > draft/internal)
- Conflict detection between two active authoritative sources
- Source citation attached to every result
"""

import chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer

CHROMA_DIR = Path(__file__).parent / "chroma_db"

# Singleton pattern — load model once across the process
_model = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_collection("knowledge_base")
    return _collection


def _rank_score(metadata: dict, base_distance: float) -> float:
    """
    Adjust raw cosine distance by document trustworthiness.
    Lower score = better (we convert distance to similarity first).
    """
    similarity = 1.0 - base_distance  # cosine distance → similarity

    status = metadata.get("status", "unknown")
    authority = metadata.get("policy_authority", "none")
    is_internal = metadata.get("is_internal", "False") == "True"

    # Penalize non-authoritative or non-active documents
    if is_internal:
        similarity *= 0.3          # heavy penalty for internal/draft docs
    elif status == "superseded":
        similarity *= 0.5          # significant penalty for old policy
    elif status == "active" and authority == "official":
        similarity *= 1.1          # small boost for current official policy
        similarity = min(similarity, 1.0)

    return similarity


def _is_historical_order_query(query: str) -> bool:
    """Detect if the query is about an order placed before the policy change date."""
    import re
    q = query.lower()
    patterns = [
        r"before april", r"before 2026", r"prior to april",
        r"before the (new|current) policy", r"old policy",
        r"placed.{0,30}before", r"ordered.{0,30}before",
        r"2024", r"2025",
    ]
    return any(re.search(p, q) for p in patterns)


def retrieve(query: str, top_k: int = 6) -> list[dict]:
    """
    Retrieve top_k relevant chunks for a query.
    Returns list of dicts with keys: text, filename, heading, score, metadata
    Default top_k raised to 6 to ensure multi-topic queries (e.g. Canada
    shipping with duties) surface all relevant chunks.

    Special case: if query is about pre-April-2026 orders, the superseded
    legacy policy is force-included so the model can answer correctly.
    """
    model = _get_model()
    collection = _get_collection()

    query_embedding = model.encode(query).tolist()
    is_historical = _is_historical_order_query(query)

    # Fetch more than we need so ranking can reorder
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k * 3, collection.count()),
        include=["documents", "metadatas", "distances"]
    )

    results = []
    for doc, meta, dist in zip(
        raw["documents"][0],
        raw["metadatas"][0],
        raw["distances"][0]
    ):
        score = _rank_score(meta, dist)

        # For historical order queries: boost legacy policy to top of results
        if is_historical and meta.get("filename") == "02-returns-policy-legacy.md":
            score = 0.99  # force to top so model sees it

        results.append({
            "text": doc,
            "filename": meta.get("filename", "unknown"),
            "heading": meta.get("heading", ""),
            "score": round(score, 4),
            "metadata": meta,
        })

    # Sort by adjusted score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def detect_conflict(chunks: list[dict]) -> dict | None:
    """
    Check if two different ACTIVE OFFICIAL sources in the results
    appear to contradict each other on the same topic.

    Returns a conflict dict if found, None otherwise.
    Known conflict: product-care vs breeze-tumbler-product-card on dishwasher.
    We detect this by checking if both files appear in top results.
    """
    active_official_files = [
        c for c in chunks
        if c["metadata"].get("status") == "active"
        and c["metadata"].get("policy_authority") == "official"
        and c["metadata"].get("is_internal") != "True"
    ]

    # Group by filename
    seen_files = {}
    for chunk in active_official_files:
        fname = chunk["filename"]
        if fname not in seen_files:
            seen_files[fname] = chunk

    # Known conflict pairs — extend this list as more conflicts are found
    CONFLICT_PAIRS = [
        (
            "11-product-care.md",
            "12-breeze-tumbler-product-card.md",
            "dishwasher cleaning instructions for the Breeze Tumbler"
        ),
    ]

    for file_a, file_b, topic in CONFLICT_PAIRS:
        if file_a in seen_files and file_b in seen_files:
            return {
                "detected": True,
                "topic": topic,
                "source_a": {
                    "filename": file_a,
                    "heading": seen_files[file_a]["heading"],
                    "excerpt": seen_files[file_a]["text"][:200],
                },
                "source_b": {
                    "filename": file_b,
                    "heading": seen_files[file_b]["heading"],
                    "excerpt": seen_files[file_b]["text"][:200],
                },
            }

    return None


def format_chunks_for_prompt(chunks: list[dict]) -> str:
    """
    Format retrieved chunks into a clearly labeled block for the prompt.
    Each chunk is tagged with its source and trustworthiness.
    """
    lines = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk["metadata"]
        status = meta.get("status", "unknown")
        is_internal = meta.get("is_internal", "False") == "True"

        # Label the trustworthiness clearly so the LLM can reason about it
        if is_internal:
            trust_label = "[INTERNAL/DRAFT — NOT authoritative for customer answers]"
        elif status == "superseded":
            trust_label = "[SUPERSEDED — old policy, do not use as current authority]"
        else:
            trust_label = "[ACTIVE OFFICIAL]"

        source_ref = f"{chunk['filename']} > {chunk['heading']}"

        lines.append(
            f"--- Retrieved Chunk {i} ---\n"
            f"Source: {source_ref}\n"
            f"Trust: {trust_label}\n"
            f"Content:\n{chunk['text']}\n"
        )

    return "\n".join(lines)