import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from src.utils import update_service


def test_update_transport_rejects_plain_http():
    with pytest.raises(ValueError, match="HTTPS"):
        update_service.ssl_fallback_connect("http://example.invalid/update.zip")


def test_update_transport_rejects_https_to_http_redirect(monkeypatch):
    class RedirectedResponse:
        def geturl(self):
            return "http://example.invalid/update.zip"

        def close(self):
            self.closed = True

    response = RedirectedResponse()
    monkeypatch.setattr(
        update_service,
        "_verified_ssl_contexts",
        lambda: [("test", object())],
    )
    monkeypatch.setattr(
        update_service.urllib.request,
        "urlopen",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(ConnectionError, match="리디렉션"):
        update_service.ssl_fallback_connect("https://example.invalid/update.zip")
    assert response.closed is True


def test_release_sha256_parser_and_file_verification(tmp_path: Path):
    payload = b"verified update bytes"
    update_file = tmp_path / "release.zip"
    update_file.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    parsed = update_service._parse_sha256(
        f"{digest}  release.zip\n",
        "release.zip",
    )
    update_service.verify_file_sha256(update_file, parsed)

    with pytest.raises(ValueError, match="SHA-256 불일치"):
        update_service.verify_file_sha256(update_file, "0" * 64)


def test_release_sha256_rejects_sidecar_for_another_file():
    with pytest.raises(ValueError, match="파일명"):
        update_service._parse_sha256(
            f"{'a' * 64}  another.zip\n",
            "release.zip",
        )


def test_safe_extract_zip_rejects_parent_traversal(tmp_path: Path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("../outside.txt", "blocked")
    archive.seek(0)

    with zipfile.ZipFile(archive, "r") as reader:
        with pytest.raises(ValueError, match="안전하지 않은 ZIP 경로"):
            update_service.safe_extract_zip(reader, tmp_path / "extract")

    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_zip_rejects_symbolic_link(tmp_path: Path):
    archive = io.BytesIO()
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr(link, "target")
    archive.seek(0)

    with zipfile.ZipFile(archive, "r") as reader:
        with pytest.raises(ValueError, match="심볼릭 링크"):
            update_service.safe_extract_zip(reader, tmp_path / "extract")


@pytest.mark.parametrize("member_name", ["payload.exe:stream", "NUL.txt", "folder./file.txt"])
def test_safe_extract_zip_rejects_windows_unsafe_names(tmp_path: Path, member_name: str):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr(member_name, "blocked")
    archive.seek(0)

    with zipfile.ZipFile(archive, "r") as reader:
        with pytest.raises(ValueError, match="Windows"):
            update_service.safe_extract_zip(reader, tmp_path / "extract")


def test_safe_extract_zip_extracts_normal_archive(tmp_path: Path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("업무지원도구/readme.txt", "정상")
    archive.seek(0)

    destination = tmp_path / "extract"
    with zipfile.ZipFile(archive, "r") as reader:
        update_service.safe_extract_zip(reader, destination)

    assert (destination / "업무지원도구" / "readme.txt").read_text(encoding="utf-8") == "정상"


def test_update_archive_rejects_unknown_executable(tmp_path: Path):
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as writer:
        writer.writestr("release/untrusted.exe", b"not an approved executable")

    with pytest.raises(FileNotFoundError, match="허용된"):
        update_service.extract_and_find_exe(archive_path, tmp_path / "extract")


def test_update_archive_never_reuses_stale_extraction_files(tmp_path: Path, monkeypatch):
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as writer:
        writer.writestr("release/readme.txt", "new archive")
    extraction_root = tmp_path / "extract"
    extraction_root.mkdir()
    (extraction_root / "업무지원도구.exe").write_bytes(b"stale executable")
    monkeypatch.setattr(update_service.shutil, "rmtree", lambda _path: None)

    with pytest.raises(OSError, match="비우지 못했습니다"):
        update_service.extract_and_find_exe(archive_path, extraction_root)


def test_update_sources_never_disable_certificate_verification():
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "src" / "utils" / "update_service.py",
        root / "src" / "utils" / "updater.py",
        root / "src" / "ui" / "settings_view.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in sources)

    assert "CERT_NONE" not in combined
    assert "check_hostname = False" not in combined
    assert "_create_unverified_ssl_context" not in combined


def test_release_workflow_publishes_checksum_and_spec_excludes_local_state():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "build-release.yml").read_text(encoding="utf-8")
    spec = (root / "WinCro.spec").read_text(encoding="utf-8")

    assert "Get-FileHash -Path $zipPath -Algorithm SHA256" in workflow
    assert "gh release upload $tag $zipPath $shaPath --clobber" in workflow
    for private_name in (
        "notification_defaults.json",
        "config.json",
        ".keyfile",
        "wincro.db",
        "game_mode_defaults.json",
        "waypoint_presets.json",
    ):
        assert f'"{private_name}"' in spec
    assert '"backup" in lower_name' in spec
    assert '".bak" in lower_name' in spec


def test_update_batches_never_fall_back_to_an_arbitrary_executable():
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "src" / "app.py",
        root / "src" / "ui" / "settings_view.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in sources)

    assert 'for %%F in ("{app_dir}\\\\*.exe")' not in combined
    assert 'if not exist "{app_dir}\\\\%NEW_EXE_NAME%"' in combined
