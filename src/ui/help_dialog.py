"""
WinCro 사용법 가이드 모듈

초보자도 쉽게 이해할 수 있는 사용법 안내를 제공합니다.
"""

import customtkinter as ctk
from typing import Optional

from ..utils.logger import get_logger
from ..utils.config import get_config, save_config
from ..utils.window_position import setup_window_position
from .theme import COLORS, IOS_METRICS

logger = get_logger(__name__)


class HelpDialog(ctk.CTkToplevel):
    """
    사용법 가이드 다이얼로그

    프로그램의 모든 기능을 초보자도 이해할 수 있게 설명합니다.
    """

    def __init__(self, parent, show_on_startup_option: bool = True):
        """
        사용법 다이얼로그 초기화

        Args:
            parent: 부모 윈도우
            show_on_startup_option: 시작 시 표시 옵션 체크박스 표시 여부
        """
        super().__init__(parent)

        self._config = get_config()
        self._show_on_startup_option = show_on_startup_option

        # 윈도우 설정
        self.title("사용법 가이드")
        self.geometry("700x600")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_content"])

        # 모달 설정
        self.transient(parent)
        self.grab_set()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "HelpDialog")

        # UI 구성
        self._setup_ui()

        logger.info("사용법 가이드 표시")

    def _setup_ui(self) -> None:
        """UI 구성"""
        # 메인 프레임
        main_frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius"],
            border_width=1,
            border_color=COLORS["separator"],
        )
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # 제목
        title_label = ctk.CTkLabel(
            main_frame,
            text="사용법 가이드",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(pady=(0, 10))

        subtitle_label = ctk.CTkLabel(
            main_frame,
            text="화면 녹화 기반 자동화 프로그램",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_secondary"],
        )
        subtitle_label.pack(pady=(0, 20))

        # 스크롤 가능한 콘텐츠 영역
        content_frame = ctk.CTkScrollableFrame(
            main_frame,
            width=640,
            height=400,
            fg_color=COLORS["bg_log"],
            scrollbar_button_color=COLORS["bg_card_hover"],
            scrollbar_button_hover_color=COLORS["accent"],
            corner_radius=IOS_METRICS["card_radius_compact"],
        )
        content_frame.pack(fill="both", expand=True, pady=10)

        # 사용법 내용 추가
        self._add_help_content(content_frame)

        # 하단 버튼 영역
        bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        bottom_frame.pack(fill="x", pady=(20, 0))

        # 시작 시 표시 체크박스
        if self._show_on_startup_option:
            self._show_startup_var = ctk.BooleanVar(value=self._config.ui.show_help_on_startup)
            startup_check = ctk.CTkCheckBox(
                bottom_frame,
                text="프로그램 시작 시 이 창 표시",
                variable=self._show_startup_var,
                command=self._on_startup_toggle,
                text_color=COLORS["text_primary"],
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
            )
            startup_check.pack(side="left")

        # 닫기 버튼
        close_btn = ctk.CTkButton(
            bottom_frame,
            text="확인",
            command=self.destroy,
            width=100,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["pill_radius"],
        )
        close_btn.pack(side="right")

    def _add_help_content(self, parent) -> None:
        """사용법 내용 추가"""

        # ===== 빠른 시작 =====
        self._add_section(parent, "빠른 시작 (3단계로 자동화!)")
        self._add_step(parent, "1", "녹화",
            "녹화 탭에서 '녹화 시작' 클릭\n"
            "→ 자동화하고 싶은 작업 수행\n"
            "→ '녹화 중지' 클릭")
        self._add_step(parent, "2", "분석",
            "분석 탭에서 녹화 영상 선택\n"
            "→ '분석 시작' 클릭\n"
            "→ 자동으로 동작 추출됨")
        self._add_step(parent, "3", "실행",
            "실행 탭에서 재생 선택\n"
            "→ '실행' 클릭\n"
            "→ 녹화한 동작이 자동 재현됨!")

        self._add_divider(parent)

        # ===== 각 탭 설명 =====
        self._add_section(parent, "탭별 상세 설명")

        # 녹화 탭
        self._add_subsection(parent, "녹화 탭")
        self._add_text(parent,
            "• 녹화 이름: 구분하기 쉬운 이름 입력\n"
            "• 녹화 시작: 녹화 시작 (마우스, 키보드 모두 기록)\n"
            "• 녹화 중지: 녹화 종료 및 저장\n"
            "• 저장된 녹화 목록: 우측에서 확인 가능\n"
            "• 삭제: 녹화 및 관련 재생 모두 삭제\n\n"
            "단축키:\n"
            "  F9  = 녹화 시작\n"
            "  F10 = 녹화 중지")

        # 분석 탭
        self._add_subsection(parent, "분석 탭")
        self._add_text(parent,
            "• 녹화 목록에서 분석할 영상 선택\n"
            "• 재생 이름 입력 (선택사항)\n"
            "• '분석 시작' 클릭\n"
            "• 분석 완료 시 클릭 위치 이미지 자동 저장\n"
            "  → 이미지 기반으로 정확한 위치 인식!")

        # 실행 탭
        self._add_subsection(parent, "실행 탭")
        self._add_text(parent,
            "• 재생 목록에서 실행할 항목 선택\n"
            "• 실행 속도: 0.5x ~ 2x 조절 가능\n"
            "• 반복 횟수: 여러 번 반복 실행 가능\n"
            "• 무한 반복: 체크하면 계속 반복\n"
            "• 실행/중지 버튼으로 제어")

        self._add_divider(parent)

        # ===== 긴급 중지 =====
        self._add_section(parent, "긴급 중지 (중요!)")
        self._add_warning(parent,
            "ESC 키를 빠르게 2번 누르면 즉시 중지됩니다!\n\n"
            "자동화 실행 중 문제가 생기면 ESC 키를 2번 누르세요.\n"
            "모든 동작이 즉시 멈춥니다.")

        self._add_divider(parent)

        # ===== AI 개입 기능 =====
        self._add_section(parent, "AI 개입 기능 (고급)")
        self._add_text(parent,
            "이미지 인식 실패 시 AI가 자동으로 화면을 분석하여\n"
            "대체 동작을 제안합니다.\n")
        self._add_subsection(parent, "설정 방법")
        self._add_text(parent,
            "1. 설정 탭 → AI 설정\n"
            "2. OpenAI API 키 입력\n"
            "3. 'AI 개입 활성화' 체크\n"
            "4. 저장")
        self._add_subsection(parent, "AI 개입 조건")
        self._add_text(parent,
            "• 이미지 매칭 실패 + 좌표 대체도 불가능할 때\n"
            "• AI가 현재 화면을 캡처하여 분석\n"
            "• 적절한 대체 동작 제안 및 실행")

        self._add_divider(parent)

        # ===== 로그 확인 =====
        self._add_section(parent, "로그 확인")
        self._add_text(parent,
            "로그 탭에서 실시간으로 동작 상태를 확인할 수 있습니다.\n\n"
            "• 이미지 매칭 성공/실패\n"
            "• AI 개입 시도 및 결과\n"
            "• 액션 실행 상태\n"
            "• 필터: ALL, DEBUG, INFO, WARNING, ERROR")

        self._add_divider(parent)

        # ===== 팁 =====
        self._add_section(parent, "유용한 팁")
        self._add_text(parent,
            "• 녹화 시 천천히, 정확하게 동작하세요\n"
            "• 클릭 사이에 잠깐씩 멈추면 더 정확해요\n"
            "• 창 위치가 바뀌어도 이미지 인식으로 찾아요\n"
            "• 문제가 생기면 로그 탭에서 원인 확인\n"
            "• ESC 2번 = 긴급 중지 (꼭 기억하세요!)")

    def _add_section(self, parent, title: str) -> None:
        """섹션 제목 추가"""
        label = ctk.CTkLabel(
            parent,
            text=f"■ {title}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        label.pack(fill="x", pady=(15, 5))

    def _add_subsection(self, parent, title: str) -> None:
        """서브섹션 제목 추가"""
        label = ctk.CTkLabel(
            parent,
            text=f"▸ {title}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["accent_blue"],
            anchor="w",
        )
        label.pack(fill="x", pady=(10, 3), padx=10)

    def _add_text(self, parent, text: str) -> None:
        """일반 텍스트 추가"""
        label = ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
            anchor="w",
            justify="left",
        )
        label.pack(fill="x", padx=20)

    def _add_step(self, parent, number: str, title: str, description: str) -> None:
        """단계 추가"""
        step_frame = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["control_radius"],
            border_width=1,
            border_color=COLORS["separator"],
        )
        step_frame.pack(fill="x", pady=5, padx=10)

        # 번호
        num_label = ctk.CTkLabel(
            step_frame,
            text=number,
            font=ctk.CTkFont(size=20, weight="bold"),
            width=40,
            text_color=COLORS["accent_blue"],
        )
        num_label.pack(side="left", padx=10, pady=10)

        # 내용
        content_frame = ctk.CTkFrame(step_frame, fg_color="transparent")
        content_frame.pack(side="left", fill="x", expand=True, pady=10)

        title_label = ctk.CTkLabel(
            content_frame,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title_label.pack(fill="x")

        desc_label = ctk.CTkLabel(
            content_frame,
            text=description,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            anchor="w",
            justify="left",
        )
        desc_label.pack(fill="x")

    def _add_warning(self, parent, text: str) -> None:
        """경고 텍스트 추가"""
        warning_frame = ctk.CTkFrame(
            parent,
            fg_color=COLORS["warning"],
            corner_radius=IOS_METRICS["control_radius"],
        )
        warning_frame.pack(fill="x", pady=5, padx=10)

        label = ctk.CTkLabel(
            warning_frame,
            text=text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["bg_content"],
            anchor="w",
            justify="left",
        )
        label.pack(padx=15, pady=15)

    def _add_divider(self, parent) -> None:
        """구분선 추가"""
        divider = ctk.CTkFrame(parent, height=2, fg_color=COLORS["separator"])
        divider.pack(fill="x", pady=15, padx=20)

    def _on_startup_toggle(self) -> None:
        """시작 시 표시 토글"""
        self._config.ui.show_help_on_startup = self._show_startup_var.get()
        save_config()


def show_help_dialog(parent, show_on_startup_option: bool = True) -> None:
    """사용법 다이얼로그 표시"""
    HelpDialog(parent, show_on_startup_option)
