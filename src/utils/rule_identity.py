"""Plan-local rule identity validation and conservative legacy repair."""

from collections import Counter
from copy import deepcopy
import logging
from uuid import NAMESPACE_URL, uuid5


logger = logging.getLogger(__name__)


def _records(data):
    def walk(rules, path, parent=None):
        for index, rule in enumerate(rules):
            location = path + (index,)
            yield rule, parent, location
            yield from walk(rule.get("children") or [], location + ("children",), rule)

    for section in ("initial_rules", "monitoring_rules"):
        yield from walk(data.get(section) or [], (section,))


def validate_rule_identities(data):
    """Reject an invalid save before the existing document can be replaced."""
    counts = Counter(rule.get("rule_id") for rule, _, _ in _records(data))
    invalid = [key for key, count in counts.items() if not key or count > 1]
    if invalid:
        raise ValueError("Missing or duplicate action IDs: " + ", ".join(map(str, invalid)))


def repair_legacy_rule_identities(data):
    """Repair only unambiguous identities, without editing the caller's JSON.

    ID-only references cannot distinguish two old owners. Refuse to guess their
    target or to rename an ID that owns a special-mode configuration/map.
    """
    records = list(_records(data))
    counts = Counter(rule.get("rule_id") for rule, _, _ in records)
    duplicates = {key for key, count in counts.items() if key and count > 1}
    if not duplicates and all(counts):
        return data

    if any(rule.get("action_type") == "game_mode" and not rule.get("rule_id")
           for rule, _, _ in records):
        raise ValueError("Missing special-mode action IDs require explicit repair")
    protected = set(data.get("game_modes") or {})
    protected.update(rule.get("rule_id") for rule, _, _ in records
                     if rule.get("action_type") == "game_mode")
    if protected & duplicates:
        raise ValueError("Duplicate special-mode action IDs require explicit repair: "
                         + ", ".join(sorted(protected & duplicates)))

    def check_references(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if (key.endswith("_rule_id") and isinstance(value, str)
                        and value in duplicates):
                    raise ValueError(f"Ambiguous action reference {key}={value}; select its target explicitly")
                check_references(value)
        elif isinstance(node, list):
            for value in node:
                check_references(value)

    check_references(data)
    repaired = deepcopy(data)
    records = list(_records(repaired))
    original_ids = {id(rule): rule.get("rule_id") for rule, _, _ in records}
    reserved = {key for key in counts if key} | protected
    seen = set()
    repaired_count = 0
    for rule, _, path in records:
        old = rule.get("rule_id")
        if old and old not in seen:
            seen.add(old)
            continue
        attempt = 0
        while True:
            seed = f"wincro:{data.get('plan_id', '')}:{'.'.join(map(str, path))}:{old}:{attempt}"
            replacement = "rule_" + uuid5(NAMESPACE_URL, seed).hex[:16]
            if replacement not in reserved:
                break
            attempt += 1
        rule["rule_id"] = replacement
        reserved.add(replacement)
        repaired_count += 1

    # Parent pointers are disambiguated by their actual containing tree branch.
    for rule, parent, _ in records:
        if (parent is not None and original_ids[id(parent)]
                and rule.get("parent_id") == original_ids[id(parent)]):
            rule["parent_id"] = parent["rule_id"]
    validate_rule_identities(repaired)
    logger.warning("Repaired %d legacy action IDs in plan %s; action order/settings preserved",
                   repaired_count, data.get("plan_id", ""))
    return repaired
