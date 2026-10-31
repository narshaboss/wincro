"""Recording selection, automatic GPT jobs and independent draft review."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from ..agent_bridge.codex_runner import default_runtime
from ..agent_bridge.contracts import walk
from ..agent_bridge.recording_jobs import RecordingJobs
from ..agent_bridge.storage import new_id, read_json, write_json
from ..utils.config import DATA_DIR
from .ai_draft_guidance import readable_action, readable_analysis
from .theme import COLORS, IOS_FONTS, IOS_METRICS


DEFAULT_GOAL = "녹화에서 수행한 작업의 목적과 흐름을 분석해 같은 작업을 위한 초안을 만드세요. 반복·종료 조건이 불명확하면 추측하지 말고 확인할 질문으로 남기세요."
JOB_LABELS = {"starting": "준비 중", "running": "GPT 분석 중", "review": "검토 가능", "needs_review": "추가 확인 필요",
              "error": "오류", "cancelled": "취소됨", "interrupted": "중단됨", "unconfirmed": "이전 실행 확인 필요"}
COMBO_STYLE = dict(fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"],
    button_color=COLORS["bg_elevated"], button_hover_color=COLORS["bg_card_hover"],
    dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_elevated"], dropdown_text_color=COLORS["text_primary"])


class RecordingWorkspace(ctk.CTkFrame):
    def __init__(self, owner):
        super().__init__(owner, fg_color="transparent")
        self.owner = owner
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WinCro" / "recording_analysis"
        root /= hashlib.sha256(str((DATA_DIR / "plans").resolve()).encode()).hexdigest()[:16]
        self.jobs = RecordingJobs(root / "jobs", DATA_DIR / "plans", DATA_DIR / "templates")
        self.settings_path = root / "runtime.json"
        self.runtime = default_runtime()
        try:
            self.runtime.update(read_json(self.settings_path))
        except (OSError, ValueError):
            pass
        self.selected = None
        self.closed = False
        self.busy = False
        self.serial = -1
        self._was_running = False
        self.queue = queue.Queue(maxsize=8)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wincro-recording-ui")
        self._after_id = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="녹화 · GPT 분석", anchor="w", width=1,
            text_color=COLORS["accent_text"], font=(IOS_FONTS["family"], 12, "bold")).grid(row=0, column=0, sticky="ew")
        self.badge = ctk.CTkLabel(header, text="GPT 연결 OFF", width=100, text_color=COLORS["text_muted"])
        self.badge.grid(row=0, column=1, padx=5)
        self._button(header, "연결 설정", self._settings, width=76).grid(row=0, column=2, padx=(0, 5))
        self._button(header, "사용방법", self._help, width=70).grid(row=0, column=3)
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["bg_content"],
            segmented_button_fg_color=COLORS["bg_card"], segmented_button_selected_color=COLORS["bg_elevated"],
            segmented_button_selected_hover_color=COLORS["bg_card_hover"], segmented_button_unselected_color=COLORS["bg_content"],
            segmented_button_unselected_hover_color=COLORS["bg_card_hover"], text_color=COLORS["accent_text"])
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.start_page = self.tabs.add("녹화·분석")
        self.result_page = self.tabs.add("분석 결과 편집")
        self.start_page.grid_columnconfigure(0, weight=1)
        self.start_page.grid_rowconfigure(1, weight=1, minsize=90)
        controls = ctk.CTkFrame(self.start_page, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        owner._setup_record_card(controls)
        catalog = ctk.CTkFrame(self.start_page, fg_color="transparent")
        catalog.grid(row=1, column=0, sticky="nsew")
        owner._setup_recordings_card(catalog)
        self._build_analysis_controls()
        self.result = DraftResultPane(self.result_page, self)
        self.result.pack(fill="both", expand=True)
        self._refresh_jobs()
        self._after_id = self.after(150, self._poll)

    def _button(self, parent, text, command, **kwargs):
        style = dict(height=30, fg_color=COLORS["bg_elevated"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], border_color=COLORS["button_border"], border_width=IOS_METRICS["card_border_width"],
            corner_radius=IOS_METRICS["control_radius"])
        style.update(kwargs)
        return ctk.CTkButton(parent, text=text, command=command, **style)

    def _build_analysis_controls(self):
        panel = ctk.CTkFrame(self.start_page, fg_color=COLORS["bg_glass"], border_width=IOS_METRICS["card_border_width"], border_color=COLORS["border"])
        panel.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        panel.grid_columnconfigure(0, weight=1)
        self.selection_label = ctk.CTkLabel(panel, text="목록에서 분석할 녹화를 선택하세요.", anchor="w", width=1,
            text_color=COLORS["text_primary"], height=24)
        self.selection_label.grid(row=0, column=0, sticky="ew", padx=10)
        self.goal = ctk.CTkEntry(panel, placeholder_text="원하는 작업·반복·종료 조건 (선택)", height=30,
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
        self.goal.grid(row=1, column=0, sticky="ew", padx=10)
        buttons = ctk.CTkFrame(panel, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        buttons.grid_columnconfigure(0, weight=1)
        self.analyze = self._button(buttons, "선택한 녹화 분석 시작", self._analyze,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], text_color=COLORS["text_on_accent"])
        self.analyze.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.cancel_button = self._button(buttons, "분석 취소", self._cancel, width=86, state="disabled")
        self.cancel_button.grid(row=0, column=1)
        self.status = ctk.CTkLabel(panel, text="분석은 GPT가 합니다. 분석 시작 시 자동 연결하며 기존 플랜은 바꾸지 않습니다.",
            anchor="w", justify="left", wraplength=560, text_color=COLORS["text_muted"], font=(IOS_FONTS["family"], 11))
        self.status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 5))
        self.status.bind("<Button-1>", lambda _event: self._job_details(), add="+")
        tk.Misc.bind(self.status, "<Configure>", lambda event: self.status.configure(
            wraplength=max(120, int(event.width / self.status._get_widget_scaling()) - 4)), add="+")

    def choose(self, media):
        if self.jobs.busy or self.busy:
            self.status.configure(text="분석 준비·실행 중에는 다른 녹화를 선택할 수 없습니다.")
            return
        previous = self.selected.path if self.selected else None
        self.selected = media
        self.selection_label.configure(text="선택: " + media.name[:80])
        for index, item in enumerate(self.owner._recording_items):
            if item.path in {previous, media.path}:
                self.owner._recordings_scroll.refresh_item(index)

    def _analyze(self):
        if self.jobs.busy or self.busy or self.owner._is_recording or self.owner._starting or self.owner._stopping:
            self.status.configure(text="녹화 또는 분석이 끝난 뒤 다시 시작하세요.")
            return
        if not self.selected:
            self.status.configure(text="먼저 녹화 목록에서 파일을 선택하세요.")
            return
        if self.result.dirty or self.result.busy:
            self.status.configure(text="분석 결과 탭의 미저장 입력을 먼저 저장하거나 취소하세요.")
            return
        from .ai_draft_editor import MediaConsentDialog
        MediaConsentDialog(self, self.selected.path, self._consented)

    def _consented(self, path, log, excluded):
        if self.jobs.busy or self.busy or not self.selected or path != self.selected.path:
            return False
        try:
            self.jobs.start(path, self.goal.get().strip() or DEFAULT_GOAL, self.runtime,
                input_log=log, excluded=excluded, reference=self.runtime.get("reference"))
        except Exception as exc:
            self.status.configure(text=str(exc))
            return False
        self.status.configure(text="GPT 연결과 녹화 자료를 준비하고 있습니다. 결과는 별도 초안으로만 보관합니다.")
        return True

    def _cancel(self):
        self.jobs.cancel()
        self.status.configure(text="취소 요청 중입니다. 저장된 초안은 보관하며 새 도구 호출은 차단했습니다.")

    def _submit(self, job, done, failed=None):
        if self.busy or self.closed:
            return False
        self.busy = True
        def run():
            try:
                value, error = job(), None
            except Exception as exc:
                value, error = None, str(exc)
            if not self.closed:
                self.queue.put_nowait((value, error, done, failed))
        self.worker.submit(run)
        return True

    def _poll(self):
        if self.closed:
            return
        try:
            value, error, done, failed = self.queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                if failed:
                    failed(error)
                else:
                    self.status.configure(text=error)
            else:
                done(value)
        busy = self.jobs.busy
        self.analyze.configure(state="disabled" if busy or self.busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        connected = self.jobs.connected
        self.badge.configure(text="GPT 연결 ON" if connected else "GPT 연결 OFF",
            text_color=COLORS["success_text"] if connected else COLORS["text_muted"])
        if self.serial != self.jobs.serial:
            self.serial = self.jobs.serial
            state = self.jobs.snapshot()
            if state:
                message = state["message"].replace("\n", " ")
                self.status.configure(text=message if len(message) <= 140 else message[:120] + "… (눌러서 상세 보기)")
                if not busy:
                    self._refresh_jobs(select=state["job_id"])
        self.result.set_running(busy)
        if self._was_running and not busy:
            state = self.jobs.snapshot()
            self._refresh_jobs(select=state["job_id"] if state and state.get("draft_id") else None)
        self._was_running = busy
        if busy:
            self.owner._start_btn.configure(state="disabled")
        elif not (self.owner._is_recording or self.owner._starting or self.owner._stopping):
            self.owner._start_btn.configure(state="normal")
        self._after_id = self.after(150, self._poll)

    def _refresh_jobs(self, select=None):
        def loaded(states):
            self.result.set_jobs(states, select)
        self._submit(self.jobs.list_jobs, loaded)

    def _settings(self):
        if self.jobs.busy or self.busy:
            self.status.configure(text="분석이 끝난 뒤 연결 설정을 변경하세요.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("GPT 자동 분석 연결 설정")
        dialog.geometry("650x500")
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(dialog, fg_color=COLORS["bg_card"])
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(body, text="로그인된 Codex 실행기가 별도 GPT 분석 작업을 시작합니다.\n현재 대화를 깨우거나 전역 Codex 설정을 바꾸지 않습니다.\nMCP용 Python에는 mcp==1.25.0이 필요합니다.",
            wraplength=340, width=1, justify="left", text_color=COLORS["text_primary"]).grid(row=0, column=0, columnspan=2, sticky="ew", pady=6)
        entries = {}
        for row, (key, label) in enumerate((("codex", "Codex 실행 파일 (.exe)"), ("python", "MCP용 Python (.exe)"),
                                            ("model", "모델 이름 (비우면 Codex 기본 모델)")), 1):
            ctk.CTkLabel(body, text=label, anchor="w", text_color=COLORS["text_primary"]).grid(row=row*2-1, column=0, columnspan=2, sticky="ew")
            entry = ctk.CTkEntry(body, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
            entry.insert(0, str(self.runtime.get(key, "")))
            entry.grid(row=row*2, column=0, sticky="ew", pady=(0, 5))
            entries[key] = entry
            if key in {"codex", "python"}:
                def browse(target=entry):
                    path = filedialog.askopenfilename(parent=dialog, title="실행 파일 선택", filetypes=[("실행 파일", "*.exe")])
                    if path:
                        target.delete(0, "end")
                        target.insert(0, path)
                self._button(body, "찾기", browse, width=60).grid(row=row*2, column=1, padx=(5, 0))
        ctk.CTkLabel(body, text="참고 플랜 (읽기 전용 / 선택)", anchor="w", text_color=COLORS["text_primary"]).grid(row=7, column=0, sticky="ew")
        references = {"선택하지 않음": None}
        reference_combo = ctk.CTkComboBox(body, values=list(references), state="readonly", **COMBO_STYLE)
        reference_combo.set("선택하지 않음")
        reference_combo.grid(row=8, column=0, sticky="ew", pady=5)
        def reference_list():
            rows = []
            for path in sorted((DATA_DIR / "plans").glob("*.json")):
                try:
                    state = read_json(path)
                    rows.append((str(state.get("name", path.stem)), path.stem))
                except (OSError, ValueError):
                    continue
            return rows
        def show_references(rows):
            if not dialog.winfo_exists():
                return
            for name, key in rows:
                references[name + " · " + key] = key
            reference_combo.configure(values=list(references))
            reference_combo.set(next((label for label, key in references.items() if key == self.runtime.get("reference")), "선택하지 않음"))
        self._submit(reference_list, show_references)
        note = ctk.CTkLabel(dialog, text="인증 정보는 저장하지 않습니다. 분석 시작 때 실행기·의존성을 확인합니다.",
            wraplength=340, width=1, text_color=COLORS["text_secondary"])
        note.grid(row=1, column=0, sticky="ew", padx=12)
        def save():
            if self.busy:
                note.configure(text="목록을 읽는 중입니다. 잠시 후 저장을 누르세요.")
                return
            values = {key: entry.get().strip() for key, entry in entries.items()}
            values.update(max_seconds=1200, reference=references.get(reference_combo.get()))
            def saved(_):
                self.runtime = values
                if dialog.winfo_exists():
                    dialog.destroy()
                self.status.configure(text="연결 설정을 파일에 저장하고 다시 읽어 확인했습니다. 분석 시작 시 GPT에 연결합니다.")
            def persist():
                write_json(self.settings_path, values)
                if read_json(self.settings_path) != values:
                    raise OSError("연결 설정 저장 재검증 실패")
            self._submit(persist, saved, lambda error: note.configure(text="저장 실패: " + error))
        self._button(dialog, "설정 저장", save).grid(row=2, column=0, sticky="ew", padx=12, pady=10)
        dialog.transient(self.winfo_toplevel())

    def _help(self):
        text = ("1. 녹화 시작으로 작업 과정을 녹화한 뒤 F7로 중지하세요. 기존 영상은 녹화 목록의 파일 추가로 넣을 수 있습니다.\n\n"
            "2. 목록에서 녹화를 선택하고 필요하면 작업·반복·종료 조건을 적은 뒤 분석 시작을 누르세요. 자료 제공 동의와 비공개 구간을 확인합니다.\n\n"
            "3. 연결 설정의 Codex 로그인과 MCP용 Python이 준비되어 있으면 별도 GPT 작업이 자동 시작됩니다. 현재 대화를 자동으로 깨우는 기능은 아닙니다. ON은 실제 MCP 통신 확인 상태입니다.\n\n"
            "4. 분석 결과 편집에서 액션·목적·질문을 검토하고 대기·반복을 수정해 저장할 수 있습니다. 수정하면 이전 GPT 교차검수는 무효가 됩니다.\n\n"
            "기존 재생 플랜·맵·재생목록은 수정하지 않습니다. 이 화면은 초안만 저장합니다. 분석·교차검수 기록이 있어도 실제 인식·재생 성공은 별도로 검증해야 합니다. 취소·오류 시 저장된 초안은 보관됩니다.")
        dialog = ctk.CTkToplevel(self)
        dialog.title("녹화·GPT 분석 사용방법")
        dialog.geometry("640x480")
        box = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")
        self._button(dialog, "닫기", dialog.destroy).pack(fill="x", padx=12, pady=(0, 12))
        dialog.transient(self.winfo_toplevel())

    def _job_details(self):
        state = self.jobs.snapshot()
        if state:
            dialog = ctk.CTkToplevel(self)
            dialog.title("GPT 분석 상태 상세")
            dialog.geometry("640x420")
            box = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
            box.pack(fill="both", expand=True, padx=12, pady=12)
            box.insert("1.0", state["message"] + "\n\n" + state.get("model_message", ""))
            box.configure(state="disabled")
            self._button(dialog, "닫기", dialog.destroy).pack(fill="x", padx=12, pady=(0, 12))
            dialog.transient(self.winfo_toplevel())

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.jobs.close()
        self.result.close()
        self.worker.shutdown(wait=False, cancel_futures=False)
        if self._after_id:
            self.after_cancel(self._after_id)


class DraftResultPane(ctk.CTkFrame):
    def __init__(self, parent, workspace):
        super().__init__(parent, fg_color="transparent")
        self.workspace = workspace
        self.service = self.current = self.context = None
        self.dirty = self.busy = self.closed = False
        self.loading = False
        self.running = False
        self.options = {}
        self.rows = {}
        self.row_cache = {}
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wincro-result-save")
        self.results = queue.Queue(maxsize=8)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.jobs = ctk.CTkComboBox(self, values=["아직 분석 결과가 없습니다"], state="readonly", command=self._select, **COMBO_STYLE)
        self.jobs.set("아직 분석 결과가 없습니다")
        self.jobs.grid(row=0, column=0, sticky="ew", pady=4)
        tree_host = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"])
        tree_host.grid(row=1, column=0, sticky="nsew")
        tree_host.grid_columnconfigure(0, weight=1)
        tree_host.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        if "RecordingResult.field" not in style.element_names():
            style.element_create("RecordingResult.field", "from", "clam", "Treeview.field")
        style.layout("RecordingResult.Treeview", [("RecordingResult.field", {"sticky": "nswe", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
        style.configure("RecordingResult.Treeview", background=COLORS["bg_dark"], foreground=COLORS["text_primary"],
            fieldbackground=COLORS["bg_dark"], rowheight=28, font=(IOS_FONTS["family"], 11))
        style.map("RecordingResult.Treeview", background=[("selected", COLORS["accent"])], foreground=[("selected", COLORS["text_on_accent"])])
        if "RecordingResult.heading" not in style.element_names():
            style.element_create("RecordingResult.heading", "from", "clam", "Treeheading.cell")
        style.layout("RecordingResult.Treeview.Heading", [("RecordingResult.heading", {"sticky": "nswe", "children": [
            ("Treeheading.padding", {"sticky": "nswe", "children": [("Treeheading.text", {"sticky": "we"})]})]})])
        style.configure("RecordingResult.Treeview.Heading", background=COLORS["bg_elevated"], foreground=COLORS["accent_text"],
            font=(IOS_FONTS["family"], 11, "bold"), relief="flat")
        self.tree = ttk.Treeview(tree_host, columns=("wait", "repeat"), selectmode="browse", height=4, style="RecordingResult.Treeview")
        self.tree.heading("#0", text="액션 순서 / 이름")
        self.tree.heading("wait", text="대기(초)")
        self.tree.heading("repeat", text="반복")
        self.tree.column("#0", width=280, minwidth=100)
        self.tree.column("wait", width=65, minwidth=45, stretch=False)
        self.tree.column("repeat", width=55, minwidth=40, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_host, command=self.tree.yview, fg_color=COLORS["bg_dark"],
            button_color=COLORS["border"], button_hover_color=COLORS["accent"])
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        form = ctk.CTkFrame(self, fg_color=COLORS["bg_glass"])
        form.grid(row=2, column=0, sticky="ew", pady=5)
        form.grid_columnconfigure((0, 1), weight=1, uniform="fields")
        self.variables = {}
        self.entries = {}
        for index, (field, label) in enumerate((("description", "액션 이름"), ("wait_after", "액션 후 대기(초)"),
                                               ("repeat_count", "반복 횟수"), ("repeat_delay", "반복 사이 대기(초)"))):
            row, column = divmod(index, 2)
            ctk.CTkLabel(form, text=label, anchor="w", text_color=COLORS["text_primary"], height=20).grid(
                row=row*2, column=column, sticky="ew", padx=8)
            variable = tk.StringVar(master=self)
            variable.trace_add("write", self._changed)
            entry = ctk.CTkEntry(form, textvariable=variable, height=28, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
            entry.grid(row=row*2+1, column=column, sticky="ew", padx=8, pady=(0, 3))
            self.variables[field] = variable
            self.entries[field] = entry
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=5)
        buttons.grid_columnconfigure((0, 1), weight=1)
        self.save = workspace._button(buttons, "이 수정 저장", self._save, state="disabled")
        self.save.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        workspace._button(buttons, "입력 취소 · 다시 읽기", self._reload).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.summary = ctk.CTkTextbox(self, height=65, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.summary.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        self.summary.insert("1.0", "GPT 분석 결과·행동 목적·남은 질문이 여기에 표시됩니다. 기존 재생 플랜은 바뀌지 않습니다.")
        self.summary.configure(state="disabled")
        self._after_id = self.after(120, self._poll)

    def set_jobs(self, states, select=None):
        self.options = {f"{state['recording_name']} · {JOB_LABELS.get(state['status'], state['status'])} · {state['job_id'][-6:]}": state["job_id"] for state in states}
        self.jobs.configure(values=list(self.options) or ["아직 분석 결과가 없습니다"])
        if select and not self.dirty and not self.busy:
            label = next((name for name, key in self.options.items() if key == select), None)
            if label:
                self.jobs.set(label)
                self._select(label)

    def set_running(self, running):
        self.running = running
        self.save.configure(state="normal" if self.current and self.context and self.dirty and not self.busy and not running and not self.service.closed else "disabled")
        for entry in self.entries.values():
            entry.configure(state="disabled" if running or self.busy or not self.context or self.service.closed else "normal")
        self.jobs.configure(state="disabled" if self.busy else "readonly")

    def _select(self, label):
        if self.dirty:
            self.workspace.status.configure(text="미저장 입력이 있습니다. 먼저 저장하거나 입력 취소를 누르세요.")
            return
        key = self.options.get(label)
        if key:
            self._submit(lambda: self.workspace.jobs.load_result(key), self._display)

    def _submit(self, job, done):
        if self.busy or self.closed:
            return False
        self.busy = True
        self.set_running(self.running)
        def run():
            try:
                value, error = job(), None
            except Exception as exc:
                value, error = None, f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
            if not self.closed:
                self.results.put_nowait((value, error, done))
        self.worker.submit(run)
        return True

    def _poll(self):
        if self.closed:
            return
        try:
            value, error, done = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                self.workspace.status.configure(text=error)
            else:
                done(value)
            self.set_running(self.running)
        self._after_id = self.after(120, self._poll)

    def _display(self, value):
        if self.dirty:
            self.workspace.status.configure(text="미저장 입력을 보관했습니다. 저장하거나 취소한 뒤 결과를 다시 선택하세요.")
            return
        self.service, state = value
        self.current = state
        self.context = None
        self.rows = {rule["rule_id"]: rule for rule, *_ in walk(state["plan"])}
        existing = set()
        def collect(parent=""):
            for key in self.tree.get_children(parent):
                existing.add(key)
                collect(key)
        collect()
        for key in existing - set(self.rows):
            if self.tree.exists(key):
                self.tree.delete(key)
        for rule, _, parent, step in walk(state["plan"]):
            key = rule["rule_id"]
            values = (rule.get("wait_after", .5), rule.get("repeat_count", 1))
            text = step + "  " + (rule.get("description") or rule.get("action_type", ""))
            position = int(step.split("-")[-1]) - 1
            metadata = (parent or "", position, text, values)
            if self.tree.exists(key):
                previous = self.row_cache.get(key)
                if previous is None or previous[:2] != metadata[:2]:
                    self.tree.move(key, parent or "", position)
                if previous is None or previous[2:] != metadata[2:]:
                    self.tree.item(key, text=text, values=values)
            else:
                self.tree.insert(parent or "", position, iid=key, text=text, values=values, open=True)
            self.row_cache[key] = metadata
        self.row_cache = {key: self.row_cache[key] for key in self.rows}
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", readable_analysis(state))
        self.summary.configure(state="disabled")
        if not self.tree.selection() and self.rows:
            self.tree.selection_set(next(iter(self.rows)))
        self._selected()

    def _selected(self, _event=None):
        if not self.current or not self.tree.selection() or self.busy:
            return
        key = self.tree.selection()[0]
        if self.dirty and self.context and self.context[2] != key:
            self.tree.selection_set(self.context[2])
            self.workspace.status.configure(text="선택한 액션의 입력을 먼저 저장하거나 취소하세요.")
            return
        if self.dirty:
            return
        rule = self.rows.get(key)
        if rule:
            self.context = (self.current["draft_id"], self.current["revision"], key)
            self.loading = True
            try:
                for field, variable in self.variables.items():
                    variable.set(str(rule.get(field, "" if field == "description" else 1 if field == "repeat_count" else .5)))
            finally:
                self.loading = False

    def _changed(self, *_):
        if not self.loading:
            self.dirty = True
            if hasattr(self, "save"):
                self.set_running(self.running)

    def _save(self):
        if not self.context or self.running or self.busy or self.service.closed:
            return
        try:
            fields = {"description": self.variables["description"].get(), "wait_after": float(self.variables["wait_after"].get()),
                "repeat_count": int(self.variables["repeat_count"].get()), "repeat_delay": float(self.variables["repeat_delay"].get())}
        except ValueError:
            self.workspace.status.configure(text="대기는 숫자, 반복 횟수는 정수로 입력하세요.")
            return
        key, revision, rule_id = self.context
        service = self.service
        def save():
            service.stage_edits(key, revision, new_id("ui_"), [{"op": "update_rule", "rule_id": rule_id, "fields": fields}])
            state = service.read_draft(key)
            fresh = type(service)(service.plans_dir, service.templates_dir, service.workspace, allow_publication=False).read_draft(key)
            if fresh != state:
                raise OSError("초안 저장 재읽기 결과가 다릅니다.")
            return service, state
        def saved(value):
            self.dirty = False
            self._display(value)
            self.workspace.status.configure(text="수정 내용을 초안에 저장하고 새 연결로 다시 읽어 확인했습니다. 이전 GPT 검수는 무효입니다.")
            self.workspace._refresh_jobs()
        self._submit(save, saved)

    def _reload(self):
        if self.busy or not self.current:
            return
        self.dirty = False
        service, key = self.service, self.current["draft_id"]
        self._submit(lambda: (service, service.read_draft(key)), self._display)

    def close(self):
        self.closed = True
        self.worker.shutdown(wait=False, cancel_futures=False)
        if self._after_id:
            self.after_cancel(self._after_id)
