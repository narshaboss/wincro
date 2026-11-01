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

from ..agent_bridge.codex_runner import default_runtime, load_runtime
from ..agent_bridge.contracts import walk
from ..agent_bridge.recording_jobs import RecordingJobs
from ..agent_bridge.recording_connection import RecordingConnection
from ..agent_bridge.recording_catalog import RecordingVisibility
from ..agent_bridge.storage import new_id, read_json, write_json
from ..agent_bridge.service import DraftService
from ..utils.config import DATA_DIR
from .ai_draft_guidance import readable_action, readable_analysis, review_progress
from .analysis_progress import AnalysisProgress
from .theme import COLORS, IOS_FONTS, IOS_METRICS


DEFAULT_GOAL = "녹화에서 수행한 작업의 목적과 흐름을 분석해 같은 작업을 위한 초안을 만드세요. 반복·종료 조건이 불명확하면 추측하지 말고 확인할 질문으로 남기세요."
JOB_LABELS = {"starting": "준비 중", "running": "GPT 분석 중", "review": "검토 가능", "published": "플랜 등록 완료", "needs_review": "추가 확인 필요",
              "error": "오류", "cancelled": "취소됨", "interrupted": "중단됨", "unconfirmed": "이전 실행 확인 필요"}
COMBO_STYLE = dict(fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"],
    button_color=COLORS["bg_elevated"], button_hover_color=COLORS["bg_card_hover"],
    dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_elevated"], dropdown_text_color=COLORS["text_primary"])


def configure_changed(widget, **values):
    changes = {key: value for key, value in values.items() if widget.cget(key) != value}
    if changes:
        widget.configure(**changes)


class RecordingWorkspace(ctk.CTkFrame):
    def __init__(self, owner):
        super().__init__(owner, fg_color="transparent")
        self.owner = owner
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WinCro" / "recording_analysis"
        root /= hashlib.sha256(str((DATA_DIR / "plans").resolve()).encode()).hexdigest()[:16]
        self.jobs = RecordingJobs(root / "jobs", DATA_DIR / "plans", DATA_DIR / "templates")
        self.connection = RecordingConnection(root / "connections")
        self.visibility = RecordingVisibility(root / "hidden_recordings.json")
        self.settings_path = root / "runtime.json"
        self.runtime = default_runtime()
        settings_error = None
        try:
            self.runtime = load_runtime(self.settings_path)
        except (OSError, ValueError) as exc:
            settings_error = "연결 설정을 읽지 못했습니다. 원본 파일은 보존했습니다. 연결 설정에서 확인하세요: " + str(exc)
        self.selected = None
        self.closed = False
        self.busy = False
        self.serial = -1
        self._was_running = False
        self._was_connected = False
        self._connection_status = None
        self._status_detail = ""
        self._consent_dialog = None
        self._last_failed_job = None
        self._analysis_progress = AnalysisProgress()
        self._progress_job_id = None
        self._progress_bar_value = 0.0
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
        self.badge = self._button(header, "GPT 연결 OFF", self._toggle_connection, width=118,
            text_color=COLORS["error"], border_color=COLORS["error"])
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
        if settings_error:
            self.set_status(settings_error)
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
        progress_row = ctk.CTkFrame(panel, fg_color="transparent", height=20)
        progress_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 1))
        progress_row.grid_columnconfigure(1, weight=1)
        self.progress_label = ctk.CTkLabel(progress_row, text="분석 대기", anchor="w", width=104,
            height=20, text_color=COLORS["text_muted"], font=(IOS_FONTS["family"], 11))
        self.progress_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.progress_bar = ctk.CTkProgressBar(progress_row, height=8, width=80, mode="determinate",
            fg_color=COLORS["bg_elevated"], progress_color=COLORS["text_muted"], corner_radius=4)
        self.progress_bar.grid(row=0, column=1, sticky="ew")
        self.progress_bar.set(0)
        self.progress_percent = ctk.CTkLabel(progress_row, text="0%", width=48, height=20,
            anchor="e", text_color=COLORS["text_muted"], font=(IOS_FONTS["family"], 13, "bold"))
        self.progress_percent.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._status_font = ctk.CTkFont(family=IOS_FONTS["family"], size=11)
        self.status = ctk.CTkLabel(panel, text="", height=22,
            anchor="w", width=1, text_color=COLORS["text_muted"], font=self._status_font)
        self.status.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 5))
        self.set_status("먼저 GPT 연결 OFF를 눌러 연결하세요. 녹화를 선택한 뒤 분석을 시작합니다.")
        self.status.bind("<Button-1>", lambda _event: self._job_details(), add="+")
        tk.Misc.bind(self.status, "<Configure>", lambda event: self._refresh_status_text(event.width), add="+")

    def _refresh_analysis_progress(self, state, busy):
        if not state or state.get("job_id") != self._progress_job_id or (not busy and self.selected is None):
            state = None
        progress = self._analysis_progress.update(state, busy,
            cancelling=self.jobs.cancel_event.is_set())
        captions = {"idle": "분석 대기", "starting": "예상 진행률", "running": "예상 진행률",
            "finishing": "저장 마무리", "complete": "분석 완료", "needs_review": "추가 확인 필요",
            "cancelling": "분석 취소 중", "cancelled": "분석 취소됨", "error": "분석 오류",
            "unconfirmed": "진행 확인 필요"}
        if progress.phase == "complete":
            color = COLORS["success"]
        elif progress.phase == "error":
            color = COLORS["error"]
        elif progress.phase in {"cancelling", "cancelled", "needs_review", "unconfirmed"}:
            color = COLORS["warning"]
        else:
            color = COLORS["text_muted"] if progress.phase == "idle" else COLORS["accent"]
        configure_changed(self.progress_label, text=captions[progress.phase], text_color=color)
        configure_changed(self.progress_percent, text=f"{progress.percent}%", text_color=color)
        configure_changed(self.progress_bar, progress_color=color)
        if progress.value != self._progress_bar_value:
            self.progress_bar.set(progress.value)
            self._progress_bar_value = progress.value

    def set_status(self, text):
        self._status_detail = str(text)
        self._refresh_status_text()

    def _refresh_status_text(self, width=None):
        display = " ".join(self._status_detail.split())
        width = self.status.winfo_width() if width is None else width
        available = int(width / self.status._get_widget_scaling()) - 8 if width > 1 else 560
        if len(display) > 58 or self._status_font.measure(display) > available:
            suffix = "… (상세)"
            low, high = 0, min(50, len(display))
            while low < high:
                middle = (low + high + 1) // 2
                if self._status_font.measure(display[:middle] + suffix) <= available:
                    low = middle
                else:
                    high = middle - 1
            display = display[:low] + suffix
        configure_changed(self.status, text=display)

    def _toggle_connection(self):
        if self.connection.busy:
            self._was_connected = False
            self.jobs.cancel()
            self.connection.disable()
            self.set_status("GPT 연결을 해제했습니다. 진행 중 분석은 취소하고 저장된 초안은 보관합니다.")
        else:
            if self.busy:
                self.set_status("설정·목록 저장이 끝난 뒤 연결 ON을 눌러주세요.")
                return
            try:
                self.connection.enable(self.runtime)
            except Exception as exc:
                self.set_status(str(exc))

    def choose(self, media):
        if self.jobs.busy or self.busy:
            self.set_status("분석 준비·실행 중에는 다른 녹화를 선택할 수 없습니다.")
            return
        previous = self.selected.path if self.selected else None
        if previous == media.path:
            return
        self.selected = media
        self._progress_job_id = None
        self._refresh_analysis_progress(None, False)
        self.selection_label.configure(text="선택: " + media.name[:80])
        self.owner._update_media_selection()

    def _analyze(self):
        if self.jobs.busy or self.busy or self.owner._is_recording or self.owner._starting or self.owner._stopping:
            self.set_status("녹화 또는 분석이 끝난 뒤 다시 시작하세요.")
            return
        if not self.selected:
            self.set_status("먼저 녹화 목록에서 파일을 선택하세요.")
            return
        if not self.connection.connected:
            self.set_status("먼저 GPT 연결 OFF를 눌러 ON이 된 뒤 분석을 시작하세요.")
            return
        if self.result.dirty or self.result.busy:
            self.set_status("분석 결과 탭의 미저장 입력을 먼저 저장하거나 취소하세요.")
            return
        from .ai_draft_editor import MediaConsentDialog
        if self._consent_dialog is not None and self._consent_dialog.winfo_exists():
            self._consent_dialog.lift()
            return
        self._consent_dialog = MediaConsentDialog(self, self.selected.path, self._consented, action_text="동의하고 분석 시작")

    def _consented(self, path, log, excluded):
        if self.jobs.busy or self.busy or not self.selected or path != self.selected.path:
            self.set_status("다른 작업이 진행 중이거나 선택한 녹화가 바뀌었습니다. 작업 종료 후 녹화를 다시 선택하세요.")
            return False
        if not self.connection.connected:
            self.set_status("GPT 연결이 OFF입니다. 다시 ON으로 연결한 뒤 분석 시작을 누르세요.")
            return False
        if self.owner._is_recording or self.owner._starting or self.owner._stopping:
            self.set_status("녹화가 진행 중입니다. 녹화를 마친 뒤 분석 시작을 다시 누르세요.")
            return False
        if self.result.dirty or self.result.busy:
            self.set_status("분석 결과의 미저장 입력을 먼저 저장하거나 취소하세요.")
            return False
        self._progress_job_id = None
        self._refresh_analysis_progress(None, False)
        try:
            started = self.jobs.start(path, self.goal.get().strip() or DEFAULT_GOAL, self.runtime,
                input_log=log, excluded=excluded, reference=self.runtime.get("reference"))
        except Exception as exc:
            self.set_status(str(exc))
            return False
        self._progress_job_id = started["job_id"]
        self._refresh_analysis_progress(started, True)
        self.set_status("GPT 연결과 녹화 자료를 준비하고 있습니다. 결과는 별도 초안으로만 보관합니다.")
        configure_changed(self.analyze, state="disabled", text="GPT 분석 준비 중…")
        configure_changed(self.cancel_button, state="normal")
        return True

    def _cancel(self):
        self.jobs.cancel()
        self._refresh_analysis_progress(self.jobs.snapshot(), self.jobs.busy)
        self.set_status("취소 요청 중입니다. 저장된 초안은 보관하며 새 도구 호출은 차단했습니다.")

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
                    self.set_status(error)
            else:
                done(value)
        busy = self.jobs.busy
        connected = self.connection.connected
        connection = self.connection.snapshot()
        if self._was_connected and not connected:
            reason = connection["message"]
            self.jobs.cancel()
            self.connection.disable()
            self.set_status("GPT 연결이 끊겼습니다. 분석을 취소하고 저장된 초안은 보관합니다.\n" + reason)
            connection = self.connection.snapshot()
        self._was_connected = connected
        configure_changed(self.analyze, state="disabled" if busy or self.busy or not connected else "normal",
            text="GPT 분석 진행 중…" if busy else "선택한 녹화 분석 시작")
        configure_changed(self.cancel_button, state="normal" if busy else "disabled")
        state = connection["state"]
        label = "GPT 연결 ON" if connected else "연결 중 · 취소" if state == "connecting" else "GPT 연결 OFF"
        color = COLORS["success"] if connected else COLORS["warning"] if state == "connecting" else COLORS["error"]
        configure_changed(self.badge, text=label, text_color=color, border_color=color)
        signature = (state, connection["message"])
        if signature != self._connection_status:
            self._connection_status = signature
            if state != "off" or self.connection.busy:
                self.set_status(connection["message"])
        if self.serial != self.jobs.serial:
            self.serial = self.jobs.serial
            state = self.jobs.snapshot()
            if state:
                message = state["message"].replace("\n", " ")
                self.set_status(message)
                if not busy:
                    self._refresh_jobs(select=state["job_id"] if state.get("draft_id") else None)
        self.result.set_running(busy)
        if self._was_running and not busy:
            state = self.jobs.snapshot()
            self._refresh_jobs(select=state["job_id"] if state and state.get("draft_id") else None)
        self._was_running = busy
        state = self.jobs.snapshot()
        self._refresh_analysis_progress(state, busy)
        if not busy and state and state.get("status") == "error" and state["job_id"] != self._last_failed_job:
            self._last_failed_job = state["job_id"]
            self._show_job_error(state)
        if busy:
            configure_changed(self.owner._start_btn, state="disabled")
        elif not (self.owner._is_recording or self.owner._starting or self.owner._stopping):
            configure_changed(self.owner._start_btn, state="normal")
        self._after_id = self.after(150, self._poll)

    def _refresh_jobs(self, select=None):
        def loaded(states):
            self.result.set_jobs(states, select)
        self._submit(self.jobs.list_jobs, loaded)

    def _dialog(self, title, width, height):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        scale = dialog._get_window_scaling()
        width = max(340, min(width, int(dialog.winfo_screenwidth() / scale) - 60))
        height = max(300, min(height, int(dialog.winfo_screenheight() / scale) - 90))
        dialog.geometry(f"{width}x{height}+20+20")
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self.winfo_toplevel())
        return dialog

    def _settings(self):
        if self.jobs.busy or self.busy or self.connection.busy:
            self.set_status("GPT 연결을 OFF로 끈 뒤 연결 설정을 변경하세요.")
            return
        dialog = self._dialog("GPT 자동 분석 연결 설정", 650, 500)
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
                    if isinstance(state, dict):
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
                self.set_status("연결 설정을 저장·재확인했습니다. GPT 연결 OFF를 눌러 연결하세요.")
            def persist():
                write_json(self.settings_path, values)
                if read_json(self.settings_path) != values:
                    raise OSError("연결 설정 저장 재검증 실패")
            def failed(error):
                self.set_status("연결 설정 저장 실패: " + error)
                if dialog.winfo_exists():
                    note.configure(text="저장 실패: " + error)
            self._submit(persist, saved, failed)
        self._button(dialog, "설정 저장", save).grid(row=2, column=0, sticky="ew", padx=12, pady=10)

    def _help(self):
        text = ("1. 연결 설정을 저장한 뒤 빨간 GPT 연결 OFF를 누르세요. 로그인 정보와 실제 통신을 확인하면 초록 ON이 됩니다. 연결 중에는 녹화를 GPT에 보내지 않습니다. ON은 모델 응답 성공이나 분석 완료의 증명이 아닙니다.\n\n"
            "2. 녹화 시작으로 작업을 녹화하고 F7로 중지하세요. 목록에서 녹화를 선택한 뒤 분석 시작을 누르고 자료 제공에 동의합니다. 기존 영상은 파일 추가로 넣습니다.\n\n"
            "3. ON 버튼을 누르면 OFF가 되어 연결과 진행 중 분석을 종료합니다. 분석 취소는 분석만 중단하며 ON 연결은 유지합니다. 현재 대화를 깨우는 기능은 아닙니다.\n\n"
            "4. 분석 결과 편집에서 액션·목적·질문을 검토하고 대기·반복을 수정해 저장할 수 있습니다. 수정하면 이전 GPT 교차검수는 무효가 됩니다.\n\n"
            "진행 바는 정확한 작업량이 아닌 예상 진행률입니다. 분석 중에는 100%가 되지 않으며, 분석·교차검수 기록과 초안 저장을 확인한 뒤에만 100%로 표시합니다. 실제 인식·재생 성공을 보장하지 않습니다.\n\n"
            "5. 최신 GPT 분석·양방향 검수가 완료된 결과에 플랜 제목을 입력하고 동작분석에 등록을 누르세요. 승인하면 새 플랜 파일을 저장·재읽기 확인하며 동작분석 목록에 표시합니다. 기존 플랜·재생목록은 바꾸지 않고 자동 실행하지 않습니다. 수정으로 검수가 무효가 된 초안은 다시 GPT 분석·검수가 필요합니다.\n\n"
            "녹화의 삭제 버튼은 목록에서만 제거합니다. 원본 영상·키 기록·분석 결과·기존 플랜은 보존되며 파일 추가로 복원할 수 있습니다. 기존 재생 플랜·맵은 수정하지 않습니다. 실제 인식·재생 성공은 별도 검증이 필요합니다.")
        dialog = self._dialog("녹화·GPT 분석 사용방법", 640, 480)
        box = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")
        self._button(dialog, "닫기", dialog.destroy).pack(fill="x", padx=12, pady=(0, 12))

    def _job_details(self):
        state = self.jobs.snapshot()
        if state or self._status_detail:
            dialog = self._dialog("GPT 분석 상태 상세", 640, 420)
            box = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
            box.pack(fill="both", expand=True, padx=12, pady=12)
            box.insert("1.0", self._status_detail + "\n\n" + (state or {}).get("model_message", ""))
            box.configure(state="disabled")
            self._button(dialog, "닫기", dialog.destroy).pack(fill="x", padx=12, pady=(0, 12))

    def _show_job_error(self, state):
        self.set_status("GPT 분석 실패 · " + state["message"])
        dialog = self._dialog("GPT 분석 실패 · 원인 확인", 660, 430)
        ctk.CTkLabel(dialog, text="분석이 완료되지 않았습니다. 아래 원인을 확인하세요.",
            text_color=COLORS["error"], wraplength=310).pack(fill="x", padx=12, pady=10)
        box = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        box.pack(fill="both", expand=True, padx=12)
        box.insert("1.0", state["message"] + "\n\n이미 저장된 초안은 보관했고 기존 재생 플랜은 변경하지 않았습니다. 연결 설정을 바꾸려면 먼저 GPT 연결을 OFF로 끄세요.")
        box.configure(state="disabled")
        self._button(dialog, "확인", dialog.destroy).pack(fill="x", padx=12, pady=12)
        dialog.lift()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.connection.close()
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
        self.job_states = []
        self.published_jobs = set()
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
        form.grid_columnconfigure((1, 3), weight=1, uniform="fields")
        self.variables = {}
        self.entries = {}
        for index, (field, label) in enumerate((("description", "액션 이름"), ("wait_after", "액션 후 대기(초)"),
                                               ("repeat_count", "반복 횟수"), ("repeat_delay", "반복 사이 대기(초)"))):
            row, column = divmod(index, 2)
            ctk.CTkLabel(form, text=label, anchor="w", text_color=COLORS["text_primary"], height=28).grid(
                row=row, column=column*2, sticky="w", padx=(8, 4), pady=3)
            variable = tk.StringVar(master=self)
            variable.trace_add("write", self._changed)
            entry = ctk.CTkEntry(form, textvariable=variable, width=80, height=28, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
            entry.grid(row=row, column=column*2+1, sticky="ew", padx=(0, 8), pady=3)
            self.variables[field] = variable
            self.entries[field] = entry
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=3)
        buttons.grid_columnconfigure((0, 1), weight=1)
        self.save = workspace._button(buttons, "이 수정 저장", self._save, state="disabled")
        self.save.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        workspace._button(buttons, "입력 취소 · 다시 읽기", self._reload).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.summary = ctk.CTkTextbox(self, height=65, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.summary.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        self.summary.insert("1.0", "GPT 분석 결과·행동 목적·남은 질문이 여기에 표시됩니다. 기존 재생 플랜은 바뀌지 않습니다.")
        self.summary.configure(state="disabled")
        registration = ctk.CTkFrame(self, fg_color=COLORS["bg_glass"])
        registration.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        registration.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(registration, text="플랜 제목", text_color=COLORS["text_primary"], width=60).grid(
            row=0, column=0, padx=(8, 4), pady=5)
        self.plan_title = tk.StringVar(master=self)
        self.title_entry = ctk.CTkEntry(registration, textvariable=self.plan_title, height=28,
            placeholder_text="동작분석에 표시할 제목", fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"], border_color=COLORS["border"])
        self.title_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.register_button = workspace._button(registration, "동작분석에 등록", self._register_plan, width=130, state="disabled")
        self.register_button.grid(row=0, column=2, padx=8, pady=5)
        self.registration_status = ctk.CTkLabel(registration, text="최신 GPT 검수 완료 후 새 플랜으로 등록합니다.",
            anchor="w", text_color=COLORS["text_secondary"], height=20, font=ctk.CTkFont(size=11))
        self.registration_status.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))
        self.registration_status.bind("<Button-1>", lambda _event: self.workspace._job_details(), add="+")
        self._after_id = self.after(120, self._poll)

    def set_jobs(self, states, select=None):
        previous = self.options.get(self.jobs.get())
        if previous is None and self.current and self.service:
            previous = self.service.workspace.parent.name
        self.job_states = list(states)
        def label(state):
            status = "published" if state["job_id"] in self.published_jobs else state["status"]
            return f"{state['recording_name']} · {JOB_LABELS.get(status, status)} · {state['job_id'][-6:]}"
        self.options = {label(state): state["job_id"] for state in self.job_states}
        configure_changed(self.jobs, values=list(self.options) or ["아직 분석 결과가 없습니다"])
        selected_label = next((name for name, key in self.options.items() if key == (select or previous)), None)
        if selected_label and self.jobs.get() != selected_label:
            self._set_job_selection(selected_label)
        elif not self.options:
            self._set_job_selection("아직 분석 결과가 없습니다")
        if select and not self.dirty and not self.busy:
            if selected_label:
                self._select(selected_label)

    def _set_job_selection(self, label):
        # CTkComboBox.set() silently ignores writes to its disabled Entry.
        state = self.jobs.cget("state")
        if state == "disabled":
            self.jobs.configure(state="readonly")
        try:
            self.jobs.set(label)
        finally:
            if state == "disabled":
                self.jobs.configure(state=state)

    def set_running(self, running):
        self.running = running
        published = bool(self.current and self.current.get("publication"))
        available = bool(self.current and not self.busy and not running and not self.service.closed and not published)
        configure_changed(self.save, state="normal" if available and self.context and self.dirty else "disabled")
        for entry in self.entries.values():
            configure_changed(entry, state="normal" if available and self.context else "disabled")
        configure_changed(self.jobs, state="disabled" if self.busy else "readonly")
        configure_changed(self.title_entry, state="normal" if available else "disabled")
        configure_changed(self.register_button, state="normal" if available and not self.dirty else "disabled")

    def _select(self, label):
        if self.dirty:
            self.workspace.set_status("미저장 입력이 있습니다. 먼저 저장하거나 입력 취소를 누르세요.")
            if self.service:
                previous = self.service.workspace.parent.name
                self.jobs.set(next((name for name, key in self.options.items() if key == previous), self.jobs.get()))
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
                self._result_error(error)
            else:
                done(value)
            self.set_running(self.running)
        self._after_id = self.after(120, self._poll)

    def _display(self, value):
        if self.dirty:
            self.workspace.set_status("미저장 입력을 보관했습니다. 저장하거나 취소한 뒤 결과를 다시 선택하세요.")
            return
        previous_id = self.current and self.current["draft_id"]
        self.service, state = value
        self.current = state
        publication = state.get("publication")
        if publication:
            # A late pre-publication job-list response must not downgrade a verified receipt.
            self.published_jobs.add(self.service.workspace.parent.name)
            self.set_jobs(self.job_states)
        if previous_id != state["draft_id"] or publication:
            self.plan_title.set(publication["name"] if publication else state["plan"]["name"])
        _, ready = review_progress(state)
        configure_changed(self.registration_status, text="등록 완료 · 동작분석 목록에서 확인하세요." if publication else
            "최신 GPT 검수 완료 · 제목 입력 후 등록하세요." if ready else "추가 GPT 분석·양방향 검수 필요 · 등록할 수 없습니다.",
            text_color=COLORS["text_secondary"])
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
            self.workspace.set_status("선택한 액션의 입력을 먼저 저장하거나 취소하세요.")
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
            self.workspace.set_status("대기는 숫자, 반복 횟수는 정수로 입력하세요.")
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
            self.workspace.set_status("수정 내용을 초안에 저장하고 새 연결로 다시 읽어 확인했습니다. 이전 GPT 검수는 무효입니다.")
            self.workspace._refresh_jobs()
        self._submit(save, saved)

    def _reload(self):
        if self.busy or not self.current:
            return
        self.dirty = False
        service, key = self.service, self.current["draft_id"]
        self._submit(lambda: (service, service.read_draft(key)), self._display)

    def _register_plan(self):
        if self.dirty:
            self._result_error("먼저 수정 내용을 초안에 저장하세요. 수정 후에는 GPT 재검수가 필요합니다.", "미저장 수정이 있습니다. 먼저 저장하세요.")
            return
        if not self.current or self.busy or self.running or self.service.closed or self.current.get("publication"):
            return
        try:
            title = DraftService.publication_title(self.plan_title.get())
        except ValueError as exc:
            self._result_error(str(exc), "플랜 제목을 1~200자로 입력하세요.")
            return
        state = self.current
        message, ready = review_progress(state)
        if not ready:
            self._result_error(message, "최신 GPT 분석·양방향 검수 필요 · 상세 보기")
            return
        configure_changed(self.registration_status, text="최신 GPT 검수 완료 · 제목 입력 후 등록하세요.", text_color=COLORS["text_secondary"])
        if not messagebox.askyesno("새 플랜 등록 승인", f"제목: {title}\n\n현재 분석 결과를 검토했으며 새 플랜 등록을 승인하시겠습니까?\n"
                "기존 플랜·재생목록은 변경하지 않습니다. 실제 인식·재생은 별도로 확인해야 합니다.", parent=self):
            return
        job_id = self.service.workspace.parent.name
        key, revision = state["draft_id"], state["revision"]
        configure_changed(self.registration_status, text="등록 중 · 파일 저장·재읽기 확인 중", text_color=COLORS["text_secondary"])
        def registered(value):
            publication, result = value
            self._display(result)
            self.workspace.set_status(f"‘{publication['name']}’ 플랜 파일을 저장·재읽기 확인했습니다. 동작분석에서 확인하세요. 자동 실행하지 않습니다.")
            self.workspace._refresh_jobs()
            # Refresh only the analyzed-plan list; never reset a running playlist.
            main_window = self.workspace.owner.winfo_toplevel()
            analyzer = getattr(main_window, "_views", {}).get("analyzer")
            if analyzer is not None:
                analyzer.refresh()
        self._submit(lambda: self.workspace.jobs.register_plan(job_id, key, revision, title), registered)

    def _result_error(self, detail, summary="작업 실패 · 여기를 눌러 상세 확인"):
        self.workspace.set_status(detail)
        configure_changed(self.registration_status, text=summary, text_color=COLORS["error"])

    def close(self):
        self.closed = True
        self.worker.shutdown(wait=False, cancel_futures=False)
        if self._after_id:
            self.after_cancel(self._after_id)
