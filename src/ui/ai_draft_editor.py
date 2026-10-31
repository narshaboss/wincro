"""Independent AI draft review window; no original editor/player callbacks."""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .theme import COLORS, IOS_FONTS
from ..agent_bridge.contracts import walk
from ..agent_bridge.service import DraftService
from ..agent_bridge.media import MediaTools
from .ai_draft_guidance import (ROLE_TEXT, HELP_TEXT, ACTION_NAMES, authoring_request,
                                friendly_error, parse_exclusions, readable_action,
                                readable_analysis, review_progress)

NO_REFERENCE = "참고 플랜 없음 · 새 작업 만들기"


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
        self._host_lock = threading.Lock()
        self.closed = False
        self.current = None
        self._row_cache = {}
        self.serial = -1
        self.busy = False
        self.form_dirty = False
        self.form_error = None
        self.form_context = None
        self._loading_form = False
        self.last_error = None
        self.connection_path = None
        self._connection_display = None
        self._wrapped = []
        self._wrap_sizes = {}
        self._poll_id = None
        self.results = queue.Queue(maxsize=8)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-draft-ui")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.title("GPT로 플랜 만들기")
        window_scale = self._get_window_scaling()
        width = max(620, min(1060, int(self.winfo_screenwidth() / window_scale) - 40))
        height = max(480, min(760, int(self.winfo_screenheight() / window_scale) - 90))
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(min(740, width), min(580, height))
        self.configure(fg_color=COLORS["bg_content"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.status = tk.StringVar(value="처음이라면 사용방법을 읽고, 1번 자료 준비부터 시작하세요.")
        self.connection_status = tk.StringVar(value="GPT 연결 OFF")
        self.review_status = tk.StringVar()
        try:
            self._build()
            self.bind("<Configure>", self._resize_copy, add="+")
            self._poll_id = self.after(120, self._poll)
            self._refresh_choices()
        except Exception:
            self._shutdown()
            self.destroy()
            raise

    def _button(self, parent, text, command, **kwargs):
        options = dict(height=32, fg_color=COLORS["bg_elevated"], text_color=COLORS["text_primary"],
                       hover_color=COLORS["bg_card_hover"], border_width=2, border_color=COLORS["button_border"])
        options.update(kwargs)
        return ctk.CTkButton(parent, text=text, command=command, **options)

    def _label(self, parent, text="", **kwargs):
        options = dict(text_color=COLORS["text_secondary"], anchor="w", justify="left", wraplength=520)
        options.update(kwargs)
        label = ctk.CTkLabel(parent, text=text, **options)
        self._wrapped.append(label)
        # CTkLabel.bind targets its internal text widget; observe the outer frame instead.
        tk.Misc.bind(label, "<Configure>", lambda event, item=label: self._wrap_label(item), add="+")
        return label

    def _wrap_label(self, label):
        if self.closed or label.winfo_width() <= 1:
            return
        width = max(120, int(label.winfo_width() / label._get_widget_scaling()) - 4)
        if self._wrap_sizes.get(label) != width:
            self._wrap_sizes[label] = width
            label.configure(wraplength=width)

    def _section(self, title, explanation):
        card = ctk.CTkFrame(self.prepare_page, fg_color=COLORS["bg_card"], border_width=1, border_color=COLORS["border"])
        card.pack(fill="x", pady=(0, 10))
        card.grid_columnconfigure((0, 1), weight=1, uniform="section")
        self._label(card, title, text_color=COLORS["text_primary"], font=(IOS_FONTS["family"], 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 0))
        self._label(card, explanation).grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        return card

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="GPT로 플랜 만들기", text_color=COLORS["text_primary"], font=(IOS_FONTS["family"], 18, "bold")).grid(row=0, column=0, sticky="w")
        self.connection_badge = ctk.CTkLabel(top, textvariable=self.connection_status, width=118,
            fg_color=COLORS["bg_card"], corner_radius=8, text_color=COLORS["text_muted"])
        self.connection_badge.grid(row=0, column=1, padx=8)
        self._button(top, "사용방법", self._show_help, width=90).grid(row=0, column=2)
        self._label(self, ROLE_TEXT, text_color=COLORS["accent_text"]).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
        nav.grid_columnconfigure((0, 1), weight=1, uniform="pages")
        self.page_buttons = {}
        for column, (key, label) in enumerate((("prepare", "준비하기"), ("review", "초안 검토"))):
            self.page_buttons[key] = self._button(nav, label, lambda value=key: self._show_page(value))
            self.page_buttons[key].grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))
        self.page_container = ctk.CTkFrame(self, fg_color="transparent")
        self.page_container.grid(row=3, column=0, sticky="nsew", padx=14)
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)
        self.prepare_page = ctk.CTkScrollableFrame(self.page_container, fg_color=COLORS["bg_content"])
        self.review_page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.review_page.grid_columnconfigure(0, weight=1)
        self.review_page.grid_rowconfigure(1, weight=1)
        self.pages = {"prepare": self.prepare_page, "review": self.review_page}
        self._build_prepare()
        self._build_review()
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 8))
        footer.grid_columnconfigure(0, weight=1)
        self._label(footer, textvariable=self.status).grid(row=0, column=0, sticky="ew")
        self.error_button = self._button(footer, "오류 상세", self._show_error, width=82, state="disabled")
        self.error_button.grid(row=0, column=1, padx=(8, 0))
        self._show_page("prepare")
        self._update_review_controls()

    def _build_prepare(self):
        card = self._section("1. 자료 준비", "따라 만들 작업의 녹화를 추가하세요. 참고 플랜은 선택 사항이며 원본은 바뀌지 않습니다.")
        combo_style = dict(fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"],
            border_color=COLORS["border"], button_color=COLORS["bg_elevated"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_text_color=COLORS["text_primary"])
        self.combo_style = combo_style
        self._label(card, "참고할 기존 플랜 (선택)").grid(row=2, column=0, columnspan=2, sticky="ew", padx=12)
        self.plans = ctk.CTkComboBox(card, values=[NO_REFERENCE], state="readonly", **combo_style)
        self.plans.set(NO_REFERENCE)
        self.plans.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
        self.media_button = self._button(card, "녹화 / 이미지 추가", self._select_media)
        self.media_button.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        self.media_summary = self._label(card, "추가된 자료 없음 · 녹화가 없으면 참고 플랜 분석만 가능합니다.")
        self.media_summary.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 10))
        card = self._section("2. GPT 연결", "연결 준비를 켠 뒤 외부 GPT 도구의 연결 설정을 마치세요. 준비만 켜서는 분석이 시작되지 않습니다.")
        self.connection_button = self._button(card, "연결 준비 켜기", self._toggle)
        self.connection_button.grid(row=2, column=0, sticky="ew", padx=(12, 4), pady=(0, 8))
        self.setup_button = self._button(card, "연결 설정 안내", self._show_connection_setup)
        self.setup_button.grid(row=2, column=1, sticky="ew", padx=(4, 12), pady=(0, 8))
        self.connection_hint = self._label(card, "GPT 연결 OFF · 아직 외부 GPT와 통신하지 않았습니다.")
        self.connection_hint.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        card = self._section("3. GPT에 작업 요청", "원하는 작업과 반복·종료 조건을 적으세요. 요청문을 복사해 연결된 GPT 대화에 보내면 GPT가 분석합니다.")
        self.request_entry = ctk.CTkTextbox(card, height=92, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.request_entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
        self.copy_request_button = self._button(card, "GPT 요청문 복사", self._copy_request, fg_color=COLORS["accent"], text_color=COLORS["text_on_accent"], hover_color=COLORS["accent_hover"])
        self.copy_request_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        self._label(card, "자동 전송하지 않습니다. 작업 요청은 복사용이며 창을 닫으면 입력란의 글은 보관되지 않습니다.").grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        self._label(self.prepare_page, "4. 결과 확인은 '초안 검토'에서 · GPT 분석·검수를 읽고 새 플랜으로 등록하세요.").pack(fill="x", pady=(0, 8))

    def _build_review(self):
        controls = ctk.CTkFrame(self.review_page, fg_color=COLORS["bg_card"])
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        controls.grid_columnconfigure(0, weight=1)
        self._label(controls, "검토할 초안").grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        self.new_draft_button = self._button(controls, "직접 초안 만들기", self._new_draft, width=142)
        self.new_draft_button.grid(row=0, column=1, rowspan=2, padx=8, pady=6)
        self.drafts = ctk.CTkComboBox(controls, values=["초안 선택"], state="readonly", command=self._select_draft, **self.combo_style)
        self.drafts.set("초안 선택")
        self.drafts.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self._label(controls, textvariable=self.review_status).grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        self.analysis_button = self._button(controls, "GPT 분석·검수 요약 보기", self._show_analysis)
        self.analysis_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        body = ctk.CTkFrame(self.review_page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", pady=4)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        tree_frame = ctk.CTkFrame(body, fg_color=COLORS["bg_card"])
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        # Import only a scoped field element; never change the app's global ttk theme.
        if "AIDraft.field" not in style.element_names():
            style.element_create("AIDraft.field", "from", "clam", "Treeview.field")
        style.layout("AIDraft.Treeview", [("AIDraft.field", {"sticky": "nswe", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
        style.configure("AIDraft.Treeview", background=COLORS["bg_dark"], foreground=COLORS["text_primary"],
                        fieldbackground=COLORS["bg_dark"], rowheight=28)
        style.map("AIDraft.Treeview", background=[("selected", COLORS["accent"])],
                  foreground=[("selected", COLORS["text_on_accent"])])
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
            ctk.CTkLabel(detail, text=label, anchor="w", text_color=COLORS["text_primary"]).grid(row=index * 2, column=0, sticky="ew", padx=6)
            variable = tk.StringVar(master=self)
            variable.trace_add("write", lambda *_: self._dirty())
            entry = ctk.CTkEntry(detail, textvariable=variable, fg_color=COLORS["bg_dark"],
                                 text_color=COLORS["text_primary"], border_color=COLORS["border"])
            entry.grid(row=index * 2 + 1, column=0, sticky="ew", padx=6, pady=(0, 5))
            self.entries[key] = entry
            entry._ai_value = variable
        self.apply_button = self._button(detail, "이 수정 저장", self._apply_fields)
        self.apply_button.grid(row=8, column=0, sticky="ew", padx=6, pady=6)
        self.details = ctk.CTkTextbox(detail, height=180, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        self.details.grid(row=9, column=0, sticky="ew", padx=6, pady=6)
        self.details.configure(state="disabled")
        self._button(detail, "고급: 원본 설정 보기", self._show_raw_action).grid(row=10, column=0, sticky="ew", padx=6, pady=6)
        self._button(detail, "입력 취소 · 최신값 보기", self._reload_form).grid(row=11, column=0, sticky="ew", padx=6, pady=6)
        self._button(detail, "대상 이미지 미리보기", self._preview_asset).grid(row=13, column=0, sticky="ew", padx=6, pady=6)
        self.preview = ctk.CTkLabel(detail, text="등록된 PNG 이미지를 확인할 수 있습니다.", text_color=COLORS["text_secondary"], wraplength=200)
        self.preview.grid(row=14, column=0, sticky="ew", padx=6, pady=6)
        self.preview_image = None
        self.preview_context = None
        bottom = ctk.CTkFrame(self.review_page, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for column in range(3):
            bottom.grid_columnconfigure(column, weight=1)
        self.save_button = self._button(bottom, "초안 저장", self._save)
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.validate_button = self._button(bottom, "구조 검사", self._validate)
        self.validate_button.grid(row=0, column=1, sticky="ew", padx=5)
        self.publish_button = self._button(bottom, "새 플랜 등록", self._publish, fg_color=COLORS["accent"],
            text_color=COLORS["text_on_accent"], hover_color=COLORS["accent_hover"])
        self.publish_button.grid(row=0, column=2, sticky="ew", padx=(5, 0))

    def _show_page(self, key):
        self.active_page = key
        for name, page in self.pages.items():
            if name == key:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()
            self.page_buttons[name].configure(fg_color=COLORS["accent"] if name == key else COLORS["bg_elevated"],
                text_color=COLORS["text_on_accent"] if name == key else COLORS["text_primary"])
        self._resize_copy()
        self.after_idle(self._resize_copy)

    def _resize_copy(self, event=None):
        if self.closed:
            return
        if event is not None and event.widget is not self:
            return
        for label in self._wrapped:
            self._wrap_label(label)

    def _update_review_controls(self):
        message, ready = review_progress(self.current, self.form_dirty)
        self.review_status.set(message)
        editable = self.current and not self.current.get("publication") and not self.busy
        self.publish_button.configure(state="normal" if ready and not self.busy else "disabled",
            fg_color=COLORS["accent"] if ready and not self.busy else COLORS["bg_elevated"],
            text_color_disabled=COLORS["text_muted"])
        self.save_button.configure(state="normal" if editable and not self.form_dirty else "disabled")
        self.validate_button.configure(state="normal" if self.current and not self.busy else "disabled")
        self.apply_button.configure(state="normal" if editable and self.form_context else "disabled")
        self.analysis_button.configure(state="normal" if self.current else "disabled")
        self.new_draft_button.configure(state="disabled" if self.busy else "normal")
        self.media_button.configure(state="disabled" if self.busy else "normal")
        self.connection_button.configure(state="disabled" if self.busy else "normal")

    def _update_connection_indicator(self):
        connected = bool(self.host and self.host.client_connected)
        prepared = self.host is not None
        failed = bool(self.host and self.host.error)
        state = (prepared, connected, failed)
        if state == self._connection_display:
            return
        self._connection_display = state
        self.connection_status.set("GPT 연결 ON" if connected else "GPT 연결 OFF")
        self.connection_badge.configure(text_color=COLORS["success_text"] if connected else COLORS["text_muted"])
        self.connection_hint.configure(text=("연결 준비가 중단되었습니다. 준비를 끄고 다시 켜세요." if failed else
            "GPT 연결 ON · 통신 확인됨. 작업은 GPT 대화에서 요청하세요." if connected else
            "GPT 연결 OFF · 연결 준비만 켜져 있습니다. 외부 도구 설정 또는 연결 확인이 필요합니다." if prepared else
            "GPT 연결 OFF · 연결 준비를 켜고 설정 안내를 확인하세요."))

    def _show_text(self, title, content, copy_action=None):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        scale = dialog._get_window_scaling()
        width = max(420, min(700, int(dialog.winfo_screenwidth() / scale) - 40))
        height = max(350, min(560, int(dialog.winfo_screenheight() / scale) - 90))
        dialog.geometry(f"{width}x{height}+30+30")
        dialog.configure(fg_color=COLORS["bg_content"])
        text = ctk.CTkTextbox(dialog, wrap="word", fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"])
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", content)
        text.configure(state="disabled")
        if copy_action:
            self._button(dialog, "연결 설정 요청문 복사", copy_action).pack(fill="x", padx=12, pady=(0, 6))
        self._button(dialog, "닫기", dialog.destroy).pack(fill="x", padx=12, pady=(0, 12))
        dialog.transient(self)
        return dialog

    def _show_help(self):
        return self._show_text("GPT 플랜 제작 사용방법", HELP_TEXT)

    def _show_error(self):
        if self.last_error:
            self._show_text("오류 상세 (고급)", self.last_error)

    def _show_connection_setup(self):
        path = self.connection_path or "아직 없음 · 먼저 연결 준비를 켜세요."
        server = Path(__file__).resolve().parents[2] / "tools" / "wincro_mcp_server.py"
        text = ("GPT 연결 설정 안내\n\n"
            "1. WinCro에서 연결 준비를 켭니다. 이 창을 열어 두세요.\n"
            "2. 로컬 MCP를 지원하는 외부 GPT 도구에 아래 편집 서버를 등록합니다. 별도 Python과 mcp==1.25.0 환경이 필요합니다. WinCro EXE 자체를 서버 명령으로 사용하지 마세요.\n"
            "3. 등록 후 GPT에게 WinCro의 connection_status 도구를 호출해 달라고 요청합니다. 실제 통신이 확인되면 상단에 ON이 표시됩니다.\n"
            "4. 준비하기 화면에서 작업 요청문을 복사해 GPT에게 보냅니다.\n\n"
            "외부 도구 설정은 이 버튼이 자동 변경하지 않습니다. 일반 웹 채팅에 파일 경로만 붙여넣어서는 로컬 도구가 연결되지 않습니다.\n\n"
            f"연결 정보 파일 경로:\n{path}\n\n서버 스크립트:\n{server}\n"
            + ("" if server.is_file() else "주의: 이 설치에는 서버 스크립트가 없습니다. 별도 편집 도구 설치가 필요합니다.\n")
            + "\n연결 준비를 껐다 켜면 경로가 달라집니다. 새 경로로 다시 설정하세요.\n"
            "연결 파일 내용에는 인증 키가 있습니다. 파일을 채팅에 업로드하거나 내용을 공개하지 마세요. 아래 복사 버튼은 내용이 아니라 설정 요청과 경로만 복사합니다.\n\n"
            "OpenAI 공식 MCP 설정 안내:\nhttps://developers.openai.com/codex/mcp")
        return self._show_text("GPT 연결 설정 안내", text, self._copy_setup)

    def _copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_setup(self):
        if not self.connection_path:
            self.status.set("먼저 연결 준비를 켠 뒤 다시 복사하세요.")
            return
        server = Path(__file__).resolve().parents[2] / "tools" / "wincro_mcp_server.py"
        self._copy_text("WinCro 편집 전용 MCP 연결 설정을 도와줘. 실행 가능한 별도 Python(mcp==1.25.0)을 확인하고 변경할 설정을 먼저 설명해 줘.\n"
            f"서버 스크립트: {server}\n--connection-file 경로: {self.connection_path}\n"
            "연결 파일을 업로드하거나 인증 키를 출력하지 마. 연결 뒤 connection_status를 호출해 확인해 줘. 메인 PC 입력·앱 재시작·실제 재생은 하지 마.")
        self.status.set("연결 설정 요청문을 복사했습니다. GPT 대화에 붙여넣으세요. 자동 설정·전송은 하지 않았습니다.")

    def _copy_request(self):
        if self.busy:
            self.status.set("현재 작업이 끝난 뒤 요청문을 복사하세요.")
            return
        goal = self.request_entry.get("1.0", "end").strip()
        if not self.host or not self.host.client_connected:
            self.status.set("아직 GPT 연결 OFF입니다. 2번 연결 설정을 마치고 GPT에 연결 확인을 요청하세요.")
            return
        if not goal:
            self.status.set("먼저 GPT에게 요청할 작업과 종료 조건을 적어주세요.")
            return
        if len(goal) > 2000:
            self.status.set("작업 요청을 2,000자 이내로 적어주세요. 긴 설명은 GPT 대화에서 이어서 전달할 수 있습니다.")
            return
        reference = self.plan_options.get(self.plans.get())
        if not reference and not self.service.selected_media:
            self.status.set("녹화 자료를 추가하거나 참고 플랜을 선택하세요.")
            return
        self._copy_text(authoring_request(goal, reference, self.current["draft_id"] if self.current else None,
                                         bool(self.service.selected_media)))
        self.status.set("GPT 요청문을 복사했습니다. 연결된 GPT 대화에 붙여넣어 보내세요. 자동 전송하지 않았습니다.")

    def _submit(self, job, done=None):
        if self.busy or self.closed:
            return False
        self.busy = True
        self.status.set("처리 중입니다. 완료될 때까지 잠시 기다려 주세요.")
        self._update_review_controls()
        for entry in self.entries.values():
            entry.configure(state="disabled")

        def work():
            try:
                result, error = job(), None
            except Exception as exc:
                result, error = None, f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
            if not self.closed:
                self.results.put((result, error, done))

        self.worker.submit(work)
        return True

    def _poll(self):
        if self.closed:
            return
        try:
            result, error, done = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for entry in self.entries.values():
                entry.configure(state="normal")
            if error:
                self.last_error = error
                self.error_button.configure(state="normal")
                message = friendly_error(error)
                if self.form_dirty:
                    self.form_error = message
                self.status.set(message)
            elif done:
                self.last_error = None
                self.error_button.configure(state="disabled")
                done(result)
            self._refresh_choices()
            self._update_review_controls()
        if not self.busy and self.service.change_serial != self.serial and self.service.lock.acquire(blocking=False):
            try:
                self.serial = self.service.change_serial
                if self.current:
                    self._render(self.service.read_draft(self.current["draft_id"]))
                elif self.service.active_draft_id and not self.form_dirty:
                    self._render(self.service.read_draft(self.service.active_draft_id))
                self._refresh_choices()
            finally:
                self.service.lock.release()
        self._update_connection_indicator()
        self._poll_id = self.after(120, self._poll)

    def _refresh_choices(self):
        if not self.service.lock.acquire(blocking=False):
            return
        try:
            self._refresh_choices_locked()
        finally:
            self.service.lock.release()

    def _refresh_choices_locked(self):
        self.plan_options = {NO_REFERENCE: None}
        for row in self.service.list_plans():
            self.plan_options[row["name"] + " · " + row["plan_id"]] = row["plan_id"]
        self.plans.configure(values=list(self.plan_options))
        self.draft_options = {row["name"] + " · " + row["draft_id"][-8:]: row["draft_id"] for row in self.service.list_drafts()}
        self.drafts.configure(values=list(self.draft_options) or ["초안 선택"])

    def _new_draft(self):
        if self.busy:
            return
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
        self.drafts.set(state["plan"]["name"] + " · " + state["draft_id"][-8:])
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
            label = f"{path}  {rule.get('description') or rule.get('action_type')}"
            values = (ACTION_NAMES.get(rule.get("action_type"), rule.get("action_type")), rule.get("wait_after", .5))
            metadata = (parent or "", position, label, values)
            if self.tree.exists(key):
                old = self._row_cache.get(key)
                if old is None or old[:2] != metadata[:2]:
                    self.tree.move(key, parent or "", position)
                if old is None or old[2:] != metadata[2:]:
                    self.tree.item(key, text=label, values=values)
            else:
                self.tree.insert(parent or "", position, iid=key, text=label, values=values, open=True)
            self._row_cache[key] = metadata
        self._row_cache = {key: self._row_cache[key] for key in desired}
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._selected_rule()
        self._update_review_controls()

    def _selected_rule(self, _event=None):
        if self.busy:
            return
        if not self.current or not self.tree.selection():
            return
        if self.form_dirty:
            self.status.set(self.form_error or "미적용 입력 보관 중 · 적용 시 편집 당시 버전으로 충돌 검사합니다.")
            return
        key = self.tree.selection()[0]
        rule = next((r for r, *_ in walk(self.current["plan"]) if r["rule_id"] == key), None)
        if rule:
            context = (self.current["draft_id"], key, rule.get("target_image"))
            if self.preview_context != context:
                self.preview_context = context
                self.preview_image = None
                self.preview.configure(image=None, text="이미지 미리보기를 눌러 선택한 액션의 PNG를 확인하세요.")
            self.form_context = (self.current["draft_id"], self.current["revision"], key)
            self._loading_form = True
            try:
                for field, entry in self.entries.items():
                    entry.delete(0, "end")
                    entry.insert(0, str(rule.get(field, "")))
            finally:
                self._loading_form = False
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", readable_action(rule))
            self.details.configure(state="disabled")
            self._update_review_controls()

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
            self.status.set("수정 사항을 초안 파일에 저장하고 다시 읽어 확인했습니다. GPT 분석·검수는 다시 받아야 합니다.")
        self._submit(lambda: self.service.stage_edits(draft_id, revision, new_id("ui_"), [{"op": "update_rule", "rule_id": key, "fields": fields}]), applied)

    def _dirty(self, _event=None):
        if self._loading_form or not hasattr(self, "apply_button"):
            return
        self.form_dirty = True
        self.form_error = None
        self._update_review_controls()

    def _reload_form(self):
        if self.busy:
            return
        self.form_dirty = False
        self.form_error = None
        self._selected_rule()
        self._update_review_controls()
        self.status.set("입력을 취소하고 현재 초안의 최신값을 표시했습니다.")

    def _show_raw_action(self):
        if not self.current or not self.tree.selection():
            return
        key = self.tree.selection()[0]
        rule = next((r for r, *_ in walk(self.current["plan"]) if r["rule_id"] == key), None)
        if rule:
            self._show_text("원본 액션 설정 (고급 / 읽기 전용)", json.dumps({k: v for k, v in rule.items() if k != "children"}, ensure_ascii=False, indent=2))

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
            latest = next((r for r, *_ in walk(self.current["plan"]) if r["rule_id"] == key), {}) if self.current else {}
            if (self.current and self.current["draft_id"] == state["draft_id"] and latest.get("target_image") == name
                    and self.tree.selection() and self.tree.selection()[0] == key):
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
        if self.form_dirty:
            self.status.set("입력 중인 수정 사항을 먼저 저장하거나 취소하세요. 아직 파일에는 적용되지 않았습니다.")
            return
        if self.current:
            draft_id = self.current["draft_id"]
            def checked(result):
                content = f"액션 {result['rule_count']}개의 구조·참조·이미지 파일을 확인했습니다.\n\n"
                content += ("특화모드 포함: 이 창에서는 정식 등록할 수 없습니다.\n" if not result["publish_allowed"] else "새 플랜 등록 가능 여부는 GPT 분석·검수와 사용자 승인도 확인합니다.\n")
                content += "실제 화면 인식·키 입력·재생 성공은 검사하지 않았습니다."
                self.status.set("구조·자산 검사 완료 · 실제 인식·재생 검증은 별도입니다.")
                self._show_text("구조 검사 결과", content)
            self._submit(lambda: self.service.validate_draft(draft_id), checked)

    def _show_analysis(self):
        if not self.current:
            return
        return self._show_text("GPT 분석·검수 요약", readable_analysis(self.current))

    def _publish(self):
        if not self.current or self.busy or self.form_dirty:
            return
        message, ready = review_progress(self.current)
        if not ready:
            self.status.set(message)
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
        return MediaConsentDialog(self, path, self._register_media)

    def _register_media(self, path, log, excluded):
        def added(_):
            records = list(self.service.selected_media.values())
            self.media_summary.configure(text=f"추가된 자료 {len(records)}개\n" + "\n".join(str(r["path"].name) for r in records))
            self.status.set("자료 추가 완료 · GPT 대화에 분석을 요청하세요. 시간·좌표 정합성은 별도 확인이 필요합니다.")
        return self._submit(lambda: MediaTools(self.service).register(path, log, excluded), added)

    def _toggle(self):
        if self.busy:
            return
        if self.host:
            self.host.close()
            self.host = None
            self.connection_path = None
            self.plans.configure(state="readonly")
            self.connection_button.configure(text="연결 준비 켜기")
            self._update_connection_indicator()
            self.status.set("GPT 연결 OFF · 이전 연결 정보는 폐기했습니다. 저장된 초안은 보관됩니다.")
            return
        from ..agent_bridge.transport import LocalHost
        selected = self.plan_options.get(self.plans.get())

        def start():
            with self.service.lock:
                self.service.granted_sources = {selected} if selected else set()
            host = LocalHost(self.service, self.connection_dir)
            try:
                path = host.start()
                with self._host_lock:
                    if self.closed:
                        host.close()
                        return None
                    self.host = host
            except Exception:
                host.close()
                raise
            return path
        def connected(path):
            self.connection_path = path
            self.plans.configure(state="disabled")
            self.connection_button.configure(text="연결 준비 끄기")
            self._update_connection_indicator()
            self.status.set("연결 준비 완료 · 아직 GPT 연결 OFF입니다. 연결 설정 안내를 확인하세요.")
            self._show_connection_setup()
        self._submit(start, connected)

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
        with self._host_lock:
            if self.closed:
                return
            self.closed = True
            host, self.host = self.host, None
        if host:
            host.close()
        self.service.closed = True
        self.worker.shutdown(wait=False, cancel_futures=True)
        try:
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)
        except tk.TclError:
            pass


class MediaConsentDialog(ctk.CTkToplevel):
    """One explicit consent screen; no registration until the user accepts."""

    def __init__(self, parent, path, accept):
        super().__init__(parent)
        self.path = path
        self.accept = accept
        self.log = None
        self.title("GPT에 제공할 자료 확인")
        scale = self._get_window_scaling()
        width = max(420, min(650, int(self.winfo_screenwidth() / scale) - 40))
        height = max(350, min(550, int(self.winfo_screenheight() / scale) - 90))
        self.geometry(f"{width}x{height}+30+30")
        self.configure(fg_color=COLORS["bg_content"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        content = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"])
        content.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        content.grid_columnconfigure(0, weight=1)
        self.labels = []

        def label(text, row, **kwargs):
            widget = ctk.CTkLabel(content, text=text, anchor="w", justify="left", wraplength=350,
                                 text_color=COLORS["text_primary"], **kwargs)
            widget.grid(row=row, column=0, sticky="ew", padx=8, pady=5)
            self.labels.append(widget)
            return widget

        label("선택한 자료: " + Path(path).name, 0)
        label("장면 분석은 GPT가 합니다. 비밀번호·인증코드가 담긴 자료는 추가하지 마세요. 자동 민감정보 가림 기능은 없습니다.", 1)
        label("보여주지 않을 시간 구간 (선택 / 초)", 2)
        self.exclusions = tk.StringVar(master=self)
        self.exclude_entry = ctk.CTkEntry(content, textvariable=self.exclusions, placeholder_text="예: 0-5,12-18 / 없으면 빈칸",
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"], border_color=COLORS["border"])
        self.exclude_entry.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        label("키 입력 기록은 없어도 됩니다. 제외 구간을 입력하면 시간 어긋남에 따른 유출 방지를 위해 키 기록은 제공하지 않습니다.", 4)
        self.log_button = parent._button(content, "키 입력 기록 추가 (선택)", self._select_log)
        self.log_button.grid(row=5, column=0, sticky="ew", padx=8, pady=4)
        self.log_label = label("추가된 키 입력 기록 없음", 6)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure((0, 1), weight=1, uniform="actions")
        self.consent = tk.BooleanVar(master=self, value=False)
        self.consent_check = ctk.CTkCheckBox(footer, text="GPT 자료 제공에 동의", variable=self.consent,
            text_color=COLORS["text_primary"], fg_color=COLORS["accent"], checkmark_color=COLORS["text_on_accent"])
        self.consent_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.add_button = parent._button(footer, "자료 추가", self._accept, state="disabled")
        self.add_button.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        parent._button(footer, "취소", self.destroy).grid(row=1, column=1, sticky="ew", padx=(4, 0))
        self.error_label = ctk.CTkLabel(footer, text="동의 후 자료 추가를 누르세요. 취소하면 전달하지 않습니다.",
            anchor="w", justify="left", wraplength=350, text_color=COLORS["text_secondary"])
        self.error_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.labels.append(self.error_label)
        self.consent.trace_add("write", self._consent_changed)
        self.exclusions.trace_add("write", self._private_changed)
        self.bind("<Configure>", self._resize, add="+")
        self.transient(parent)

    def _resize(self, event=None):
        if event is not None and event.widget is not self:
            return
        width = max(220, int(self.winfo_width() / self.exclude_entry._get_widget_scaling()) - 65)
        for label in self.labels:
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)

    def _private_changed(self, *_):
        private = bool(self.exclusions.get().strip())
        if private:
            self.log = None
            self.log_label.configure(text="비공개 구간 사용 중 · 키 입력 기록은 제공하지 않습니다.")
        else:
            self.log_label.configure(text="추가된 키 입력 기록 없음")
        self.log_button.configure(state="disabled" if private else "normal")

    def _consent_changed(self, *_):
        agreed = self.consent.get()
        self.add_button.configure(state="normal" if agreed else "disabled")
        self.error_label.configure(text="동의 확인됨 · 자료 추가를 누르면 GPT에 제공할 자료로 등록합니다." if agreed else
                                   "동의 후 자료 추가를 누르세요. 취소하면 전달하지 않습니다.")

    def _select_log(self):
        self.log = filedialog.askopenfilename(parent=self, title="선택한 녹화의 키 입력 기록 JSON", filetypes=[("키 입력 기록", "*.json")]) or None
        self.log_label.configure(text=Path(self.log).name if self.log else "추가된 키 입력 기록 없음")

    def _accept(self):
        if not self.consent.get():
            self.error_label.configure(text="자료를 확인하고 GPT 자료 제공에 동의해주세요.")
            return
        try:
            excluded = parse_exclusions(self.exclusions.get())
        except ValueError as exc:
            self.error_label.configure(text=str(exc))
            return
        if self.accept(self.path, None if excluded else self.log, excluded):
            self.destroy()


def open_ai_draft_editor(parent):
    from ..utils.config import DATA_DIR
    existing = getattr(parent, "_ai_draft_editor", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return existing
    editor = AIDraftEditor(parent, DATA_DIR / "plans", DATA_DIR / "templates")
    parent._ai_draft_editor = editor
    return editor
