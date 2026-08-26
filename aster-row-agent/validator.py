"""
validator.py
Post-response safety validator.
"""

import re

FORBIDDEN_PATTERNS = [
    r'[\w\.-]+@[\w\.-]+\.\w+',
    r'\brisk.?score\b',
    r'\bwarehouse.?note\b',
    r'\bsupport.?tag\b',
    r'ava\.morgan@example\.test',
    r'220 King Street',
    r'fraud review cleared',
    r'Manual fraud review',
    r'REF-\d+',
    r'risk_score',
]

FALSE_ACTION_PATTERNS = [
    r'(refund|cancellation|replacement|address change|adjustment|warranty).{0,30}(approved|completed|processed|issued|done|confirmed)',
    r'(cancelled|canceled) your order',
    r'(issued|applied|added).{0,20}(coupon|credit|refund)',
    r'escalation.{0,20}(created|opened|submitted)',
    r'ticket.{0,20}(created|opened|submitted)',
]

# More specific order signals — require ORD- pattern OR explicit "my order" + status words
ORDER_STATUS_WORDS = [
    "shipped", "delivered", "processing", "pending",
    "cancelled", "returned", "delayed", "exception",
    "tracking", "estimated delivery"
]


class ValidationResult:
    def __init__(self):
        self.passed = True
        self.flags = []
        self.response = ""


def validate(
    response: str,
    tool_was_called: bool,
    is_order_question: bool
) -> ValidationResult:
    result = ValidationResult()
    result.response = response

    if is_order_question and not tool_was_called:
        lower = response.lower()
        if any(word in lower for word in ORDER_STATUS_WORDS):
            result.passed = False
            result.flags.append("ORDER_STATUS_WITHOUT_TOOL_CALL")
            result.response = (
                "I need to look up your order to give you accurate information. "
                "Could you please provide your order ID (e.g. ORD-1007)?"
            )
            return result

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            result.passed = False
            result.flags.append(f"FORBIDDEN_FIELD_LEAK: {pattern}")

    for pattern in FALSE_ACTION_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            result.passed = False
            result.flags.append(f"FALSE_ACTION_CLAIM: {pattern}")

    return result


def is_order_question(user_message: str) -> bool:
    """
    Heuristic: does this message seem to be asking about a specific order?
    Requires either an order ID OR explicit 'my order' phrasing with status words.
    Avoids false positives on damage/product questions.
    """
    lower = user_message.lower()

    # Direct order ID reference - always an order question
    if re.search(r'\bord[-\s]?\d+\b', lower):
        return True

    # "my order" with clear order-tracking intent
    if 'my order' in lower and any(w in lower for w in ['where', 'status', 'track', 'when', 'arrive', 'cancel']):
        return True

    return False