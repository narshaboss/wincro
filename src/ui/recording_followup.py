"""Explicit user answers and renewed consent for recording draft continuation."""

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .theme import COLORS


class RecordingFollowupDialog(ctk.CTkToplevel):
    def __init__(self, workspace, info, accept):
        super().__init__(workspace)
        self.workspace, self.info, self.accept = workspace, info, accept
        self.recording_path = (info.get("media") or {}).get("path")
        self.consent_dialog = None
        self.title("GPT 질문 답변 · 분석 계속")
        scale = self._get_window_scaling()
        width = max(380, min(680, int(self.winfo_screenwidth() / scale) - 60))
        height = max(330, min(530, int(self.winfo_screenheight() / scale) - 90))
        self.geometry(f"{width}x{height}+20+20")
        self.configure(fg_color=COLORS["bg_content"])
        self.transient(workspace.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure((2, 4), weight=1)
        self.grid_rowconfigure(2, minsize=50)
        self.grid_rowconfigure(4, minsize=55)
        notice = ("답변과 선택한 영상을 GPT에 다시 제공해 같은 초안의 분석·검수를 이어갑니다."
            if info.get("media") else "이전 버전의 자료 기록이 없습니다. 녹화를 다시 선택하면 기존 초안은 보존하고 답변을 반영한 새 분석을 시작합니다.")
        self.notice = ctk.CTkLabel(self, text=notice, justify="left", anchor="w", width=1,
            wraplength=width - 30, text_color=COLORS["accent_text"])
        self.notice.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        ctk.CTkLabel(self, text="GPT가 남긴 질문과 안내", anchor="w", text_color=COLORS["text_primary"]).grid(
            row=1, column=0, sticky="ew", padx=12)
        self.questions = ctk.CTkTextbox(self, height=120, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.questions.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 6))
        text = "원래 요청: " + info["goal"] + "\n\n"
        text += "\n".join(f"{index}. {question}" for index, question in enumerate(info["questions"], 1))
        if info.get("message"):
            text += "\n\nGPT 안내:\n" + info["message"]
        if not info["questions"]:
            text += "\n\n추가 질문이 없으면 답변을 비우고 누락된 검수만 이어갈 수 있습니다."
        self.questions.insert("1.0", text)
        self.questions.configure(state="disabled")
        ctk.CTkLabel(self, text="답변 / 수정 요청 (질문이 있으면 필수)", anchor="w", text_color=COLORS["text_primary"]).grid(
            row=3, column=0, sticky="ew", padx=12)
        self.answer = ctk.CTkTextbox(self, height=100, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.answer.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 6))
        media = ctk.CTkFrame(self, fg_color="transparent")
        media.grid(row=5, column=0, sticky="ew", padx=12, pady=4)
        media.grid_columnconfigure(1, weight=1)
        self.choose_button = workspace._button(media, "녹화 파일 선택", self._choose, width=120)
        self.choose_button.grid(row=0, column=0, padx=(0, 8))
        self.media_label = ctk.CTkLabel(media, text="", width=1, anchor="w", text_color=COLORS["text_secondary"])
        self.media_label.grid(row=0, column=1, sticky="ew")
        self._media_label()
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, sticky="ew", padx=12, pady=4)
        buttons.grid_columnconfigure((0, 1), weight=1)
        self.continue_button = workspace._button(buttons, "자료 확인 후 계속", self._continue)
        self.continue_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        workspace._button(buttons, "닫기", self._close).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.error = ctk.CTkLabel(self, text="예: 녹화와 같은 속도로 진행하고 녹화 끝에서 종료해주세요.",
            anchor="w", width=1, wraplength=width - 30, text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11))
        self.error.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 8))

    def _media_label(self):
        self.media_label.configure(text=Path(self.recording_path).name[-45:] if self.recording_path else "기존 분석에 사용한 녹화를 선택하세요.")

    def _choose(self):
        path = filedialog.askopenfilename(parent=self, title="분석에 제공할 녹화 선택",
            filetypes=[("녹화", "*.mp4 *.avi *.mkv *.webm *.png")])
        if path:
            self.recording_path = path
            self._media_label()

    def _continue(self):
        answer = self.answer.get("1.0", "end").strip()
        if len(answer) > 4000 or (self.info["questions"] and not answer):
            self.error.configure(text="질문에 대한 답변을 1~4,000자로 입력하세요.", text_color=COLORS["error"])
            return
        if not self.recording_path:
            self.error.configure(text="먼저 분석할 녹화 파일을 선택하세요.", text_color=COLORS["error"])
            return
        if self.consent_dialog is not None and self.consent_dialog.winfo_exists():
            self.consent_dialog.lift()
            return
        from .ai_draft_editor import MediaConsentDialog
        def accepted(path, log, excluded):
            if not self.winfo_exists():
                return False
            if str(path) != str(self.recording_path) or answer != self.answer.get("1.0", "end").strip():
                self.workspace.set_status("답변 또는 녹화가 바뀌었습니다. 자료 확인 창을 닫고 다시 계속하세요.")
                return False
            if self.accept(path, log, excluded, answer):
                self.destroy()
                return True
            return False
        self.consent_dialog = MediaConsentDialog(self.workspace, self.recording_path, accepted,
            action_text="동의하고 분석 계속")

    def _close(self):
        if self.consent_dialog is not None and self.consent_dialog.winfo_exists():
            self.consent_dialog.destroy()
        self.destroy()
