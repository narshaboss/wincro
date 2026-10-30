"""AST-only editor integration with headless dialogs and temporary JSON/SQLite.

No application module is imported or GUI instantiated. Persistence tests execute
only in remote CI and reopen the actual temporary JSON/database after Save.
"""

import __future__
import ast
import builtins
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = "src/ui/player_view.py"
CONFIG = {"executable_name": "client.exe", "title_contains": "Test client", "operation": "maximize"}


def _tree(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))


def _load(path, names, namespace=None, *, owner=None, imports=None):
    namespace = {} if namespace is None else namespace
    namespace.setdefault("__name__", __name__)
    imports = {} if imports is None else imports

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (level, name) in imports:
            return imports[level, name]
        if level == 0 and name in {"copy", "uuid"}:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise AssertionError(f"Unmocked import in AST harness: {level}:{name}")

    namespace["__builtins__"] = dict(vars(builtins), __import__=safe_import)
    body = _tree(path).body
    if owner is not None:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == owner).body
    selected, found = [], set()
    for node in body:
        node_names = set()
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            node_names.add(node.name)
        elif isinstance(node, ast.Assign):
            node_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if node_names.intersection(names):
            selected.append(copy.deepcopy(node))
            found.update(node_names.intersection(names))
    assert found == set(names), f"Missing pending-integration API in {path}: {set(names) - found}"
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / path), "exec",
                 flags=__future__.annotations.compiler_flag, dont_inherit=True), namespace)
    return namespace


class Item:
    """Minimal model double; serialization includes nested settings and children."""
    def __init__(self, **fields):
        defaults = dict(
            rule_id=f"item-{id(self)}", action_type="click", description="existing action",
            window_control_config={}, wait_after=0.8, parent_id=None, children=[],
        )
        defaults.update(fields)
        self.__dict__.update(copy.deepcopy(defaults))
        self.action_id = fields.get("action_id", self.rule_id)
        self.children = [Item(**c) if isinstance(c, dict) else c for c in self.children]

    def to_dict(self):
        return {**copy.deepcopy({k: v for k, v in vars(self).items() if k != "children"}),
                "children": [child.to_dict() for child in self.children]}


class Plan:
    def __init__(self, initial_rules=None, **fields):
        self.plan_id = fields.get("plan_id", "test-plan")
        self.name = fields.get("name", "Original plan")
        self.description = fields.get("description", "Unrelated document settings")
        self.total_repeat_count = fields.get("total_repeat_count", 7)
        self.modified = fields.get("modified", False)
        self.initial_rules = initial_rules or []
        self.monitoring_rules = []
        self.game_modes = {}

    def to_dict(self):
        return dict(plan_id=self.plan_id, name=self.name, description=self.description,
                    total_repeat_count=self.total_repeat_count, modified=self.modified,
                    initial_rules=[r.to_dict() for r in self.initial_rules], monitoring_rules=[], game_modes={})

    @classmethod
    def from_dict(cls, data, **kwargs):
        values = copy.deepcopy(data)
        values["initial_rules"] = [Item(**r) for r in values["initial_rules"]]
        return cls(**values)


class Sequence:
    def __init__(self, actions=None, **fields):
        self.id = fields.get("id", 23)
        self.name = fields.get("name", "Original sequence")
        self.description = fields.get("description", "Unrelated sequence settings")
        self.repeat_count = fields.get("repeat_count", 7)
        self.actions = actions or []

    def to_dict(self):
        return dict(id=self.id, name=self.name, description=self.description,
                    repeat_count=self.repeat_count, actions=[a.to_dict() for a in self.actions])


def _corrupt_readback(data, fault):
    data = copy.deepcopy(data)
    if fault == "mismatch_settings":
        data["name"] = "Wrong document name"
        key = "total_repeat_count" if "initial_rules" in data else "repeat_count"
        data[key] += 1
    elif fault == "mismatch_action":
        roots = data.get("initial_rules", data.get("actions", []))

        def change(nodes):
            for node in nodes:
                if node["action_type"] == "window_control":
                    node["window_control_config"]["operation"] = "minimize"
                    return True
                if change(node["children"]):
                    return True
            return False

        assert change(roots)
    return data


class DurableSequenceDB:
    """Reopen SQLite for each call, rather than returning the in-memory model."""
    def __init__(self, path, state):
        self.path, self.state = path, state
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE sequences (id INTEGER PRIMARY KEY, document TEXT)")
            connection.commit()

    def update_sequence(self, sequence):
        self.state.writes += 1
        if self.state.fault == "write":
            raise OSError("simulated database write failure")
        data = _corrupt_readback(sequence.to_dict(), self.state.fault)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT OR REPLACE INTO sequences VALUES (?, ?)",
                               (sequence.id, json.dumps(data)))
            connection.commit()

    def get_sequence(self, sequence_id):
        self.state.reads.append(("db", sequence_id))
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT document FROM sequences WHERE id = ?", (sequence_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        data["actions"] = [Item(**a) for a in data["actions"]]
        return Sequence(**data)


@pytest.fixture(params=["plan", "sequence"])
def editor(request, tmp_path):
    is_plan = request.param == "plan"
    state = SimpleNamespace(fault=None, writes=0, reads=[])
    # Deliberately differ from DATA_DIR/plans to catch wrong readback paths.
    plans_dir = tmp_path / "artifacts" / "window_control"
    dialogs = []

    class Dialog:
        def __init__(self, owner, **kwargs):
            dialogs.append(SimpleNamespace(owner=owner, **kwargs))

        def get_result(self):
            return None

    def write_json(path, data, **kwargs):
        state.writes += 1
        if state.fault == "write":
            raise OSError("simulated JSON write failure")
        path.write_text(json.dumps(data), encoding="utf-8")

    def read_json(path):
        state.reads.append(Path(path))
        return json.loads(Path(path).read_text(encoding="utf-8"))

    namespace = {"load_json_file": read_json}
    imports = {
        (2, "analyzer.automation_models"): SimpleNamespace(AutomationRule=Item, AutomationPlan=Plan),
        (2, "database.models"): SimpleNamespace(Action=Item),
        (1, "window_control_dialog"): SimpleNamespace(WindowControlDialog=Dialog),
        (2, "utils.config"): SimpleNamespace(DATA_DIR=tmp_path / "different-data-dir"),
        (1, "player_view"): SimpleNamespace(PLANS_DIR=plans_dir),
    }
    names = {"_add_window_control_action", "_edit_window_control_action",
             "_persist_window_control_document", "_refresh_window_control_item"}
    _load("src/ui/window_control_editor.py", names, namespace,
          owner="WindowControlEditorMixin", imports=imports)
    host = type("EditorHarness", (), {name: namespace[name] for name in names})()
    host._window_control_is_plan = is_plan
    host._modified = False
    parent = Item(rule_id="parent", action_id="parent", description="unrelated parent")
    sibling = Item(rule_id="sibling", action_id="sibling", action_type="hotkey", keys=["ctrl", "a"])
    host._plan = Plan([parent, sibling])
    host._sequence = Sequence([parent, sibling])
    host._db = DurableSequenceDB(tmp_path / "sequences.sqlite", state)
    for name in ("_refresh_after_rule_added", "_refresh_after_action_added", "_refresh_rule_row",
                 "_refresh_action_row", "_invalidate_rule_tree_cache", "_invalidate_action_tree_cache"):
        setattr(host, name, Mock())
    host._update_rule_row_in_place = Mock(return_value=False)
    host._update_compact_action_row = Mock(return_value=False)

    def attach(item):
        item.parent_id = parent.rule_id if is_plan else parent.action_id
        parent.children.append(item)
        return parent

    host._add_rule_to_current_parent = Mock(side_effect=attach)
    host._add_action_to_current_parent = Mock(side_effect=attach)
    save_globals = {"PLANS_DIR": plans_dir, "dump_json_file": write_json,
                    "load_json_file": read_json, "logger": Mock()}
    save_plan = _load(PLAYER_VIEW, {"_save_plan"}, save_globals, owner="PlanDetailDialog")["_save_plan"]
    sequence_methods = _load(PLAYER_VIEW, {"_save_sequence_silent", "_save_sequence_with_options"},
                             {"logger": Mock()}, owner="SequenceDetailDialog")
    host._save_sequence_with_options = sequence_methods["_save_sequence_with_options"].__get__(host)
    host._save_sequence_silent = sequence_methods["_save_sequence_silent"].__get__(host)
    host._name_entry = SimpleNamespace(get=lambda: "")

    def save_plan_then_external_change(*, show_message):
        assert show_message is False
        saved = save_plan(host, show_message=False)
        if saved and state.fault in {"mismatch_action", "mismatch_settings"}:
            path = plans_dir / f"{host._plan.plan_id}.json"
            data = _corrupt_readback(json.loads(path.read_text(encoding="utf-8")), state.fault)
            path.write_text(json.dumps(data), encoding="utf-8")
        return saved

    host._save_plan = save_plan_then_external_change

    def reload_document():
        if is_plan:
            path = plans_dir / f"{host._plan.plan_id}.json"
            return Plan.from_dict(read_json(path))
        return host._db.get_sequence(host._sequence.id)

    return SimpleNamespace(host=host, parent=parent, sibling=sibling, state=state, dialogs=dialogs,
                           is_plan=is_plan, plans_dir=plans_dir, reload=reload_document,
                           document=host._plan if is_plan else host._sequence)


def test_cancel_without_save_does_not_add_or_persist_any_item(editor):
    before = editor.document.to_dict()
    editor.host._add_window_control_action()
    assert len(editor.dialogs) == 1
    assert editor.document.to_dict() == before
    assert not editor.parent.children and not editor.host._modified
    assert editor.state.writes == 0 and not editor.state.reads
    editor.host._add_rule_to_current_parent.assert_not_called()
    editor.host._add_action_to_current_parent.assert_not_called()


def test_nested_add_save_updates_model_writes_and_reloads_without_outer_save(editor):
    sibling_before = editor.sibling.to_dict()
    editor.host._add_window_control_action()
    config = dict(CONFIG)
    assert editor.dialogs[-1].on_save(config) is True
    assert len(editor.parent.children) == 1
    item = editor.parent.children[0]
    assert item.window_control_config == CONFIG and item.window_control_config is not config
    assert item.action_type == "window_control" and item.parent_id == "parent"
    assert editor.sibling.to_dict() == sibling_before
    assert editor.reload().to_dict() == editor.document.to_dict()
    assert not editor.host._modified and editor.state.writes == 1
    if editor.is_plan:
        expected_path = editor.plans_dir / f"{editor.document.plan_id}.json"
        assert editor.state.reads and all(path == expected_path for path in editor.state.reads)
    config["title_contains"] = "caller mutated"
    assert item.window_control_config == CONFIG


@pytest.mark.parametrize("fault", ["write", "mismatch_action", "mismatch_settings"])
def test_failed_unsaved_add_can_retry_without_duplicate_item_or_false_success(editor, fault):
    editor.state.fault = fault
    editor.host._add_window_control_action()
    save = editor.dialogs[-1].on_save
    with pytest.raises(OSError):
        save(dict(CONFIG))
    assert editor.host._modified and len(editor.parent.children) == 1
    first_item = editor.parent.children[0]
    attach = editor.host._add_rule_to_current_parent if editor.is_plan else editor.host._add_action_to_current_parent
    attach.assert_called_once_with(first_item)
    assert first_item.window_control_config == CONFIG

    editor.state.fault = None
    corrected = {**CONFIG, "title_contains": "Updated after failure", "operation": "foreground"}
    assert save(corrected) is True
    assert len(editor.parent.children) == 1 and editor.parent.children[0] is first_item
    attach.assert_called_once_with(first_item)
    assert first_item.window_control_config == corrected
    assert editor.reload().to_dict() == editor.document.to_dict()
    assert not editor.host._modified and editor.state.writes == 2


@pytest.mark.parametrize("fault", ["mismatch_action", "mismatch_settings"])
def test_edit_rejects_readback_mismatch_and_preserves_unsaved_state_for_retry(editor, fault):
    item = Item(action_type="window_control", window_control_config=CONFIG, parent_id="parent")
    editor.parent.children.append(item)
    unrelated_before = editor.sibling.to_dict()
    editor.host._edit_window_control_action(item)
    dialog = editor.dialogs[-1]
    assert dialog.config == CONFIG and editor.state.writes == 0
    editor.state.fault = fault
    replacement = {**CONFIG, "title_contains": "Edited client"}
    with pytest.raises(OSError):
        dialog.on_save(replacement)
    assert editor.host._modified and item.window_control_config == replacement
    assert editor.sibling.to_dict() == unrelated_before
    editor.state.fault = None
    assert dialog.on_save(replacement) is True
    assert editor.parent.children == [item]
    assert editor.reload().to_dict() == editor.document.to_dict()
    editor.host._add_rule_to_current_parent.assert_not_called()
    editor.host._add_action_to_current_parent.assert_not_called()


def test_cancel_edit_leaves_existing_config_untouched(editor):
    item = Item(action_type="window_control", window_control_config=CONFIG)
    editor.parent.children.append(item)
    before = editor.document.to_dict()
    editor.host._edit_window_control_action(item)
    assert editor.document.to_dict() == before and not editor.host._modified
    assert editor.state.writes == 0 and not editor.state.reads


def test_exception_during_persistence_keeps_one_item_and_retryable_callback(editor):
    original = editor.host._persist_window_control_document
    editor.host._persist_window_control_document = Mock(side_effect=OSError("readback unavailable"))
    editor.host._add_window_control_action()
    callback = editor.dialogs[-1].on_save
    with pytest.raises(OSError, match="readback unavailable"):
        callback(dict(CONFIG))
    assert editor.host._modified and len(editor.parent.children) == 1
    editor.host._persist_window_control_document = original
    assert callback(dict(CONFIG)) is True
    assert len(editor.parent.children) == 1
    assert editor.reload().to_dict() == editor.document.to_dict()


@pytest.mark.parametrize("owner,id_attr,copy_method,paste_child,paste_top", [
    ("PlanDetailDialog", "rule_id", "_copy_rule", "_paste_rule", "_paste_rule_top"),
    ("SequenceDetailDialog", "action_id", "_copy_action", "_paste_action", "_paste_action_top"),
])
@pytest.mark.parametrize("as_child", [False, True])
def test_deep_copy_and_paste_preserve_nested_window_settings_without_aliasing(
    owner, id_attr, copy_method, paste_child, paste_top, as_child,
):
    clipboard = [None]
    namespace = _load(PLAYER_VIEW, {copy_method, paste_child, paste_top}, {
        "logger": Mock(), "get_action_clipboard": lambda: clipboard[0],
        "set_action_clipboard": lambda value: clipboard.__setitem__(0, value),
    }, owner=owner)

    def node(node_id, action_type, **kwargs):
        return SimpleNamespace(**{id_attr: node_id, "action_type": action_type,
                                  "parent_id": None, "children": [], "window_control_config": {}, **kwargs})

    nested = node("nested", "window_control", window_control_config={**CONFIG, "operation": "minimize"})
    normal = node("normal", "key_press", keys=["enter"], children=[nested])
    source = node("source", "window_control", window_control_config=dict(CONFIG), children=[normal])
    before = copy.deepcopy(source)
    parent = node("parent", "click")
    host = SimpleNamespace(
        _plan=SimpleNamespace(initial_rules=[], game_modes={}), _sequence=SimpleNamespace(actions=[]),
        _collapsed_items={"parent"}, _modified=False, _invalidate_rule_tree_cache=Mock(),
        _invalidate_action_tree_cache=Mock(), _refresh_after_rule_added=Mock(), _refresh_after_action_added=Mock(),
    )
    namespace[copy_method](host, source)
    copied = clipboard[0]
    for _ in range(2):
        if as_child:
            namespace[paste_child](host, parent)
        else:
            namespace[paste_top](host)
    pasted = parent.children if as_child else (host._plan.initial_rules if owner == "PlanDetailDialog" else host._sequence.actions)
    assert len(pasted) == 2 and host._modified
    for item in pasted:
        assert item.window_control_config == CONFIG
        assert item.window_control_config is not copied.window_control_config
        assert item.children[0].action_type == "key_press" and item.children[0].keys == ["enter"]
        assert item.children[0].children[0].window_control_config == nested.window_control_config
        assert item.parent_id == ("parent" if as_child else None)
        assert item.children[0].parent_id == getattr(item, id_attr)
        assert item.children[0].children[0].parent_id == getattr(item.children[0], id_attr)
    ids = [getattr(item, id_attr) for root in [source, copied, *pasted]
           for item in [root, root.children[0], root.children[0].children[0]]]
    assert len(set(ids)) == 9, "The clipboard keeps source IDs; each paste needs three fresh IDs"
    pasted[0].window_control_config["title_contains"] = "changed"
    pasted[0].children[0].children[0].window_control_config["operation"] = "foreground"
    assert pasted[1].window_control_config == CONFIG
    assert pasted[1].children[0].children[0].window_control_config == nested.window_control_config
    assert source == before and copied == before


def _monitor_helpers():
    namespace = _load("src/ui/constants.py", {"collect_all_actions"})
    return _load(PLAYER_VIEW, {"_reject_window_control_monitor_paste", "_convert_action_to_monitor_dict"}, {
        "collect_all_actions": namespace["collect_all_actions"], "WINDOW_CONTROL_ACTION_TYPE": "window_control",
        "messagebox": SimpleNamespace(showwarning=Mock()),
    })


@pytest.mark.parametrize("depth", [0, 1, 3])
def test_monitor_clipboard_rejects_window_control_even_when_deeply_nested(depth):
    namespace = _monitor_helpers()
    clipboard = SimpleNamespace(action_type="window_control", children=[])
    for _ in range(depth):
        clipboard = SimpleNamespace(action_type="click", children=[clipboard])
    before = copy.deepcopy(clipboard)
    owner = object()
    assert namespace["_reject_window_control_monitor_paste"](owner, clipboard) is True
    namespace["messagebox"].showwarning.assert_called_once()
    assert namespace["messagebox"].showwarning.call_args.kwargs["parent"] is owner
    assert clipboard == before


def test_monitor_clipboard_allows_normal_nested_actions_without_warning():
    namespace = _monitor_helpers()
    clipboard = SimpleNamespace(action_type="click", children=[SimpleNamespace(action_type="key_press", children=[])])
    assert namespace["_reject_window_control_monitor_paste"](object(), clipboard) is False
    namespace["messagebox"].showwarning.assert_not_called()


@pytest.mark.parametrize("target_image", [None, "stray-imported-image.png"])
def test_window_control_monitor_conversion_never_falls_back_to_click_zero(target_image):
    # Deliberately omit action_x/action_y: rejecting must happen before click fallback.
    action = SimpleNamespace(action_type="window_control", target_image=target_image, window_control_config=CONFIG)
    assert _monitor_helpers()["_convert_action_to_monitor_dict"](action) is None


def test_normal_monitor_conversion_preserves_coordinates_and_timing():
    action = SimpleNamespace(action_type="click", target_image=None, action_x=17, action_y=29,
                             wait_after=0.7, repeat_count=3)
    result = _monitor_helpers()["_convert_action_to_monitor_dict"](action)
    assert result["x"] == 17 and result["y"] == 29 and result["click_type"] == "click"
    assert result["wait_after"] == 0.7 and result["repeat_count"] == 3


def test_all_monitor_paste_entrypoints_reject_before_mutation():
    expected = {
        "PlanDetailDialog": {"_paste_as_monitor_action", "_paste_as_monitoring_watch"},
        "SequenceDetailDialog": {"_paste_as_monitor_action_seq", "_paste_as_monitoring_watch_action"},
    }
    for cls in (n for n in _tree(PLAYER_VIEW).body if isinstance(n, ast.ClassDef) and n.name in expected):
        for name in expected[cls.name]:
            method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
            namespace = _load(PLAYER_VIEW, {name}, {
                "get_action_clipboard": lambda: object(),
                "_reject_window_control_monitor_paste": Mock(return_value=True),
            }, owner=cls.name)
            args = (0, 0) if name in {"_paste_as_monitor_action", "_paste_as_monitor_action_seq"} else (object(),)
            namespace[name](object(), *args)
            namespace["_reject_window_control_monitor_paste"].assert_called_once()


def test_metadata_has_window_control_names_colors_without_changing_existing_actions():
    theme = _load("src/ui/theme.py", {"COLORS"})
    constants = _load("src/ui/constants.py", {"ACTION_NAMES", "ACTION_NAMES_SHORT", "ACTION_COLORS"}, theme)
    assert constants["ACTION_NAMES"]["window_control"] == "\ucc3d \uc81c\uc5b4"
    assert constants["ACTION_NAMES_SHORT"]["window_control"] == "\ucc3d \uc81c\uc5b4"
    assert constants["ACTION_COLORS"]["window_control"] in constants["COLORS"].values()
    for action_type, color in (("click", "accent_blue"), ("hotkey", "accent_orange"), ("wait", "text_muted")):
        assert constants["ACTION_NAMES"][action_type]
        assert constants["ACTION_COLORS"][action_type] == constants["COLORS"][color]


def test_both_editors_use_mixin_header_and_all_four_settings_render_paths():
    tree = _tree(PLAYER_VIEW)
    counts = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in {"PlanDetailDialog", "SequenceDetailDialog"}):
        assert any(isinstance(base, ast.Name) and base.id == "WindowControlEditorMixin" for base in cls.bases)
        calls = [n for n in ast.walk(cls) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        headers = [n for n in calls if n.func.attr == "_add_window_control_button"]
        settings = [n for n in calls if n.func.attr == "_add_window_control_settings_button"]
        assert len(headers) == 1
        counts[cls.name] = len(settings)
        assert any(any(k.arg == "compact" and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in n.keywords) for n in settings)
    assert counts == {"PlanDetailDialog": 2, "SequenceDetailDialog": 2}


@pytest.mark.parametrize("compact", [False, True])
def test_mixin_buttons_bind_headless_add_and_edit_callbacks(compact):
    theme = _load("src/ui/theme.py", {"COLORS", "IOS_METRICS"})
    widgets = SimpleNamespace(CTkFrame=Mock(), CTkButton=Mock(), CTkFont=Mock())
    namespace = _load("src/ui/window_control_editor.py", {
        "_add_window_control_button", "_add_window_control_settings_button",
    }, owner="WindowControlEditorMixin", imports={
        (0, "customtkinter"): widgets,
        (1, "theme"): SimpleNamespace(**theme),
    })
    host = SimpleNamespace(_add_window_control_action=Mock(), _edit_window_control_action=Mock())
    parent, item = object(), object()
    namespace["_add_window_control_button"](host, parent)
    add_command = widgets.CTkButton.call_args.kwargs["command"]
    host._add_window_control_action.assert_not_called()
    add_command()
    host._add_window_control_action.assert_called_once_with()
    namespace["_add_window_control_settings_button"](host, parent, item, compact=compact)
    edit_command = widgets.CTkButton.call_args.kwargs["command"]
    host._edit_window_control_action.assert_not_called()
    edit_command()
    host._edit_window_control_action.assert_called_once_with(item)


@pytest.mark.parametrize("method", ["_edit_trigger_image", "_edit_monitoring_mode"])
def test_plan_window_trigger_and_monitor_handlers_return_before_dialog_or_save(method):
    namespace = _load(PLAYER_VIEW, {method}, {"WINDOW_CONTROL_ACTION_TYPE": "window_control"},
                      owner="PlanDetailDialog")
    rule = SimpleNamespace(
        action_type="window_control", window_control_config=dict(CONFIG), trigger_image="stale-trigger.png",
        is_monitoring_mode=True, monitoring_watches=[{"image": "stale-watch.png"}],
    )
    host = SimpleNamespace(_modified=False, _save_plan=Mock(side_effect=AssertionError("Unexpected save")))
    before = copy.deepcopy(rule)
    # The AST loader rejects every unmocked import, so removing the early guard
    # fails before any real tkinter or monitoring editor can be imported.
    assert namespace[method](host, rule) is None
    assert rule == before and not host._modified
    host._save_plan.assert_not_called()


@pytest.mark.parametrize("method", ["_create_compact_rule_item", "_create_action_item"])
@pytest.mark.parametrize("label", ["M", "c"])
def test_plan_monitor_and_trigger_buttons_disable_only_window_actions(method, label):
    cls = next(node for node in _tree(PLAYER_VIEW).body
               if isinstance(node, ast.ClassDef) and node.name == "PlanDetailDialog")
    function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
    buttons = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "CTkButton"
        and any(keyword.arg == "text" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == label for keyword in node.keywords)
    ]
    assert len(buttons) == 1, f"Expected one {label} button in {method}"
    states = [keyword.value for keyword in buttons[0].keywords if keyword.arg == "state"]
    assert len(states) == 1, f"{method} must explicitly guard its {label} button"
    # Evaluate only the state expression, never a widget constructor or renderer.
    expression = compile(ast.Expression(body=copy.deepcopy(states[0])), str(ROOT / PLAYER_VIEW), "eval")
    for action_type, expected in (("window_control", "disabled"), ("click", "normal"), ("hotkey", "normal")):
        actual = eval(expression, {"__builtins__": {}, "WINDOW_CONTROL_ACTION_TYPE": "window_control"},
                      {"rule": SimpleNamespace(action_type=action_type)})
        assert actual == expected
