"""Single-writer draft service. No player, capture or input controller imports."""

import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading

from .contracts import BridgeError, MAX_MESSAGE, MAX_OPERATIONS, digest, encoded, validate, walk
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


def has_legacy_relative_jump(value):
    if isinstance(value, dict):
        if value.get("rewind_previous_on_trigger_missing") and not value.get("trigger_missing_rewind_rule_id"):
            return True
        if value.get("jump_enabled") and not value.get("goto_rule_id"):
            return True
        return any(has_legacy_relative_jump(item) for item in value.values())
    if isinstance(value, list):
        return any(has_legacy_relative_jump(item) for item in value)
    return False


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
    def __init__(self, plans_dir, templates_dir, workspace, *, allow_publication=True):
        self.plans_dir = Path(plans_dir).resolve()
        self.templates_dir = Path(templates_dir).resolve()
        self.workspace = Path(workspace).resolve()
        if not allow_publication and any(
            self.workspace.is_relative_to(path) or path.is_relative_to(self.workspace)
            for path in (self.plans_dir, self.templates_dir)
        ):
            raise BridgeError("PATH_DENIED", "Draft-only storage must not overlap playback data")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        self.instance_id = new_id("app_")
        self.active_draft_id = None
        self.selected_media = {}
        self.frame_traces = {}
        self.granted_sources = set()
        self.change_serial = 0
        self.allow_publication = bool(allow_publication)

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
        if len(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) > MAX_MESSAGE:
            raise BridgeError("LIMIT_REACHED", "Draft metadata exceeds the durable read limit")
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
            plan["user_verified"] = False
            plan["modified"] = True
            validate(plan)
            state = {"draft_id": new_id("draft_"), "revision": 1, "plan": plan,
                     "source": {"plan_id": plan_id, "sha256": source["source_sha256"]} if source else None,
                     "receipts": {}, "asset_files": {}, "source_assets": {}, "analysis": None,
                     "crosschecks": [], "approval": None, "publication": None}
            self._pin_assets(state)
            self._save(state)
            self.active_draft_id = state["draft_id"]
            return self._public(state)

    def _public(self, state):
        result = copy.deepcopy(state)
        result.pop("approval", None)
        return result

    def read_draft(self, draft_id):
        with self.lock:
            return self._public(self._load(draft_id))

    def _inspection_source(self, draft_id=None, plan_id=None):
        if (draft_id is None) == (plan_id is None):
            raise BridgeError("INVALID_REQUEST", "Select exactly one draft or reference plan")
        if draft_id:
            state = self._load(draft_id)
            return state["plan"], {"draft_id": draft_id, "revision": state["revision"]}
        if plan_id not in self.granted_sources:
            raise BridgeError("PERMISSION_REQUIRED", "Select this reference plan in WinCro first")
        source = self.read_plan(plan_id)
        return source["plan"], {"plan_id": plan_id, "source_sha256": source["source_sha256"]}

    def read_actions(self, draft_id=None, plan_id=None, offset=0, limit=20):
        with self.lock:
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
                raise BridgeError("INVALID_REQUEST", "Use an offset and a page size from 1 to 50")
            plan, header = self._inspection_source(draft_id, plan_id)
            rows = list(walk(plan))
            result = []
            keys = {"rule_id", "description", "action_type", "enabled", "target_image", "confidence",
                    "search_region", "wait_after", "wait_random", "repeat_count", "repeat_delay",
                    "skip_on_not_found", "click_until_image_disappears"}
            for rule, _, parent, step in rows[offset:offset + limit]:
                entry = {k: copy.deepcopy(v) for k, v in rule.items() if k in keys}
                entry.update(parent_id=parent, step=step, children_count=len(rule.get("children", [])),
                             monitoring_watch_count=len(rule.get("monitoring_watches", [])))
                result.append(entry)
            return dict(header, name=plan["name"], total=len(rows), rows=result,
                        next_offset=offset + limit if offset + limit < len(rows) else None)

    def inspect_action(self, rule_id, draft_id=None, plan_id=None):
        with self.lock:
            plan, header = self._inspection_source(draft_id, plan_id)
            for rule, _, parent, step in walk(plan):
                if rule["rule_id"] == rule_id:
                    detail = {k: copy.deepcopy(v) for k, v in rule.items() if k != "children"}
                    if len(encoded(detail)) > 100_000:
                        raise BridgeError("MESSAGE_TOO_LARGE", "Protected action configuration exceeds detail limit")
                    return dict(header, step=step, action=detail,
                                children_ids=[r["rule_id"] for r in rule.get("children", [])])
            raise BridgeError("NOT_FOUND", "Action ID not found")

    def draft_status(self, draft_id):
        with self.lock:
            state = self._load(draft_id)
            analysis = state.get("analysis") or {}
            return {"draft_id": draft_id, "revision": state["revision"], "name": state["plan"]["name"],
                    "rule_count": len(list(walk(state["plan"]))), "goal": analysis.get("goal"),
                    "open_questions": analysis.get("open_questions", []), "publication": state.get("publication"),
                    "crosschecks": [{"direction": r["direction"], "passed": r["passed"], "remaining_issues": r["remaining_issues"]}
                                    for r in state.get("crosschecks", [])]}

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
            self._pin_assets(candidate)
            candidate["revision"] += 1
            candidate["approval"] = None
            candidate["analysis"] = None
            candidate["crosschecks"] = []
            receipt = {"request_id": request_id, "digest": fingerprint, "revision": candidate["revision"]}
            candidate["receipts"][request_id] = receipt
            self._save(candidate)
            return receipt

    def _apply(self, plan, op):
        if not isinstance(op, dict):
            raise BridgeError("INVALID_REQUEST", "Operation must be an object")
        rows = {r["rule_id"]: (r, siblings, parent) for r, siblings, parent, _ in walk(plan)}
        kind = op.get("op")
        if kind in {"add_rule", "move_rule", "delete_rule"} and has_legacy_relative_jump(plan):
            raise BridgeError("INVALID_REFERENCE", "Legacy positional jumps must be resolved before changing order")
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
        path = within(self.templates_dir / name, self.templates_dir)
        expected = state.get("source_assets", {}).get(name)
        if expected and self._file_hash(path) != expected:
            raise BridgeError("MEDIA_NOT_READY", "Reference asset changed; review a new draft")
        return path

    @staticmethod
    def _file_hash(path):
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1_000_000), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _pin_assets(self, state):
        for name in set(assets(state["plan"])):
            path = self._asset_path(state, name)
            if name not in state["asset_files"] and name not in state["source_assets"]:
                state["source_assets"][name] = self._file_hash(path)

    def validate_draft(self, draft_id):
        with self.lock:
            state = self._load(draft_id)
            rows = validate(state["plan"])
            for name in set(assets(state["plan"])):
                path = self._asset_path(state, name)
                try:
                    if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
                        import cv2
                        capture = cv2.VideoCapture(str(path))
                        try:
                            if not capture.isOpened() or capture.get(cv2.CAP_PROP_FRAME_COUNT) <= 0:
                                raise ValueError("Undecodable video")
                        finally:
                            capture.release()
                    else:
                        from PIL import Image
                        with Image.open(path) as image:
                            if image.width * image.height > 16_000_000:
                                raise ValueError("Oversized image")
                            image.verify()
                except Exception as exc:
                    raise BridgeError("MEDIA_NOT_READY", "Referenced asset cannot be decoded: " + name) from exc
            # Test runtime-model loading without importing the player or generating input.
            from ..analyzer.automation_models import AutomationPlan
            from dataclasses import fields
            from ..analyzer.automation_models import AutomationRule
            AutomationPlan.from_dict(copy.deepcopy(state["plan"]), self.templates_dir)
            unknown = set(state["plan"]) - {f.name for f in fields(AutomationPlan)} - {"game_mode"}
            known_rule_fields = {f.name for f in fields(AutomationRule)} | set(AutomationRule().to_dict())
            unknown_rules = {key for rule, *_ in rows for key in set(rule) - known_rule_fields}
            if unknown or unknown_rules:
                raise BridgeError("UNSUPPORTED_SCHEMA", "Unknown fields are preserved in the draft but cannot be published: "
                                  + ", ".join(sorted(unknown | unknown_rules)))
            protected = bool(state["plan"].get("game_modes")) or any(r.get("action_type") == "game_mode" for r, *_ in rows)
            return {"valid": True, "revision": state["revision"], "rule_count": len(rows),
                      "publish_allowed": not protected and self.allow_publication, "runtime_tested": False,
                      "publication_enabled": self.allow_publication,
                    "ai_analysis_current": bool(state.get("analysis") and state["analysis"]["plan_digest"] == digest(state["plan"])),
                    "crosscheck_ready": self._crosscheck_ready(state),
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
                trace = self.frame_traces.get(step.get("frame_trace_id"))
                if not trace or trace["recording_id"] != step["recording_id"] or trace["source_sha256"] != record["sha256"]:
                    raise BridgeError("ANALYSIS_REQUIRED", "Read the evidence frames with get_frames before reporting analysis")
                from .contracts import number
                seconds = step.get("seconds")
                if not number(seconds, 0, 86400):
                    raise BridgeError("INVALID_ANALYSIS", "Provide the evidence time in seconds")
                if abs(seconds - trace["requested_seconds"]) > .1:
                    raise BridgeError("INVALID_ANALYSIS", "Analysis time does not match the evidence frame")
                from .media import MediaTools
                MediaTools._private(record, seconds)
            if covered != ids:
                raise BridgeError("INVALID_ANALYSIS", "Every action requires an AI purpose/evidence summary")
            questions = open_questions or []
            if not isinstance(questions, list) or not all(isinstance(q, str) and len(q) <= 2000 for q in questions):
                raise BridgeError("INVALID_ANALYSIS", "Open questions must be a list of text")
            state["analysis"] = {"goal": goal, "steps": copy.deepcopy(steps), "open_questions": questions,
                                 "plan_digest": digest(state["plan"]), "understanding_not_proven": True}
            state["crosschecks"] = []
            state["revision"] += 1
            state["approval"] = None
            self._save(state)
            return {"revision": state["revision"], "actions_covered": len(covered), "open_questions": questions,
                    "saved_and_reloaded": True}

    @staticmethod
    def _crosscheck_ready(state):
        latest = state.get("crosschecks", [])[-2:]
        return (len(latest) == 2 and [r["direction"] for r in latest] == ["video_to_plan", "plan_to_video"]
                and all(r["passed"] and r["plan_digest"] == digest(state["plan"]) for r in latest))

    def record_crosscheck(self, draft_id, expected_revision, direction, checks, remaining_issues=None):
        """Record the AI's alternating video/plan review, not a local semantic verdict."""
        with self.lock:
            state = self._load(draft_id)
            self._expect(state, expected_revision)
            if not state.get("analysis") or state["analysis"]["plan_digest"] != digest(state["plan"]):
                raise BridgeError("ANALYSIS_REQUIRED", "Analyze the current edited draft first")
            if direction not in {"video_to_plan", "plan_to_video"} or not isinstance(checks, list):
                raise BridgeError("INVALID_ANALYSIS", "Select a video-to-plan or plan-to-video review")
            ids = {r["rule_id"] for r, *_ in walk(state["plan"])}
            covered = set()
            for check in checks:
                if not isinstance(check, dict) or check.get("rule_id") not in ids or check["rule_id"] in covered:
                    raise BridgeError("INVALID_ANALYSIS", "Review each action exactly once per pass")
                trace = self.frame_traces.get(check.get("frame_trace_id"))
                if not trace or trace["recording_id"] not in self.selected_media:
                    raise BridgeError("ANALYSIS_REQUIRED", "Cross-check against frames read through get_frames")
                if check.get("result") not in {"match", "mismatch", "uncertain"} or not isinstance(check.get("note"), str) or not 1 <= len(check["note"].strip()) <= 2000:
                    raise BridgeError("INVALID_ANALYSIS", "Record a result and concise comparison note")
                covered.add(check["rule_id"])
            if covered != ids:
                raise BridgeError("INVALID_ANALYSIS", "The cross-check must cover every action")
            issues = remaining_issues or []
            if not isinstance(issues, list) or not all(isinstance(i, str) and len(i) <= 2000 for i in issues):
                raise BridgeError("INVALID_ANALYSIS", "Remaining issues must be text")
            entry = {"direction": direction, "checks": copy.deepcopy(checks), "remaining_issues": issues,
                     "plan_digest": digest(state["plan"]), "passed": not issues and all(c["result"] == "match" for c in checks)}
            state["crosschecks"] = (state.get("crosschecks", []) + [entry])[-6:]
            state["revision"] += 1
            state["approval"] = None
            self._save(state)
            return {"revision": state["revision"], "passed": entry["passed"],
                    "crosscheck_ready": self._crosscheck_ready(state), "saved_and_reloaded": True}

    @staticmethod
    def publication_title(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise BridgeError("INVALID_TITLE", "플랜 제목을 줄바꿈 없이 1~200자로 입력하세요.")
        return name.strip()

    def approve_publication(self, draft_id, expected_revision, name=None):
        """UI-only method. Never exposed through MCP dispatch."""
        if not self.allow_publication:
            raise BridgeError("PUBLICATION_DISABLED", "This recording workspace only saves independent drafts")
        with self.lock:
            state = self._load(draft_id)
            self._expect(state, expected_revision)
            result = self.validate_draft(draft_id)
            if not result["publish_allowed"]:
                raise BridgeError("PROTECTED_ACTION", result["warning"])
            if not result["ai_analysis_current"] or state["analysis"]["open_questions"]:
                raise BridgeError("ANALYSIS_REQUIRED", "The AI must review this revision and resolve open questions before approval")
            if not result["crosscheck_ready"]:
                raise BridgeError("CROSSCHECK_REQUIRED", "AI must complete clean video-to-plan AND plan-to-video reviews after the last edit")
            title = self.publication_title(state["plan"]["name"] if name is None else name)
            state["approval"] = {"instance_id": self.instance_id, "revision": expected_revision,
                                 "digest": digest(state["plan"]), "name": title}
            self._save(state)

    def publish_new_plan(self, draft_id, expected_revision, name=None):
        if not self.allow_publication:
            raise BridgeError("PUBLICATION_DISABLED", "This recording workspace cannot write playback plans")
        with self.lock:
            state = self._load(draft_id)
            title = self.publication_title(state["plan"]["name"] if name is None else name)
            if state.get("publication"):
                publication = state["publication"]
                if expected_revision != publication["revision"] or title != publication["name"]:
                    raise BridgeError("ALREADY_PUBLISHED", "이미 등록한 결과입니다. 기존 플랜을 덮어쓰거나 다른 제목으로 중복 등록하지 않습니다.")
                target = self.plans_dir / (publication["plan_id"] + ".json")
                if not target.exists() or self._file_hash(within(target, self.plans_dir)) != publication["sha256"]:
                    raise BridgeError("SAVE_STATE_UNKNOWN", "등록된 플랜이 삭제되었거나 변경되었습니다. 기존 파일을 확인하세요.")
                return copy.deepcopy(publication)
            self._expect(state, expected_revision)
            approval = state.get("approval") or {}
            if approval != {"instance_id": self.instance_id, "revision": expected_revision,
                            "digest": digest(state["plan"]), "name": title}:
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
            # A user-selected export title does not invalidate the reviewed actions.
            plan["name"] = title
            plan["user_verified"] = True
            plan["modified"] = True
            filename = plan["plan_id"] + ".json"
            payload = encoded(plan)
            # Persist intent before filesystem publication so uncertain replies are recoverable.
            intent = self.workspace / (draft_id + ".publish-intent.json")
            expected_intent = {"filename": filename, "sha256": hashlib.sha256(payload).hexdigest()}
            if intent.exists() and read_json(within(intent, self.workspace)) != expected_intent:
                raise BridgeError("SAVE_STATE_UNKNOWN", "이전 등록의 저장 결과를 먼저 확인해야 합니다. 제목이나 내용을 바꾸어 재시도하지 마세요.")
            write_json(intent, expected_intent)
            target = self.plans_dir / filename
            if target.exists():
                if within(target, self.plans_dir).read_bytes() != payload:
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
                   "read_actions", "inspect_action", "draft_status", "record_analysis", "record_crosscheck",
                   "list_recordings", "read_recording", "get_frames", "get_input_events", "create_asset_crop"}
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
