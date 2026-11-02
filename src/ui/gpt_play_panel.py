"""Responsive live-play controls in the recording workspace; no auto-start."""

from pathlib import Path
import tkinter as tk
import queue
import threading
import time
import customtkinter as ctk
from PIL import Image

from ..ai_play.jobs import PlayJobs
from ..ai_play.key_policy import automatic_keys, reserved_keys
from ..ai_play.experience import memory_root
from .theme import COLORS, IOS_FONTS


STATE_LABELS = {"ready": "준비 중", "exploring": "GPT 관찰·플레이", "question": "답변 대기",
    "paused": "일시정지", "verifying": "후보 독립 재생", "reviewing_trial": "결과 확인 중",
    "complete": "검수 완료", "needs_review": "미완료 · 기록 보관", "stopped": "중지됨", "error": "오류"}


class GPTPlayPanel(ctk.CTkFrame):
    def __init__(self, parent, workspace, root, plans_dir, templates_dir):
        super().__init__(parent, fg_color="transparent")
        self.workspace = workspace
        self.jobs = PlayJobs(root / "gpt_play", plans_dir, templates_dir, lambda: workspace.connection.connected,
                             memory_dir=memory_root())
        self.targets = {}
        self.closed = False
        self._signature = self._image_id = None
        self._published = set()
        self._after_id = None
        self._log_trace = None
        self._log_sequence = self._log_lines = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=5)
        header.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(header, text="GPT 연결 ON 후 시작할 수 있습니다.", anchor="w", width=1,
            height=42, justify="left", wraplength=510, text_color=COLORS["text_muted"])
        self.status.grid(row=0, column=0, sticky="ew")
        self.interrupt_button = self._button(header, "개입·명령 F10", self._interrupt, width=125, state="disabled")
        self.interrupt_button.grid(row=0, column=1, padx=(6, 0))
        self.memory_rows = {}
        self.memory_queue = queue.Queue(maxsize=1)
        self.memory_worker = None
        self.memory_next = 0
        self.memory_signature = None
        memory_bar = ctk.CTkFrame(self, fg_color="transparent")
        memory_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=3)
        memory_bar.grid_columnconfigure(0, weight=1)
        self.memory_selection = ctk.CTkComboBox(memory_bar, values=["새 경험 시작"], state="readonly",
            command=self._select_memory, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
        self.memory_selection.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.memory_selection.set("새 경험 시작")
        self.memory_refresh = self._button(memory_bar, "기록 새로고침", self._request_memory, width=110)
        self.memory_refresh.grid(row=0, column=1)
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["bg_content"])
        self.tabs.grid(row=2, column=0, sticky="nsew")
        for label in ("플레이", "경험 기록", "플랜 검수"):
            page = self.tabs.add(label)
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(self.tabs.tab("플레이"), fg_color=COLORS["bg_content"])
        body.grid(row=0, column=0, sticky="nsew")
        self.memory_text = ctk.CTkTextbox(self.tabs.tab("경험 기록"), wrap="word", state="disabled",
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.memory_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.verification_text = ctk.CTkTextbox(self.tabs.tab("플랜 검수"), wrap="word", state="disabled",
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.verification_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self._verification_signature = None
        body.grid_columnconfigure(0, weight=1)
        self.body = body
        self._label("1. 테스트용 게임 창 선택", 0)
        selection = ctk.CTkFrame(body, fg_color="transparent")
        selection.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 7))
        selection.grid_columnconfigure(0, weight=1)
        self.target = ctk.CTkComboBox(selection, values=["새로고침 후 선택"], state="readonly",
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
        self.target.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.target.set("새로고침 후 선택")
        self.refresh = self._button(selection, "새로고침", self._refresh, width=90)
        self.refresh.grid(row=0, column=1)
        self._label("2. GPT에게 맡길 목표", 2)
        self.goal = ctk.CTkTextbox(body, height=85, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.goal.grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 7))
        self.goal.insert("1.0", "화면의 NPC를 클릭해 게임에 참가하고, 클리어한 뒤 보상을 받아라. 판단이 어려우면 질문해라.")
        self._label("3. 키 자동 허용 · 직접 입력할 필요 없습니다", 4)
        self.keys_summary = ctk.CTkLabel(body, text="", justify="left", anchor="w", wraplength=510,
            text_color=COLORS["text_muted"])
        self.keys_summary.grid(row=5, column=0, sticky="ew", padx=10, pady=(2, 7))
        self.test_environment = tk.BooleanVar(value=True)
        self.confirm = ctk.CTkCheckBox(body, text="테스트 PC 모드 · 항상 사용", variable=self.test_environment,
            state="disabled", text_color=COLORS["text_primary"])
        self.confirm.grid(row=6, column=0, sticky="w", padx=10, pady=8)
        self.warning = ctk.CTkLabel(body, text="F10 개입·중간 명령 / F11 일시정지 / F12 긴급 중지\n시작·계속은 화면 버튼 · 명령 전달은 Ctrl+Enter\nArduino 전용: 키 유지·2키 동시 입력은 GL1 펌웨어가 필요합니다. 자동 업로드하지 않습니다.\n위 목록에서 경험을 선택하면 기록을 이어갑니다. 과거 화면·입력은 자동 재생하지 않습니다.\n질문·개입 중에도 게임은 계속됩니다. 테스트 PC에서만 시작하세요.",
            justify="left", anchor="w", text_color=COLORS["warning"], wraplength=510)
        self.warning.grid(row=7, column=0, sticky="ew", padx=10, pady=5)
        self.actions = ctk.CTkFrame(body, fg_color="transparent")
        self.actions.grid(row=8, column=0, sticky="ew", padx=10, pady=8)
        self.actions.grid_columnconfigure((0, 1), weight=1)
        self.start_button = self._button(self.actions, "GPT 플레이 시작", self._start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.pause_button = self._button(self.actions, "일시정지", self.jobs.pause)
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=3)
        self.resume_button = self._button(self.actions, "계속", self._resume)
        self.resume_button.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.stop_button = self._button(self.actions, "중지", self.jobs.stop)
        self.stop_button.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=3)
        log_header = ctk.CTkFrame(body, fg_color="transparent")
        log_header.grid(row=9, column=0, sticky="ew", padx=10)
        log_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_header, text="실행 로그 · 최근 300줄", anchor="w",
            text_color=COLORS["accent_text"]).grid(row=0, column=0, sticky="ew")
        self.copy_log = self._button(log_header, "로그 복사", self._copy_log, width=85)
        self.copy_log.grid(row=0, column=1, padx=(5, 0))
        self.detail = ctk.CTkTextbox(body, height=150, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.detail.grid(row=10, column=0, sticky="ew", padx=10, pady=5)
        self.detail.configure(state="disabled")
        self.command_card = ctk.CTkFrame(body, fg_color=COLORS["bg_card"])
        self.command_card.grid(row=11, column=0, sticky="ew", padx=10, pady=5)
        self.command_card.grid_columnconfigure(0, weight=1)
        self.command_hint = ctk.CTkLabel(self.command_card, text="중간 명령 · F10으로 GPT 입력을 멈추고 여기에 지시하세요.",
            anchor="w", justify="left", wraplength=490, text_color=COLORS["accent_text"])
        self.command_hint.grid(row=0, column=0, sticky="ew", padx=10, pady=7)
        self.command = ctk.CTkTextbox(self.command_card, height=70, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.command.grid(row=1, column=0, sticky="ew", padx=10, pady=3)
        self.command.bind("<Control-Return>", lambda _event: self._send_command() or "break", add="+")
        self.command_button = self._button(self.command_card, "명령 전달 후 계속 · Ctrl+Enter", self._send_command, state="disabled")
        self.command_button.grid(row=2, column=0, sticky="ew", padx=10, pady=8)
        self.question_card = ctk.CTkFrame(body, fg_color=COLORS["bg_card"])
        self.question_card.grid(row=12, column=0, sticky="ew", padx=10, pady=5)
        self.question_card.grid_columnconfigure(0, weight=1)
        self.question_text = ctk.CTkLabel(self.question_card, text="", anchor="w", justify="left", wraplength=500,
            text_color=COLORS["text_primary"])
        self.question_text.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        self.preview = ctk.CTkLabel(self.question_card, text="")
        self.preview.grid(row=1, column=0, padx=10, pady=5)
        self.answer = ctk.CTkTextbox(self.question_card, height=70, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.answer.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self.answer_button = self._button(self.question_card, "답변 후 계속", self._answer)
        self.answer_button.grid(row=3, column=0, sticky="ew", padx=10, pady=8)
        self.question_card.grid_remove()
        self.result = ctk.CTkLabel(body, text="기존 재생 플랜·녹화·특화모드는 변경하지 않습니다.",
            anchor="w", justify="left", text_color=COLORS["text_muted"], wraplength=510)
        self.result.grid(row=13, column=0, sticky="ew", padx=10, pady=8)
        self.bind("<Configure>", self._resize, add="+")
        self._poll()

    def _select_memory(self, label):
        if self.jobs.active or self.jobs.busy:
            return
        value = self.memory_rows.get(label)
        if value:
            self.goal.delete("1.0", "end")
            self.goal.insert("1.0", value["goal"])
        self.memory_next = 0

    def _request_memory(self):
        if self.closed or (self.memory_worker and self.memory_worker.is_alive()):
            return
        selected = self.memory_rows.get(self.memory_selection.get(), {}).get("task_id")
        # The worker never reads/writes Tk widgets, including during shutdown.
        def query():
            try:
                value = self.jobs.experience_snapshot(selected)
            except Exception as exc:
                value = {"error": str(exc)}
            try:
                self.memory_queue.put_nowait(value)
            except queue.Full:
                pass
        self.memory_worker = threading.Thread(target=query, daemon=True, name="gpt-memory-view")
        self.memory_worker.start()
        self.memory_next = time.monotonic() + 3

    @staticmethod
    def _replace_text(widget, value):
        position = widget._textbox.yview()[0]
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget._textbox.yview_moveto(position)
        widget.configure(state="disabled")

    def _render_memory(self):
        if time.monotonic() >= self.memory_next:
            self._request_memory()
        try:
            value = self.memory_queue.get_nowait()
        except queue.Empty:
            return
        if value == self.memory_signature:
            return
        self.memory_signature = value
        if "error" in value:
            self._replace_text(self.memory_text, "경험을 읽지 못했습니다. 원본은 삭제하지 않았습니다.\n" + value["error"])
            return
        self.memory_rows = {f"{row['goal'][:38]} · {row['task_id'][-8:]}": row for row in value["tasks"]}
        self.memory_selection.configure(values=["새 경험 시작"] + list(self.memory_rows))
        context = value.get("context")
        if not context:
            message = "저장된 경험을 위 목록에서 선택하세요.\n게임 성공·패배와 기술 오류·미완료를 따로 보관합니다.\n기록 원본은 이 PC에 저장되며 GPT 판단에 관련 근거가 전달됩니다."
        else:
            task = context["task"]
            lines = [task["goal"], "대상: " + task["environment"]["executable"],
                     "저장 위치: " + str(self.jobs.memory_dir), "", "최근 시도 (최대 10개)"]
            names = {"running": "진행 중", "success": "성공 관찰", "defeat": "게임 패배", "technical_error": "기술 오류", "incomplete": "미완료"}
            for item in context["attempts"]:
                lines += [f"• {names.get(item['status'], item['status'])} / {item['stage']}",
                    "전략: " + item["strategy"], "관찰: " + item.get("observation", "결과 미기록"),
                    "원인 후보(미확정): " + item.get("hypothesis", "-"), "다음 실험: " + item.get("next_experiment", "-"), ""]
            lines += ["재사용 구간 (화면 조건 확인 횟수이며 게임 승률이 아닙니다)"]
            for item in context["behaviors"]:
                lines += [f"• {item['name']} | {item['status']} | 조건 확인 {item['successes']} / 불일치 {item['failures']}"]
            lines += ["", "최근 경험 근거"]
            for item in context["recent"]:
                record = item["record"]
                lines += [f"• {item['kind']}: {record.get('observation', record.get('status', ''))}",
                          "근거: " + str(record.get("frame_id", "-"))]
            checkpoint = context.get("checkpoint") or {}
            if checkpoint.get("uncertain_requests"):
                lines += ["", "주의: 전달 결과가 불확실한 과거 입력이 있습니다. 이어하기는 현재 화면부터 확인합니다."]
            message = "\n".join(lines)
        self._replace_text(self.memory_text, message)

    def _label(self, text, row):
        ctk.CTkLabel(self.body, text=text, anchor="w", text_color=COLORS["accent_text"],
            font=(IOS_FONTS["family"], 12, "bold")).grid(row=row, column=0, sticky="ew", padx=10, pady=(8, 0))

    def _button(self, parent, text, command, **kwargs):
        return self.workspace._button(parent, text, command, **kwargs)

    def _resize(self, event):
        width = max(220, int(event.width / self._get_widget_scaling()) - 65)
        self.warning.configure(wraplength=width)
        self.result.configure(wraplength=width)
        self.question_text.configure(wraplength=max(180, width - 20))
        self.status.configure(wraplength=max(180, width - 135))
        self.keys_summary.configure(wraplength=width)
        self.command_hint.configure(wraplength=max(180, width - 20))

    def _message(self, value):
        self.jobs.trace.emit("ui_message", value)
        self._render_log()

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.detail.get("1.0", "end-1c"))

    def _render_log(self):
        trace = self.jobs.trace
        changed = trace is not self._log_trace
        rows = trace.since(0 if changed else self._log_sequence)
        if not changed and not rows:
            return
        follow = changed or self.detail._textbox.yview()[1] >= .98
        self.detail.configure(state="normal")
        if changed:
            self._log_trace = trace
            self._log_sequence = self._log_lines = 0
            self.detail.delete("1.0", "end")
        for row in rows:
            level = {"warning": "주의", "error": "오류"}.get(row["level"], "진행")
            self.detail.insert("end", f"{row['time'][11:19]} [{level}] {row['message']}\n")
        self._log_lines += len(rows)
        if self._log_lines > trace.max_rows:
            self.detail.delete("1.0", f"{self._log_lines - trace.max_rows + 1}.0")
            self._log_lines = trace.max_rows
        if rows:
            self._log_sequence = rows[-1]["seq"]
        if follow:
            self.detail.see("end")
        self.detail.configure(state="disabled")

    def _refresh(self):
        if self.jobs.active or self.jobs.busy:
            return
        try:
            from ..ai_play.windows import windows
            targets = windows()
            self.targets = {f"{i}. {w.executable_name} | {w.title[:70]}": w for i, w in enumerate(targets, 1)}
            labels = list(self.targets) or ["실행 중인 대상 창이 없습니다"]
            self.target.configure(values=labels)
            self.target.set(labels[0])
        except Exception as exc:
            self.jobs.report_error("window_list_failed", exc)
            self._render_log()

    def _start(self):
        self.jobs.trace.emit("start_button", "플레이 시작 버튼 요청 · 설정과 실행 가능 상태를 확인합니다.")
        try:
            w = self.workspace
            if w.jobs.busy or w.busy or w.owner._is_recording or w.owner._starting or w.owner._stopping:
                raise ValueError("기존 녹화·분석을 끝낸 뒤 시작하세요.")
            # Standalone special-mode test dialogs bypass the ordinary player.
            pending = list(self.winfo_toplevel().winfo_children())
            while pending:
                widget = pending.pop()
                if type(widget).__name__ == "GameModeDialog":
                    raise ValueError("특화모드 창을 닫은 뒤 테스트 PC에서 시작하세요.")
                pending.extend(widget.winfo_children())
            target = self.targets.get(self.target.get())
            if target is None:
                raise ValueError("먼저 게임 창 목록을 새로고침하고 대상을 선택하세요.")
            keys = automatic_keys()
            self.jobs.start(target, self.goal.get("1.0", "end").strip(), keys, w.runtime,
                test_environment=True, task_id=self.memory_rows.get(self.memory_selection.get(), {}).get("task_id"))
        except Exception as exc:
            self.jobs.report_error("start_rejected", exc)
        self._render_log()

    def _interrupt(self):
        try:
            self.jobs.interrupt()
        except Exception as exc:
            self.jobs.report_error("intervention_rejected", exc)
            self._render_log()

    def _show_intervention(self):
        self.workspace.tabs.set("GPT 플레이")
        self.tabs.set("플레이")
        root = self.winfo_toplevel()
        root.deiconify()
        root.lift()
        root.focus_force()
        self.body.update_idletasks()
        self.body._parent_canvas.yview_moveto(self.command_card.winfo_y() / max(1, self.body.winfo_height()))
        self.command.focus_set()
        self.jobs.trace.emit("intervention_ui", "입력 해제 확인 후 WinCro 중간 명령창을 열었습니다. 명령 전달 전에는 재개하지 않습니다.")

    def _send_command(self):
        try:
            self.jobs.send_command(self.command.get("1.0", "end").strip())
            self.command.delete("1.0", "end")
        except Exception as exc:
            self.jobs.report_error("command_rejected", exc)
            self._render_log()

    def _resume(self):
        try:
            self.jobs.resume()
        except Exception as exc:
            self.jobs.report_error("resume_rejected", exc)
            self._render_log()

    def _answer(self):
        try:
            self.jobs.resume(self.answer.get("1.0", "end").strip())
            self.answer.delete("1.0", "end")
        except Exception as exc:
            self.jobs.report_error("answer_rejected", exc)
            self._render_log()

    def _refresh_controls(self):
        from .recording_workspace import configure_changed
        state = self.jobs.ui_snapshot() or {}
        connected = self.workspace.connection.connected
        active = self.jobs.active or self.jobs.busy
        enabled = connected and not active
        session = self.jobs.session
        released = bool(session and session.input_released)
        configure_changed(self.start_button, state="normal" if enabled else "disabled")
        configure_changed(self.pause_button, state="normal" if active and state.get("state") not in {"paused", "question"} else "disabled")
        configure_changed(self.stop_button, state="normal" if active else "disabled")
        configure_changed(self.resume_button, state="normal" if connected and state.get("state") == "paused" and not self.jobs.busy and released else "disabled")
        configure_changed(self.answer_button, state="normal" if connected and state.get("state") == "question" and not self.jobs.busy and released else "disabled")
        configure_changed(self.interrupt_button, state="normal" if self.jobs.active and state.get("state") not in {"complete", "needs_review", "stopped", "error"} else "disabled")
        configure_changed(self.command_button, state="normal" if connected and released and not self.jobs.busy and state.get("state") in {"paused", "question"} else "disabled")
        configure_changed(self.refresh, state="disabled" if active else "normal")
        configure_changed(self.target, state="disabled" if active else "readonly")
        configure_changed(self.goal, state="disabled" if active else "normal")
        configure_changed(self.memory_selection, state="disabled" if active else "readonly")
        configure_changed(self.start_button, text="새 실행으로 경험 이어하기" if self.memory_selection.get() != "새 경험 시작" else "GPT 플레이 시작")
        configure_changed(self.keys_summary, text="일반 키·숫자·기호·방향키·기능키 자동 사용\n제외: "
                          + ", ".join(key.upper() for key in sorted(reserved_keys())) + " · Windows 등 OS 제어키·동시 조합키")

    def _poll(self):
        if self.closed:
            return
        from .recording_workspace import configure_changed
        self._refresh_controls()
        self._render_log()
        self._render_memory()
        if self.jobs.intervention_requested.is_set() and self.jobs.session and self.jobs.session.input_released:
            self.jobs.intervention_requested.clear()
            self._show_intervention()
        state = self.jobs.ui_snapshot()
        summary = (state or {}).get("verification")
        if summary != self._verification_signature:
            self._verification_signature = summary
            if summary:
                self._replace_text(self.verification_text,
                    f"후보 v{summary['revision']}\n현재 후보 독립 성공: {summary['successes']} / {summary['required']}회\n"
                    "실제 게임 결과를 GPT가 확인한 횟수이며 모든 상황을 보증하지 않습니다.\n\n"
                    + "\n".join(summary["steps"]) + "\n\n미해결: " + (", ".join(summary["remaining"]) or "없음")
                    + "\n기존 플랜·재생목록은 덮어쓰지 않습니다. 동적 GPT 판단이 필수인 후보는 완료 등록하지 않습니다.")
        if state:
            problem = state["state"] in {"paused", "error", "needs_review"}
            configure_changed(self.status, text=f'{STATE_LABELS.get(state["state"], state["state"])} | 관찰 {state["frame_count"]}회 · 요청 {state["action_count"]}/{state["max_actions"]}\n'
                f'{state["phase"]} · 마지막 진행 후 {state["quiet_seconds"]}초',
                text_color=COLORS["warning"] if problem else COLORS["success"] if self.workspace.connection.connected else COLORS["error"])
            signature = (state["state"], state["message"], (state.get("question") or {}).get("id"))
            if signature != self._signature:
                self._signature = signature
                question = state.get("question")
                if question:
                    self.question_text.configure(text=question["context"] + "\n\n" + question["question"])
                    self.question_card.grid()
                    path = self.jobs.session.folder / "frames" / (question["observation_id"] + ".png")
                    try:
                        with Image.open(path) as raw:
                            image = raw.copy()
                        image.thumbnail((360, 180))
                        self._preview_image = ctk.CTkImage(image, image, size=image.size)
                        self.preview.configure(image=self._preview_image, text="")
                    except (OSError, ValueError):
                        self.preview.configure(image=None, text="관찰 화면을 읽지 못했습니다. 상황을 확인한 뒤 답변하세요.")
                    self.workspace.tabs.set("GPT 플레이")
                    self.tabs.set("플레이")
                    self.body.update_idletasks()
                    self.body._parent_canvas.yview_moveto(1.0)
                    self.winfo_toplevel().deiconify()
                    self.winfo_toplevel().lift()
                else:
                    self.question_card.grid_remove()
            publication = state.get("publication")
            if publication:
                configure_changed(self.result, text="동작분석에 새 플랜 등록: " + publication["name"] + "\n기존 재생목록에는 추가하지 않았습니다.")
                if publication["plan_id"] not in self._published:
                    self._published.add(publication["plan_id"])
                    self.workspace._notify_publication(publication)
            elif state.get("candidate"):
                configure_changed(self.result, text=f'후보 v{state["candidate"]["revision"]} · 재생 검수 {state["trial_count"]}회\n미완료 결과는 기존 플랜과 분리 보관합니다.')
        else:
            configure_changed(self.status, text="GPT 연결 ON · 시작 대기" if self.workspace.connection.connected else "GPT 연결 OFF · 시작할 수 없습니다.",
                text_color=COLORS["success"] if self.workspace.connection.connected else COLORS["error"])
        self._after_id = self.after(250, self._poll)

    def close(self):
        self.closed = True
        if getattr(self, "jobs", None) is not None:
            self.jobs.close()
        if getattr(self, "_after_id", None):
            self.after_cancel(self._after_id)
            self._after_id = None
