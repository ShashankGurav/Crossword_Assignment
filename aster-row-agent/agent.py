# """
# agent.py
# Main agent loop for Aster & Row support agent.

# Flow per turn:
# 1. Detect if order lookup is needed
# 2. Retrieve relevant knowledge-base chunks
# 3. Detect source conflicts
# 4. Call order tool if needed
# 5. Build prompt with context
# 6. Call Groq LLM
# 7. Validate response
# 8. Return structured result
# """

# import os
# import re
# import json
# import uuid
# from dotenv import load_dotenv
# from groq import Groq

# from retriever import retrieve, detect_conflict, format_chunks_for_prompt
# from order_tool import lookup_order
# from prompt import build_messages
# from validator import validate, is_order_question
# from logger import (
#     log_turn_start, log_retrieval, log_tool_call,
#     log_response, log_error
# )

# load_dotenv()

# # In-memory session store: session_id → list of {role, content}
# _sessions: dict[str, list] = {}
# MAX_HISTORY = 6  # last 6 turns kept per session

# GROQ_MODEL = "openai/gpt-oss-120b"


# def _get_client() -> Groq:
#     key = os.getenv("GROQ_API_KEY")
#     if not key:
#         raise EnvironmentError("GROQ_API_KEY not set. Copy .env.example to .env and add your key.")
#     return Groq(api_key=key)


# def _extract_order_id(text: str) -> str | None:
#     """Extract ORD-XXXX pattern from user message or recent history."""
#     match = re.search(r'\bORD[-\s]?\d+\b', text, re.IGNORECASE)
#     if match:
#         return match.group(0).replace(" ", "-").upper()
#     return None


# def _extract_any_order_like(text: str) -> str | None:
#     """
#     Extract anything that looks like an order ID the user provided,
#     even if malformed (e.g. ABC-HELLO). Used to trigger the tool
#     so it can return a safe error rather than ignoring the request.
#     """
#     # Standard ORD- pattern first
#     match = re.search(r'\bORD[-\s]?\d+\b', text, re.IGNORECASE)
#     if match:
#         return match.group(0).replace(" ", "-").upper()
#     # Any word that follows "order" keyword and looks like an ID
#     match = re.search(r'(?:order|check|look up|lookup)\s+(?:number\s+)?([A-Z0-9][A-Z0-9\-]{2,})', text, re.IGNORECASE)
#     if match:
#         return match.group(1).upper()
#     return None


# # def _needs_order_lookup(message: str, history: list) -> tuple[bool, str | None]:
# #     """
# #     Decide whether to call the order tool.
# #     Returns (should_lookup, order_id_or_None)
# #     """
# #     # Check current message first — including malformed IDs
# #     oid = _extract_any_order_like(message)
# #     if oid:
# #         return True, oid

# #     # Check recent history for a previously mentioned order ID
# #     if is_order_question(message):
# #         for turn in reversed(history[-4:]):  # last 2 exchanges
# #             oid = _extract_order_id(turn.get("content", ""))
# #             if oid:
# #                 return True, oid
# #         # Order question but no ID found — signal to ask
# #         return True, None

# #     return False, None
# def _needs_order_lookup(message: str, history: list) -> tuple[bool, str | None]:
#     """
#     Decide whether to call the order tool.
#     Returns (should_lookup, order_id_or_None)
#     """
#     # Check current message first — including malformed IDs
#     oid = _extract_any_order_like(message)
#     if oid:
#         return True, oid

#     # For ANY follow-up that seems order-related,
#     # scan ALL history for a previously mentioned order ID
#     lower = message.lower()
#     followup_signals = [
#         'when will it', 'when does it', 'where is it',
#         'more details', 'tell me more', 'what about',
#         'will it arrive', 'arriving', 'get here',
#         'status', 'update', 'tracking'
#     ]
#     if any(signal in lower for signal in followup_signals):
#         for turn in reversed(history):
#             oid = _extract_order_id(turn.get("content", ""))
#             if oid:
#                 return True, oid

#     # Standard order question check
#     if is_order_question(message):
#         for turn in reversed(history[-4:]):
#             oid = _extract_order_id(turn.get("content", ""))
#             if oid:
#                 return True, oid
#         return True, None

#     return False, None

# def chat(
#     user_message: str,
#     session_id: str | None = None
# ) -> dict:
#     """
#     Process one user message and return a structured response.

#     Args:
#         user_message: The customer's message
#         session_id: Session identifier (created if not provided)

#     Returns:
#         {
#             "session_id": str,
#             "response": str,
#             "sources": list[str],
#             "needs_handoff": bool,
#             "conflict": dict | None,
#             "tool_called": bool,
#             "validator_flags": list[str],
#         }
#     """
#     if session_id is None:
#         session_id = str(uuid.uuid4())

#     history = _sessions.get(session_id, [])
#     log_turn_start(session_id, user_message, history)

#     tool_called = False
#     needs_handoff = False
#     order_result = None
#     conflict = None
#     sources = []

#     try:
#         # ── Step 1: Order lookup ──────────────────────────────────────────
#         wants_order, order_id = _needs_order_lookup(user_message, history)

#         if wants_order and order_id is None:
#             # Ask for order ID — don't call LLM at all
#             response_text = "I'd be happy to help with your order. Could you please share your order ID? It looks like ORD- followed by numbers (for example, ORD-1007)."
#             _sessions[session_id] = (history + [
#                 {"role": "user", "content": user_message},
#                 {"role": "assistant", "content": response_text}
#             ])[-MAX_HISTORY:]
#             return {
#                 "session_id": session_id,
#                 "response": response_text,
#                 "sources": [],
#                 "needs_handoff": False,
#                 "conflict": None,
#                 "tool_called": False,
#                 "validator_flags": [],
#             }

#         if wants_order and order_id:
#             order_result = lookup_order(order_id)
#             tool_called = True
#             log_tool_call("order_lookup", {"order_id": order_id}, order_result)

#             if order_result.get("needs_handoff"):
#                 needs_handoff = True

#         # ── Step 2: RAG retrieval ─────────────────────────────────────────
#         # Build a richer query by including recent context
#         history_context = " ".join(
#             t["content"] for t in history[-2:] if t["role"] == "user"
#         )
#         query = f"{history_context} {user_message}".strip()
#         chunks = retrieve(query, top_k=5)

#         # ── Step 3: Conflict detection ────────────────────────────────────
#         conflict = detect_conflict(chunks)
#         if conflict:
#             needs_handoff = True

#         log_retrieval(chunks, conflict)
#         sources = list({c["filename"] for c in chunks
#                        if c["metadata"].get("is_internal") != "True"
#                        and c["metadata"].get("status") != "superseded"})

#         # ── Step 4: Build context block ───────────────────────────────────
#         context_parts = []

#         if order_result:
#             if order_result["found"]:
#                 context_parts.append(
#                     "## Order lookup result\n"
#                     + json.dumps(order_result["order"], indent=2, default=str)
#                 )
#             else:
#                 context_parts.append(
#                     f"## Order lookup result\nNot found: {order_result['reason']}"
#                 )

#         if conflict:
#             context_parts.append(
#                 f"## ⚠ SOURCE CONFLICT DETECTED on topic: {conflict['topic']}\n"
#                 f"Source A ({conflict['source_a']['filename']} > {conflict['source_a']['heading']}):\n"
#                 f"{conflict['source_a']['excerpt']}\n\n"
#                 f"Source B ({conflict['source_b']['filename']} > {conflict['source_b']['heading']}):\n"
#                 f"{conflict['source_b']['excerpt']}\n\n"
#                 "You MUST surface this conflict to the customer and recommend human confirmation."
#             )

#         context_parts.append(format_chunks_for_prompt(chunks))
#         context_block = "\n\n".join(context_parts)

#         # ── Step 5: Build messages and call Groq ──────────────────────────
#         messages = build_messages(history, context_block)
#         messages.append({"role": "user", "content": user_message})

#         client = _get_client()
#         completion = client.chat.completions.create(
#             model=GROQ_MODEL,
#             messages=messages,
#             temperature=0.1,   # low temp = more consistent, less hallucination
#             max_tokens=600,
#         )
#         response_text = completion.choices[0].message.content.strip()

#         # ── Step 6: Validate response ─────────────────────────────────────
#         val = validate(
#             response=response_text,
#             tool_was_called=tool_called,
#             is_order_question=is_order_question(user_message)
#         )

#         if not val.passed:
#             response_text = val.response  # use corrected response if flagged

#         if "[HANDOFF RECOMMENDED]" in response_text:
#             needs_handoff = True

#         log_response(response_text, needs_handoff, flagged=not val.passed)

#         # ── Step 7: Update session history ────────────────────────────────
#         history = history + [
#             {"role": "user", "content": user_message},
#             {"role": "assistant", "content": response_text},
#         ]
#         _sessions[session_id] = history[-MAX_HISTORY:]

#         return {
#             "session_id": session_id,
#             "response": response_text,
#             "sources": sources,
#             "needs_handoff": needs_handoff,
#             "conflict": conflict,
#             "tool_called": tool_called,
#             "validator_flags": val.flags,
#         }

#     except Exception as e:
#         log_error(str(e), context=f"session={session_id} msg={user_message[:80]}")
#         raise


# def new_session() -> str:
#     """Create a fresh session ID."""
#     sid = str(uuid.uuid4())
#     _sessions[sid] = []
#     return sid


# def clear_session(session_id: str):
#     """Clear a session's history."""
#     _sessions.pop(session_id, None)
"""
agent.py
Main agent loop for Aster & Row support agent.

Flow per turn:
1. Detect if order lookup is needed
2. Retrieve relevant knowledge-base chunks
3. Detect source conflicts
4. Call order tool if needed
5. Build prompt with context
6. Call Groq LLM
7. Validate response
8. Return structured result
"""

import os
import re
import json
import uuid
from dotenv import load_dotenv
from groq import Groq

from retriever import retrieve, detect_conflict, format_chunks_for_prompt
from order_tool import lookup_order
from prompt import build_messages
from validator import validate, is_order_question
from logger import (
    log_turn_start, log_retrieval, log_tool_call,
    log_response, log_error
)

load_dotenv()

# In-memory session store: session_id → list of {role, content}
_sessions: dict[str, list] = {}
MAX_HISTORY = 6  # last 6 turns kept per session

GROQ_MODEL = "openai/gpt-oss-120b"


def _get_client() -> Groq:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise EnvironmentError("GROQ_API_KEY not set. Copy .env.example to .env and add your key.")
    return Groq(api_key=key)


def _extract_order_id(text: str) -> str | None:
    """Extract ORD-XXXX pattern from user message or recent history."""
    match = re.search(r'\bORD[-\s]?\d+\b', text, re.IGNORECASE)
    if match:
        return match.group(0).replace(" ", "-").upper()
    return None


def _extract_any_order_like(text: str) -> str | None:
    """
    Extract anything that looks like an order ID the user provided,
    even if malformed (e.g. ABC-HELLO) or casually written (e.g. 'order id 1007').
    Used to trigger the tool so it can return a safe error rather than ignoring.
    """
    # Standard ORD- pattern first
    match = re.search(r'\bORD[-\s]?\d+\b', text, re.IGNORECASE)
    if match:
        return match.group(0).replace(" ", "-").upper()

    # Casual pattern: "order id 1007", "order number 1007", "order 1007"
    match = re.search(
        r'\border\s+(?:id\s+|number\s+|#\s*)?(\d{4,})\b',
        text, re.IGNORECASE
    )
    if match:
        return f"ORD-{match.group(1)}"

    # Any word that follows "order/check/lookup" and looks like an ID (e.g. ABC-HELLO)
    match = re.search(
        r'(?:order|check|look up|lookup)\s+(?:number\s+)?([A-Z0-9][A-Z0-9\-]{2,})',
        text, re.IGNORECASE
    )
    if match:
        candidate = match.group(1).upper()
        # Don't match common English words accidentally
        if candidate not in {"THE", "MY", "AN", "FOR", "ID", "STATUS", "NUMBER"}:
            return candidate

    return None


def _needs_order_lookup(message: str, history: list) -> tuple[bool, str | None]:
    """
    Decide whether to call the order tool.
    Returns (should_lookup, order_id_or_None)
    """
    # Check current message first — including malformed IDs and casual formats
    oid = _extract_any_order_like(message)
    if oid:
        return True, oid

    # For follow-up messages that seem order-related,
    # scan ALL history for a previously mentioned order ID
    lower = message.lower()
    followup_signals = [
        'when will it', 'when does it', 'where is it',
        'more details', 'tell me more', 'what about it',
        'will it arrive', 'arriving', 'get here',
        'status', 'update', 'tracking', 'when will',
        'how long', 'still coming', 'any update',
    ]
    if any(signal in lower for signal in followup_signals):
        for turn in reversed(history):
            oid = _extract_order_id(turn.get("content", ""))
            if oid:
                return True, oid

    # Standard order question check
    if is_order_question(message):
        for turn in reversed(history[-4:]):
            oid = _extract_order_id(turn.get("content", ""))
            if oid:
                return True, oid
        # Order question but no ID found — signal to ask
        return True, None

    return False, None


def chat(
    user_message: str,
    session_id: str | None = None
) -> dict:
    """
    Process one user message and return a structured response.

    Args:
        user_message: The customer's message
        session_id: Session identifier (created if not provided)

    Returns:
        {
            "session_id": str,
            "response": str,
            "sources": list[str],
            "needs_handoff": bool,
            "conflict": dict | None,
            "tool_called": bool,
            "validator_flags": list[str],
        }
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    history = _sessions.get(session_id, [])
    log_turn_start(session_id, user_message, history)

    tool_called = False
    needs_handoff = False
    order_result = None
    conflict = None
    sources = []

    try:
        # ── Step 1: Order lookup ──────────────────────────────────────────
        wants_order, order_id = _needs_order_lookup(user_message, history)

        if wants_order and order_id is None:
            # Ask for order ID — don't call LLM at all
            response_text = (
                "I'd be happy to help with your order. Could you please share "
                "your order ID? It looks like ORD- followed by numbers "
                "(for example, ORD-1007)."
            )
            _sessions[session_id] = (history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": response_text}
            ])[-MAX_HISTORY:]
            return {
                "session_id": session_id,
                "response": response_text,
                "sources": [],
                "needs_handoff": False,
                "conflict": None,
                "tool_called": False,
                "validator_flags": [],
            }

        if wants_order and order_id:
            order_result = lookup_order(order_id)
            tool_called = True
            log_tool_call("order_lookup", {"order_id": order_id}, order_result)

            if order_result.get("needs_handoff"):
                needs_handoff = True

        # ── Step 2: RAG retrieval ─────────────────────────────────────────
        # Build a richer query by including recent context
        history_context = " ".join(
            t["content"] for t in history[-2:] if t["role"] == "user"
        )
        query = f"{history_context} {user_message}".strip()
        chunks = retrieve(query, top_k=6)

        # ── Step 3: Conflict detection ────────────────────────────────────
        conflict = detect_conflict(chunks)
        if conflict:
            needs_handoff = True

        log_retrieval(chunks, conflict)
        sources = list({c["filename"] for c in chunks
                       if c["metadata"].get("is_internal") != "True"
                       and c["metadata"].get("status") != "superseded"})

        # ── Step 4: Build context block ───────────────────────────────────
        context_parts = []

        if order_result:
            if order_result["found"]:
                context_parts.append(
                    "## Order lookup result\n"
                    + json.dumps(order_result["order"], indent=2, default=str)
                )
            else:
                context_parts.append(
                    f"## Order lookup result\nNot found: {order_result['reason']}"
                )

        if conflict:
            context_parts.append(
                f"## SOURCE CONFLICT DETECTED on topic: {conflict['topic']}\n"
                f"Source A ({conflict['source_a']['filename']} > {conflict['source_a']['heading']}):\n"
                f"{conflict['source_a']['excerpt']}\n\n"
                f"Source B ({conflict['source_b']['filename']} > {conflict['source_b']['heading']}):\n"
                f"{conflict['source_b']['excerpt']}\n\n"
                "You MUST surface this conflict to the customer and recommend human confirmation."
            )

        context_parts.append(format_chunks_for_prompt(chunks))
        context_block = "\n\n".join(context_parts)

        # ── Step 5: Build messages and call Groq ──────────────────────────
        messages = build_messages(history, context_block)
        messages.append({"role": "user", "content": user_message})

        client = _get_client()
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.1,   # low temp = more consistent, less hallucination
            max_tokens=600,
        )
        response_text = completion.choices[0].message.content.strip()

        # ── Step 6: Validate response ─────────────────────────────────────
        val = validate(
            response=response_text,
            tool_was_called=tool_called,
            is_order_question=is_order_question(user_message)
        )

        if not val.passed:
            response_text = val.response  # use corrected response if flagged

        if "[HANDOFF RECOMMENDED]" in response_text:
            needs_handoff = True

        log_response(response_text, needs_handoff, flagged=not val.passed)

        # ── Step 7: Update session history ────────────────────────────────
        history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response_text},
        ]
        _sessions[session_id] = history[-MAX_HISTORY:]

        return {
            "session_id": session_id,
            "response": response_text,
            "sources": sources,
            "needs_handoff": needs_handoff,
            "conflict": conflict,
            "tool_called": tool_called,
            "validator_flags": val.flags,
        }

    except Exception as e:
        log_error(str(e), context=f"session={session_id} msg={user_message[:80]}")
        raise


def new_session() -> str:
    """Create a fresh session ID."""
    sid = str(uuid.uuid4())
    _sessions[sid] = []
    return sid


def clear_session(session_id: str):
    """Clear a session's history."""
    _sessions.pop(session_id, None)