"""
logger.py
Structured debug logging for the agent pipeline.
Logs: user message, history, retrieved chunks+scores, tool calls,
      sanitized tool results, final response, errors.

NEVER logs: email, address, risk_score, warehouse_note, support_tags.
"""

import json
import os
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"

# Fields that must never appear in logs
FORBIDDEN_LOG_FIELDS = {
    "email", "shipping_address", "risk_score",
    "warehouse_note", "support_tags", "address"
}


def _sanitize_for_log(obj):
    """Recursively strip forbidden fields from any dict/list before logging."""
    if isinstance(obj, dict):
        return {
            k: _sanitize_for_log(v)
            for k, v in obj.items()
            if k not in FORBIDDEN_LOG_FIELDS
        }
    if isinstance(obj, list):
        return [_sanitize_for_log(i) for i in obj]
    return obj


def _is_debug() -> bool:
    return os.getenv("DEBUG", "false").lower() == "true"


def _write(entry: dict):
    """Write a log entry to logs/agent.log and optionally print to console."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "agent.log"

    line = json.dumps(entry, ensure_ascii=False, default=str)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    if _is_debug():
        print(f"\n[DEBUG] {entry.get('event', 'log')}")
        print(json.dumps(_sanitize_for_log(entry), indent=2, default=str))


def log_turn_start(session_id: str, user_message: str, history: list):
    _write({
        "event": "turn_start",
        "timestamp": datetime.utcnow().isoformat(),
        "session_id": session_id,
        "user_message": user_message,
        "history_turns": len(history),
    })


def log_retrieval(chunks: list, conflict: dict | None):
    safe_chunks = [
        {
            "filename": c["filename"],
            "heading": c["heading"],
            "score": c["score"],
            "status": c["metadata"].get("status"),
            "is_internal": c["metadata"].get("is_internal"),
            "text_preview": c["text"][:120],
        }
        for c in chunks
    ]
    _write({
        "event": "retrieval",
        "timestamp": datetime.utcnow().isoformat(),
        "chunks_retrieved": len(chunks),
        "chunks": safe_chunks,
        "conflict_detected": conflict is not None,
        "conflict": conflict,
    })


def log_tool_call(tool_name: str, arguments: dict, result: dict):
    """Log tool call — sanitize result before writing."""
    safe_result = _sanitize_for_log(result)
    _write({
        "event": "tool_call",
        "timestamp": datetime.utcnow().isoformat(),
        "tool": tool_name,
        "arguments": arguments,
        "result": safe_result,
    })


def log_response(response: str, needs_handoff: bool, flagged: bool = False):
    _write({
        "event": "response",
        "timestamp": datetime.utcnow().isoformat(),
        "response_length": len(response),
        "needs_handoff": needs_handoff,
        "validator_flagged": flagged,
        "response_preview": response[:300],
    })


def log_error(error: str, context: str = ""):
    _write({
        "event": "error",
        "timestamp": datetime.utcnow().isoformat(),
        "error": error,
        "context": context,
    })
