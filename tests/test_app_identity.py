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
    assert "작업도우미.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES
    assert "dwm.exe" in app_identity.LEGACY_EXECUTABLE_ALIASES


def test_refresh_random_app_name_replaces_existing_alias(monkeypatch):
    names = iter(["old-name", "new-name"])
    monkeypatch.setattr(app_identity, "generate_random_app_name", lambda: next(names))

    save_calls = []
    ui = DummyUI(random_name_mode=True, random_name_alias="old-name")

    result = app_identity.refresh_random_app_name(ui, save_callback=lambda: save_calls.append(True))

    assert result == "new-name"
    assert ui.random_name_alias == "new-name"
    assert save_calls == [True]
    assert app_identity.get_effective_app_name(ui) == "new-name"


def test_startup_entry_name_stays_stable_when_random_mode_is_enabled():
    ui = DummyUI(
        app_name="stable-entry",
        random_name_mode=True,
        random_name_alias="visible-random",
    )

    assert app_identity.get_effective_app_name(ui) == "visible-random"
    assert app_identity.get_startup_entry_name(ui) == "stable-entry"
