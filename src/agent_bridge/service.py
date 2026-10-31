"""Single-writer draft service. No player, capture or input controller imports."""

import copy
from datetime import datetime
import hashlib
from pathlib import Path
import threading

from .contracts import BridgeError, MAX_OPERATIONS, digest, encoded, validate, walk
from .storage import new_id, publish_bytes, read_json, within, write_json

EDITABLE = {
    "description", "enabled", "action_x", "action_y", "action_text", "action_keys",
    "action_key_events", "wait_after", "wait_random", "wait_random_range",
    "repeat_count", "repeat_delay", "repeat_delay_random", "repeat_delay_random_range",
    "target_image", "target_images", "confidence", "search_region", "skip_on_not_found",
    "verify_image_color", "verify_image_brightness", "scroll_amount", "drag_to_x",
    "drag_to_y", "drag_duration",
}
BASIC_TYPES = {"click", "double_click", "right_click", "type", "hotkey", "key_press", "scroll", "drag"}
IMAGE_FIELDS = {"image", "target_image", "target_images", "trigger_image", "monitoring_final_image",
                "condition_image", "auto_list_repeat_confirm_image"}


def assets(value, key=""):
    if isinstance(value, dict):
        for k, item in value.items():
            yield from assets(item, k)
    elif isinstance(value, list):
        for item in value:
            yield from assets(item, key)
    elif key in IMAGE_FIELDS and isinstance(value, str) and value:
        yield value


class DraftService:
    def __init__(self, plans_dir, templates_dir, workspace):
        self.plans_dir = Path(plans_dir).resolve()
        self.templates_dir = Path(templates_dir).resolve()
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        self.instance_id = new_id("app_")
        self.selected_media = {}
        self.granted_sources = set()
        self.change_serial = 0

    def _check(self):
        if self.closed:
            raise BridgeError("APP_NOT_CONNECTED", "Editing session is closed")

    def _path(self, draft_id):
        if not isinstance(draft_id, str) or not draft_id.startswith("draft_") or not draft_id[6:].isalnum():
            raise BridgeError("INVALID_ID", "Invalid draft ID")
        return self.workspace / (draft_id + ".json")

    def _load(self, draft_id):
        self._check()
        path = self._path(draft_id)
        if not path.is_file() or path.is_symlink():
            raise BridgeError("NOT_FOUND", "Draft not found")
        return read_json(path)

    def _save(self, state):
        self._check()
        write_json(self._path(state["draft_id"]), state)
        self.change_serial += 1

    def list_plans(self):
        self._check()
        result = []
        for path in sorted(self.plans_dir.glob("*.json")):
            try:
                plan = read_json(within(path, self.plans_dir))
                result.append({"plan_id": path.stem, "name": plan.get("name", path.stem)})
            except (ValueError, OSError):
                continue
        return result

    def read_plan(self, plan_id):
        self._check()
        if not isinstance(plan_id, str) or not plan_id.startswith("plan_") or not plan_id[5:].replace("_", "").isalnum():
            raise BridgeError("INVALID_ID", "Invalid plan ID")
        path = within(self.plans_dir / (plan_id + ".json"), self.plans_dir)
        data = path.read_bytes()
        from .contracts import decode
        return {"plan": decode(data), "source_sha256": hashlib.sha256(data).hexdigest(),
                "source": "disk", "unsaved_editor_state_included": False}

    def open_draft(self, name, plan_id=None):
        with self.lock:
            self._check()
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise BridgeError("INVALID_FIELD", "A short draft name is required")
            if len(self.list_drafts()) >= 50:
                raise BridgeError("LIMIT_REACHED", "Keep at most 50 drafts in this workspace")
            source = self.read_plan(plan_id) if plan_id else None
            plan = copy.deepcopy(source["plan"]) if source else {
                "initial_rules": [], "monitoring_rules": [], "total_repeat_count": 1,
                "created_at": datetime.now().isoformat(), "user_verified": False, "modified": True,
            }
            plan["plan_id"] = new_id("plan_")
            plan["name"] = name.strip()
            validate(plan)
            state = {"draft_id": new_id("draft_"), "revision": 1, "plan": plan,
                     "source": {"plan_id": plan_id, "sha256": source["source_sha256"]} if source else None,
                     "receipts": {}, "asset_files": {}, "analysis": None, "approval": None, "publication": None}
            self._save(state)
            return self._public(state)

    def _public(self, state):
        result = copy.deepcopy(state)
        result.pop("approval", None)
        return result

    def read_draft(self, draft_id):
        with self.lock:
            return self._public(self._load(draft_id))

    def list_drafts(self):
        with self.lock:
            self._check()
            return [{"draft_id": s["draft_id"], "name": s["plan"]["name"], "revision": s["revision"]}
                    for s in (read_json(p) for p in sorted(self.workspace.glob("draft_*.json"))
                              if p.stem[6:].isalnum())]

    @staticmethod
    def _expect(state, revision):
        if type(revision) is not int or state["revision"] != revision:
            raise BridgeError("REVISION_CONFLICT", "Read the latest draft before changing it")
        if state.get("publication"):
            raise BridgeError("ALREADY_PUBLISHED", "Published drafts are immutable; create a new draft")

    @staticmethod
    def _editable(rule):
        if rule.get("action_type") not in BASIC_TYPES or rule.get("is_monitoring_mode"):
            raise BridgeError("PROTECTED_ACTION", "This action is read-only in the initial bridge")

    def stage_edits(self, draft_id, expected_revision, request_id, operations):
        with self.lock:
            state = self._load(draft_id)
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise BridgeError("INVALID_ID", "A request ID is required")
            fingerprint = digest({"revision": expected_revision, "operations": operations})
            old = state["receipts"].get(request_id)
            if old:
                if old["digest"] != fingerprint:
                    raise BridgeError("REQUEST_ID_REUSED", "Request ID has different contents")
                return copy.deepcopy(old)
            self._expect(state, expected_revision)
            if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
                raise BridgeError("INVALID_REQUEST", "Submit 1 to 100 operations")
            if len(state["receipts"]) >= 1000:
                raise BridgeError("LIMIT_REACHED", "Draft request history is full")
            candidate = copy.deepcopy(state)
            for operation in operations:
                self._apply(candidate["plan"], operation)
            validate(candidate["plan"])
            candidate["revision"] += 1
            candidate["approval"] = None
            candidate["analysis"] = None
            receipt = {"request_id": request_id, "digest": fingerprint, "revision": candidate["revision"]}
            candidate["receipts"][request_id] = receipt
            self._save(candidate)
            return receipt

    def _apply(self, plan, op):
        if not isinstance(op, dict):
            raise BridgeError("INVALID_REQUEST", "Operation must be an object")
        rows = {r["rule_id"]: (r, siblings, parent) for r, siblings, parent, _ in walk(plan)}
        kind = op.get("op")
        if kind == "rename_plan":
            name = op.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise BridgeError("INVALID_FIELD", "Invalid plan name")
            plan["name"] = name.strip()
            return
        if kind == "add_rule":
            action_type = op.get("action_type", "click")
            if action_type not in BASIC_TYPES:
                raise BridgeError("PROTECTED_ACTION", "Unsupported action type")
            from ..analyzer.automation_models import AutomationRule
            rule = AutomationRule(action_type=action_type).to_dict()
            rule["rule_id"] = new_id("rule_")
            fields = op.get("fields", {})
            self._fields(rule, fields)
            siblings, parent_id = self._destination(plan, rows, op)
            rule["parent_id"] = parent_id
            self._insert(siblings, rule, op.get("before_id"))
            return
        if op.get("rule_id") not in rows:
            raise BridgeError("NOT_FOUND", "Action ID not found")
        rule, siblings, parent = rows[op["rule_id"]]
        self._editable(rule)
        if kind == "update_rule":
            self._fields(rule, op.get("fields", {}))
        elif kind == "delete_rule":
            if rule.get("children"):
                raise BridgeError("INVALID_REQUEST", "Move/delete children explicitly first")
            siblings.remove(rule)
        elif kind == "move_rule":
            target, parent_id = self._destination(plan, rows, op)
            # Validate the candidate tree after movement; reject self/descendant parents first.
            descendants = {r["rule_id"] for r, *_ in walk({"initial_rules": [dict(rule, parent_id=None)]})}
            if parent_id in descendants:
                raise BridgeError("INVALID_TREE", "An action cannot contain itself")
            if op.get("before_id") == rule["rule_id"]:
                return
            siblings.remove(rule)
            rule["parent_id"] = parent_id
            self._insert(target, rule, op.get("before_id"))
        else:
            raise BridgeError("INVALID_REQUEST", "Unknown operation")

    @staticmethod
    def _fields(rule, fields):
        if not isinstance(fields, dict) or set(fields) - EDITABLE:
            raise BridgeError("INVALID_FIELD", "Field is not editable")
        for key, value in fields.items():
            if key in {"enabled", "wait_random", "repeat_delay_random", "skip_on_not_found",
                       "verify_image_color", "verify_image_brightness"} and type(value) is not bool:
                raise BridgeError("INVALID_FIELD", "Boolean field needs a boolean")
            if key in {"action_x", "action_y", "drag_to_x", "drag_to_y", "scroll_amount"} and value is not None and type(value) is not int:
                raise BridgeError("INVALID_FIELD", "Coordinate needs an integer")
            if key in {"action_keys", "target_images"} and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise BridgeError("INVALID_FIELD", "Expected a list of strings")
            if key == "action_key_events" and not isinstance(value, list):
                raise BridgeError("INVALID_FIELD", "Expected recorded events")
            if key in {"description", "action_text", "target_image"} and value is not None and not isinstance(value, str):
                raise BridgeError("INVALID_FIELD", "Expected text")
            rule[key] = copy.deepcopy(value)

    def _destination(self, plan, rows, op):
        parent_id = op.get("parent_id")
        if not parent_id:
            return plan["initial_rules"], None
        if parent_id not in rows:
            raise BridgeError("INVALID_REFERENCE", "Parent action not found")
        parent = rows[parent_id][0]
        self._editable(parent)
        return parent.setdefault("children", []), parent_id

    @staticmethod
    def _insert(siblings, rule, before_id):
        if before_id:
            index = next((i for i, r in enumerate(siblings) if r["rule_id"] == before_id), None)
            if index is None:
                raise BridgeError("INVALID_REFERENCE", "Insertion anchor is not a sibling")
            siblings.insert(index, rule)
        else:
            siblings.append(rule)

    def _asset_path(self, state, name):
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or ":" in name:
            raise BridgeError("PATH_DENIED", "Asset references must be registered file names")
        if name in state["asset_files"]:
            path = within(self.workspace / "assets" / name, self.workspace / "assets")
            if hashlib.sha256(path.read_bytes()).hexdigest() != state["asset_files"][name]["sha256"]:
                raise BridgeError("MEDIA_NOT_READY", "Draft asset content changed")
            return path
        return within(self.templates_dir / name, self.templates_dir)

    def validate_draft(self, draft_id):
        with self.lock:
            state = self._load(draft_id)
            rows = validate(state["plan"])
            for name in set(assets(state["plan"])):
                self._asset_path(state, name)
            # Test runtime-model loading without importing the player or generating input.
            from ..analyzer.automation_models import AutomationPlan
            AutomationPlan.from_dict(copy.deepcopy(state["plan"]), self.templates_dir)
            protected = bool(state["plan"].get("game_modes")) or any(r.get("action_type") == "game_mode" for r, *_ in rows)
            return {"valid": True, "revision": state["revision"], "rule_count": len(rows),
                    "publish_allowed": not protected, "runtime_tested": False,
                    "ai_analysis_current": bool(state.get("analysis") and state["analysis"]["plan_digest"] == digest(state["plan"])),
                    "warning": "Special-mode plans cannot be published through this bridge" if protected else "Recognition and real execution require separate verification"}

    def record_analysis(self, draft_id, expected_revision, goal, steps, open_questions=None):
        """Store the agent's evidence summary. This does NOT infer behavior locally."""
        with self.lock:
            state = self._load(draft_id)
            self._expect(state, expected_revision)
            if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000 or not isinstance(steps, list):
                raise BridgeError("INVALID_ANALYSIS", "Provide a goal and evidence-backed action summaries")
            ids = {r["rule_id"] for r, *_ in walk(state["plan"])}
            covered = set()
            for step in steps:
                if not isinstance(step, dict) or step.get("rule_id") not in ids:
                    raise BridgeError("INVALID_ANALYSIS", "Analysis must reference this draft's stable action IDs")
                if step["rule_id"] in covered:
                    raise BridgeError("INVALID_ANALYSIS", "Duplicate analysis action")
                covered.add(step["rule_id"])
                for field in ("observation", "purpose", "expected_result"):
                    if not isinstance(step.get(field), str) or not 1 <= len(step[field].strip()) <= 2000:
                        raise BridgeError("INVALID_ANALYSIS", "Summarize observation, purpose and expected result")
                record = self.selected_media.get(step.get("recording_id"))
                if not record:
                    raise BridgeError("PERMISSION_REQUIRED", "Analyze a recording selected by the user")
                from .contracts import number
                seconds = step.get("seconds")
                if not number(seconds, 0, 86400):
                    raise BridgeError("INVALID_ANALYSIS", "Provide the evidence time in seconds")
                from .media import MediaTools
                MediaTools._private(record, seconds)
            if covered != ids:
                raise BridgeError("INVALID_ANALYSIS", "Every action requires an AI purpose/evidence summary")
            questions = open_questions or []
            if not isinstance(questions, list) or not all(isinstance(q, str) and len(q) <= 2000 for q in questions):
                raise BridgeError("INVALID_ANALYSIS", "Open questions must be a list of text")
            state["analysis"] = {"goal": goal, "steps": copy.deepcopy(steps), "open_questions": questions,
                                 "plan_digest": digest(state["plan"]), "understanding_not_proven": True}
            state["revision"] += 1
            state["approval"] = None
            self._save(state)
            return {"revision": state["revision"], "actions_covered": len(covered), "open_questions": questions,
                    "saved_and_reloaded": True}

    def approve_publication(self, draft_id, expected_revision):
        """UI-only method. Never exposed through MCP dispatch."""
        with self.lock:
            state = self._load(draft_id)
            self._expect(state, expected_revision)
            result = self.validate_draft(draft_id)
            if not result["publish_allowed"]:
                raise BridgeError("PROTECTED_ACTION", result["warning"])
            if not result["ai_analysis_current"] or state["analysis"]["open_questions"]:
                raise BridgeError("ANALYSIS_REQUIRED", "The AI must review this revision and resolve open questions before approval")
            state["approval"] = {"instance_id": self.instance_id, "revision": expected_revision,
                                 "digest": digest(state["plan"])}
            self._save(state)

    def publish_new_plan(self, draft_id, expected_revision):
        with self.lock:
            state = self._load(draft_id)
            if state.get("publication"):
                return copy.deepcopy(state["publication"])
            self._expect(state, expected_revision)
            approval = state.get("approval") or {}
            if approval != {"instance_id": self.instance_id, "revision": expected_revision,
                            "digest": digest(state["plan"])}:
                raise BridgeError("PERMISSION_REQUIRED", "Approve this exact draft in WinCro first")
            source = state.get("source")
            if source and self.read_plan(source["plan_id"])["source_sha256"] != source["sha256"]:
                raise BridgeError("REVISION_CONFLICT", "Reference plan changed; review a new copy")
            self.validate_draft(draft_id)
            for name, metadata in state["asset_files"].items():
                path = self._asset_path(state, name)
                target = self.templates_dir / name
                if target.exists():
                    if hashlib.sha256(within(target, self.templates_dir).read_bytes()).hexdigest() != metadata["sha256"]:
                        raise BridgeError("SAVE_FAILED", "Asset name collision")
                else:
                    publish_bytes(self.templates_dir, name, path.read_bytes())
            plan = copy.deepcopy(state["plan"])
            plan["user_verified"] = True
            plan["modified"] = True
            filename = plan["plan_id"] + ".json"
            payload = encoded(plan)
            # Persist intent before filesystem publication so uncertain replies are recoverable.
            intent = self.workspace / (draft_id + ".publish-intent.json")
            write_json(intent, {"filename": filename, "sha256": hashlib.sha256(payload).hexdigest()})
            target = self.plans_dir / filename
            if target.exists():
                if target.read_bytes() != payload:
                    raise BridgeError("SAVE_STATE_UNKNOWN", "Publication target differs; do not retry blindly")
            else:
                publish_bytes(self.plans_dir, filename, payload)
            if read_json(target) != plan:
                raise BridgeError("SAVE_STATE_UNKNOWN", "Published plan verification failed")
            state["publication"] = {"plan_id": plan["plan_id"], "name": plan["name"],
                                    "revision": state["revision"], "sha256": hashlib.sha256(payload).hexdigest(),
                                    "saved_and_reloaded": True, "playlist_changed": False}
            self._save(state)
            return copy.deepcopy(state["publication"])

    def save_draft(self, draft_id, expected_revision):
        with self.lock:
            state = self._load(draft_id)
            self._expect(state, expected_revision)
            self._save(state)
            return {"revision": state["revision"], "saved_and_reloaded": True}

    def close(self):
        with self.lock:
            self.closed = True

    def dispatch(self, method, arguments):
        allowed = {"list_plans", "read_plan", "open_draft", "read_draft", "list_drafts",
                   "stage_edits", "validate_draft", "save_draft", "publish_new_plan",
                   "record_analysis", "list_recordings", "read_recording", "get_frames", "get_input_events", "create_asset_crop"}
        if method not in allowed:
            raise BridgeError("TOOL_DENIED", "Tool not available in the initial bridge")
        with self.lock:
            self._check()
            if method == "list_recordings":
                return [{"recording_id": key, "name": value["path"].name}
                        for key, value in self.selected_media.items()]
            if method == "list_plans":
                return [row for row in self.list_plans() if row["plan_id"] in self.granted_sources]
            if method in {"read_plan", "open_draft"}:
                source = arguments.get("plan_id")
                if source and source not in self.granted_sources:
                    raise BridgeError("PERMISSION_REQUIRED", "Select this reference plan in WinCro first")
            if method in {"read_recording", "get_frames", "get_input_events", "create_asset_crop"}:
                from .media import MediaTools
                return getattr(MediaTools(self), method)(**arguments)
            return getattr(self, method)(**arguments)
