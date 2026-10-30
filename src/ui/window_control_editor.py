"""Shared editor integration; saving a window action saves its owning document."""

from ..utils.json_utils import load_json_file


class WindowControlEditorMixin:
    _window_control_is_plan = True

    def _add_window_control_button(self, parent):
        import customtkinter as ctk
        from .theme import COLORS, IOS_METRICS

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(3, 0))
        ctk.CTkButton(
            row, text="창 제어", command=self._add_window_control_action,
            width=225, height=28, fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"], text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
        ).pack(fill="x")

    def _add_window_control_settings_button(self, parent, item, *, compact=False):
        import customtkinter as ctk
        from .theme import COLORS, IOS_METRICS

        button = ctk.CTkButton(
            parent, text="설정", width=46, height=24 if compact else 26,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
            command=lambda: self._edit_window_control_action(item),
        )
        button.pack(side="right", **({"padx": 3, "pady": 8} if compact else {"padx": (4, 0)}))

    def _persist_window_control_document(self):
        if self._window_control_is_plan:
            from ..analyzer.automation_models import AutomationPlan
            from ..utils.config import DATA_DIR
            from .player_view import PLANS_DIR

            if not self._save_plan(show_message=False):
                return False
            data = load_json_file(PLANS_DIR / f"{self._plan.plan_id}.json")
            reloaded = AutomationPlan.from_dict(data, templates_dir=DATA_DIR / "templates")
            return reloaded.to_dict() == self._plan.to_dict()
        if not self._save_sequence_silent():
            return False
        reloaded = self._db.get_sequence(self._sequence.id)
        return reloaded is not None and [a.to_dict() for a in reloaded.actions] == [
            a.to_dict() for a in self._sequence.actions
        ]

    def _refresh_window_control_item(self, item, *, parent=None, added=False):
        if self._window_control_is_plan:
            if added:
                self._refresh_after_rule_added(parent, item)
            elif not self._update_rule_row_in_place(item):
                self._refresh_rule_row(item.rule_id)
        elif added:
            self._refresh_after_action_added(parent, item)
        elif not self._update_compact_action_row(item):
            self._refresh_action_row(item)

    def _add_window_control_action(self):
        from ..analyzer.automation_models import AutomationRule
        from ..database.models import Action
        from .window_control_dialog import WindowControlDialog

        state = {"item": None}

        def save(config):
            added = state["item"] is None
            parent = None
            if added:
                model = AutomationRule if self._window_control_is_plan else Action
                state["item"] = model(
                    action_type="window_control", description="창 제어",
                    window_control_config=dict(config), wait_after=0.5,
                )
                attach = self._add_rule_to_current_parent if self._window_control_is_plan else self._add_action_to_current_parent
                parent = attach(state["item"])
            item = state["item"]
            item.window_control_config = dict(config)
            self._modified = True
            try:
                verified = self._persist_window_control_document()
            except Exception:
                self._modified = True
                self._refresh_window_control_item(item, parent=parent, added=added)
                raise
            if not verified:
                self._modified = True
            self._refresh_window_control_item(item, parent=parent, added=added)
            if not verified:
                raise OSError("저장 및 재로딩 확인에 실패했습니다. 변경사항은 편집기에 남아 있습니다.")
            return True

        WindowControlDialog(self, on_save=save).get_result()

    def _edit_window_control_action(self, item):
        from .window_control_dialog import WindowControlDialog

        def save(config):
            item.window_control_config = dict(config)
            self._modified = True
            try:
                verified = self._persist_window_control_document()
            except Exception:
                self._modified = True
                self._refresh_window_control_item(item)
                raise
            if not verified:
                self._modified = True
            self._refresh_window_control_item(item)
            if not verified:
                raise OSError("저장 및 재로딩 확인에 실패했습니다. 변경사항은 편집기에 남아 있습니다.")
            return True

        WindowControlDialog(
            self, config=getattr(item, "window_control_config", {}), on_save=save,
        ).get_result()
