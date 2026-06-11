from src.utils import app_identity


class DummyUI:
    def __init__(self, app_name="fixed-name", random_name_mode=False, random_name_alias=""):
        self.app_name = app_name
        self.random_name_mode = random_name_mode
        self.random_name_alias = random_name_alias


def test_primary_branding_uses_korean_business_support_name():
    assert app_identity.PRIMARY_APP_NAME == "업무지원도구"
    assert app_identity.PRIMARY_APP_DESCRIPTION == "업무 지원 자동화 도구"
    assert app_identity.PRIMARY_EXECUTABLE_FILE == "업무지원도구.exe"
    assert "결재 도우미.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES
    assert "결제도우미.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES
    assert "작업도우미.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES
    assert "dwm.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES


def test_effective_and_startup_names_ignore_legacy_random_mode():
    ui = DummyUI(
        app_name="stable-entry",
        random_name_mode=True,
        random_name_alias="visible-random",
    )

    assert app_identity.get_effective_app_name(ui) == "stable-entry"
    assert app_identity.get_startup_entry_name(ui) == "stable-entry"
