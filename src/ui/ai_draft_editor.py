"""Independent AI draft review window; no original editor/player callbacks."""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .theme import COLORS, IOS_FONTS
from ..agent_bridge.contracts import walk
from ..agent_bridge.service import DraftService
from ..agent_bridge.media import MediaTools


class AIDraftEditor(ctk.CTkToplevel):
    def __init__(self, parent, plans_dir, templates_dir, workspace=None):
        super().__init__(parent)
        root = Path(workspace) if workspace else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WinCro" / "ai_editing"
        root = root / hashlib.sha256(str(Path(plans_dir).resolve()).encode()).hexdigest()[:16]
        from ..agent_bridge.transport import protect_directory
        protect_directory(root)
        self.service = DraftService(plans_dir, templates_dir, root / "drafts")
        self.connection_dir = root / "connections"
        self.host = None
        self.closed = False
        self.current = None
        self.serial = -1
        self.busy = False
        self.form_dirty = False
        self.form_error = None
        self.form_context = None
        self._loading_form = False
        self.results = queue.Queue(maxsize=8)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-draft-ui")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.title("AI 플랜 초안 편집")
        self.geometry("1060x760")
        self.minsize(820, 640)
        self.configure(fg_color=COLORS["bg_content"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.status = tk.StringVar(value="연결 OFF · 원본/재생목록 변경 없음 · 실제 실행 없음")
        self._build()
        self._poll_id = self.after(120, self._poll)
        self._refresh_choices()

    def _button(self, parent, text, command, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, height=30,
            fg_color=COLORS["bg_elevated"], text_color=COLORS["text_primary"],
            border_width=2, border_color=COLORS["button_border"], **kwargs)

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="AI 플랜 초안", font=(IOS_FONTS["family"], 18, "bold")).grid(row=0, column=0, sticky="w")
        self._button(top, "연결 ON / OFF", self._toggle, width=140).grid(row=0, column=1)
        ctk.CTkLabel(self, textvariable=self.status, anchor="w", wraplength=780,
                     text_color=COLORS["text_secondary"]).grid(row=1, column=0, sticky="ew", padx=14)
        controls = ctk.CTkFrame(self)
        controls.grid(row=2, column=0, sticky="ew", padx=14, pady=6)
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)
        self.plans = ctk.CTkComboBox(controls, values=["빈 플랜"], state="readonly")
        self.plans.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.drafts = ctk.CTkComboBox(controls, values=["초안 선택"], state="readonly", command=self._select_draft)
        self.drafts.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        self._button(controls, "참고 플랜 복사 / 새 초안", self._new_draft).grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self._button(controls, "녹화 자료 선택", self._select_media).grid(row=1, column=1, sticky="ew", padx=6, pady=(0, 6))
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=14, pady=4)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        tree_frame = ctk.CTkFrame(body)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure("AIDraft.Treeview", background=COLORS["bg_dark"], foreground=COLORS["text_primary"],
                        fieldbackground=COLORS["bg_dark"], rowheight=28)
        self.tree = ttk.Treeview(tree_frame, columns=("type", "wait"), style="AIDraft.Treeview", selectmode="browse")
        self.tree.heading("#0", text="액션 순서 / 이름")
        self.tree.heading("type", text="종류")
        self.tree.heading("wait", text="대기")
        self.tree.column("#0", width=245, minwidth=140)
        self.tree.column("type", width=95, minwidth=60)
        self.tree.column("wait", width=55, minwidth=45)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._selected_rule)
        detail = ctk.CTkScrollableFrame(body, fg_color=COLORS["bg_card"])
        detail.grid(row=0, column=1, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        self.entries = {}
        for index, (key, label) in enumerate((("description", "액션 이름"), ("wait_after", "액션 후 대기 (초)"),
                                              ("repeat_count", "반복 횟수"), ("repeat_delay", "반복 사이 대기 (초)"))):
            ctk.CTkLabel(detail, text=label, anchor="w").grid(row=index * 2, column=0, sticky="ew", padx=6)
            variable = tk.StringVar(master=self)
            variable.trace_add("write", lambda *_: self._dirty())
            entry = ctk.CTkEntry(detail, textvariable=variable)
            entry.grid(row=index * 2 + 1, column=0, sticky="ew", padx=6, pady=(0, 5))
            self.entries[key] = entry
            entry._ai_value = variable
        self._button(detail, "선택 액션에 적용", self._apply_fields).grid(row=8, column=0, sticky="ew", padx=6, pady=6)
        self.details = ctk.CTkTextbox(detail, height=180, wrap="word")
        self.details.grid(row=9, column=0, sticky="ew", padx=6, pady=6)
        self.details.configure(state="disabled")
        ctk.CTkLabel(detail, text="이미지·키·범위·상하위 구성은 MCP로 편집합니다.\n구조 검사는 실제 인식/재생 성공을 의미하지 않습니다.",
                     wraplength=280, justify="left", text_color=COLORS["text_muted"]).grid(row=10, column=0, sticky="ew", padx=6)
        self._button(detail, "미적용 입력 취소 / 최신값 읽기", self._reload_form).grid(row=11, column=0, sticky="ew", padx=6, pady=6)
        self._button(detail, "AI 행동 분석 요약 보기", self._show_analysis).grid(row=12, column=0, sticky="ew", padx=6, pady=6)
        self._button(detail, "대상 이미지 미리보기", self._preview_asset).grid(row=13, column=0, sticky="ew", padx=6, pady=6)
        self.preview = ctk.CTkLabel(detail, text="등록된 PNG 이미지를 확인할 수 있습니다.", wraplength=270)
        self.preview.grid(row=14, column=0, sticky="ew", padx=6, pady=6)
        self.preview_image = None
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=14, pady=(6, 12))
        for column in range(3):
            bottom.grid_columnconfigure(column, weight=1)
        self._button(bottom, "초안 저장 / 재확인", self._save).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._button(bottom, "구조·자산 검사", self._validate).grid(row=0, column=1, sticky="ew", padx=5)
        self._button(bottom, "승인 후 새 플랜 등록", self._publish).grid(row=0, column=2, sticky="ew", padx=(5, 0))

    def _submit(self, job, done=None):
        if self.busy or self.closed:
            return
        self.busy = True

        def work():
            try:
                result, error = job(), None
            except Exception as exc:
                result, error = None, f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
            if not self.closed:
                self.results.put((result, error, done))

        self.worker.submit(work)

    def _poll(self):
        if self.closed:
            return
        try:
            result, error, done = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                if self.form_dirty:
                    self.form_error = error
                self.status.set(error)
            elif done:
                done(result)
            self._refresh_choices()
        if not self.busy and self.service.change_serial != self.serial and self.service.lock.acquire(blocking=False):
            try:
                self.serial = self.service.change_serial
                if self.current:
                    self._render(self.service.read_draft(self.current["draft_id"]))
                self._refresh_choices()
            finally:
                self.service.lock.release()
        self._poll_id = self.after(120, self._poll)

    def _refresh_choices(self):
        if not self.service.lock.acquire(blocking=False):
            return
        try:
            self._refresh_choices_locked()
        finally:
            self.service.lock.release()

    def _refresh_choices_locked(self):
        self.plan_options = {"빈 플랜": None}
        for row in self.service.list_plans():
            self.plan_options[row["name"] + " · " + row["plan_id"]] = row["plan_id"]
        self.plans.configure(values=list(self.plan_options))
        self.draft_options = {row["name"] + " · " + row["draft_id"][-8:]: row["draft_id"] for row in self.service.list_drafts()}
        self.drafts.configure(values=list(self.draft_options) or ["초안 선택"])

    def _new_draft(self):
        if self.form_dirty:
            self.status.set("미적용 입력을 먼저 적용하거나 취소하세요.")
            return
        dialog = ctk.CTkInputDialog(text="새 초안 이름", title="AI 초안 생성")
        name = dialog.get_input()
        if name:
            plan_id = self.plan_options.get(self.plans.get())
            self._submit(lambda: self.service.open_draft(name, plan_id), self._render)

    def _select_draft(self, choice):
        if self.form_dirty:
            self.status.set("미적용 입력을 먼저 적용하거나 취소하세요.")
            return
        if choice in self.draft_options and not self.busy:
            key = self.draft_options[choice]
            self._submit(lambda: self.service.read_draft(key), self._render)

    def _render(self, state):
        self.current = state
        selected = self.tree.selection()
        rows = list(walk(state["plan"]))
        desired = [r["rule_id"] for r, *_ in rows]
        existing = []

        def collect(parent=""):
            for item in self.tree.get_children(parent):
                existing.append(item)
                collect(item)
        collect()
        if existing != desired:
            # Move/reuse rows by stable ID; preserve expanded/selected/scroll state.
            wanted = set(desired)
            for item in reversed(existing):
                if item not in wanted and self.tree.exists(item):
                    self.tree.delete(item)
        for rule, _, parent, path in rows:
            key = rule["rule_id"]
            position = int(path.split("-")[-1]) - 1
            if self.tree.exists(key):
                self.tree.move(key, parent or "", position)
                self.tree.item(key, text=f"{path}  {rule.get('description') or rule.get('action_type')}",
                               values=(rule.get("action_type"), rule.get("wait_after", .5)))
            else:
                self.tree.insert(parent or "", position, iid=key, text=f"{path}  {rule.get('description') or rule.get('action_type')}",
                                 values=(rule.get("action_type"), rule.get("wait_after", .5)), open=True)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.status.set(f"초안: {state['plan']['name']} · 버전 {state['revision']} · {len(rows)}개 액션 · 실제 실행 없음")
        self._selected_rule()

    def _selected_rule(self, _event=None):
        if not self.current or not self.tree.selection():
            return
        if self.form_dirty:
            self.status.set(self.form_error or "미적용 입력 보관 중 · 적용 시 편집 당시 버전으로 충돌 검사합니다.")
            return
        key = self.tree.selection()[0]
        rule = next((r for r, *_ in walk(self.current["plan"]) if r["rule_id"] == key), None)
        if rule:
            self.form_context = (self.current["draft_id"], self.current["revision"], key)
            self._loading_form = True
            try:
                for field, entry in self.entries.items():
                    entry.delete(0, "end")
                    entry.insert(0, str(rule.get(field, "")))
            finally:
                self._loading_form = False
            safe = {k: rule.get(k) for k in ("rule_id", "action_type", "target_image", "search_region", "action_keys", "wait_random", "wait_random_range")}
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", json.dumps(safe, ensure_ascii=False, indent=2))
            self.details.configure(state="disabled")

    def _apply_fields(self):
        if not self.current or not self.tree.selection():
            return
        from ..agent_bridge.storage import new_id
        try:
            fields = {"description": self.entries["description"].get(),
                      "wait_after": float(self.entries["wait_after"].get()),
                      "repeat_count": int(self.entries["repeat_count"].get()),
                      "repeat_delay": float(self.entries["repeat_delay"].get())}
        except ValueError:
            self.status.set("대기는 숫자, 반복 횟수는 정수로 입력하세요.")
            return
        if not self.form_context:
            return
        draft_id, revision, key = self.form_context
        def applied(_):
            self.form_dirty = False
            self.form_error = None
        self._submit(lambda: self.service.stage_edits(draft_id, revision, new_id("ui_"), [{"op": "update_rule", "rule_id": key, "fields": fields}]), applied)

    def _dirty(self, _event=None):
        if self._loading_form:
            return
        self.form_dirty = True
        self.form_error = None

    def _reload_form(self):
        self.form_dirty = False
        self.form_error = None
        self._selected_rule()

    def _preview_asset(self):
        if not self.current or not self.tree.selection():
            return
        key = self.tree.selection()[0]
        state = copy.deepcopy(self.current)
        rule = next(r for r, *_ in walk(state["plan"]) if r["rule_id"] == key)
        name = rule.get("target_image")
        if not name or not name.lower().endswith(".png"):
            self.status.set("이 미리보기는 PNG 대상 이미지용입니다. 영상 장면은 MCP get_frames로 확인하세요.")
            return

        def load():
            from PIL import Image
            path = self.service._asset_path(state, name)
            with Image.open(path) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError("이미지가 너무 큽니다.")
                result = image.convert("RGB")
                result.thumbnail((280, 160))
                return result

        def show(image):
            if self.tree.selection() and self.tree.selection()[0] == key:
                self.preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                self.preview.configure(image=self.preview_image, text="")
        self._submit(load, show)

    def _save(self):
        if self.form_dirty:
            self.status.set("현재 입력은 아직 미적용 상태입니다. 먼저 적용하거나 취소하세요.")
            return
        if self.current:
            draft_id, revision = self.current["draft_id"], self.current["revision"]
            self._submit(lambda: self.service.save_draft(draft_id, revision), lambda _: self.status.set("초안 저장·재로딩 확인 완료 (정식 등록과 별개)"))

    def _validate(self):
        if self.current:
            draft_id = self.current["draft_id"]
            self._submit(lambda: self.service.validate_draft(draft_id), lambda result: messagebox.showinfo("구조 검사", json.dumps(result, ensure_ascii=False, indent=2), parent=self))

    def _show_analysis(self):
        if not self.current:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("AI 행동 분석 요약")
        dialog.geometry("700x500")
        text = ctk.CTkTextbox(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", json.dumps(self.current.get("analysis") or {"status": "AI 분석 미등록"}, ensure_ascii=False, indent=2))
        text.configure(state="disabled")

    def _publish(self):
        if not self.current or self.busy or self.form_dirty:
            return
        draft_id, revision = self.current["draft_id"], self.current["revision"]
        if not messagebox.askyesno("새 플랜 등록 승인", f"'{self.current['plan']['name']}' 버전 {revision}을 새 플랜으로 등록할까요?\n원본/재생목록은 바꾸지 않습니다. 실제 인식·재생 검증은 별도입니다.", parent=self):
            return

        def publish():
            self.service.approve_publication(draft_id, revision)
            return self.service.publish_new_plan(draft_id, revision)
        self._submit(publish, lambda r: messagebox.showinfo("새 플랜 등록", f"저장·재로딩 확인 완료\n{r['name']}\n{r['plan_id']}\n재생목록에는 자동 추가하지 않았습니다.", parent=self))

    def _select_media(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, title="AI에 제공할 녹화 또는 이미지 선택", filetypes=[("녹화/이미지", "*.mp4 *.avi *.mkv *.webm *.png")])
        if not path:
            return
        if not messagebox.askyesno("자료 제공 동의", "선택한 장면과 입력 로그는 AI 분석에 제공될 수 있습니다.\n비밀번호·인증코드가 없는 자료인지 확인하세요.\n다음 단계에서 비공개 시간 구간을 제외할 수 있습니다. 계속할까요?", parent=self):
            return
        dialog = ctk.CTkInputDialog(text="제외할 시간 구간 (초). 예: 0-5,12-18\n없으면 비워두세요.", title="비공개 구간")
        raw = dialog.get_input()
        if raw is None:
            return
        try:
            excluded = [[float(n) for n in part.strip().split("-")] for part in raw.split(",") if part.strip()]
        except ValueError:
            self.status.set("비공개 구간 형식이 올바르지 않습니다.")
            return
        log = None
        if messagebox.askyesno("입력 로그", "선택한 녹화의 입력 로그도 제공할까요?\n입력한 비밀번호가 포함될 수 있으므로 반드시 확인하세요.", parent=self):
            log = filedialog.askopenfilename(parent=self, title="입력 로그 JSON 선택", filetypes=[("입력 로그", "*.json")]) or None
        self._submit(lambda: MediaTools(self.service).register(path, log, excluded),
                     lambda r: messagebox.showinfo("자료 선택 완료", f"{r['name']}\nrecording_id: {r['recording_id']}\n기존 녹화의 시간·좌표 연결은 별도 확인이 필요합니다.", parent=self))

    def _toggle(self):
        if self.busy:
            return
        if self.host:
            self.host.close()
            self.host = None
            self.status.set("연결 OFF · 이전 연결 정보는 폐기했습니다.")
            return
        from ..agent_bridge.transport import LocalHost
        selected = self.plan_options.get(self.plans.get())

        def start():
            with self.service.lock:
                self.service.granted_sources = {selected} if selected else set()
            host = LocalHost(self.service, self.connection_dir)
            try:
                path = host.start()
                if self.closed:
                    host.close()
                    return None
            except Exception:
                self.service.closed = False
                raise
            self.host = host
            return path
        self._submit(start, lambda path: messagebox.showinfo("MCP 연결 ON", "MCP 설정의 --connection-file에 아래 파일 경로를 지정하세요.\n\n" + path + "\n\n이 파일에는 인증 정보가 있으므로 공유하지 마세요.", parent=self))

    def _close(self):
        if self.closed:
            return
        if self.form_dirty and not messagebox.askyesno("미적용 입력", "아직 적용하지 않은 입력을 버리고 닫을까요? 저장된 초안은 보관됩니다.", parent=self):
            return
        self._shutdown()
        self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self._shutdown()

    def _shutdown(self):
        if self.closed:
            return
        self.closed = True
        if self.host:
            self.host.close()
        self.service.closed = True
        self.worker.shutdown(wait=False, cancel_futures=True)
        try:
            self.after_cancel(self._poll_id)
        except tk.TclError:
            pass


def open_ai_draft_editor(parent):
    from ..utils.config import DATA_DIR
    existing = getattr(parent, "_ai_draft_editor", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return existing
    editor = AIDraftEditor(parent, DATA_DIR / "plans", DATA_DIR / "templates")
    parent._ai_draft_editor = editor
    return editor
