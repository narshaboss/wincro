"""
WinCro 설정 화면 모듈

애플리케이션 설정을 위한 UI를 제공합니다.
"""

import json
import tkinter as tk
import threading
import customtkinter as ctk
from typing import Optional
from pathlib import Path

from ..utils.logger import get_logger
from ..utils.config import get_config, save_config, config_manager
from ..utils.app_identity import (
    PRIMARY_APP_NAME,
    clear_random_app_name,
    ensure_random_app_name,
    get_effective_app_name,
    get_startup_entry_name,
)
from ..i18n import SETTINGS, BUTTONS, MESSAGES
from .main_window import BaseView, COLORS

logger = get_logger(__name__)


class SettingsView(BaseView):
    """
    설정 화면 클래스

    애플리케이션 설정을 위한 UI를 제공합니다.
    """

    def __init__(self, parent, **kwargs):
        """설정 뷰 초기화"""
        super().__init__(parent, **kwargs)

        self._stop_flag = threading.Event()
        self._active_threads: list = []

        self._setup_ui()
        self.after(0, self._load_settings)  # UI 렌더 후 설정 로드

    def cleanup(self) -> None:
        """리소스 정리 - 백그라운드 스레드 중지 (논블로킹)"""
        self._stop_flag.set()
        # 스레드 join을 별도 데몬 스레드에서 수행하여 UI 차단 방지
        threads_to_join = list(self._active_threads)
        self._active_threads.clear()
        if threads_to_join:
            import threading
            def _join_all():
                for t in threads_to_join:
                    try:
                        t.join(timeout=1.0)
                    except Exception:
                        pass
            threading.Thread(target=_join_all, daemon=True).start()

    def _setup_ui(self) -> None:
        """UI 구성"""
        # 메인 컨테이너
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 그리드 설정 (4행 2열)
        main_frame.grid_columnconfigure(0, weight=1, uniform="col")
        main_frame.grid_columnconfigure(1, weight=1, uniform="col")
        main_frame.grid_rowconfigure(0, weight=3, uniform="row")
        main_frame.grid_rowconfigure(1, weight=3, uniform="row")
        main_frame.grid_rowconfigure(2, weight=2)  # 업데이트 설정 행
        main_frame.grid_rowconfigure(3, weight=0)  # 저장 버튼 행 (고정)

        # 좌상단: 일반 설정
        general_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        general_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
        self._setup_general_settings(general_frame)

        # 우상단: 녹화 설정
        recording_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        recording_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))
        self._setup_recording_settings(recording_frame)

        # 좌하단: 재생 설정
        player_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        player_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        self._setup_player_settings(player_frame)

        # 우하단: 외관 설정
        appearance_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        appearance_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(4, 0))
        self._setup_appearance_settings(appearance_frame)

        # 3행: 업데이트 설정
        update_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        update_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=0, pady=(4, 0))
        self._setup_update_settings(update_frame)

        # 4행: 저장 버튼 (크게)
        save_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        save_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=0, pady=(8, 0))
        self._setup_save_button(save_frame)

    def _setup_general_settings(self, parent) -> None:
        """일반 설정 섹션"""
        card = self.create_card(parent, title=SETTINGS["general"])
        card.pack(fill="both", expand=True)

        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 프로그램 이름 섹션
        name_label = ctk.CTkLabel(
            scroll_frame,
            text="프로그램 이름",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        name_label.pack(anchor="w", padx=10, pady=(5, 5))

        name_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        name_frame.pack(fill="x", padx=10, pady=(0, 5))

        self._app_name_var = ctk.StringVar()
        app_name_entry = ctk.CTkEntry(
            name_frame,
            textvariable=self._app_name_var,
            placeholder_text=PRIMARY_APP_NAME,
            width=200,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12),
        )
        app_name_entry.pack(side="left")

        ctk.CTkLabel(
            name_frame,
            text="  ",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        # 랜덤 이름 모드 체크박스
        self._random_name_var = ctk.BooleanVar()
        random_name_check = ctk.CTkCheckBox(
            name_frame,
            text="랜덤 이름 모드",
            variable=self._random_name_var,
            command=self._on_random_name_mode_changed,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["border"],
            checkmark_color="white",
        )
        random_name_check.pack(side="left", padx=10)

        self._random_name_refresh_btn = ctk.CTkButton(
            name_frame,
            text="새 이름",
            command=self._regenerate_random_name,
            width=70,
            height=28,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["accent"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        self._random_name_refresh_btn.pack(side="left", padx=(0, 8))

        self._random_name_preview = ctk.CTkLabel(
            scroll_frame,
            text=f"현재 표시 이름: {PRIMARY_APP_NAME}",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        self._random_name_preview.pack(anchor="w", padx=12, pady=(0, 6))

        # 창 모드 섹션
        mode_label = ctk.CTkLabel(
            scroll_frame,
            text="창 모드",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        mode_label.pack(anchor="w", padx=10, pady=(10, 5))

        mode_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        mode_frame.pack(fill="x", padx=10, pady=(0, 5))

        self._window_mode_var = ctk.StringVar()
        window_mode_combo = ctk.CTkComboBox(
            mode_frame,
            values=["플레이 모드", "에디터 모드"],
            variable=self._window_mode_var,
            width=150,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12),
        )
        window_mode_combo.pack(side="left")

        ctk.CTkLabel(
            mode_frame,
            text="  (플레이 모드: 간편 실행)",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=10)

        # 체크박스 프레임
        options_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        options_frame.pack(fill="x", padx=10, pady=(0, 10))

        # 실행 전 확인
        self._confirm_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            options_frame, SETTINGS["confirm_before_run"], self._confirm_var,
            help_text="실행 시 확인창 표시"
        )

        # 실행 시 최소화
        self._minimize_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            options_frame, SETTINGS["minimize_on_run"], self._minimize_var,
            help_text="자동화 중 창 숨기기"
        )

        # 툴팁 표시
        self._tooltips_var = ctk.BooleanVar()
        self._create_checkbox(
            options_frame, SETTINGS["show_tooltips"], self._tooltips_var
        )

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=5)

        # 관리자 권한 섹션
        admin_label = ctk.CTkLabel(
            scroll_frame,
            text="관리자 권한",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        admin_label.pack(anchor="w", padx=10, pady=(5, 5))

        # 관리자 상태 표시 프레임
        admin_status_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        admin_status_frame.pack(fill="x", padx=10, pady=(0, 5))

        # 현재 상태 레이블
        from ..utils.admin import is_admin
        status_text = "관리자 권한으로 실행 중" if is_admin() else "일반 권한으로 실행 중"
        status_color = COLORS["success"] if is_admin() else COLORS["text_muted"]

        self._admin_status_label = ctk.CTkLabel(
            admin_status_frame,
            text=f"현재: {status_text}",
            font=ctk.CTkFont(size=11),
            text_color=status_color,
        )
        self._admin_status_label.pack(side="left")

        # 관리자로 재시작 버튼 (관리자가 아닐 때만 표시)
        if not is_admin():
            self._restart_admin_btn = ctk.CTkButton(
                admin_status_frame,
                text="관리자로 재시작",
                command=self._restart_as_admin,
                width=100,
                height=26,
                fg_color=COLORS["warning"],
                hover_color="#c9a227",
                text_color="#000000",
                font=ctk.CTkFont(size=11),
            )
            self._restart_admin_btn.pack(side="right")

        # 관리자 권한 옵션 프레임
        admin_options = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        admin_options.pack(fill="x", padx=10, pady=(0, 5))

        # 시작 시 관리자 권한으로 실행
        self._run_as_admin_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            admin_options, "시작 시 관리자 권한 요청", self._run_as_admin_var,
            help_text="앱 시작 시 UAC 권한 상승"
        )

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=5)

        # 아두이노 설정 레이블
        arduino_label = ctk.CTkLabel(
            scroll_frame,
            text="아두이노 (HID 입력)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        arduino_label.pack(anchor="w", padx=10, pady=(5, 5))

        # 아두이노 체크박스 프레임
        arduino_options = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        arduino_options.pack(fill="x", padx=10, pady=(0, 5))

        # 아두이노 사용
        self._arduino_enabled_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            arduino_options, "아두이노 사용", self._arduino_enabled_var,
            help_text="하드웨어 입력 (안티치트 우회)"
        )

        # 자동 연결
        self._arduino_auto_var = ctk.BooleanVar()
        self._create_checkbox(arduino_options, "시작 시 자동 연결", self._arduino_auto_var)

        self._arduino_require_playback_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            arduino_options, "아두이노 활성시 재생", self._arduino_require_playback_var,
            help_text="켜면 아두이노가 연결되지 않은 상태에서는 재생을 시작하지 않습니다"
        )

        self._arduino_strict_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            arduino_options, "아두이노 전용 입력", self._arduino_strict_var,
            help_text="클릭/키/스크롤은 소프트웨어 fallback 없이 Arduino HID를 우선 사용합니다. 마우스 이동은 현재 소프트웨어 이동을 유지합니다"
        )

        # COM 포트 설정 그리드
        arduino_grid = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        arduino_grid.pack(fill="x", padx=10, pady=(0, 5))

        # COM 포트 선택
        self.create_label(arduino_grid, text="COM 포트", style="caption").grid(
            row=0, column=0, padx=(0, 10), pady=4, sticky="w"
        )

        self._arduino_port_var = ctk.StringVar()
        self._arduino_port_combo = ctk.CTkComboBox(
            arduino_grid,
            values=[""],
            variable=self._arduino_port_var,
            width=100,
            height=28,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12),
        )
        self._arduino_port_combo.grid(row=0, column=1, pady=4, sticky="w")

        # 포트 스캔 버튼
        scan_btn = ctk.CTkButton(
            arduino_grid,
            text="스캔",
            command=self._scan_com_ports,
            width=50,
            height=28,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["accent"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        scan_btn.grid(row=0, column=2, padx=(5, 0), pady=4)

        # Baud Rate 선택
        self.create_label(arduino_grid, text="Baud Rate", style="caption").grid(
            row=1, column=0, padx=(0, 10), pady=4, sticky="w"
        )

        self._arduino_baud_var = ctk.StringVar()
        baud_combo = ctk.CTkComboBox(
            arduino_grid,
            values=["9600", "19200", "38400", "57600", "115200"],
            variable=self._arduino_baud_var,
            width=100,
            height=28,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12),
        )
        baud_combo.grid(row=1, column=1, pady=4, sticky="w")

        # 연결 테스트 버튼
        self._arduino_test_btn = ctk.CTkButton(
            arduino_grid,
            text="테스트",
            command=self._test_arduino_connection,
            width=50,
            height=28,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
        )
        self._arduino_test_btn.grid(row=1, column=2, padx=(5, 0), pady=4)

        # 연결 상태 표시 프레임
        status_frame = ctk.CTkFrame(scroll_frame, fg_color=COLORS["bg_dark"], corner_radius=6)
        status_frame.pack(fill="x", padx=10, pady=(5, 10))

        status_inner = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_inner.pack(fill="x", padx=10, pady=8)

        # 상태 인디케이터 (원형)
        self._arduino_status_dot = ctk.CTkLabel(
            status_inner,
            text="●",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_muted"],
            width=20,
        )
        self._arduino_status_dot.pack(side="left")

        # 상태 텍스트
        self._arduino_status_label = ctk.CTkLabel(
            status_inner,
            text="연결 안됨",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
        self._arduino_status_label.pack(side="left", padx=(5, 0))

        # 연결/해제 버튼
        self._arduino_connect_btn = ctk.CTkButton(
            status_inner,
            text="연결",
            command=self._toggle_arduino_connection,
            width=60,
            height=26,
            fg_color=COLORS["success"],
            hover_color="#45a049",
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
        )
        self._arduino_connect_btn.pack(side="right")

        # 펌웨어 업로드 버튼
        self._arduino_upload_btn = ctk.CTkButton(
            status_inner,
            text="펌웨어 업로드",
            command=self._upload_arduino_firmware,
            width=90,
            height=26,
            fg_color=COLORS["accent"],
            hover_color="#1a5fb4",
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
        )
        self._arduino_upload_btn.pack(side="right", padx=(0, 5))

        # 아두이노 연결 객체
        self._arduino_serial = None

        # 초기 포트 스캔
        self.after(500, self._scan_com_ports)

        # 시작 시 자동 연결 시도
        self.after(1500, self._auto_connect_arduino)

    def _setup_recording_settings(self, parent) -> None:
        """녹화 설정 섹션"""
        card = self.create_card(parent, title=SETTINGS["recording"])
        card.pack(fill="both", expand=True)

        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 설정 그리드
        grid_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        grid_frame.pack(fill="x", padx=10, pady=(0, 10))

        # FPS
        self._fps_var = ctk.StringVar()
        self._create_setting_row_with_help(
            grid_frame, "FPS", self._fps_var, ["15", "30", "60"], row=0,
            help_text="초당 캡처 횟수"
        )

        # 화질
        self._quality_var = ctk.StringVar()
        self._create_setting_row_with_help(
            grid_frame, "화질", self._quality_var, ["low", "medium", "high"], row=1,
            help_text="low=작음, high=선명"
        )

        # 체크박스 프레임
        options_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        options_frame.pack(fill="x", padx=10, pady=(0, 10))

        # 마우스 커서 포함
        self._cursor_var = ctk.BooleanVar()
        self._create_checkbox(options_frame, "마우스 커서 포함", self._cursor_var)

        # 입력 로그 저장
        self._input_log_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            options_frame, "입력 로그 저장", self._input_log_var,
            help_text="자동화 분석에 필요"
        )

    def _setup_player_settings(self, parent) -> None:
        """재생 설정 섹션"""
        card = self.create_card(parent, title=SETTINGS["playback"])
        card.pack(fill="both", expand=True)

        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 설정 그리드
        grid_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        grid_frame.pack(fill="x", padx=10, pady=(0, 10))

        # 기본 실행 속도
        self._speed_var = ctk.StringVar()
        self._create_setting_row_with_help(
            grid_frame,
            "실행 속도",
            self._speed_var,
            ["0.5", "0.75", "1.0", "1.25", "1.5", "2.0"],
            row=0,
            help_text="0.5=느림, 1.0=보통, 2.0=빠름"
        )

        # 기본 대기 시간
        self._wait_var = ctk.StringVar()
        self._create_entry_row_with_help(
            grid_frame, "대기시간(ms)", self._wait_var, row=1,
            help_text="동작 후 대기 (1000=1초)"
        )

        # 재시도 횟수
        self._retry_var = ctk.StringVar()
        self._create_entry_row_with_help(
            grid_frame, "재시도 횟수", self._retry_var, row=2,
            help_text="실패 시 재시도"
        )

        # 긴급 중지 키
        self._stop_key_var = ctk.StringVar()
        self._create_setting_row_with_help(
            grid_frame,
            "중지 키",
            self._stop_key_var,
            ["escape", "f12", "pause"],
            row=3,
            help_text="2번 누르면 즉시 중지"
        )

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=10)

        # 시작 설정
        start_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        start_frame.pack(fill="x", padx=10)

        self._auto_start_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            start_frame,
            "윈도우 시작시 자동실행",
            self._auto_start_var,
            help_text="PC 부팅시 자동 시작"
        )

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=10)

        # 플랜 목록 로드
        self._auto_run_plan_list = self._load_plan_list()

        # 플랜 순서 실행 설정
        seq_label = ctk.CTkLabel(
            scroll_frame,
            text="플랜 순서",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        seq_label.pack(anchor="w", padx=10)

        seq_auto_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        seq_auto_frame.pack(fill="x", padx=10, pady=(5, 5))

        self._auto_run_enabled_var = ctk.BooleanVar()
        self._create_checkbox_with_help(
            seq_auto_frame,
            "프로그램 시작 시 자동실행",
            self._auto_run_enabled_var,
            help_text="앱 시작 시 아래 플랜 순서대로 자동 실행"
        )

        # 플랜 추가 드롭다운 + 추가 버튼
        seq_add_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        seq_add_frame.pack(fill="x", padx=10, pady=(0, 5))

        seq_plan_names = [p["name"] for p in self._auto_run_plan_list] if self._auto_run_plan_list else ["(플랜 없음)"]
        self._seq_plan_add_var = ctk.StringVar(value=seq_plan_names[0] if seq_plan_names else "")
        self._seq_plan_add_dropdown = ctk.CTkComboBox(
            seq_add_frame,
            variable=self._seq_plan_add_var,
            values=seq_plan_names,
            width=200,
            height=28,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            font=ctk.CTkFont(size=11),
        )
        self._seq_plan_add_dropdown.pack(side="left")

        ctk.CTkButton(
            seq_add_frame,
            text="+ 추가",
            command=self._seq_add_plan,
            width=60,
            height=28,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(5, 0))

        # 플랜 순서 리스트
        seq_list_frame = ctk.CTkFrame(scroll_frame, fg_color=COLORS["bg_dark"], corner_radius=6)
        seq_list_frame.pack(fill="x", padx=10, pady=(0, 5))

        self._seq_listbox = tk.Listbox(
            seq_list_frame,
            height=5,
            font=("Consolas", 11),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
        )
        self._seq_listbox.pack(fill="x", padx=5, pady=5)

        # 버튼 프레임 (삭제, 위로, 아래로)
        seq_btn_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        seq_btn_frame.pack(fill="x", padx=10, pady=(0, 5))

        for btn_text, btn_cmd in [
            ("삭제", self._seq_remove_plan),
            ("▲ 위로", self._seq_move_up),
            ("▼ 아래로", self._seq_move_down),
        ]:
            ctk.CTkButton(
                seq_btn_frame,
                text=btn_text,
                command=btn_cmd,
                width=60,
                height=26,
                fg_color=COLORS["bg_card"],
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_primary"],
                font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=(0, 5))

        # 반복횟수 수정 프레임
        seq_repeat_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        seq_repeat_frame.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(
            seq_repeat_frame,
            text="반복횟수:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self._seq_repeat_var = ctk.StringVar(value="1")
        self._seq_repeat_entry = ctk.CTkEntry(
            seq_repeat_frame,
            textvariable=self._seq_repeat_var,
            width=60,
            height=26,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        self._seq_repeat_entry.pack(side="left", padx=(5, 5))

        ctk.CTkButton(
            seq_repeat_frame,
            text="적용",
            command=self._seq_apply_repeat,
            width=50,
            height=26,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
        ).pack(side="left")

        # 리스트박스 선택 시 반복횟수 표시
        self._seq_listbox.bind("<<ListboxSelect>>", self._seq_on_select)

    def _seq_add_plan(self) -> None:
        """플랜 순서 목록에 플랜 추가"""
        # json은 top-level에서 import됨

        plan_name = self._seq_plan_add_var.get()
        if not plan_name or plan_name == "(플랜 없음)":
            return
        # 경로 찾기
        plan_path = ""
        for p in self._auto_run_plan_list:
            if p["name"] == plan_name:
                plan_path = p["path"]
                break
        if not plan_path:
            return

        # 플랜 파일에서 반복횟수 읽기
        repeat_count = 1
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                repeat_count = data.get("total_repeat_count", 1) or 1
        except Exception:
            pass

        self._seq_listbox.insert(tk.END, f"{plan_name}  ({repeat_count}회)")
        # 내부 리스트에도 추가
        if not hasattr(self, '_seq_plan_paths'):
            self._seq_plan_paths = []
        if not hasattr(self, '_seq_plan_repeats'):
            self._seq_plan_repeats = []
        self._seq_plan_paths.append(plan_path)
        self._seq_plan_repeats.append(repeat_count)

    def _seq_remove_plan(self) -> None:
        """플랜 순서 목록에서 선택 항목 삭제"""
        sel = self._seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self._seq_listbox.delete(idx)
        if hasattr(self, '_seq_plan_paths') and idx < len(self._seq_plan_paths):
            self._seq_plan_paths.pop(idx)
        if hasattr(self, '_seq_plan_repeats') and idx < len(self._seq_plan_repeats):
            self._seq_plan_repeats.pop(idx)

    def _seq_move_up(self) -> None:
        """플랜 순서 목록에서 선택 항목을 위로 이동"""
        sel = self._seq_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        # 리스트박스 항목 교환
        text = self._seq_listbox.get(idx)
        self._seq_listbox.delete(idx)
        self._seq_listbox.insert(idx - 1, text)
        self._seq_listbox.selection_set(idx - 1)
        # 경로 리스트 교환
        if hasattr(self, '_seq_plan_paths') and idx < len(self._seq_plan_paths):
            self._seq_plan_paths[idx], self._seq_plan_paths[idx - 1] = \
                self._seq_plan_paths[idx - 1], self._seq_plan_paths[idx]
        # 반복횟수 리스트 교환
        if hasattr(self, '_seq_plan_repeats') and idx < len(self._seq_plan_repeats):
            self._seq_plan_repeats[idx], self._seq_plan_repeats[idx - 1] = \
                self._seq_plan_repeats[idx - 1], self._seq_plan_repeats[idx]

    def _seq_move_down(self) -> None:
        """플랜 순서 목록에서 선택 항목을 아래로 이동"""
        sel = self._seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= self._seq_listbox.size() - 1:
            return
        # 리스트박스 항목 교환
        text = self._seq_listbox.get(idx)
        self._seq_listbox.delete(idx)
        self._seq_listbox.insert(idx + 1, text)
        self._seq_listbox.selection_set(idx + 1)
        # 경로 리스트 교환
        if hasattr(self, '_seq_plan_paths') and idx < len(self._seq_plan_paths):
            self._seq_plan_paths[idx], self._seq_plan_paths[idx + 1] = \
                self._seq_plan_paths[idx + 1], self._seq_plan_paths[idx]
        # 반복횟수 리스트 교환
        if hasattr(self, '_seq_plan_repeats') and idx < len(self._seq_plan_repeats):
            self._seq_plan_repeats[idx], self._seq_plan_repeats[idx + 1] = \
                self._seq_plan_repeats[idx + 1], self._seq_plan_repeats[idx]

    def _seq_on_select(self, event=None) -> None:
        """플랜 순서 리스트 선택 시 반복횟수 표시"""
        sel = self._seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if hasattr(self, '_seq_plan_repeats') and idx < len(self._seq_plan_repeats):
            self._seq_repeat_var.set(str(self._seq_plan_repeats[idx]))

    def _seq_apply_repeat(self) -> None:
        """선택된 플랜의 반복횟수 변경"""
        sel = self._seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        try:
            new_count = int(self._seq_repeat_var.get())
            if new_count < 1:
                new_count = 1
            elif new_count > 9999:
                new_count = 9999
        except ValueError:
            return

        if not hasattr(self, '_seq_plan_repeats') or idx >= len(self._seq_plan_repeats):
            return

        self._seq_plan_repeats[idx] = new_count

        # 플랜 파일에도 반복횟수 저장 (플레이 모드와 동기화)
        plan_path = self._seq_plan_paths[idx]
        try:
            if Path(plan_path).exists():
                with open(plan_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["total_repeat_count"] = new_count
                with open(plan_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"플랜 반복횟수 저장: {Path(plan_path).stem} → {new_count}회")
        except Exception as e:
            logger.error(f"플랜 반복횟수 저장 실패: {e}")

        # 리스트박스 텍스트 갱신
        plan_name = Path(plan_path).stem
        for p in self._auto_run_plan_list:
            if p["path"] == plan_path:
                plan_name = p["name"]
                break
        self._seq_listbox.delete(idx)
        self._seq_listbox.insert(idx, f"{plan_name}  ({new_count}회)")
        self._seq_listbox.selection_set(idx)

    def _load_plan_list(self) -> list:
        """플랜 목록 로드"""
        import json
        from ..utils.config import DATA_DIR

        plans = []
        plans_dir = DATA_DIR / "plans"
        if plans_dir.exists():
            for plan_file in plans_dir.glob("*.json"):
                try:
                    with open(plan_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        plans.append({
                            "name": data.get("name", plan_file.stem),
                            "path": str(plan_file)
                        })
                except Exception:
                    pass
        return plans

    def _setup_appearance_settings(self, parent) -> None:
        """외관 설정 + 버튼 섹션"""
        card = self.create_card(parent, title=SETTINGS["appearance"])
        card.pack(fill="both", expand=True)

        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 설정 그리드
        grid_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        grid_frame.pack(fill="x", padx=10, pady=(0, 10))

        # 테마
        self._theme_var = ctk.StringVar()
        self._create_setting_row(
            grid_frame,
            SETTINGS["theme"],
            self._theme_var,
            [SETTINGS["theme_dark"], SETTINGS["theme_light"]],
            row=0,
            width=140,
        )

        # 언어
        self._language_var = ctk.StringVar()
        self._create_setting_row(
            grid_frame,
            SETTINGS["language"],
            self._language_var,
            ["한국어"],
            row=1,
            width=140,
        )

        # 구분선
        ctk.CTkFrame(scroll_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=10, pady=10)

        # 진단 도구 섹션
        diag_label = ctk.CTkLabel(
            scroll_frame,
            text="진단 도구",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        diag_label.pack(anchor="w", padx=10)

        diag_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        diag_frame.pack(fill="x", padx=10, pady=(5, 10))

        # pyautogui 테스트 버튼
        self._test_btn = self.create_button(
            diag_frame,
            text="마우스 테스트",
            command=self._test_pyautogui,
            style="secondary",
            width=100,
            height=30,
        )
        self._test_btn.pack(side="left", padx=(0, 5))

        # 전체 기능 테스트 버튼
        self._full_test_btn = self.create_button(
            diag_frame,
            text="전체 테스트",
            command=self._run_full_test,
            style="primary",
            width=100,
            height=30,
        )
        self._full_test_btn.pack(side="left")

    def _setup_update_settings(self, parent) -> None:
        """업데이트 설정 섹션 (GitHub 기반)"""
        from ..utils.config import APP_VERSION

        card = self.create_card(parent, title="업데이트 설정")
        card.pack(fill="both", expand=True)

        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
            orientation="horizontal",
        )
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 내용 프레임
        content_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        content_frame.pack(fill="both", expand=True)

        # 1행: 버전 + 저장소
        row1 = ctk.CTkFrame(content_frame, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))

        # 버전 표시
        self._version_frame = ctk.CTkFrame(row1, fg_color=COLORS["accent"], corner_radius=6)
        self._version_frame.pack(side="left", padx=(0, 15))

        self._current_version_label = ctk.CTkLabel(
            self._version_frame,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ffffff",
        )
        self._current_version_label.pack(padx=12, pady=6)

        # 최신 버전 여부 플래그
        self._is_latest_version = False

        # GitHub 저장소 입력
        ctk.CTkLabel(
            row1,
            text="저장소:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 5))

        self._github_repo_var = ctk.StringVar()
        self._github_repo_entry = ctk.CTkEntry(
            row1,
            textvariable=self._github_repo_var,
            placeholder_text="username/repo",
            width=200,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        self._github_repo_entry.pack(side="left")

        # 2행: 버튼들
        row2 = ctk.CTkFrame(content_frame, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))

        # 버전 확인 버튼
        self._check_update_btn = ctk.CTkButton(
            row2,
            text="🔍 버전 확인",
            command=self._check_for_updates,
            width=120,
            height=40,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._check_update_btn.pack(side="left", padx=(0, 10))

        # 업데이트 다운로드 버튼
        self._do_update_btn = ctk.CTkButton(
            row2,
            text="⬇️ 업데이트",
            command=self._perform_update,
            width=120,
            height=40,
            fg_color="#2ecc71",
            hover_color="#27ae60",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._do_update_btn.pack(side="left", padx=(0, 10))

        # 자동 업데이트 체크박스
        self._auto_update_var = ctk.BooleanVar(value=get_config().update.auto_check)
        self._auto_update_checkbox = ctk.CTkCheckBox(
            row2,
            text="자동 확인",
            variable=self._auto_update_var,
            command=self._toggle_auto_update,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        )
        self._auto_update_checkbox.pack(side="left")

        # 3행: 상태 표시
        row3 = ctk.CTkFrame(content_frame, fg_color=COLORS["bg_dark"], corner_radius=6)
        row3.pack(fill="x")

        self._update_status_icon = ctk.CTkLabel(
            row3,
            text="ℹ️",
            font=ctk.CTkFont(size=12),
        )
        self._update_status_icon.pack(side="left", padx=(8, 4), pady=6)

        self._update_status_label = ctk.CTkLabel(
            row3,
            text="저장소를 입력하세요",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        )
        self._update_status_label.pack(side="left", padx=(0, 8), pady=6)

        # 마지막 업데이트 시간 (우측)
        self._last_update_label = ctk.CTkLabel(
            row3,
            text="",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        self._last_update_label.pack(side="right", padx=8, pady=6)

        # 릴리즈 데이터 초기화
        self._latest_release = None

    def _setup_save_button(self, parent) -> None:
        """하단 저장 버튼 (모든 설정 저장)"""
        # 버튼 컨테이너
        btn_container = ctk.CTkFrame(parent, fg_color="transparent")
        btn_container.pack(fill="x", pady=5)

        # 왼쪽: 초기화 버튼
        self._reset_btn = ctk.CTkButton(
            btn_container,
            text="🔄 설정 초기화",
            command=self._reset_settings,
            width=120,
            height=45,
            fg_color=COLORS["warning"],
            hover_color="#c9a227",
            text_color="#000000",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._reset_btn.pack(side="left", padx=(0, 10))

        # 오른쪽: 저장 버튼
        self._save_all_btn = ctk.CTkButton(
            btn_container,
            text="💾 모든 설정 저장",
            command=self._save_all_settings,
            width=150,
            height=45,
            fg_color=COLORS["success"],
            hover_color="#45a049",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._save_all_btn.pack(side="left")

    def _save_all_settings(self) -> None:
        """모든 설정 저장"""
        from tkinter import messagebox

        try:
            # 기존 _save_settings 호출 (일반/녹화/재생/외관 설정)
            self._save_settings()

            # GitHub 저장소 저장
            repo = self._github_repo_var.get().strip()
            if repo:
                config = get_config()
                config.update.github_repo = repo
                save_config()

            # 자동 업데이트 설정 저장
            config = get_config()
            config.update.auto_check = self._auto_update_var.get()
            save_config()

            # 상태 업데이트
            self._update_status_icon.configure(text="✅")
            self._update_status_label.configure(
                text="모든 설정이 저장되었습니다",
                text_color=COLORS["success"]
            )

            messagebox.showinfo("저장 완료", "모든 설정이 저장되었습니다!")
            logger.info("모든 설정 저장 완료")

        except Exception as e:
            logger.error(f"설정 저장 오류: {e}", exc_info=True)
            messagebox.showerror("오류", f"설정 저장 중 오류가 발생했습니다:\n{e}")

    def _toggle_auto_update(self) -> None:
        """자동 업데이트 확인 설정 토글"""
        config = get_config()
        config.update.auto_check = self._auto_update_var.get()
        save_config()
        logger.info(f"자동 업데이트 확인: {config.update.auto_check}")

    def _save_github_repo(self) -> None:
        """GitHub 저장소 저장"""
        from tkinter import messagebox

        repo = self._github_repo_var.get().strip()

        if not repo:
            messagebox.showwarning("경고", "GitHub 저장소를 입력하세요.")
            return

        # 형식 검증 (username/repo)
        if "/" not in repo or repo.count("/") != 1:
            messagebox.showwarning(
                "형식 오류",
                "저장소 형식이 올바르지 않습니다.\n\n"
                "올바른 형식: username/repository\n"
                "예: myname/wincro"
            )
            return

        # 설정에 저장
        config = get_config()
        config.update.github_repo = repo
        save_config()

        # UI 업데이트
        self._update_status_icon.configure(text="✅")
        self._update_status_label.configure(
            text="저장소가 저장되었습니다",
            text_color=COLORS["success"]
        )

        messagebox.showinfo("저장 완료", f"GitHub 저장소가 저장되었습니다.\n\n{repo}")
        logger.info(f"GitHub 저장소 저장: {repo}")

    def _check_for_updates(self) -> None:
        """GitHub에서 새 버전 확인"""
        import threading

        repo = self._github_repo_var.get().strip()
        if not repo:
            from tkinter import messagebox
            messagebox.showwarning("경고", "GitHub 저장소를 먼저 입력하세요.")
            return

        # 버튼 비활성화
        self._check_update_btn.configure(state="disabled", text="확인 중...")
        self._update_status_icon.configure(text="⏳")
        self._update_status_label.configure(text="버전 확인 중...", text_color=COLORS["warning"])

        thread = threading.Thread(target=self._check_version_thread, args=(repo,), daemon=True)
        self._active_threads = [t for t in self._active_threads if t.is_alive()]
        self._active_threads.append(thread)
        thread.start()

    def _check_version_thread(self, repo: str) -> None:
        """버전 확인 스레드 - 여러 방법 시도"""
        from ..utils.config import APP_VERSION
        from ..utils.updater import check_for_update
        import urllib.request
        import urllib.error
        import ssl
        import json
        import socket
        import time

        try:
            # 캐싱된 업데이트 체크 사용 (API 제한 방지)
            result = check_for_update(repo, APP_VERSION)
            if result:
                if result.get("cached"):
                    logger.info("캐시된 업데이트 정보 사용")
                if result.get("update_available"):
                    ver = result["version"]
                    rel_data = result.get("release_data")
                    self.after(0, lambda v=ver, d=rel_data: self._show_update_available(v, d))
                else:
                    ver = APP_VERSION
                    self.after(0, lambda v=ver: self._show_up_to_date(v))
                return

            # updater 실패 시 기존 방법으로 폴백
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            logger.info(f"버전 확인 시작 (폴백): {api_url}")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/vnd.github.v3+json'
            }

            data = None
            last_error = None
            _fallback_start = time.monotonic()
            _FALLBACK_OVERALL_TIMEOUT = 20  # 전체 폴백 제한 (초)

            # 방법 1: 기본 SSL 컨텍스트
            try:
                logger.debug("방법 1: 기본 SSL 컨텍스트 시도")
                ssl_context = ssl.create_default_context()
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=8, context=ssl_context) as response:
                    data = json.loads(response.read().decode())
                logger.info("방법 1 성공")
            except Exception as e1:
                last_error = e1
                logger.warning(f"방법 1 실패: {e1}")

                if time.monotonic() - _fallback_start < _FALLBACK_OVERALL_TIMEOUT:
                    # 방법 2: SSL 검증 완화
                    try:
                        logger.debug("방법 2: SSL 검증 완화 시도")
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                        req = urllib.request.Request(api_url, headers=headers)
                        with urllib.request.urlopen(req, timeout=8, context=ssl_context) as response:
                            data = json.loads(response.read().decode())
                        logger.info("방법 2 성공")
                    except Exception as e2:
                        last_error = e2
                        logger.warning(f"방법 2 실패: {e2}")

                        if time.monotonic() - _fallback_start < _FALLBACK_OVERALL_TIMEOUT:
                            # 방법 3: SSL 컨텍스트 없이 시도
                            try:
                                logger.debug("방법 3: SSL 컨텍스트 없이 시도")
                                req = urllib.request.Request(api_url, headers=headers)
                                with urllib.request.urlopen(req, timeout=8) as response:
                                    data = json.loads(response.read().decode())
                                logger.info("방법 3 성공")
                            except Exception as e3:
                                last_error = e3
                                logger.warning(f"방법 3 실패: {e3}")

                                if time.monotonic() - _fallback_start < _FALLBACK_OVERALL_TIMEOUT:
                                    # 방법 4: 프록시 환경변수 확인 후 시도
                                    try:
                                        import os
                                        logger.debug("방법 4: 프록시 설정 확인")
                                        proxy_handler = urllib.request.ProxyHandler()
                                        opener = urllib.request.build_opener(proxy_handler)
                                        req = urllib.request.Request(api_url, headers=headers)
                                        with opener.open(req, timeout=8) as response:
                                            data = json.loads(response.read().decode())
                                        logger.info("방법 4 성공")
                                    except Exception as e4:
                                        last_error = e4
                                        logger.error(f"방법 4 실패: {e4}")
                                else:
                                    logger.warning("전체 폴백 시간 초과, 방법 4 건너뜀")
                        else:
                            logger.warning("전체 폴백 시간 초과, 방법 3-4 건너뜀")
                else:
                    logger.warning("전체 폴백 시간 초과, 방법 2-4 건너뜀")

            # 결과 처리
            if data:
                try:
                    latest_version = data.get("tag_name", "").lstrip("v")
                    current_version = APP_VERSION
                    logger.info(f"버전 비교: 최신={latest_version}, 현재={current_version}")

                    if self._compare_versions(latest_version, current_version) > 0:
                        ver = latest_version
                        rel_data = data
                        self.after(0, lambda v=ver, d=rel_data: self._show_update_available(v, d))
                    else:
                        ver = current_version
                        self.after(0, lambda v=ver: self._show_up_to_date(v))
                except Exception as e:
                    logger.error(f"버전 비교 오류: {e}")
                    self.after(0, lambda: self._update_check_failed("버전 정보 파싱 실패"))
            else:
                # 모든 방법 실패 - 사용자 친화적 메시지
                error_detail = "연결 실패"
                if last_error:
                    error_str = str(last_error)
                    reason_str = str(getattr(last_error, 'reason', ''))

                    if isinstance(last_error, urllib.error.HTTPError):
                        if last_error.code == 404:
                            error_detail = "저장소를 찾을 수 없음"
                        elif last_error.code == 403:
                            error_detail = "API 제한 - 잠시 후 재시도"
                        else:
                            error_detail = f"HTTP {last_error.code}"
                    elif isinstance(last_error, urllib.error.URLError):
                        if "SSL" in reason_str or "CERTIFICATE" in reason_str.upper():
                            error_detail = "SSL 오류 - VPN/방화벽 확인"
                        elif "Connection refused" in reason_str:
                            error_detail = "연결 거부됨"
                        elif "Name or service not known" in reason_str:
                            error_detail = "인터넷 연결 확인"
                        elif "timed out" in reason_str.lower():
                            error_detail = "시간 초과"
                        else:
                            error_detail = reason_str[:20] if reason_str else "연결 오류"
                    elif isinstance(last_error, socket.timeout):
                        error_detail = "시간 초과"
                    elif "SSL" in error_str or "CERTIFICATE" in error_str.upper():
                        error_detail = "SSL 오류"
                    else:
                        error_detail = error_str[:20]

                logger.error(f"모든 연결 방법 실패: {last_error}")
                self.after(0, lambda e=error_detail: self._update_check_failed(e))

        except Exception as e:
            # 전체 스레드 예외 발생 시 UI 복구
            logger.error(f"버전 확인 스레드 예외: {e}", exc_info=True)
            self.after(0, lambda: self._update_check_failed("예기치 않은 오류"))

    def _compare_versions(self, v1: str, v2: str) -> int:
        """버전 비교 (v1 > v2: 1, v1 == v2: 0, v1 < v2: -1)"""
        try:
            parts1 = [int(x) for x in v1.split(".")]
            parts2 = [int(x) for x in v2.split(".")]

            # 길이 맞추기
            while len(parts1) < len(parts2):
                parts1.append(0)
            while len(parts2) < len(parts1):
                parts2.append(0)

            for p1, p2 in zip(parts1, parts2):
                if p1 > p2:
                    return 1
                elif p1 < p2:
                    return -1
            return 0
        except (ValueError, AttributeError):
            return 0

    def _show_update_available(self, new_version: str, release_data: dict) -> None:
        """새 버전 사용 가능 표시"""
        try:
            from ..utils.config import APP_VERSION
            logger.info(f"새 버전 발견: {new_version} (현재: {APP_VERSION})")

            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            self._update_status_icon.configure(text="🆕")
            self._update_status_label.configure(
                text=f"새 버전: v{new_version} (현재: v{APP_VERSION})",
                text_color=COLORS["warning"]
            )
            # 상단 버전 라벨도 업데이트
            self._current_version_label.configure(text=f"🆕 v{APP_VERSION}→v{new_version}")
            # 버전 프레임 색상 변경 (안전하게)
            if hasattr(self, '_version_frame'):
                self._version_frame.configure(fg_color=COLORS["warning"])

            # 릴리즈 데이터 저장 (다운로드용)
            self._latest_release = release_data
            self._is_latest_version = False

            # 버전 저장
            config = get_config()
            config.update.last_version = new_version
            save_config()
        except Exception as e:
            logger.error(f"_show_update_available 오류: {e}", exc_info=True)

    def _show_up_to_date(self, current_version: str) -> None:
        """최신 버전 표시"""
        try:
            logger.info(f"최신 버전 확인됨: {current_version}")
            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            self._update_status_icon.configure(text="✅")
            self._update_status_label.configure(
                text=f"최신 버전입니다! (v{current_version})",
                text_color=COLORS["success"]
            )
            # 상단 버전 라벨도 업데이트
            self._current_version_label.configure(text=f"✅ v{current_version}")
            # 버전 프레임 색상 변경 (안전하게)
            if hasattr(self, '_version_frame'):
                self._version_frame.configure(fg_color=COLORS["success"])
            self._latest_release = None
            self._is_latest_version = True
        except Exception as e:
            logger.error(f"_show_up_to_date 오류: {e}", exc_info=True)

    def _update_check_failed(self, message: str) -> None:
        """버전 확인 실패"""
        try:
            logger.warning(f"버전 확인 실패: {message}")
            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            self._update_status_icon.configure(text="❌")
            self._update_status_label.configure(
                text=f"확인 실패: {message}",
                text_color=COLORS["error"]
            )
        except Exception as e:
            logger.error(f"_update_check_failed 오류: {e}", exc_info=True)

    def _fetch_latest_release_direct(self, repo: str) -> dict:
        """릴리즈 정보 직접 가져오기 (여러 방법 시도)"""
        import urllib.request
        import urllib.error
        import ssl
        import json

        try:
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/vnd.github.v3+json'
            }

            methods = [
                ("기본 SSL", lambda: ssl.create_default_context()),
                ("SSL 검증 완화", lambda: self._create_unverified_ssl_context()),
                ("SSL 없음", lambda: None),
            ]

            for method_name, get_context in methods:
                try:
                    logger.debug(f"릴리즈 가져오기 시도: {method_name}")
                    req = urllib.request.Request(api_url, headers=headers)
                    ctx = get_context()
                    if ctx:
                        with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                            return json.loads(response.read().decode())
                    else:
                        with urllib.request.urlopen(req, timeout=30) as response:
                            return json.loads(response.read().decode())
                except Exception as e:
                    logger.warning(f"{method_name} 실패: {e}")
                    continue

            return None
        except Exception as e:
            logger.error(f"릴리즈 정보 가져오기 전체 오류: {e}")
            return None

    def _create_unverified_ssl_context(self):
        """SSL 검증을 완화한 컨텍스트 생성"""
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _perform_update(self) -> None:
        """GitHub에서 업데이트 수행 - 버전 확인 없이도 가능"""
        from tkinter import messagebox
        from ..utils.config import APP_VERSION
        import threading

        repo = self._github_repo_var.get().strip()
        if not repo:
            messagebox.showwarning("경고", "GitHub 저장소를 먼저 입력하세요.")
            return

        # 최신 버전 체크 (버전 확인이 성공한 경우)
        if hasattr(self, '_is_latest_version') and self._is_latest_version:
            messagebox.showinfo("안내", "이미 최신 버전입니다!")
            return

        # 릴리즈 정보가 있으면 바로 사용
        if hasattr(self, '_latest_release') and self._latest_release:
            release = self._latest_release
            version = release.get("tag_name", "").lstrip("v")
        else:
            # 릴리즈 정보가 없으면 - 현재 버전 보여주고 물어봄
            if not messagebox.askyesno(
                "업데이트",
                f"현재 버전: v{APP_VERSION}\n\n"
                f"버전 확인을 하지 않았거나 실패했습니다.\n"
                f"최신 릴리즈를 직접 다운로드하시겠습니까?\n\n"
                f"(네트워크 문제로 버전 확인이 안 될 때 사용)"
            ):
                return

            # 직접 릴리즈 정보 가져오기 시도
            self._do_update_btn.configure(state="disabled", text="확인 중...")
            self._check_update_btn.configure(state="disabled")
            self._update_status_label.configure(text="릴리즈 정보 가져오는 중...", text_color=COLORS["warning"])
            self.update()

            release = self._fetch_latest_release_direct(repo)
            if not release:
                self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
                self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
                messagebox.showerror("오류", "릴리즈 정보를 가져올 수 없습니다.\n\n네트워크 연결을 확인해주세요.")
                return

            version = release.get("tag_name", "").lstrip("v")

        # 다운로드할 에셋 찾기 (zip 파일만 지원)
        assets = release.get("assets", [])
        zip_asset = None

        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".zip"):
                zip_asset = asset
                break

        if not zip_asset:
            self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            messagebox.showerror("오류", "다운로드 가능한 zip 파일이 없습니다.\n\nGitHub Release에 zip 파일을 첨부해주세요.")
            return

        # 다운로드 URL 검증
        download_url = zip_asset.get("browser_download_url")
        if not download_url:
            self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            messagebox.showerror("오류", "다운로드 URL을 찾을 수 없습니다.")
            return

        # 확인 메시지
        asset_to_download = zip_asset
        file_name = asset_to_download.get("name", "unknown")
        file_size = asset_to_download.get("size", 0) / (1024 * 1024)

        if not messagebox.askyesno(
            "업데이트 확인",
            f"새 버전을 다운로드합니다.\n\n"
            f"현재 버전: v{APP_VERSION}\n"
            f"새 버전: v{version}\n"
            f"파일: {file_name}\n"
            f"크기: {file_size:.1f} MB\n\n"
            f"다운로드 후 프로그램이 재시작됩니다.\n"
            f"계속하시겠습니까?"
        ):
            self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
            self._check_update_btn.configure(state="normal", text="🔍 버전 확인")
            return

        # 버튼 비활성화
        self._do_update_btn.configure(state="disabled", text="다운로드 중...")
        self._check_update_btn.configure(state="disabled")

        self._update_status_icon.configure(text="⏳")
        self._update_status_label.configure(text="다운로드 중...", text_color=COLORS["warning"])

        # 다운로드 스레드 시작
        thread = threading.Thread(
            target=self._download_update_thread,
            args=(asset_to_download, version),
            daemon=True
        )
        thread.start()

    def _download_update_thread(self, asset: dict, version: str) -> None:
        """업데이트 다운로드 스레드 (전체 폴더 교체 방식) - SSL 다중 폴백"""
        import os
        import sys
        from ..utils.update_service import (
            ssl_fallback_connect, download_file,
            extract_and_find_exe, save_update_config,
            get_update_paths, classify_error,
        )

        try:
            download_url = asset.get("browser_download_url")
            file_name = asset.get("name", "update.zip")

            if not download_url:
                self.after(0, lambda: self._update_failed("다운로드 URL이 없습니다"))
                return

            if not file_name.endswith(".zip"):
                file_name = f"{file_name}.zip"

            logger.info(f"다운로드 시작: {download_url}")
            self.after(0, lambda: self._update_status_label.configure(text="연결 중..."))

            paths = get_update_paths()
            temp_path = os.path.join(paths["temp_dir"], file_name)
            logger.info(f"다운로드 경로: {temp_path}")

            # SSL 다중 폴백 방식으로 연결 시도
            try:
                response = ssl_fallback_connect(
                    download_url,
                    on_status=lambda msg: self.after(0, lambda m=msg:
                        self._update_status_label.configure(text=m)),
                )
            except ConnectionError as e:
                self.after(0, lambda msg=str(e): self._update_failed(msg))
                return

            with response:
                total_size = int(response.headers.get('Content-Length', 0))
                logger.info(f"파일 크기: {total_size / (1024*1024):.1f} MB")
                last_ui_percent = -1

                def _on_progress(downloaded, total):
                    nonlocal last_ui_percent
                    if total > 0:
                        percent = int(downloaded / total * 100)
                        if percent >= last_ui_percent + 5 or percent == 100:
                            last_ui_percent = percent
                            mb_down = downloaded / (1024 * 1024)
                            mb_total = total / (1024 * 1024)
                            self.after(0, lambda p=percent, d=mb_down, t=mb_total:
                                self._update_status_label.configure(
                                    text=f"다운로드 중... {p}% ({d:.1f}/{t:.1f}MB)"
                                ))

                self.after(0, lambda t=total_size: self._update_status_label.configure(
                    text=f"다운로드 중... 0% (0/{t/(1024*1024):.1f}MB)"
                ))

                download_file(response, temp_path, chunk_size=131072, on_progress=_on_progress)

            self.after(0, lambda: self._update_status_label.configure(text="압축 해제 중..."))

            # 개발 모드 체크
            if not getattr(sys, 'frozen', False):
                self.after(0, lambda: self._update_success_dev(version, temp_path))
                return

            # 압축 해제 및 exe 찾기
            try:
                new_app_dir, new_exe_name = extract_and_find_exe(temp_path, paths["extract_dir"])
            except FileNotFoundError:
                self.after(0, lambda: self._update_failed("업데이트 파일에 exe가 없습니다"))
                return

            logger.info(f"새 앱 디렉토리: {new_app_dir}, 새 exe: {new_exe_name}")
            self.after(0, lambda: self._update_status_label.configure(text="업데이트 준비 중..."))

            # 배치 파일 생성 (전체 폴더 교체)
            current_exe = paths["current_exe"]
            app_dir = paths["app_dir"]
            exe_name = paths["exe_name"]
            temp_dir = paths["temp_dir"]
            extract_dir = paths["extract_dir"]
            batch_path = paths["batch_path"]
            data_dir = paths["data_dir"]
            data_backup = paths["data_backup"]

            batch_content = f'''@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   작업도우미 업데이트 v{version}
echo ========================================
echo.

echo [1/6] 프로그램 강제 종료 중...
taskkill /f /im "{exe_name}" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/6] 사용자 데이터 백업 중...
if exist "{data_dir}" (
    xcopy /E /I /Y /Q "{data_dir}" "{data_backup}" >nul 2>&1
)

echo [3/6] 기존 파일 삭제 중...
rd /s /q "{app_dir}\\_internal" 2>nul
del /q "{current_exe}" 2>nul

echo [4/6] 새 파일 복사 중...
xcopy /E /I /Y /Q "{new_app_dir}\\*" "{app_dir}\\" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] 파일 복사 실패!
    pause
    exit /b 1
)
if not exist "{app_dir}\\{new_exe_name}" (
    echo.
    echo [오류] 새 exe 파일이 없습니다: {new_exe_name}
    pause
    exit /b 1
)

echo [5/6] 설정 파일 복원 중...
if exist "{data_backup}\\작업도우미.db" (
    copy /y "{data_backup}\\작업도우미.db" "{app_dir}\\_internal\\data\\작업도우미.db" >nul 2>&1
)
if exist "{data_backup}\\wincro.db" (
    copy /y "{data_backup}\\wincro.db" "{app_dir}\\_internal\\data\\wincro.db" >nul 2>&1
)
if exist "{data_backup}\\window_positions.json" (
    copy /y "{data_backup}\\window_positions.json" "{app_dir}\\_internal\\data\\window_positions.json" >nul 2>&1
)
if exist "{data_backup}\\.keyfile" (
    copy /y "{data_backup}\\.keyfile" "{app_dir}\\_internal\\data\\.keyfile" >nul 2>&1
)
if exist "{data_backup}\\config.json" (
    copy /y "{data_backup}\\config.json" "{app_dir}\\_internal\\data\\config.json" >nul 2>&1
)

echo [6/6] 사용자 데이터 병합 중...
if exist "{data_backup}\\recordings" (
    xcopy /E /I /Y /Q "{data_backup}\\recordings\\*" "{app_dir}\\_internal\\data\\recordings\\" >nul 2>&1
)
REM plans 폴더는 새 버전 파일로 덮어씀 (백업 안함)
if exist "{data_backup}\\sequences" (
    xcopy /E /I /Y /Q "{data_backup}\\sequences\\*" "{app_dir}\\_internal\\data\\sequences\\" >nul 2>&1
)
if exist "{data_backup}\\templates" (
    xcopy /E /I /Y /Q "{data_backup}\\templates\\*" "{app_dir}\\_internal\\data\\templates\\" >nul 2>&1
)
if exist "{data_backup}\\triggers" (
    xcopy /E /I /Y /Q "{data_backup}\\triggers\\*" "{app_dir}\\_internal\\data\\triggers\\" >nul 2>&1
)
rd /s /q "{data_backup}" 2>nul

echo.
echo ========================================
echo   업데이트 완료! 재시작 중...
echo ========================================
timeout /t 2 /nobreak >nul

start "" "{app_dir}\\{new_exe_name}"

rd /s /q "{extract_dir}" 2>nul
del /q "{temp_path}" 2>nul
del "%~f0"
'''

            with open(batch_path, 'w', encoding='utf-8') as f:
                f.write(batch_content)

            # 마지막 업데이트 시간 저장
            save_update_config(version)

            # 배치 파일 실행하고 프로그램 종료
            self.after(0, lambda: self._start_update_and_exit(batch_path))

        except Exception as e:
            error_msg = classify_error(e)
            logger.error(f"업데이트 다운로드 오류: {e}", exc_info=True)
            self.after(0, lambda msg=error_msg: self._update_failed(msg))

    def _start_update_and_exit(self, batch_path: str) -> None:
        """배치 파일 실행 후 종료"""
        import subprocess
        import sys
        import os

        from tkinter import messagebox

        # 배치 파일 존재 확인
        if not os.path.exists(batch_path):
            messagebox.showerror("업데이트 오류", "업데이트 스크립트를 찾을 수 없습니다.")
            self._update_failed("배치 파일 생성 실패")
            return

        messagebox.showinfo(
            "업데이트",
            "업데이트를 적용하기 위해 프로그램을 종료합니다.\n"
            "업데이트 창이 열리면 자동으로 진행됩니다.\n\n"
            "⚠️ 업데이트 창을 닫지 마세요!"
        )

        logger.info(f"배치 파일 실행: {batch_path}")

        # 배치 파일 실행 (새 창에서 보이도록)
        try:
            subprocess.Popen(
                ['cmd', '/c', 'start', 'cmd', '/c', batch_path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        except Exception as e:
            logger.error(f"배치 파일 실행 실패: {e}")
            messagebox.showerror("업데이트 오류", f"업데이트 스크립트 실행 실패:\n{e}")
            return

        # 프로그램 종료
        try:
            self.winfo_toplevel().destroy()
        except Exception:
            pass
        sys.exit(0)

    def _update_success_dev(self, version: str, file_path: str) -> None:
        """개발 모드 업데이트 성공 (테스트용)"""
        from tkinter import messagebox

        self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
        self._check_update_btn.configure(state="normal", text="🔍 버전 확인")

        self._update_status_icon.configure(text="✅")
        self._update_status_label.configure(
            text=f"다운로드 완료 (개발 모드)",
            text_color=COLORS["success"]
        )

        messagebox.showinfo(
            "다운로드 완료 (개발 모드)",
            f"새 버전 v{version} 다운로드 완료!\n\n"
            f"파일 위치: {file_path}\n\n"
            f"(개발 모드에서는 자동 업데이트가 적용되지 않습니다)"
        )

    def _update_success(self) -> None:
        """업데이트 성공 처리"""
        from tkinter import messagebox

        # UI 복구
        self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
        self._check_update_btn.configure(state="normal", text="🔍 버전 확인")

        # 상태 업데이트
        self._update_status_icon.configure(text="✅")
        self._update_status_label.configure(
            text="업데이트 완료!",
            text_color=COLORS["success"]
        )

        # 마지막 업데이트 시간 표시
        config = get_config()
        if config.update.last_update:
            self._last_update_label.configure(text=f"마지막 업데이트: {config.update.last_update}")

        messagebox.showinfo(
            "업데이트 완료",
            "업데이트가 성공적으로 완료되었습니다!"
        )
        logger.info("업데이트 완료")

    def _update_failed(self, message: str) -> None:
        """업데이트 실패 처리"""
        from tkinter import messagebox

        # UI 복구
        self._do_update_btn.configure(state="normal", text="⬇️ 업데이트")
        self._check_update_btn.configure(state="normal", text="🔍 버전 확인")

        # 상태 업데이트
        self._update_status_icon.configure(text="❌")
        self._update_status_label.configure(
            text=f"업데이트 실패: {message[:50]}",
            text_color=COLORS["error"]
        )

        messagebox.showerror("업데이트 실패", f"업데이트에 실패했습니다.\n\n{message}")
        logger.error(f"업데이트 실패: {message}")

    def _create_checkbox(
        self, parent, text: str, variable: ctk.BooleanVar
    ) -> ctk.CTkCheckBox:
        """체크박스 생성"""
        checkbox = ctk.CTkCheckBox(
            parent,
            text=text,
            variable=variable,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        checkbox.pack(anchor="w", pady=6)
        return checkbox

    def _create_setting_row(
        self,
        parent,
        label: str,
        variable: ctk.StringVar,
        values: list,
        row: int,
        width: int = 120,
    ) -> None:
        """설정 행 생성 (ComboBox)"""
        self.create_label(parent, text=label, style="caption").grid(
            row=row, column=0, padx=(0, 15), pady=8, sticky="w"
        )

        combo = ctk.CTkComboBox(
            parent,
            values=values,
            variable=variable,
            width=width,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        combo.grid(row=row, column=1, pady=8, sticky="w")

    def _create_entry_row(
        self, parent, label: str, variable: ctk.StringVar, row: int, width: int = 120
    ) -> None:
        """설정 행 생성 (Entry)"""
        self.create_label(parent, text=label, style="caption").grid(
            row=row, column=0, padx=(0, 15), pady=8, sticky="w"
        )

        entry = ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=width,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        entry.grid(row=row, column=1, pady=8, sticky="w")

    def _create_setting_row_with_help(
        self,
        parent,
        label: str,
        variable: ctk.StringVar,
        values: list,
        row: int,
        help_text: str = "",
        width: int = 120,
    ) -> None:
        """설정 행 생성 (ComboBox + 도움말)"""
        # 레이블 프레임
        label_frame = ctk.CTkFrame(parent, fg_color="transparent")
        label_frame.grid(row=row, column=0, padx=(0, 15), pady=8, sticky="w")

        self.create_label(label_frame, text=label, style="caption").pack(anchor="w")
        if help_text:
            ctk.CTkLabel(
                label_frame,
                text=help_text,
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_muted"],
            ).pack(anchor="w")

        combo = ctk.CTkComboBox(
            parent,
            values=values,
            variable=variable,
            width=width,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        combo.grid(row=row, column=1, pady=8, sticky="w")

    def _create_entry_row_with_help(
        self, parent, label: str, variable: ctk.StringVar, row: int,
        help_text: str = "", width: int = 120
    ) -> None:
        """설정 행 생성 (Entry + 도움말)"""
        # 레이블 프레임
        label_frame = ctk.CTkFrame(parent, fg_color="transparent")
        label_frame.grid(row=row, column=0, padx=(0, 15), pady=8, sticky="w")

        self.create_label(label_frame, text=label, style="caption").pack(anchor="w")
        if help_text:
            ctk.CTkLabel(
                label_frame,
                text=help_text,
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_muted"],
            ).pack(anchor="w")

        entry = ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=width,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        entry.grid(row=row, column=1, pady=8, sticky="w")

    def _create_checkbox_with_help(
        self, parent, text: str, variable: ctk.BooleanVar, help_text: str = ""
    ) -> ctk.CTkCheckBox:
        """체크박스 생성 (도움말 포함)"""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(anchor="w", pady=6)

        checkbox = ctk.CTkCheckBox(
            frame,
            text=text,
            variable=variable,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13),
        )
        checkbox.pack(side="left")

        if help_text:
            ctk.CTkLabel(
                frame,
                text=f"  ({help_text})",
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_muted"],
            ).pack(side="left")

        return checkbox

    def _load_settings(self) -> None:
        """설정 로드"""
        config = get_config()

        # 일반 설정
        self._confirm_var.set(config.ui.confirm_before_run)
        self._minimize_var.set(config.ui.minimize_on_run)
        self._tooltips_var.set(config.ui.show_tooltips)

        # 창 모드 (이전 값 호환: small→play, medium/large→editor)
        old_to_new = {"small": "play", "medium": "editor", "large": "editor"}
        current_mode = old_to_new.get(config.ui.window_mode, config.ui.window_mode)
        mode_map = {"play": "플레이 모드", "editor": "에디터 모드"}
        self._window_mode_var.set(mode_map.get(current_mode, "에디터 모드"))

        # 관리자 권한 설정
        self._run_as_admin_var.set(config.ui.run_as_admin)

        # 아두이노 설정
        self._arduino_enabled_var.set(config.arduino.enabled)
        self._arduino_auto_var.set(config.arduino.auto_connect)
        self._arduino_require_playback_var.set(getattr(config.arduino, "require_for_playback", True))
        self._arduino_strict_var.set(getattr(config.arduino, "strict_mode", True))
        self._arduino_port_var.set(config.arduino.com_port)
        self._arduino_baud_var.set(str(config.arduino.baud_rate))

        # 녹화 설정
        self._fps_var.set(str(config.recording.fps))
        self._quality_var.set(config.recording.quality)
        self._cursor_var.set(config.recording.include_cursor)
        self._input_log_var.set(config.recording.save_input_log)

        # 재생 설정
        self._speed_var.set(str(config.player.speed_multiplier))
        self._wait_var.set(str(config.player.default_wait_ms))
        self._retry_var.set(str(config.player.retry_count))
        self._stop_key_var.set(config.player.emergency_stop_key)
        self._auto_start_var.set(config.ui.auto_start)
        self._auto_run_enabled_var.set(config.player.auto_run_enabled)

        # 플랜 순서 설정 - 플랜 파일에서 최신 반복횟수 읽기
        self._seq_plan_paths = list(config.player.plan_sequence)
        self._seq_plan_repeats = []
        self._seq_listbox.delete(0, tk.END)
        for i, seq_path in enumerate(self._seq_plan_paths):
            seq_name = Path(seq_path).stem
            for p in self._auto_run_plan_list:
                if p["path"] == seq_path:
                    seq_name = p["name"]
                    break
            # 플랜 파일에서 최신 반복횟수 읽기
            repeat = 1
            try:
                if Path(seq_path).exists():
                    with open(seq_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        repeat = data.get("total_repeat_count", 1) or 1
            except Exception:
                pass
            self._seq_plan_repeats.append(repeat)
            self._seq_listbox.insert(tk.END, f"{seq_name}  ({repeat}회)")

        # 외관 설정
        self._app_name_var.set(config.ui.app_name)
        self._random_name_var.set(config.ui.random_name_mode)
        self._theme_var.set(
            SETTINGS["theme_dark"]
            if config.ui.theme == "dark"
            else SETTINGS["theme_light"]
        )
        self._language_var.set("한국어")
        self._update_random_name_preview()

        # 업데이트 설정 (GitHub)
        self._github_repo_var.set(config.update.github_repo)
        if config.update.last_update:
            self._last_update_label.configure(text=f"마지막 업데이트: {config.update.last_update}")
        if config.update.github_repo:
            self._update_status_icon.configure(text="✅")
            self._update_status_label.configure(
                text=f"저장소: {config.update.github_repo}",
                text_color=COLORS["text_secondary"]
            )

        logger.debug("설정 로드 완료")

    def _save_settings(self) -> None:
        """설정 저장"""
        config = get_config()
        validation_errors = []

        # 일반 설정
        config.ui.confirm_before_run = self._confirm_var.get()
        config.ui.minimize_on_run = self._minimize_var.get()
        config.ui.show_tooltips = self._tooltips_var.get()
        config.ui.run_as_admin = self._run_as_admin_var.get()

        # 창 모드
        mode_map = {"플레이 모드": "play", "에디터 모드": "editor"}
        config.ui.window_mode = mode_map.get(self._window_mode_var.get(), "editor")

        # 아두이노 설정
        config.arduino.enabled = self._arduino_enabled_var.get()
        config.arduino.auto_connect = self._arduino_auto_var.get()
        config.arduino.require_for_playback = self._arduino_require_playback_var.get()
        config.arduino.strict_mode = self._arduino_strict_var.get()
        config.arduino.com_port = self._arduino_port_var.get()
        baud_rate = self._parse_int(self._arduino_baud_var.get(), 300, 115200, "Baud Rate")
        if baud_rate is not None:
            config.arduino.baud_rate = baud_rate

        # 녹화 설정 (검증 포함)
        fps = self._parse_int(self._fps_var.get(), 1, 60, "FPS")
        if fps is not None:
            config.recording.fps = fps
        else:
            validation_errors.append("FPS는 1-60 사이의 숫자여야 합니다")

        config.recording.quality = self._quality_var.get()
        config.recording.include_cursor = self._cursor_var.get()
        config.recording.save_input_log = self._input_log_var.get()

        # 재생 설정 (검증 포함)
        speed = self._parse_float(self._speed_var.get(), 0.1, 10.0, "재생 속도")
        if speed is not None:
            config.player.speed_multiplier = speed
        else:
            validation_errors.append("재생 속도는 0.1-10.0 사이의 숫자여야 합니다")

        wait_ms = self._parse_int(self._wait_var.get(), 0, 60000, "대기 시간")
        if wait_ms is not None:
            config.player.default_wait_ms = wait_ms
        else:
            validation_errors.append("대기 시간은 0-60000ms 사이의 숫자여야 합니다")

        retry_count = self._parse_int(self._retry_var.get(), 0, 100, "재시도 횟수")
        if retry_count is not None:
            config.player.retry_count = retry_count
        else:
            validation_errors.append("재시도 횟수는 0-100 사이의 숫자여야 합니다")

        config.player.emergency_stop_key = self._stop_key_var.get()
        config.player.auto_run_enabled = self._auto_run_enabled_var.get()
        config.player.plan_sequence = list(getattr(self, '_seq_plan_paths', []))
        config.player.plan_sequence_repeats = list(getattr(self, '_seq_plan_repeats', []))

        # 자동 시작 설정
        new_auto_start = self._auto_start_var.get()
        old_auto_start = config.ui.auto_start
        config.ui.auto_start = new_auto_start

        # 외관 설정
        app_name = self._app_name_var.get().strip()
        if app_name:
            config.ui.app_name = app_name
        old_random_name_mode = getattr(config.ui, "random_name_mode", False)
        new_random_name_mode = self._random_name_var.get()
        config.ui.random_name_mode = new_random_name_mode
        if not new_random_name_mode:
            clear_random_app_name(config.ui)
        else:
            ensure_random_app_name(config.ui, save_callback=None)
        config.ui.theme = (
            "dark" if self._theme_var.get() == SETTINGS["theme_dark"] else "light"
        )

        # 검증 오류가 있으면 표시
        if validation_errors:
            error_msg = "\n".join(validation_errors)
            self._show_message(MESSAGES["error"], f"설정값 오류:\n{error_msg}")
            return

        # 저장
        if save_config():
            logger.info("설정 저장 완료")
            if new_auto_start != old_auto_start or new_random_name_mode != old_random_name_mode:
                self._update_auto_start_registry(new_auto_start)
            top = self.winfo_toplevel()
            if hasattr(top, "update_title"):
                top.update_title()
            self._update_random_name_preview()
            self._show_message(MESSAGES["success"], SETTINGS["changes_saved"])
        else:
            logger.error("설정 저장 실패")
            self._show_message(MESSAGES["error"], "설정 저장에 실패했습니다.")

    def _parse_int(self, value: str, min_val: int, max_val: int, field_name: str) -> Optional[int]:
        """정수값 파싱 및 범위 검증"""
        try:
            parsed = int(value)
            if min_val <= parsed <= max_val:
                return parsed
            logger.warning(f"{field_name} 범위 초과: {parsed} (허용: {min_val}-{max_val})")
            return None
        except (ValueError, TypeError):
            logger.warning(f"{field_name} 변환 실패: {value}")
            return None

    def _parse_float(self, value: str, min_val: float, max_val: float, field_name: str) -> Optional[float]:
        """실수값 파싱 및 범위 검증"""
        try:
            parsed = float(value)
            if min_val <= parsed <= max_val:
                return parsed
            logger.warning(f"{field_name} 범위 초과: {parsed} (허용: {min_val}-{max_val})")
            return None
        except (ValueError, TypeError):
            logger.warning(f"{field_name} 변환 실패: {value}")
            return None

    def _update_random_name_preview(self) -> None:
        config = get_config()
        effective_name = get_effective_app_name(config.ui, save_callback=None)
        if hasattr(self, "_random_name_preview"):
            self._random_name_preview.configure(text=f"현재 표시 이름: {effective_name}")
        if hasattr(self, "_random_name_refresh_btn"):
            self._random_name_refresh_btn.configure(
                state="normal" if self._random_name_var.get() else "disabled"
            )

    def _on_random_name_mode_changed(self) -> None:
        config = get_config()
        if self._random_name_var.get():
            ensure_random_app_name(config.ui, save_callback=save_config)
        self._update_random_name_preview()

    def _regenerate_random_name(self) -> None:
        config = get_config()
        clear_random_app_name(config.ui)
        ensure_random_app_name(config.ui, save_callback=save_config)
        self._update_random_name_preview()

    def _get_auto_start_entry_candidates(self) -> list[str]:
        config = get_config()
        candidates = {
            PRIMARY_APP_NAME,
            (config.ui.app_name or "").strip(),
            (getattr(config.ui, "random_name_alias", "") or "").strip(),
            get_startup_entry_name(config.ui),
            "WinCro",
        }
        return sorted(name for name in candidates if name)

    def _update_auto_start_registry(self, enable: bool) -> None:
        """윈도우 시작시 자동실행 레지스트리 설정"""
        import sys
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

        try:
            # 실행 파일 경로 결정
            if getattr(sys, 'frozen', False):
                # exe로 실행 중
                exe_path = sys.executable
            else:
                # 스크립트로 실행 중 - pythonw.exe 사용
                python_exe = sys.executable.replace("python.exe", "pythonw.exe")
                script_path = str(Path(__file__).parent.parent / "app.py")
                exe_path = f'"{python_exe}" "{script_path}"'

            # 레지스트리 키 열기
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
            )

            if enable:
                # 시작프로그램에 추가
                entry_name = get_startup_entry_name(get_config().ui, save_callback=None)
                winreg.SetValueEx(key, entry_name, 0, winreg.REG_SZ, exe_path)
                logger.info(f"[자동시작] 레지스트리 등록: {entry_name} -> {exe_path}")
            else:
                # 시작프로그램에서 제거
                for candidate in self._get_auto_start_entry_candidates():
                    try:
                        winreg.DeleteValue(key, candidate)
                        logger.info(f"[자동시작] 레지스트리에서 제거됨: {candidate}")
                    except FileNotFoundError:
                        pass

            winreg.CloseKey(key)

        except Exception as e:
            logger.error(f"[자동시작] 레지스트리 설정 실패: {e}")
            self._show_message("오류", f"자동시작 설정 실패: {e}")

    def _reset_settings(self) -> None:
        """설정 초기화"""
        reset_message = (
            "모든 설정을 기본값으로 되돌립니다.\n\n"
            "초기화되는 항목:\n"
            "• 녹화 설정 (FPS, 화질 등)\n"
            "• 재생 설정 (속도, 대기시간 등)\n"
            "• 외관 설정 (테마)\n\n"
            "계속하시겠습니까?"
        )
        if self._ask_confirmation("설정 초기화", reset_message):
            config_manager.reset()
            self._load_settings()
            logger.info("설정 초기화 완료")
            self._show_message("초기화 완료", "모든 설정이 기본값으로 복원되었습니다.")

    def _show_message(self, title: str, message: str) -> None:
        """메시지 표시"""
        from tkinter import messagebox

        messagebox.showinfo(title, message)

    def _ask_confirmation(self, title: str, message: str) -> bool:
        """확인 대화상자"""
        from tkinter import messagebox

        return messagebox.askyesno(title, message)

    def _test_pyautogui(self) -> None:
        """pyautogui moveTo 테스트 (백그라운드 스레드에서 실행)"""
        import pyautogui
        import time
        import threading
        from tkinter import messagebox

        # 안내 메시지
        if not messagebox.askyesno(
            "마우스 테스트",
            "테스트 시작 전 창이 최소화됩니다.\n"
            "3초 후 마우스가 자동으로 움직입니다.\n\n"
            "테스트 중 마우스를 만지지 마세요!\n\n"
            "시작하시겠습니까?"
        ):
            return

        # 창 최소화
        top = self.winfo_toplevel()
        top.iconify()

        def _run_test():
            """백그라운드에서 마우스 테스트 실행"""
            time.sleep(0.5)

            # 카운트다운
            for i in range(3, 0, -1):
                logger.info(f"테스트 시작까지 {i}초...")
                time.sleep(1)

            results = []
            screen_w, screen_h = pyautogui.size()
            results.append(f"화면 크기: {screen_w} x {screen_h}")
            results.append(f"시작 위치: {pyautogui.position()}")
            results.append("")

            # 테스트할 좌표들
            test_coords = [
                (100, 100, "좌상단"),
                (screen_w // 2, screen_h // 2, "중앙"),
                (screen_w - 100, 100, "우측 y=100"),
                (screen_w - 100, 300, "우측 y=300"),
                (screen_w - 100, screen_h // 2, "우측 중앙"),
                (2000, 540, "x=2000"),
                (2200, 540, "x=2200"),
                (2400, 540, "x=2400"),
            ]

            for x, y, name in test_coords:
                pyautogui.moveTo(x, y, duration=0.2)
                time.sleep(0.1)
                actual = pyautogui.position()

                diff_x = abs(actual.x - x)
                diff_y = abs(actual.y - y)

                if diff_x <= 5 and diff_y <= 5:
                    results.append(f"✓ {name} ({x}, {y}) - 성공")
                else:
                    results.append(f"✗ {name} ({x}, {y}) - 실패!")
                    results.append(f"   실제 위치: ({actual.x}, {actual.y})")
                    results.append(f"   오차: x={diff_x}, y={diff_y}")

            # 결과 표시
            result_text = "\n".join(results)
            logger.info(f"pyautogui 테스트 결과:\n{result_text}")

            # 메인 스레드에서 창 복원 및 결과 표시
            def _show_results():
                top.deiconify()
                top.lift()
                self._show_test_results(result_text)

            try:
                self.after(0, _show_results)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=_run_test, daemon=True).start()

    def _show_test_results(self, result_text: str) -> None:
        """마우스 테스트 결과 다이얼로그 표시"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("마우스 이동 테스트 결과")
        dialog.geometry("400x350")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 400) // 2
        y = (dialog.winfo_screenheight() - 350) // 2
        dialog.geometry(f"+{x}+{y}")

        # 텍스트 영역
        text_box = ctk.CTkTextbox(
            dialog,
            width=360,
            height=250,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"],
        )
        text_box.pack(padx=20, pady=(20, 10))
        text_box.insert("1.0", result_text)

        # 버튼 프레임
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))

        def copy_to_clipboard():
            dialog.clipboard_clear()
            dialog.clipboard_append(result_text)
            copy_btn.configure(text="복사됨!")
            def reset_button_text():
                try:
                    copy_btn.configure(text="복사")
                except (tk.TclError, RuntimeError):
                    pass
            dialog.after(1500, reset_button_text)

        copy_btn = ctk.CTkButton(
            btn_frame, text="복사", command=copy_to_clipboard,
            width=80, height=32, fg_color=COLORS["accent"]
        )
        copy_btn.pack(side="left")

        close_btn = ctk.CTkButton(
            btn_frame, text="닫기", command=dialog.destroy,
            width=80, height=32, fg_color=COLORS["bg_card"]
        )
        close_btn.pack(side="right")

    def _run_full_test(self) -> None:
        """전체 기능 테스트 실행"""
        from ..utils.self_test import get_self_tester, TestStatus

        # 테스트 결과 다이얼로그 생성
        dialog = ctk.CTkToplevel(self)
        dialog.title("전체 기능 테스트")
        dialog.geometry("550x500")
        dialog.resizable(True, True)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self.winfo_toplevel())

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 550) // 2
        y = (dialog.winfo_screenheight() - 500) // 2
        dialog.geometry(f"+{x}+{y}")

        # 헤더
        header_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(15, 10))

        ctk.CTkLabel(
            header_frame,
            text="전체 기능 테스트",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        self._test_status_label = ctk.CTkLabel(
            header_frame,
            text="준비 중...",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
        self._test_status_label.pack(side="right")

        # 프로그레스 바
        self._test_progress = ctk.CTkProgressBar(
            dialog,
            width=510,
            height=8,
            fg_color=COLORS["bg_card"],
            progress_color=COLORS["accent"],
        )
        self._test_progress.pack(padx=20, pady=(0, 10))
        self._test_progress.set(0)

        # 결과 텍스트 영역
        self._test_textbox = ctk.CTkTextbox(
            dialog,
            width=510,
            height=340,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"],
        )
        self._test_textbox.pack(padx=20, pady=(0, 10))

        # 버튼 프레임
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        def copy_to_clipboard():
            text = self._test_textbox.get("1.0", "end-1c")
            dialog.clipboard_clear()
            dialog.clipboard_append(text)
            copy_btn.configure(text="복사됨!")
            dialog.after(1500, lambda: copy_btn.configure(text="결과 복사"))

        copy_btn = ctk.CTkButton(
            btn_frame, text="결과 복사", command=copy_to_clipboard,
            width=100, height=32, fg_color=COLORS["accent"]
        )
        copy_btn.pack(side="left")

        close_btn = ctk.CTkButton(
            btn_frame, text="닫기", command=dialog.destroy,
            width=80, height=32, fg_color=COLORS["bg_card"]
        )
        close_btn.pack(side="right")

        # 테스트 실행
        tester = get_self_tester()
        self._test_count = 0
        self._total_tests = 11  # 총 테스트 개수

        def on_progress(test_name: str, result):
            """테스트 진행 콜백"""
            try:
                if result.status == TestStatus.RUNNING:
                    self._test_status_label.configure(text=f"테스트 중: {test_name}")
                else:
                    self._test_count += 1
                    progress = self._test_count / self._total_tests
                    self._test_progress.set(progress)

                    # 결과 추가
                    status_icon = {
                        TestStatus.PASSED: "✓",
                        TestStatus.FAILED: "✗",
                        TestStatus.SKIPPED: "○",
                    }.get(result.status, "?")

                    color = {
                        TestStatus.PASSED: "#00ff00",
                        TestStatus.FAILED: "#ff4444",
                        TestStatus.SKIPPED: "#888888",
                    }.get(result.status, "#ffffff")

                    line = f"{status_icon} {result.name}"
                    if result.duration_ms > 0:
                        line += f" ({result.duration_ms:.0f}ms)"
                    line += "\n"

                    if result.message:
                        line += f"   {result.message}\n"

                    if result.details:
                        for key, value in result.details.items():
                            line += f"   {key}: {value}\n"

                    line += "\n"

                    self._test_textbox.insert("end", line)
                    self._test_textbox.see("end")

            except Exception as e:
                logger.warning(f"진행 콜백 오류: {e}")

        def on_complete(results):
            """테스트 완료 콜백"""
            try:
                passed = sum(1 for r in results if r.status == TestStatus.PASSED)
                failed = sum(1 for r in results if r.status == TestStatus.FAILED)
                total = len(results)

                self._test_progress.set(1)

                summary = "=" * 40 + "\n"
                summary += f"테스트 완료: {passed}/{total} 성공"
                if failed > 0:
                    summary += f", {failed} 실패"
                summary += "\n" + "=" * 40 + "\n"

                self._test_textbox.insert("end", summary)
                self._test_textbox.see("end")

                if failed > 0:
                    self._test_status_label.configure(
                        text=f"완료 - {failed}개 실패",
                        text_color=COLORS["error"],
                    )
                else:
                    self._test_status_label.configure(
                        text="모든 테스트 통과!",
                        text_color=COLORS["success"],
                    )
            except Exception as e:
                logger.warning(f"완료 콜백 오류: {e}")

        # 콜백을 메인 스레드에서 실행하도록 래핑
        def safe_on_progress(test_name, result):
            dialog.after(0, lambda: on_progress(test_name, result))

        def safe_on_complete(results):
            dialog.after(0, lambda: on_complete(results))

        tester.set_callbacks(
            on_progress=safe_on_progress,
            on_complete=safe_on_complete,
        )

        # 헤더 추가
        self._test_textbox.insert("1.0", "자체 테스트 시작...\n\n")

        # 테스트 실행 (비동기)
        tester.run_all_tests(async_mode=True)

    def _scan_com_ports(self) -> None:
        """COM 포트 스캔"""
        try:
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
            port_list = []

            for port in ports:
                # 포트 이름과 설명 표시
                port_info = port.device
                if port.description and port.description != "n/a":
                    port_info = f"{port.device}"
                port_list.append(port_info)

            if port_list:
                self._arduino_port_combo.configure(values=port_list)
                # 현재 선택된 값이 목록에 없으면 첫 번째 포트 선택
                current = self._arduino_port_var.get()
                if current not in port_list:
                    self._arduino_port_var.set(port_list[0])
                logger.info(f"COM 포트 스캔 완료: {port_list}")
            else:
                self._arduino_port_combo.configure(values=["포트 없음"])
                self._arduino_port_var.set("포트 없음")
                logger.warning("사용 가능한 COM 포트 없음")

        except ImportError:
            logger.error("pyserial 미설치. pip install pyserial 실행 필요")
            self._arduino_port_combo.configure(values=["pyserial 필요"])
            self._arduino_port_var.set("pyserial 필요")
        except Exception as e:
            logger.error(f"COM 포트 스캔 오류: {e}")
            self._arduino_port_combo.configure(values=["스캔 오류"])
            self._arduino_port_var.set("스캔 오류")

    def _test_arduino_connection(self) -> None:
        """아두이노 연결 테스트"""
        from tkinter import messagebox

        port = self._arduino_port_var.get()
        baud = self._arduino_baud_var.get()

        if not port or port in ["포트 없음", "pyserial 필요", "스캔 오류", ""]:
            messagebox.showwarning("연결 테스트", "유효한 COM 포트를 선택하세요.")
            return

        try:
            baud_rate = int(baud)
        except ValueError:
            messagebox.showwarning("연결 테스트", "유효한 Baud Rate를 입력하세요.")
            return

        try:
            import serial
            # 연결 시도
            ser = serial.Serial(port, baud_rate, timeout=2)

            # 연결 성공
            if ser.is_open:
                # Arduino Leonardo 리셋 대기 (Leonardo는 연결 시 자동 리셋됨)
                import time
                time.sleep(0.5)

                # 간단한 테스트: 데이터 송수신 시도
                # Leonardo HID의 경우 특별한 응답이 없을 수 있음
                ser.close()

                messagebox.showinfo(
                    "연결 테스트",
                    f"아두이노 연결 성공!\n\n"
                    f"포트: {port}\n"
                    f"Baud Rate: {baud_rate}\n\n"
                    f"아두이노가 정상적으로 연결되었습니다."
                )
                logger.info(f"아두이노 연결 테스트 성공: {port} @ {baud_rate}")
            else:
                messagebox.showerror("연결 테스트", f"포트 열기 실패: {port}")

        except ImportError:
            messagebox.showerror(
                "연결 테스트",
                "pyserial이 설치되지 않았습니다.\n\n"
                "터미널에서 다음 명령을 실행하세요:\n"
                "pip install pyserial"
            )
        except serial.SerialException as e:
            messagebox.showerror(
                "연결 테스트",
                f"연결 실패: {port}\n\n"
                f"오류: {str(e)}\n\n"
                f"• 아두이노가 연결되어 있는지 확인하세요\n"
                f"• 다른 프로그램이 포트를 사용 중인지 확인하세요\n"
                f"• 올바른 COM 포트를 선택했는지 확인하세요"
            )
            logger.error(f"아두이노 연결 실패: {e}")
        except Exception as e:
            messagebox.showerror("연결 테스트", f"예상치 못한 오류: {str(e)}")
            logger.error(f"아두이노 연결 테스트 오류: {e}")

    def _toggle_arduino_connection(self) -> None:
        """아두이노 연결/해제 토글"""
        if self._arduino_serial and self._arduino_serial.is_open:
            self._disconnect_arduino()
        else:
            self._connect_arduino()

    def _connect_arduino(self) -> None:
        """아두이노 연결 (펌웨어 자동 업로드 포함)"""
        port = self._arduino_port_var.get()
        baud = self._arduino_baud_var.get()

        if not port or port in ["포트 없음", "pyserial 필요", "스캔 오류", ""]:
            self._update_arduino_status(False, "포트를 선택하세요")
            return

        try:
            baud_rate = int(baud)
        except ValueError:
            self._update_arduino_status(False, "잘못된 Baud Rate")
            return

        # 별도 스레드에서 연결 (UI 블로킹 방지)
        import threading
        thread = threading.Thread(
            target=self._connect_arduino_thread,
            args=(port, baud_rate),
            daemon=True
        )
        thread.start()

    def _connect_arduino_thread(self, port: str, baud_rate: int) -> None:
        """아두이노 연결 스레드"""
        import time
        import traceback

        # pyserial 임포트
        try:
            import serial
        except ImportError:
            self.after(0, lambda: self._update_arduino_status(False, "pyserial 미설치"))
            return

        # UI 업데이트
        self.after(0, lambda: self._update_arduino_status(False, "연결 중..."))

        try:
            # 이전 연결 정리
            if self._arduino_serial:
                try:
                    self._arduino_serial.close()
                except (OSError, serial.SerialException):
                    pass
                self._arduino_serial = None

            # 시리얼 연결
            logger.info(f"아두이노 연결 시도: {port} @ {baud_rate}")
            ser = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=2,
                write_timeout=2,
                dsrdtr=False,
                rtscts=False
            )

            if not ser.is_open:
                self.after(0, lambda: self._update_arduino_status(False, "포트 열기 실패"))
                return

            self._arduino_serial = ser

            # Leonardo 리셋
            logger.info("Leonardo 리셋 중...")
            ser.dtr = False
            time.sleep(0.1)
            ser.dtr = True
            time.sleep(2)
            ser.reset_input_buffer()

            # 펌웨어 확인 (PING 테스트)
            logger.info("펌웨어 확인 중...")
            firmware_ok = self._check_firmware(ser)

            if not firmware_ok:
                # 펌웨어 없음 - 자동 업로드 시도
                self.after(0, lambda: self._update_arduino_status(False, "펌웨어 업로드 중..."))
                logger.info("펌웨어가 설치되지 않음, 자동 업로드 시작")

                ser.close()
                self._arduino_serial = None

                # 펌웨어 업로드
                try:
                    from ..utils.arduino_uploader import upload_firmware

                    def progress_cb(msg):
                        self.after(0, lambda m=msg: self._update_arduino_status(False, m))

                    success, message = upload_firmware(port, progress_cb)

                    if not success:
                        self.after(0, lambda m=message[:40]: self._update_arduino_status(False, f"업로드 실패: {m}"))
                        logger.error(f"펌웨어 업로드 실패: {message}")
                        return
                except Exception as upload_err:
                    self.after(0, lambda: self._update_arduino_status(False, "펌웨어 업로드 오류"))
                    logger.error(f"펌웨어 업로드 예외: {upload_err}")
                    return

                # 업로드 후 재연결
                time.sleep(3)
                logger.info("업로드 후 재연결...")

                ser = serial.Serial(
                    port=port,
                    baudrate=baud_rate,
                    timeout=2,
                    write_timeout=2,
                    dsrdtr=False,
                    rtscts=False
                )
                self._arduino_serial = ser

                time.sleep(2)
                ser.reset_input_buffer()
                firmware_ok = self._check_firmware(ser)

            # ArduinoHID 인스턴스 연결 (재생 시 사용)
            try:
                from ..utils.arduino_hid import get_arduino_hid
                arduino_hid = get_arduino_hid()
                if arduino_hid:
                    arduino_hid._serial = self._arduino_serial
                    arduino_hid._connected = True
                    arduino_hid._port = port
                    arduino_hid._baud_rate = baud_rate
            except Exception as hid_err:
                logger.warning(f"ArduinoHID 설정 실패 (무시): {hid_err}")

            # 결과 표시
            if firmware_ok:
                self.after(0, lambda p=port: self._update_arduino_status(True, f"{p} 연결됨 (HID 준비)"))
                logger.info(f"아두이노 HID 연결 성공: {port}")
            else:
                self.after(0, lambda p=port: self._update_arduino_status(True, f"{p} 연결됨 (펌웨어?)"))
                logger.warning("펌웨어 응답 없음")

            # 설정 저장
            config = get_config()
            config.arduino.com_port = port
            config.arduino.baud_rate = baud_rate
            save_config()

        except PermissionError:
            logger.error(f"아두이노 연결 오류: 포트 접근 권한 없음\n{traceback.format_exc()}")
            self.after(0, lambda: self._update_arduino_status(False, "포트 사용중"))
        except FileNotFoundError:
            logger.error(f"아두이노 연결 오류: 포트를 찾을 수 없음\n{traceback.format_exc()}")
            self.after(0, lambda: self._update_arduino_status(False, "포트 없음"))
        except serial.SerialException as e:
            error_msg = str(e).lower()
            logger.error(f"아두이노 연결 오류: {e}\n{traceback.format_exc()}")
            if "permission" in error_msg or "access" in error_msg:
                self.after(0, lambda: self._update_arduino_status(False, "포트 사용중"))
            elif "could not open port" in error_msg or "filenotfound" in error_msg:
                self.after(0, lambda: self._update_arduino_status(False, "포트 없음"))
            else:
                short_msg = str(e)[:35]
                self.after(0, lambda m=short_msg: self._update_arduino_status(False, f"실패: {m}"))
        except Exception as e:
            logger.error(f"아두이노 연결 오류: {e}\n{traceback.format_exc()}")
            short_msg = str(e)[:35]
            self.after(0, lambda m=short_msg: self._update_arduino_status(False, f"실패: {m}"))

    def _check_firmware(self, ser) -> bool:
        """펌웨어 설치 확인 (PING 테스트)"""
        try:
            ser.reset_input_buffer()
            ser.write(b"PING\n")
            ser.flush()

            import time
            time.sleep(0.1)

            if ser.in_waiting:
                response = ser.readline().decode().strip()
                return response == "PONG"
            return False
        except Exception as e:
            logger.debug(f"펌웨어 확인 실패: {e}")
            return False

    def _disconnect_arduino(self) -> None:
        """아두이노 연결 해제"""
        try:
            # ArduinoHID 인스턴스도 해제
            from ..utils.arduino_hid import get_arduino_hid
            arduino_hid = get_arduino_hid()
            arduino_hid._serial = None
            arduino_hid._connected = False

            if self._arduino_serial:
                self._arduino_serial.close()
                self._arduino_serial = None
            self._update_arduino_status(False, "연결 해제됨")
            logger.info("아두이노 연결 해제")
        except Exception as e:
            logger.error(f"아두이노 연결 해제 오류: {e}")
            self._update_arduino_status(False, "해제 오류")

    def _update_arduino_status(self, connected: bool, message: str) -> None:
        """아두이노 연결 상태 UI 업데이트"""
        if connected:
            self._arduino_status_dot.configure(text_color=COLORS["success"])
            self._arduino_status_label.configure(
                text=message,
                text_color=COLORS["success"]
            )
            self._arduino_connect_btn.configure(
                text="해제",
                fg_color=COLORS["error"],
                hover_color="#c0392b"
            )
        else:
            self._arduino_status_dot.configure(text_color=COLORS["text_muted"])
            self._arduino_status_label.configure(
                text=message,
                text_color=COLORS["text_muted"]
            )
            self._arduino_connect_btn.configure(
                text="연결",
                fg_color=COLORS["success"],
                hover_color="#45a049"
            )

    def _upload_arduino_firmware(self) -> None:
        """펌웨어 수동 업로드"""
        from tkinter import messagebox

        port = self._arduino_port_var.get()
        if not port:
            messagebox.showwarning("경고", "COM 포트를 선택하세요.")
            return

        # 연결 중이면 먼저 해제
        if self._arduino_serial and self._arduino_serial.is_open:
            self._disconnect_arduino()

        # 버튼 비활성화
        self._arduino_upload_btn.configure(state="disabled", text="업로드 중...")
        self._arduino_connect_btn.configure(state="disabled")

        def upload_thread():
            try:
                from ..utils.arduino_uploader import upload_firmware

                def progress_cb(msg):
                    self.after(0, lambda m=msg: self._arduino_status_label.configure(text=m))

                success, message = upload_firmware(port, progress_cb)

                if success:
                    self.after(0, lambda: messagebox.showinfo("성공", "펌웨어 업로드 완료!\n재연결하세요."))
                    self.after(0, lambda: self._update_arduino_status(False, "업로드 완료 - 재연결 필요"))
                else:
                    self.after(0, lambda m=message: messagebox.showerror("실패", f"펌웨어 업로드 실패:\n{m}"))
                    self.after(0, lambda: self._update_arduino_status(False, "업로드 실패"))

            except Exception as e:
                logger.error(f"펌웨어 업로드 오류: {e}")
                self.after(0, lambda: messagebox.showerror("오류", f"펌웨어 업로드 오류:\n{e}"))
                self.after(0, lambda: self._update_arduino_status(False, "업로드 오류"))
            finally:
                # 버튼 복구
                self.after(0, lambda: self._arduino_upload_btn.configure(state="normal", text="펌웨어 업로드"))
                self.after(0, lambda: self._arduino_connect_btn.configure(state="normal"))

        import threading
        thread = threading.Thread(target=upload_thread, daemon=True)
        thread.start()

    def _auto_connect_arduino(self) -> None:
        """자동 연결 시도 (설정에서 활성화된 경우)"""
        config = get_config()
        if config.arduino.enabled and config.arduino.auto_connect and config.arduino.com_port:
            self._arduino_port_var.set(config.arduino.com_port)
            self._arduino_baud_var.set(str(config.arduino.baud_rate))
            self._connect_arduino()

    def _restart_as_admin(self) -> None:
        """관리자 권한으로 재시작"""
        from tkinter import messagebox
        from ..utils.admin import is_admin, restart_as_admin

        if is_admin():
            messagebox.showinfo("관리자 권한", "이미 관리자 권한으로 실행 중입니다.")
            return

        if messagebox.askyesno(
            "관리자 권한으로 재시작",
            "관리자 권한으로 앱을 재시작합니다.\n\n"
            "현재 작업 중인 내용이 있다면 저장하세요.\n"
            "계속하시겠습니까?"
        ):
            restart_as_admin()
