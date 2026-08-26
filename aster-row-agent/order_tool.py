"""
order_tool.py
Safe order lookup from orders.json.

Rules enforced here:
- Only customer-safe fields are returned to the model
- Internal fields (email, address, risk_score, warehouse_note, support_tags) are stripped
- Cancelled/returned orders never report stale ETA or carrier info
- Exception status always triggers handoff recommendation
- Order ID is normalized before lookup (lowercase, whitespace, punctuation)
"""

import json
import re
from pathlib import Path

ORDERS_FILE = Path(__file__).parent.parent / "data" / "orders.json"

# Fields from the customer object that are safe to expose
CUSTOMER_SAFE_FIELDS = {
    "order_id", "membership_tier", "placed_at", "status",
    "status_updated_at", "shipped_at", "delivered_at",
    "carrier", "tracking_number", "estimated_delivery",
    "customer_safe_message", "items"
}

# Item-level fields that are safe
ITEM_SAFE_FIELDS = {"name", "quantity", "final_sale"}

# Statuses where delivery/carrier info is stale and must not be shown
TERMINAL_STATUSES = {"cancelled", "returned"}


def _load_orders() -> dict:
    """Load and index orders by normalized order_id."""
    data = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    index = {}
    for order in data.get("orders", []):
        oid = order.get("order_id", "").upper().strip()
        index[oid] = order
    return index


def normalize_order_id(raw: str) -> str:
    """
    Normalize user-supplied order ID:
    - Strip whitespace
    - Uppercase
    - Allow ORD-XXXX pattern only
    """
    cleaned = raw.strip().upper()
    # Remove any surrounding punctuation or quotes
    cleaned = re.sub(r"[\"'`]", "", cleaned)
    return cleaned


def _sanitize_items(items: list) -> list:
    """Strip any internal item fields."""
    safe = []
    for item in items:
        safe.append({k: v for k, v in item.items() if k in ITEM_SAFE_FIELDS})
    return safe


def _sanitize_order(order: dict, status: str) -> dict:
    """
    Return only customer-safe fields.
    For cancelled/returned orders, suppress stale carrier/ETA fields.
    """
    result = {}

    for field in CUSTOMER_SAFE_FIELDS:
        if field not in order:
            continue

        # Suppress stale delivery info for terminal statuses
        if status in TERMINAL_STATUSES and field in {
            "carrier", "tracking_number", "estimated_delivery",
            "shipped_at"
        }:
            result[field] = None
            continue

        if field == "items":
            result[field] = _sanitize_items(order[field])
        else:
            result[field] = order[field]

    return result


def lookup_order(raw_order_id: str) -> dict:
    """
    Main lookup function called by the agent.

    Returns a result dict with keys:
    - found (bool)
    - order (dict | None) — sanitized, safe for model context
    - needs_handoff (bool)
    - reason (str) — plain English for the model to use
    """
    order_id = normalize_order_id(raw_order_id)

    # Reject clearly malformed IDs before hitting the index
    if not re.match(r'^ORD-\d+$', order_id):
        return {
            "found": False,
            "order": None,
            "needs_handoff": True,
            "reason": f"The order ID '{raw_order_id}' does not match the expected format (e.g. ORD-1007). Please check the ID and try again.",
        }

    orders = _load_orders()

    if order_id not in orders:
        return {
            "found": False,
            "order": None,
            "needs_handoff": True,
            "reason": f"No order found with ID {order_id}. Please verify the order ID or contact support.",
        }

    order = orders[order_id]
    status = order.get("status", "unknown")

    # Exception status always needs human review
    needs_handoff = (status == "exception")

    sanitized = _sanitize_order(order, status)

    # Add a plain-English note for the model about stale fields
    if status in TERMINAL_STATUSES:
        sanitized["_agent_note"] = (
            f"This order is {status}. Do not mention carrier, tracking, "
            f"or estimated delivery — those fields are stale."
        )

    if status == "exception":
        sanitized["_agent_note"] = (
            "This order has a shipping exception requiring human review. "
            "Recommend support handoff immediately."
        )

    return {
        "found": True,
        "order": sanitized,
        "needs_handoff": needs_handoff,
        "reason": None,
    }
