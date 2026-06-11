"""
WinCro 녹화 화면 모듈

프리미엄 카드 기반 UI 디자인
"""

import tkinter as tk
import customtkinter as ctk
import threading
import time
import ctypes
from typing import Optional
from datetime import datetime

from ..utils.logger import get_logger
from ..utils.config import get_config
from ..i18n import RECORDER, BUTTONS
from ..recorder import RecordingSession, get_screen_recorder
from ..database import Recording, get_db
from .main_window import BaseView, COLORS
from .theme import IOS_METRICS
from .ui_batcher import UiCallbackDispatcher
from .virtual_scroll import VirtualScrollFrame

logger = get_logger(__name__)


class RecorderView(BaseView):
    """녹화 화면 - 프리미엄 디자인"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self._recording_session = RecordingSession()
        self._screen_recorder = get_screen_recorder()
        self._db = get_db()

        self._is_recording = False
        self._recording_name = ""
        self._hotkey_listener = None
        self._status_update_id = None  # after 콜백 ID 저장
        self._iconify_after_id = None  # 창 최소화 after 콜백 ID
        self._stopping = False  # 녹화 종료 중 플래그 (중복 호출 방지)
        self._starting = False  # 녹화 시작 중 플래그 (중복 시작 방지)
        self._window_restored = False  # 창 복원 완료 플래그 (중복 복원 방지)
        self._stop_lock = threading.Lock()  # 녹화 종료 동기화 락
        self._async_lock = threading.Lock()  # 비동기 상태변수 동기화 락
        self._async_done = None
        self._async_error = None
        self._async_result_name = None
        self._recordings_load_generation = 0
        self._recording_items = []
        self._ui_dispatcher = UiCallbackDispatcher(self, tick_ms=20, max_callbacks_per_tick=48)

        self._setup_ui()
        self._setup_hotkeys()

    def _recorder_ui_post(self, callback):
        try:
            dispatcher = getattr(self, "_ui_dispatcher", None)
            if dispatcher is not None:
                dispatcher.post(callback)
                return
            self.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def _setup_ui(self):
        # 스크롤 가능한 메인 컨테이너 (로그 패널 확장 시 축소 가능)
        self._scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self._scroll_frame.pack(fill="both", expand=True)

        # 그리드 컨테이너
        main_frame = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 그리드 설정 (2행 2열, 균등 비율)
        main_frame.grid_columnconfigure(0, weight=1, uniform="col")
        main_frame.grid_columnconfigure(1, weight=1, uniform="col")
        main_frame.grid_rowconfigure(0, weight=1, uniform="row")
        main_frame.grid_rowconfigure(1, weight=1, uniform="row")

        # 좌상단: 녹화 시작
        record_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        record_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
        self._setup_record_card(record_frame)

        # 우상단: 녹화 상태
        status_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        status_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))
        self._setup_status_card(status_frame)

        # 좌하단: 녹화 설정
        settings_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        settings_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        self._setup_settings_card(settings_frame)

        # 우하단: 저장된 녹화
        recordings_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        recordings_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(4, 0))
        self._setup_recordings_card(recordings_frame)

    def _setup_record_card(self, parent):
        """녹화 컨트롤 카드"""
        card = self.create_card(parent, title="녹화 시작")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=15)

        # 녹화 이름 입력
        self.create_label(content, "녹화 이름", style="body").pack(anchor="w")

        self._name_entry = ctk.CTkEntry(
            content,
            placeholder_text="비워두면 자동 생성 (예: 녹화_20240118_143022)",
            height=40,
            fg_color=COLORS["bg_glass"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius"],
        )
        self._name_entry.pack(fill="x", pady=(5, 15))

        # 버튼 영역
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(fill="x")

        self._start_btn = self.create_button(
            btn_frame,
            text="⏺  녹화 시작  (F7)",
            command=self._on_start_recording,
            style="primary",
            width=160,
            height=44,
        )
        self._start_btn.pack(side="left", padx=(0, 10))

        self._stop_btn = self.create_button(
            btn_frame,
            text="⏹  녹화 중지  (F7)",
            command=self._on_stop_recording,
            style="danger",
            width=160,
            height=44,
        )
        self._stop_btn.pack(side="left")
        self._stop_btn.configure(state="disabled")

    def _setup_status_card(self, parent):
        """녹화 상태 카드"""
        card = self.create_card(parent, title="녹화 상태")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=15)

        # 상태 표시
        status_row = ctk.CTkFrame(content, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 15))

        self._status_indicator = ctk.CTkLabel(
            status_row,
            text="●",
            font=ctk.CTkFont(size=32),
            text_color=COLORS["text_muted"],
        )
        self._status_indicator.pack(side="left", padx=(0, 15))

        status_info = ctk.CTkFrame(status_row, fg_color="transparent")
        status_info.pack(side="left", fill="x", expand=True)

        self._status_label = ctk.CTkLabel(
            status_info,
            text="⏸ 대기 중",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._status_label.pack(anchor="w")

        self._status_hint = ctk.CTkLabel(
            status_info,
            text="F7 키를 눌러 녹화를 시작하세요",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
        self._status_hint.pack(anchor="w")

        # 통계
        stats_frame = ctk.CTkFrame(
            content,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=1,
            border_color=COLORS["separator"],
        )
        stats_frame.pack(fill="x")

        stats = [
            ("녹화 시간", "duration", "00:00:00"),
            ("프레임", "frame", "0"),
            ("입력 이벤트", "event", "0"),
        ]

        self._stat_labels = {}
        for i, (label, key, value) in enumerate(stats):
            stat_col = ctk.CTkFrame(stats_frame, fg_color="transparent")
            stat_col.pack(side="left", fill="x", expand=True, padx=15, pady=12)

            ctk.CTkLabel(
                stat_col,
                text=label,
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"],
            ).pack()

            self._stat_labels[key] = ctk.CTkLabel(
                stat_col,
                text=value,
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=COLORS["text_primary"],
            )
            self._stat_labels[key].pack()

    def _setup_settings_card(self, parent):
        """녹화 설정 카드"""
        card = self.create_card(parent, title="녹화 설정")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=15)

        # 설정 그리드
        settings = ctk.CTkFrame(content, fg_color="transparent")
        settings.pack(fill="x")

        # FPS 설정
        fps_row = ctk.CTkFrame(settings, fg_color="transparent")
        fps_row.pack(fill="x", pady=5)

        fps_label_frame = ctk.CTkFrame(fps_row, fg_color="transparent")
        fps_label_frame.pack(side="left")

        ctk.CTkLabel(
            fps_label_frame,
            text="녹화 FPS",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            fps_label_frame,
            text="높을수록 부드럽지만 용량 증가",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w")

        self._fps_var = ctk.StringVar(value=str(self._config.recording.fps))
        self._fps_combo = ctk.CTkComboBox(
            fps_row,
            values=["15", "30", "60"],
            variable=self._fps_var,
            width=80,
            height=32,
            fg_color=COLORS["bg_glass"],
            border_color=COLORS["border"],
            button_color=COLORS["bg_elevated"],
            button_hover_color=COLORS["bg_card_hover"],
            dropdown_fg_color=COLORS["bg_elevated"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._fps_combo.pack(side="right")

        # 체크박스 설정
        self._cursor_var = ctk.BooleanVar(value=self._config.recording.include_cursor)
        self._cursor_check = ctk.CTkCheckBox(
            settings,
            text="마우스 커서 포함",
            variable=self._cursor_var,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_secondary"],
        )
        self._cursor_check.pack(anchor="w", pady=5)

        # 입력 로그 저장 (중요 표시)
        input_log_frame = ctk.CTkFrame(settings, fg_color="transparent")
        input_log_frame.pack(anchor="w", pady=5)

        self._input_log_var = ctk.BooleanVar(value=self._config.recording.save_input_log)
        self._input_log_check = ctk.CTkCheckBox(
            input_log_frame,
            text="입력 로그 저장",
            variable=self._input_log_var,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_secondary"],
        )
        self._input_log_check.pack(side="left")

        ctk.CTkLabel(
            input_log_frame,
            text="  ⚠️ 자동화 분석에 필수",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["warning"],
        ).pack(side="left")

    def _setup_recordings_card(self, parent):
        """?? ?? ??"""
        card = self.create_card(parent, title="??? ??")
        card.pack(fill="both", expand=True)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10, 5))

        self.create_button(
            header,
            text="새로고침",
            command=self._refresh_recordings_list_async,
            style="ghost",
            width=80,
            height=28,
        ).pack(side="right")

        self._recordings_empty_label = ctk.CTkLabel(
            card,
            text="저장된 녹화가 없습니다\n녹화를 시작해보세요",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
            justify="center",
        )

        self._recordings_scroll = VirtualScrollFrame(
            card,
            item_height=72,
            buffer_count=5,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=1,
            border_color=COLORS["border"],
        )
        self._recordings_scroll.set_render_callback(self._render_recording_item)
        self._recordings_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.after(0, self._refresh_recordings_list_async)

    def _refresh_recordings_list(self):
        """?? ?? ????"""
        self._refresh_recordings_list_async()

    def _refresh_recordings_list_async(self):
        self._recordings_load_generation += 1
        current_gen = self._recordings_load_generation

        def _load():
            recordings = self._db.get_all_recordings()
            self._recorder_ui_post(lambda: self._apply_recordings_list(recordings, current_gen))

        threading.Thread(target=_load, daemon=True).start()

    def _apply_recordings_list(self, recordings, generation=None):
        if generation is not None and generation < self._recordings_load_generation:
            return

        self._recording_items = list(recordings)
        if not self._recording_items:
            self._recordings_scroll.pack_forget()
            self._recordings_empty_label.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            return

        self._recordings_empty_label.pack_forget()
        self._recordings_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)

    def _render_recording_item(self, parent, recording: Recording, index: int):
        return self._create_recording_item(recording, parent=parent)

    def _create_recording_item(self, recording: Recording, parent=None):
        """?? ?? ??"""
        item = ctk.CTkFrame(
            parent or self._recordings_scroll,
            fg_color=COLORS["bg_elevated"],
            corner_radius=IOS_METRICS["control_radius"],
            height=66,
        )
        item.pack_propagate(False)
        if parent is None:
            item.pack(fill="x", pady=3)

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=10)

        info = ctk.CTkFrame(content, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            info,
            text=recording.name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        ).pack(fill="x")

        date_str = recording.created_at.strftime("%Y-%m-%d %H:%M") if recording.created_at else ""
        ctk.CTkLabel(
            info,
            text=date_str,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
            anchor="w",
        ).pack(fill="x")

        self.create_button(
            content,
            text="삭제",
            command=lambda r=recording: self._delete_recording(r),
            style="ghost",
            width=50,
            height=28,
        ).pack(side="right")

        return item

    def _delete_recording(self, recording: Recording):
        """녹화 삭제"""
        from tkinter import messagebox

        # 잠금 체크
        if recording.locked:
            messagebox.showwarning(
                "삭제 불가",
                f"'{recording.name}'은(는) 잠금 상태입니다.\n\n"
                "삭제하려면 동작분석 탭에서 🔓해제 버튼을 눌러 잠금을 해제하세요."
            )
            return

        delete_msg = (
            f"'{recording.name}'을(를) 삭제하시겠습니까?\n\n"
            "삭제되는 항목:\n"
            "• 녹화 영상 파일\n"
            "• 입력 로그 파일\n"
            "• 관련 분석 데이터\n\n"
            "이 작업은 되돌릴 수 없습니다."
        )

        if not messagebox.askyesno("녹화 삭제", delete_msg):
            return

        try:
            if recording.id:
                self._db.delete_recording(recording.id, delete_files=True)
            self._refresh_recordings_list()
            logger.info(f"녹화 삭제: {recording.name}")
        except Exception as e:
            error_msg = (
                f"삭제 중 오류가 발생했습니다.\n\n"
                f"오류: {str(e)}\n\n"
                "해결 방법:\n"
                "• 다른 프로그램에서 파일을 닫으세요\n"
                "• 프로그램을 재시작해보세요"
            )
            messagebox.showerror("삭제 실패", error_msg)
            logger.error(f"녹화 삭제 실패: {e}")

        # 다른 뷰들도 새로고침 (분석, 실행 화면)
        main_window = self.winfo_toplevel()
        if hasattr(main_window, 'refresh_all_views'):
            main_window.refresh_all_views()

    def _sync_recordings(self):
        """원격 녹화 파일과 동기화"""
        from tkinter import messagebox
        import threading

        config = get_config()
        repo = config.update.github_repo

        if not repo:
            messagebox.showwarning(
                "설정 필요",
                "GitHub 저장소가 설정되지 않았습니다.\n\n"
                "설정 > 업데이트 설정에서 GitHub 저장소를 먼저 입력하세요."
            )
            return

        # 동기화 진행
        def sync_thread():
            try:
                from ..utils.updater import sync_recordings, upload_recording_info

                result = sync_recordings(repo)

                if result["downloaded"] > 0:
                    msg = f"동기화 완료!\n\n다운로드: {result['downloaded']}개\n건너뜀: {result['skipped']}개"
                    if result["failed"] > 0:
                        msg += f"\n실패: {result['failed']}개"
                    self.after(0, lambda: messagebox.showinfo("녹화 동기화", msg))
                    self.after(0, self._refresh_recordings_list)
                elif result["skipped"] > 0:
                    self.after(0, lambda: messagebox.showinfo(
                        "녹화 동기화",
                        f"새로운 녹화 파일이 없습니다.\n(이미 {result['skipped']}개 존재)"
                    ))
                else:
                    # 업로드 안내 표시
                    info = upload_recording_info().format(repo=repo)
                    self.after(0, lambda: messagebox.showinfo(
                        "녹화 동기화",
                        f"원격에 공유된 녹화 파일이 없습니다.\n\n{info}"
                    ))

            except Exception as e:
                logger.error(f"녹화 동기화 오류: {e}")
                self.after(0, lambda: messagebox.showerror("동기화 오류", str(e)))

        thread = threading.Thread(target=sync_thread, daemon=True)
        thread.start()
        messagebox.showinfo("녹화 동기화", "동기화를 시작합니다...\n완료되면 알림이 표시됩니다.")

    def _on_start_recording(self):
        """녹화 시작 - logger 사용 금지 (백그라운드 스레드와 데드락 발생)"""
        try:
            # 중복 시작 방지
            if self._is_recording or self._starting:
                return

            self._starting = True

            # 녹화 이름 설정
            name = self._name_entry.get().strip()
            if not name:
                name = f"녹화_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self._recording_name = name

            # UI 업데이트
            self._start_btn.configure(state="disabled")
            self._status_label.configure(text="⏳ 녹화 준비 중...")
            self._status_hint.configure(text="잠시만 기다려주세요")
            self.update_idletasks()

            # 비동기 결과 저장용 변수 초기화 (락 사용)
            with self._async_lock:
                self._async_done = None
                self._async_error = None
                self._async_result_name = None

            # GlobalHotKeys 먼저 중지 (keyboard.Listener와 훅 충돌 방지)
            # 중요: 녹화 스레드 시작 전에 반드시 중지해야 함
            self._pause_global_hotkeys()

            # 녹화 시작을 별도 스레드에서 실행 (UI 블로킹 방지)
            thread = threading.Thread(target=self._start_recording_async, args=(name,), daemon=True)
            thread.start()

            # 메인 스레드에서 폴링하여 결과 확인 (tkinter 스레드 안전)
            self._poll_async_result()

        except Exception as e:
            self._starting = False
            self._start_btn.configure(state="normal")

    def _start_recording_async(self, name: str):
        """녹화 시작 (별도 스레드에서 실행)

        주의: 이 스레드에서 logger 사용 금지 (메인 스레드 로거와 데드락 발생)
        """
        try:
            # 콜백 설정
            self._recording_session.set_trigger_capture_callback(self._on_trigger_captured)
            self._recording_session.set_f7_callback(self._stop_recording_from_hotkey)

            # 녹화 시작
            start_success = self._recording_session.start(session_name=name)

            with self._async_lock:
                if start_success:
                    self._async_result_name = name
                    self._async_done = "success"
                else:
                    self._async_done = "failed"

        except Exception as e:
            with self._async_lock:
                self._async_error = str(e)
                self._async_done = "error"

    def _poll_async_result(self, _poll_count: int = 0):
        """비동기 녹화 시작 결과를 폴링 (메인 스레드에서 실행)"""
        try:
            # 결과 확인 (락 사용)
            with self._async_lock:
                async_done = self._async_done
                async_error = self._async_error
                async_result_name = self._async_result_name

            if async_done is None:
                # 최대 100회 (~10초) 폴링 후 타임아웃
                if _poll_count >= 100:
                    logger.error("[녹화] 비동기 결과 폴링 타임아웃 (10초)")
                    self._on_recording_start_failed_with_message("녹화 시작 타임아웃 (10초)")
                    with self._async_lock:
                        self._async_done = None
                        self._async_error = None
                        self._async_result_name = None
                    return
                # 적응형 폴링: 처음 20회는 100ms, 이후 200ms
                if _poll_count < 20:
                    interval = 100
                else:
                    interval = 200
                self.after(interval, lambda: self._poll_async_result(_poll_count + 1))
                return

            if async_done == "success":
                self._on_recording_started(async_result_name)
            elif async_done == "error":
                self._on_recording_start_failed_with_message(async_error or "알 수 없는 오류")
            else:  # "failed"
                self._on_recording_start_failed()

            # 초기화 (락 사용)
            with self._async_lock:
                self._async_done = None
                self._async_error = None
                self._async_result_name = None

        except Exception as e:
            logger.error(f"폴링 오류: {e}", exc_info=True)
            self._on_recording_start_failed()

    def _on_recording_started(self, name: str):
        """녹화 시작 완료 (메인 스레드에서 호출)"""
        try:
            logger.debug(f"_on_recording_started 시작: {name}")
            self._is_recording = True
            self._starting = False  # 시작 완료
            self._stopping = False  # 종료 플래그 초기화
            self._window_restored = False  # 창 복원 플래그 초기화

            logger.debug("UI 상태 업데이트 시작")
            self._update_ui_state()

            # 캡처 엔진 표시 (_recording_session 내부의 screen_recorder 사용)
            capture_engine = self._recording_session.capture_engine
            engine_display = "DirectX" if capture_engine == "dxcam" else "GDI"

            self._status_label.configure(text=f"🔴 녹화 중 ({engine_display})")
            self._status_hint.configure(text="F7: 녹화 중지  |  F8: 트리거 이미지 캡쳐")
            self._status_indicator.configure(text_color=COLORS["error"])
            logger.info(f"녹화 시작: {name} (엔진: {capture_engine})")

            logger.debug("녹화 상태 업데이트 스케줄링")
            self._update_recording_status()

            # 전역 F8 캡쳐 비활성화 (녹화 중에는 녹화 세션에서 처리)
            logger.debug("전역 F8 캡쳐 비활성화")
            main_window = self.winfo_toplevel()
            if hasattr(main_window, 'set_recording_active'):
                main_window.set_recording_active(True)

            # GlobalHotKeys는 이미 _on_start_recording()에서 중지됨
            # F7은 InputLogger의 keyboard.Listener에서 처리됨

            # 녹화 시작 시 창 최소화 (0.3초 후 - UI 업데이트 완료 대기)
            # after ID 저장하여 녹화 종료 시 취소 가능하도록
            logger.debug("창 최소화 스케줄링")
            self._iconify_after_id = self.after(300, self._iconify_window)
            logger.debug("_on_recording_started 완료")
        except Exception as e:
            logger.error(f"_on_recording_started 오류: {e}", exc_info=True)
            # 오류 발생 시에도 기본 상태 유지
            self._is_recording = True
            self._starting = False

    def _on_recording_start_failed(self):
        """녹화 시작 실패 (메인 스레드에서 호출)"""
        self._starting = False  # 시작 실패 시 플래그 해제
        self._start_btn.configure(state="normal")
        self._status_label.configure(text="❌ 녹화 시작 실패")
        self._status_hint.configure(text="다시 시도해주세요")
        self._status_indicator.configure(text_color=COLORS["error"])
        # GlobalHotKeys 재시작 (녹화 시작 전에 중지했으므로)
        self._resume_global_hotkeys()

    def _on_recording_start_failed_with_message(self, message: str):
        """녹화 시작 실패 - 상세 메시지 표시 (메인 스레드에서 호출)"""
        from tkinter import messagebox

        self._on_recording_start_failed()

        # 힌트를 오류 메시지로 변경
        self._status_hint.configure(text="오류가 발생했습니다")

        # 사용자에게 상세 오류 메시지 표시
        messagebox.showerror(
            "녹화 시작 실패",
            f"{message}\n\n문제가 계속되면 프로그램을 재시작해보세요."
        )

    def _iconify_window(self):
        """창 최소화 (녹화 중일 때만)"""
        if self._is_recording and not self._stopping:
            try:
                main_window = self.winfo_toplevel()
                main_window.iconify()
            except (tk.TclError, RuntimeError):
                pass

    def _on_stop_recording(self):
        """녹화 중지"""
        # 락을 사용하여 중복 호출 방지 (버튼 클릭과 F7 핫키 동시 호출 방지)
        should_proceed = False
        with self._stop_lock:
            # 이미 녹화 중이 아니면 스킵
            if not self._is_recording:
                logger.debug(f"녹화 중지 스킵 (is_recording={self._is_recording})")
                return

            # _stopping이 이미 True면:
            # - F7 핫키에서 설정한 경우: 계속 진행 (첫 번째 호출)
            # - 중복 호출인 경우: 스킵 필요
            # 구분을 위해 _is_recording을 여기서 False로 설정
            if self._is_recording:
                should_proceed = True
                self._is_recording = False  # 다른 호출 차단
                if not self._stopping:
                    self._stopping = True
                    logger.info("녹화 중지 시작 (버튼 클릭)")
                else:
                    logger.info("녹화 중지 시작 (F7 핫키)")

        if not should_proceed:
            return

        # 창 최소화 예약 취소 (녹화 종료 후 다시 최소화되는 버그 방지)
        if self._iconify_after_id is not None:
            try:
                self.after_cancel(self._iconify_after_id)
                logger.debug("창 최소화 예약 취소됨")
            except (tk.TclError, ValueError):
                pass
            self._iconify_after_id = None

        # UI 즉시 업데이트 (사용자에게 피드백) - 버튼 비활성화
        self._stop_btn.configure(state="disabled")
        self._status_label.configure(text="⏳ 녹화 저장 중...")
        self._status_hint.configure(text="잠시만 기다려주세요")

        # 녹화 중지를 별도 스레드에서 실행 (UI 블로킹 방지)
        threading.Thread(target=self._stop_recording_async, daemon=True).start()

    def _stop_recording_async(self):
        """녹화 중지 (별도 스레드에서 실행) - UI 블로킹 방지"""
        try:
            result = self._recording_session.stop()
            # UI 업데이트는 메인 스레드에서
            self.after(0, lambda: self._on_recording_stopped(result))
        except Exception as e:
            logger.error(f"녹화 중지 오류: {e}")
            self.after(0, lambda: self._on_recording_stop_failed(str(e)))

    def _on_recording_stopped(self, result):
        """녹화 중지 완료 (메인 스레드에서 호출)"""
        # _is_recording은 이미 락 블록에서 False로 설정됨
        self._update_ui_state()
        self._status_label.configure(text="✅ 녹화 완료")
        self._status_hint.configure(text="녹화가 저장되었습니다. 분석 탭에서 동작을 추출하세요.")
        self._status_indicator.configure(text_color=COLORS["success"])

        # 창 복원 (F7 핫키로 이미 복원된 경우 스킵)
        if not self._window_restored:
            self._restore_window()
        self._window_restored = False  # 플래그 리셋

        # 전역 F8 캡쳐 다시 활성화
        main_window = self.winfo_toplevel()
        if hasattr(main_window, 'set_recording_active'):
            main_window.set_recording_active(False)

        # GlobalHotKeys 재시작 (녹화 중 일시 중지했던 것)
        self._resume_global_hotkeys()

        if result and result.get("video"):
            recording = Recording(
                name=self._recording_name,
                video_path=result["video"],
                input_log_path=result.get("input_log", ""),
                fps=int(self._fps_var.get()),
                created_at=datetime.now(),
            )
            self._db.create_recording(recording)
            logger.info(f"녹화 저장: {result['video']}")
            self._refresh_recordings_list()

        self._stopping = False  # 종료 완료

    def _on_recording_stop_failed(self, error_msg: str):
        """녹화 중지 실패 (메인 스레드에서 호출)"""
        self._update_ui_state()
        self._status_label.configure(text="❌ 녹화 저장 실패")
        self._status_hint.configure(text=f"오류: {error_msg}")
        self._status_indicator.configure(text_color=COLORS["error"])
        self._stopping = False

    def _restore_window(self):
        """창 복원 (Win32 API 사용 - 강화된 버전)"""
        import ctypes
        import ctypes.wintypes
        import time

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # 앱 창 찾기
            def find_app_window():
                result = []

                @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                def enum_callback(hwnd, lparam):
                    # 보이는 창만 체크
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        if "TkTopLevel" in title or self._config.ui.app_name in title:
                            result.append(hwnd)
                    return True

                user32.EnumWindows(enum_callback, 0)
                return result[0] if result else None

            wincro_hwnd = find_app_window()
            if wincro_hwnd:
                # 1. 먼저 창 복원 (SW_RESTORE = 9)
                user32.ShowWindow(wincro_hwnd, 9)

                # 2. SetForegroundWindow를 위한 트릭 - 현재 스레드에 입력 연결
                current_thread = kernel32.GetCurrentThreadId()
                foreground_thread = user32.GetWindowThreadProcessId(
                    user32.GetForegroundWindow(), None
                )

                if current_thread != foreground_thread:
                    user32.AttachThreadInput(current_thread, foreground_thread, True)

                # 3. 창을 foreground로
                user32.BringWindowToTop(wincro_hwnd)
                user32.SetForegroundWindow(wincro_hwnd)

                if current_thread != foreground_thread:
                    user32.AttachThreadInput(current_thread, foreground_thread, False)

                logger.info(f"창 복원 완료 (hwnd={wincro_hwnd})")
            else:
                # 폴백: tkinter 방식
                main_window = self.winfo_toplevel()
                main_window.deiconify()
                main_window.lift()
                main_window.focus_force()
                logger.info("창 복원 완료 (tkinter 폴백)")
        except Exception as e:
            logger.warning(f"창 복원 오류: {e}")
            # 폴백
            try:
                main_window = self.winfo_toplevel()
                main_window.deiconify()
                main_window.lift()
                main_window.focus_force()
            except (tk.TclError, RuntimeError):
                pass

    def _update_ui_state(self):
        """UI 상태 업데이트"""
        if self._is_recording:
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
            self._name_entry.configure(state="disabled")
            self._fps_combo.configure(state="disabled")
        else:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._name_entry.configure(state="normal")
            self._fps_combo.configure(state="normal")

    def _on_trigger_captured(self, filepath: str):
        """F8 트리거 이미지 캡쳐 완료 콜백"""
        # UI 스레드에서 실행
        self.after(0, lambda: self._show_trigger_capture_notification(filepath))

    def _show_trigger_capture_notification(self, filepath: str):
        """트리거 이미지 캡쳐 알림 표시"""
        from pathlib import Path
        filename = Path(filepath).name
        self._status_hint.configure(text=f"📸 트리거 이미지 캡쳐됨: {filename}")
        # 2초 후 원래 힌트로 복원
        self.after(2000, lambda: self._status_hint.configure(
            text="F7: 녹화 중지  |  F8: 트리거 이미지 캡쳐"
        ) if self._is_recording else None)

    def _update_recording_status(self):
        """녹화 상태 업데이트"""
        if not self._is_recording or self._stopping:
            self._status_update_id = None
            return

        try:
            # public 프로퍼티 사용 (캡슐화 준수) - 타임아웃 방지를 위해 빠르게 처리
            elapsed = self._recording_session.elapsed_time
            frame_count = self._recording_session.frame_count
            event_count = self._recording_session.event_count

            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)

            self._stat_labels["duration"].configure(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            self._stat_labels["frame"].configure(text=str(frame_count))
            self._stat_labels["event"].configure(text=str(event_count))

            # after ID 저장하여 취소 가능하도록
            self._status_update_id = self.after(100, self._update_recording_status)
        except Exception as e:
            logger.warning(f"녹화 상태 업데이트 오류: {e}")
            self._status_update_id = None

    def _setup_hotkeys(self):
        """단축키 설정 - pynput GlobalHotKeys 사용"""
        from pynput import keyboard
        import threading

        self._hotkey_registered = False

        def on_f7_pressed():
            """F7 키 눌림 처리"""
            logger.info(f"===== F7 감지! 녹화={self._is_recording}, 시작중={self._starting}, 종료중={self._stopping} =====")

            # 별도 스레드에서 처리 (pynput 콜백은 빠르게 반환해야 함)
            def handle_f7():
                # 녹화 중이면 중지 (종료 중이 아닐 때만)
                if self._is_recording and not self._stopping:
                    self._stop_recording_from_hotkey()
                # 녹화 중이 아니면 시작 (시작 중이 아닐 때만)
                elif not self._is_recording and not self._starting:
                    try:
                        self.after(0, self._on_start_recording)
                    except Exception as e:
                        logger.error(f"녹화 시작 요청 실패: {e}")

            threading.Thread(target=handle_f7, daemon=True).start()

        # GlobalHotKeys 사용 (백그라운드에서도 작동)
        self._global_hotkeys = keyboard.GlobalHotKeys({
            '<f7>': on_f7_pressed,
        })
        self._global_hotkeys.start()
        self._hotkey_registered = True
        self._global_hotkeys_paused = False  # 일시 중지 상태 플래그
        logger.info("===== 녹화 단축키 활성화: F7 (pynput GlobalHotKeys) =====")

    def _pause_global_hotkeys(self):
        """GlobalHotKeys 일시 중지 (녹화 중 keyboard.Listener와 충돌 방지)"""
        if hasattr(self, '_global_hotkeys') and self._global_hotkeys is not None:
            if not self._global_hotkeys_paused:
                try:
                    self._global_hotkeys.stop()
                    self._global_hotkeys_paused = True
                    logger.info("GlobalHotKeys 일시 중지됨 (녹화 중)")
                except Exception as e:
                    logger.warning(f"GlobalHotKeys 일시 중지 실패: {e}")

    def _resume_global_hotkeys(self):
        """GlobalHotKeys 재시작 (녹화 종료 후)"""
        if self._global_hotkeys_paused:
            try:
                from pynput import keyboard
                import threading

                def on_f7_pressed():
                    """F7 키 눌림 처리"""
                    logger.info(f"===== F7 감지! 녹화={self._is_recording}, 시작중={self._starting}, 종료중={self._stopping} =====")

                    def handle_f7():
                        if self._is_recording and not self._stopping:
                            self._stop_recording_from_hotkey()
                        elif not self._is_recording and not self._starting:
                            try:
                                self.after(0, self._on_start_recording)
                            except Exception as e:
                                logger.error(f"녹화 시작 요청 실패: {e}")

                    threading.Thread(target=handle_f7, daemon=True).start()

                self._global_hotkeys = keyboard.GlobalHotKeys({
                    '<f7>': on_f7_pressed,
                })
                self._global_hotkeys.start()
                self._global_hotkeys_paused = False
                logger.info("GlobalHotKeys 재시작됨 (녹화 종료)")
            except Exception as e:
                logger.warning(f"GlobalHotKeys 재시작 실패: {e}")

    def _stop_recording_from_hotkey(self):
        """F7 핫키로 녹화 중지 (별도 스레드에서 호출됨)"""
        # 락을 사용하여 경쟁 조건 방지 (2개씩 생성 버그 수정)
        with self._stop_lock:
            # 중복 호출 방지
            if not self._is_recording or self._stopping:
                logger.debug(f"F7 중지 스킵 (is_recording={self._is_recording}, stopping={self._stopping})")
                return

            # 즉시 _stopping 플래그 설정 (락 안에서 설정하여 경쟁 조건 방지)
            self._stopping = True
            logger.info("F7: 녹화 중지 요청 (stopping 플래그 설정됨)")

        # 1. 먼저 창 복원 (최소화 상태에서 after가 안될 수 있음)
        self._restore_window_win32()
        self._window_restored = True  # 창 복원 완료 플래그 설정

        # 2. UI 스레드에서 _on_stop_recording 호출
        try:
            self.after(0, self._on_stop_recording)
        except Exception as e:
            logger.error(f"F7: 녹화 중지 요청 실패: {e}")
            self._window_restored = False  # 실패 시 플래그 리셋
            self._stopping = False  # 실패 시 플래그 리셋

    def _restore_window_win32(self):
        """창 복원 - Win32 API 직접 사용 (별도 스레드에서 호출 가능, 강화된 버전)"""
        import ctypes
        import ctypes.wintypes

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # 앱 창 찾기
            result = []
            app_name = self._config.ui.app_name or "dwm"

            @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            def enum_callback(hwnd, lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if app_name in title:
                        result.append(hwnd)
                return True

            user32.EnumWindows(enum_callback, 0)

            if result:
                wincro_hwnd = result[0]

                # 1. 먼저 창 복원 (SW_RESTORE = 9)
                user32.ShowWindow(wincro_hwnd, 9)

                # 2. SetForegroundWindow를 위한 트릭
                current_thread = kernel32.GetCurrentThreadId()
                foreground_thread = user32.GetWindowThreadProcessId(
                    user32.GetForegroundWindow(), None
                )

                if current_thread != foreground_thread:
                    user32.AttachThreadInput(current_thread, foreground_thread, True)

                # 3. 창을 foreground로
                user32.BringWindowToTop(wincro_hwnd)
                user32.SetForegroundWindow(wincro_hwnd)

                if current_thread != foreground_thread:
                    user32.AttachThreadInput(current_thread, foreground_thread, False)

                logger.info(f"F7: 창 복원 완료 (hwnd={wincro_hwnd})")
            else:
                logger.warning("F7: 앱 창을 찾을 수 없음")
        except Exception as e:
            logger.error(f"F7: 창 복원 오류: {e}")

    def refresh(self):
        """뷰 새로고침"""
        self._refresh_recordings_list()

    def cleanup(self):
        """cleanup"""
        dispatcher = getattr(self, "_ui_dispatcher", None)
        if dispatcher is not None:
            dispatcher.close()

        # 녹화 상태 업데이트 콜백 취소
        if self._status_update_id is not None:
            try:
                self.after_cancel(self._status_update_id)
            except (tk.TclError, ValueError):
                pass
            self._status_update_id = None

        # GlobalHotKeys 정리
        if hasattr(self, '_global_hotkeys') and self._global_hotkeys is not None:
            try:
                self._global_hotkeys.stop()
                logger.debug("GlobalHotKeys 정리 완료")
            except Exception as e:
                logger.warning(f"GlobalHotKeys 정리 중 오류: {e}")
            self._global_hotkeys = None

        # 녹화 세션 정리
        if self._is_recording:
            try:
                self._recording_session.stop()
                logger.info("녹화 세션 정리 완료")
            except Exception as e:
                logger.error(f"녹화 세션 정리 중 오류: {e}")
