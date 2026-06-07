"""
WinCro 가이드 뷰

설치 가이드 및 필수 프로그램 링크를 제공합니다.
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from ..utils.logger import get_logger

logger = get_logger(__name__)

# 컬러 팔레트 (theme.py에서 통합 관리)
from .theme import COLORS


class GuideView(ctk.CTkFrame):
    """가이드 뷰"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_dark"], **kwargs)
        self._setup_ui()

    def _setup_ui(self):
        """UI 구성"""
        # 스크롤 가능한 프레임
        scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg_dark"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # 제목
        ctk.CTkLabel(
            scroll_frame,
            text="📖 업무지원도구 설치 가이드",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 20))

        # 필수 프로그램 섹션
        self._create_section(
            scroll_frame,
            "필수 프로그램",
            [
                {
                    "name": "Visual C++ 재배포 가능 패키지 (최신)",
                    "desc": "프로그램 실행에 필요한 Microsoft 런타임 라이브러리입니다.",
                    "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
                },
                {
                    "name": "Visual C++ 재배포 가능 패키지 (2015-2022)",
                    "desc": "구버전 호환을 위한 런타임 라이브러리입니다.",
                    "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
                },
            ]
        )

        # 선택 프로그램 섹션
        self._create_section(
            scroll_frame,
            "선택 프로그램 (아두이노 HID 사용 시)",
            [
                {
                    "name": "Arduino IDE",
                    "desc": "아두이노 펌웨어 업로드에 필요합니다.",
                    "url": "https://www.arduino.cc/en/software",
                },
                {
                    "name": "CH340 드라이버",
                    "desc": "일부 아두이노 호환 보드에 필요한 USB 드라이버입니다.",
                    "url": "https://www.wch-ic.com/downloads/CH341SER_EXE.html",
                },
            ]
        )

        # 사용법 섹션
        self._create_usage_section(scroll_frame)

    def _create_section(self, parent, title: str, items: list):
        """섹션 생성"""
        # 섹션 카드
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=10)
        card.pack(fill="x", pady=(0, 15))

        # 섹션 제목
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["accent_blue"],
        ).pack(anchor="w", padx=15, pady=(15, 10))

        # 아이템들
        for item in items:
            item_frame = ctk.CTkFrame(card, fg_color="transparent")
            item_frame.pack(fill="x", padx=15, pady=5)

            # 이름
            ctk.CTkLabel(
                item_frame,
                text=item["name"],
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(anchor="w")

            # 설명
            ctk.CTkLabel(
                item_frame,
                text=item["desc"],
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_secondary"],
            ).pack(anchor="w")

            # 링크 프레임
            link_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
            link_frame.pack(anchor="w", pady=(5, 0))

            # URL 표시
            url_entry = ctk.CTkEntry(
                link_frame,
                width=400,
                height=30,
                fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"],
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=11),
            )
            url_entry.pack(side="left", padx=(0, 5))
            url_entry.insert(0, item["url"])
            url_entry.configure(state="readonly")

            # 복사 버튼
            ctk.CTkButton(
                link_frame,
                text="복사",
                width=50,
                height=30,
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                font=ctk.CTkFont(size=12),
                command=lambda u=item["url"]: self._copy_to_clipboard(u),
            ).pack(side="left")

        # 하단 여백
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()

    def _create_usage_section(self, parent):
        """사용법 섹션"""
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=10)
        card.pack(fill="x", pady=(0, 15))

        ctk.CTkLabel(
            card,
            text="기본 사용법",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["accent_blue"],
        ).pack(anchor="w", padx=15, pady=(15, 10))

        usage_text = """1. 화면 녹화: 자동화할 작업을 녹화합니다.
2. 동작 분석: 녹화된 영상에서 동작을 추출합니다.
3. 실행: 추출된 동작을 재생합니다.

버튼 설명:
• S 버튼 (스킵): 이미지를 못찾으면 대기시간 후 다음 액션으로 넘어감
• c 버튼 (조건): 트리거 이미지 설정 (이 이미지가 보이면 실행)
• M 버튼 (모니터링): 모니터링 모드 설정
• x1, x2... (반복): 해당 액션 반복 횟수

단축키:
• F8: 실행 중 긴급 중지
• 창 모드 변경: 설정 메뉴에서 변경 가능"""

        ctk.CTkLabel(
            card,
            text=usage_text,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            justify="left",
        ).pack(anchor="w", padx=15, pady=(0, 15))

    def _copy_to_clipboard(self, text: str):
        """클립보드에 복사"""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            logger.info(f"클립보드에 복사됨: {text}")
            messagebox.showinfo("복사 완료", "링크가 클립보드에 복사되었습니다.")
        except Exception as e:
            logger.error(f"클립보드 복사 실패: {e}")
            messagebox.showerror("오류", "복사에 실패했습니다.")

    def refresh(self):
        """새로고침 (필요시)"""
        pass
