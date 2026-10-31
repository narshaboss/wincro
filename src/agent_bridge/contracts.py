"""Small, JSON-only contracts shared by the editor and local MCP client."""

import hashlib
import json
import math

PROTOCOL = 1
MAX_MESSAGE = 4_000_000
MAX_RULES = 2000
MAX_OPERATIONS = 100


class BridgeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def decode(data):
    if len(data) > MAX_MESSAGE:
        raise BridgeError("MESSAGE_TOO_LARGE", "Message exceeds the bridge limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BridgeError("INVALID_JSON", "Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          BridgeError("INVALID_JSON", "Non-finite JSON number")))


def number(value, low, high):
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def walk(plan):
    seen = set()
    count = 0

    def visit(items, parent=None, depth=0, prefix=""):
        nonlocal count
        if not isinstance(items, list) or depth > 16:
            raise BridgeError("INVALID_TREE", "Invalid/deep action tree")
        for index, rule in enumerate(items, 1):
            count += 1
            if not isinstance(rule, dict) or count > MAX_RULES:
                raise BridgeError("INVALID_TREE", "Invalid/oversized action tree")
            key = rule.get("rule_id")
            if not isinstance(key, str) or not key or key in seen:
                raise BridgeError("INVALID_REFERENCE", "Missing/duplicate action ID")
            seen.add(key)
            if rule.get("parent_id") != parent:
                raise BridgeError("INVALID_REFERENCE", "Action parent does not match tree")
            path = f"{prefix}-{index}" if prefix else str(index)
            yield rule, items, parent, path
            yield from visit(rule.get("children", []), key, depth + 1, path)

    for section in ("initial_rules", "monitoring_rules"):
        yield from visit(plan.get(section, []))


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_rule_id") and item:
                yield item
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def validate(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get("name"), str):
        raise BridgeError("INVALID_PLAN", "A named plan object is required")
    rows = list(walk(plan))
    ids = {r["rule_id"] for r, *_ in rows}
    for ref in references(plan):
        if not isinstance(ref, str) or ref not in ids:
            raise BridgeError("INVALID_REFERENCE", "Dangling action reference")
    for rule, *_ in rows:
        for field in ("wait_after", "wait_random_range", "repeat_delay", "repeat_delay_random_range"):
            if field in rule and not number(rule[field], 0, 86400):
                raise BridgeError("INVALID_FIELD", f"Invalid {field}")
        if not number(rule.get("confidence", .65), 0.01, 1):
            raise BridgeError("INVALID_FIELD", "Invalid confidence")
        count = rule.get("repeat_count", 1)
        if type(count) is not int or not 1 <= count <= 100000:
            raise BridgeError("INVALID_FIELD", "Invalid repeat count")
        region = rule.get("search_region")
        if region is not None and not (isinstance(region, list) and len(region) == 4
                and all(type(n) is int for n in region)
                and 0 <= region[0] < region[2] <= 100000
                and 0 <= region[1] < region[3] <= 100000):
            raise BridgeError("INVALID_FIELD", "Invalid search region")
    encoded(plan)
    return rows
