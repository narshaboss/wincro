"""
WinCro 업데이트 서비스

업데이트 다운로드, 압축 해제, 배치 파일 생성 등 공통 로직을 제공합니다.
app.py와 settings_view.py 모두 이 서비스를 사용합니다.
"""

import os
import sys
import shutil
import subprocess
import zipfile
import tempfile
import hashlib
import re
import ssl
import urllib.request
import urllib.error
from pathlib import Path, PurePosixPath
from typing import Optional, Callable, Tuple
from datetime import datetime

from .logger import get_logger
from .config import get_config, save_config
from .app_identity import PRIMARY_EXECUTABLE_FILE, LEGACY_EXECUTABLE_ALIASES

logger = get_logger(__name__)

# 요청 헤더
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) TaskAssistant-Updater/1.0'}
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _verified_ssl_contexts() -> list[tuple[str, ssl.SSLContext]]:
    """Return trusted TLS contexts without ever disabling verification."""
    contexts = [("시스템 인증서", ssl.create_default_context())]
    try:
        import certifi

        certifi_context = ssl.create_default_context(cafile=certifi.where())
        contexts.append(("내장 CA 인증서", certifi_context))
    except (ImportError, OSError, ssl.SSLError) as exc:
        logger.debug(f"내장 CA 인증서 컨텍스트 사용 불가: {exc}")
    return contexts


def ssl_fallback_connect(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 30,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Open an HTTPS URL using certificate-verified contexts only."""
    if headers is None:
        headers = _HEADERS

    last_error = None

    if not str(url).lower().startswith("https://"):
        raise ValueError("업데이트 연결은 HTTPS URL만 허용됩니다")

    for method_name, context in _verified_ssl_contexts():
        try:
            logger.info(f"연결 시도: {method_name}")
            if on_status:
                on_status(f"서버 연결 중... ({method_name})")
            req = urllib.request.Request(url, headers=headers)
            response = urllib.request.urlopen(req, timeout=timeout, context=context)
            final_url = str(getattr(response, "geturl", lambda: url)() or url)
            if not final_url.lower().startswith("https://"):
                response.close()
                raise ValueError("업데이트 연결이 안전하지 않은 URL로 리디렉션되었습니다")
            logger.info(f"{method_name} 성공")
            return response
        except Exception as e:
            last_error = e
            logger.warning(f"{method_name} 실패: {e}")

    if last_error:
        raise ConnectionError(f"서버 연결 실패: {last_error}") from last_error
    raise ConnectionError("서버 연결 실패: 사용할 수 있는 신뢰 CA가 없습니다")

def download_file(
    response,
    dest_path: str,
    chunk_size: int = 262144,
    on_progress: Optional[Callable[[int, int], None]] = None,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> int:
    """
    응답 스트림에서 파일을 다운로드합니다.

    Args:
        response: urllib response 객체
        dest_path: 저장할 파일 경로
        chunk_size: 청크 크기 (기본 256KB)
        on_progress: (downloaded_bytes, total_bytes) 콜백

    Returns:
        다운로드된 바이트 수
    """
    total_size = int(response.headers.get('Content-Length', 0) or 0)
    if total_size < 0 or total_size > max_bytes:
        raise ValueError(f"다운로드 크기 제한 초과: {total_size} bytes")
    downloaded = 0
    last_log_percent = -1

    destination = Path(dest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    try:
        with partial.open('wb') as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise ValueError(f"다운로드 크기 제한 초과: {downloaded} bytes")
                f.write(chunk)

                if total_size > 0:
                    percent = int(downloaded / total_size * 100)
                    if percent // 10 > last_log_percent // 10:
                        last_log_percent = percent
                        logger.info(f"다운로드 진행: {percent}%")

                if on_progress:
                    on_progress(downloaded, total_size)
            f.flush()
            os.fsync(f.fileno())

        if total_size and downloaded != total_size:
            raise IOError(f"다운로드 크기 불일치: expected={total_size}, actual={downloaded}")
        os.replace(partial, destination)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass

    logger.info(f"다운로드 완료: {downloaded / (1024*1024):.1f} MB")
    return downloaded


def safe_extract_zip(
    zip_ref: zipfile.ZipFile,
    extract_dir: str | os.PathLike[str],
    *,
    max_entries: int = 20000,
    max_total_size: int = 4 * 1024 * 1024 * 1024,
    max_file_size: int = 1024 * 1024 * 1024,
) -> None:
    """Extract a ZIP after rejecting traversal, links, and zip bombs."""
    root = Path(extract_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    infos = zip_ref.infolist()
    if len(infos) > max_entries:
        raise ValueError(f"ZIP 항목 수 제한 초과: {len(infos)}")

    total_size = 0
    seen: set[str] = set()
    validated: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in infos:
        raw_name = info.filename.replace("\\", "/")
        name = PurePosixPath(raw_name)
        if not raw_name or "\x00" in raw_name or name.is_absolute() or ".." in name.parts:
            raise ValueError(f"안전하지 않은 ZIP 경로: {info.filename!r}")
        if re.match(r"^[A-Za-z]:", raw_name):
            raise ValueError(f"드라이브 경로가 포함된 ZIP 항목: {info.filename!r}")
        for part in name.parts:
            if ":" in part or part.endswith((".", " ")):
                raise ValueError(f"Windows에서 안전하지 않은 ZIP 경로: {info.filename!r}")
            reserved_base = part.split(".", 1)[0].casefold()
            if reserved_base in _WINDOWS_RESERVED_NAMES:
                raise ValueError(f"Windows 예약 이름이 포함된 ZIP 항목: {info.filename!r}")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise ValueError(f"ZIP 심볼릭 링크는 허용되지 않습니다: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise ValueError(f"암호화 ZIP 항목은 허용되지 않습니다: {info.filename!r}")
        if info.file_size < 0 or info.file_size > max_file_size:
            raise ValueError(f"ZIP 단일 파일 크기 제한 초과: {info.filename!r}")
        total_size += info.file_size
        if total_size > max_total_size:
            raise ValueError("ZIP 전체 압축 해제 크기 제한 초과")
        if info.file_size and info.compress_size == 0:
            raise ValueError(f"비정상 ZIP 압축 크기: {info.filename!r}")
        if info.compress_size and info.file_size / info.compress_size > 1000:
            raise ValueError(f"ZIP 압축률 제한 초과: {info.filename!r}")

        normalized = "/".join(name.parts).casefold()
        if normalized in seen:
            raise ValueError(f"중복 ZIP 경로: {info.filename!r}")
        seen.add(normalized)
        target = root.joinpath(*name.parts).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"ZIP 경로 이탈 감지: {info.filename!r}") from exc
        validated.append((info, target))

    for info, target in validated:
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zip_ref.open(info, "r") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())


def extract_and_find_exe(zip_path: str, extract_dir: str) -> Tuple[str, str]:
    """
    ZIP 파일을 압축 해제하고 exe가 포함된 폴더를 찾습니다.

    Args:
        zip_path: ZIP 파일 경로
        extract_dir: 압축 해제 디렉토리

    Returns:
        (exe가 포함된 디렉토리 경로, exe 파일명)

    Raises:
        FileNotFoundError: exe를 찾을 수 없는 경우
    """
    extraction_root = Path(extract_dir)
    if extraction_root.exists():
        shutil.rmtree(extraction_root)
    if extraction_root.exists():
        raise OSError(f"기존 업데이트 임시 폴더를 비우지 못했습니다: {extract_dir}")
    extraction_root.mkdir(parents=True, exist_ok=False)

    logger.info("zip 파일 압축 해제 중...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        safe_extract_zip(zip_ref, extract_dir)
    logger.info("압축 해제 완료")

    preferred_names = [PRIMARY_EXECUTABLE_FILE] + list(LEGACY_EXECUTABLE_ALIASES)
    discovered: list[Tuple[str, str]] = []

    # exe가 있는 폴더 찾기
    for item in os.listdir(extract_dir):
        item_path = os.path.join(extract_dir, item)
        if os.path.isdir(item_path):
            for sub_item in os.listdir(item_path):
                if sub_item.endswith(".exe"):
                    discovered.append((item_path, sub_item))
        elif item.endswith(".exe"):
            discovered.append((extract_dir, item))

    if not discovered:
        raise FileNotFoundError("업데이트 파일에 exe가 없습니다")

    for preferred_name in preferred_names:
        for found_dir, found_name in discovered:
            if found_name.lower() == preferred_name.lower():
                logger.info(f"exe 발견(우선): {os.path.join(found_dir, found_name)}")
                return found_dir, found_name

    names = ", ".join(found_name for _found_dir, found_name in discovered)
    raise FileNotFoundError(f"허용된 업무지원도구 exe가 없습니다: {names}")


def find_zip_asset(release_data: dict) -> Optional[dict]:
    """릴리즈 데이터에서 zip 에셋을 찾습니다."""
    assets = release_data.get("assets", [])
    for asset in assets:
        name = asset.get("name", "").lower()
        if name.endswith(".zip"):
            return asset
    return None


def find_checksum_asset(release_data: dict, zip_asset: dict) -> Optional[dict]:
    """Find the SHA-256 sidecar matching the selected ZIP asset."""
    zip_name = str(zip_asset.get("name", ""))
    accepted = {f"{zip_name}.sha256".casefold(), f"{Path(zip_name).stem}.sha256".casefold()}
    for asset in release_data.get("assets", []):
        if str(asset.get("name", "")).casefold() in accepted:
            return asset
    return None


def _parse_sha256(text: str, expected_filename: str) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+\*?(.+))?", line)
    if not match:
        raise ValueError("SHA-256 파일 형식이 올바르지 않습니다")
    listed_name = (match.group(2) or "").strip()
    if listed_name and Path(listed_name).name.casefold() != Path(expected_filename).name.casefold():
        raise ValueError("SHA-256 파일명이 업데이트 ZIP과 일치하지 않습니다")
    return match.group(1).lower()


def fetch_expected_sha256(zip_asset: dict, checksum_asset: Optional[dict]) -> str:
    """Resolve the trusted release digest from GitHub metadata or a sidecar."""
    digest = str(zip_asset.get("digest", "") or "").strip().lower()
    if digest.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", digest[7:]):
        return digest[7:]
    if not checksum_asset:
        raise ValueError("업데이트 무결성 파일(.sha256)이 없습니다")
    checksum_url = str(checksum_asset.get("browser_download_url", "") or "")
    if not checksum_url:
        raise ValueError("업데이트 무결성 파일 URL이 없습니다")
    with ssl_fallback_connect(checksum_url, timeout=20) as response:
        payload = response.read(4097)
    if len(payload) > 4096:
        raise ValueError("SHA-256 파일이 비정상적으로 큽니다")
    return _parse_sha256(payload.decode("utf-8-sig"), str(zip_asset.get("name", "update.zip")))


def verify_file_sha256(path: str | os.PathLike[str], expected_sha256: str) -> None:
    """Raise when a downloaded file does not match its release digest."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"업데이트 SHA-256 불일치: expected={expected_sha256}, actual={actual}")
    logger.info(f"업데이트 SHA-256 검증 완료: {actual}")

def save_update_config(version: str) -> None:
    """업데이트 정보를 설정에 저장합니다."""
    config = get_config()
    config.update.last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config.update.last_version = version
    if not save_config():
        raise OSError("업데이트 상태 설정을 디스크에 저장하지 못했습니다")


def get_update_paths():
    """업데이트에 필요한 경로 정보를 반환합니다."""
    current_exe = sys.executable
    app_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    temp_dir = tempfile.gettempdir()
    data_dir = os.path.join(app_dir, "_internal", "data")
    return {
        "current_exe": current_exe,
        "app_dir": app_dir,
        "exe_name": exe_name,
        "temp_dir": temp_dir,
        "data_dir": data_dir,
        "extract_dir": os.path.join(temp_dir, "taskassistant_update_extract"),
        "data_backup": os.path.join(temp_dir, "taskassistant_data_backup"),
        "batch_path": os.path.join(temp_dir, "taskassistant_update.bat"),
    }


def _shortcut_refresh_powershell_command(*, escape_for_cmd: bool = False) -> str:
    """Build the PowerShell command used to repair existing shortcuts."""
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    primary_exe = _ps_quote(PRIMARY_EXECUTABLE_FILE)
    legacy_exe = ",".join(_ps_quote(name) for name in LEGACY_EXECUTABLE_ALIASES)
    powershell_command = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$appDir=$env:WINCRO_UPDATE_APP_DIR;"
        "$targetExe=$env:WINCRO_UPDATE_EXE;"
        "$icon=($targetExe + ',0');"
        "$names=@('업무지원도구','WinCro','작업도우미','결재 도우미','결제 도우미','결제도우미');"
        f"$legacyExe=@({primary_exe},{legacy_exe});"
        "$folders=@("
        "[Environment]::GetFolderPath('Desktop'),"
        "[Environment]::GetFolderPath('CommonDesktopDirectory'),"
        "[Environment]::GetFolderPath('StartMenu'),"
        "[Environment]::GetFolderPath('CommonStartMenu'),"
        "(Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar'),"
        "(Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\StartMenu')"
        ");"
        "$shell=New-Object -ComObject WScript.Shell;"
        "foreach($folder in $folders){"
        "if(-not $folder -or -not (Test-Path $folder)){continue};"
        "Get-ChildItem -Path $folder -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {"
        "try {"
        "$lnk=$shell.CreateShortcut($_.FullName);"
        "$target=[string]$lnk.TargetPath;"
        "$targetName=[IO.Path]::GetFileName($target);"
        "$isDeveloperShortcut=($_.BaseName -eq 'WinCro 개발');"
        "$matchName=$names -contains $_.BaseName;"
        "$matchTarget=($target -and ($target.StartsWith($appDir,[StringComparison]::OrdinalIgnoreCase) -or ($legacyExe -contains $targetName)));"
        "if((-not $isDeveloperShortcut) -and ($matchName -or $matchTarget)){"
        "$lnk.TargetPath=$targetExe;"
        "$lnk.WorkingDirectory=$appDir;"
        "$lnk.IconLocation=$icon;"
        "$lnk.Save();"
        "}"
        "} catch {}"
        "}"
        "};"
        "try { ie4uinit.exe -show } catch {};"
        "try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) } catch {}"
    )
    if escape_for_cmd:
        # Escape the PowerShell pipeline for cmd.exe because this is embedded in
        # a generated .bat file.
        powershell_command = powershell_command.replace("|", "^|")
    return powershell_command


def refresh_existing_shortcut_icons(app_dir: Optional[str] = None, exe_path: Optional[str] = None) -> bool:
    """Refresh stale WinCro shortcut icons from the currently running app.

    Older versions generate the update batch, so they cannot run newer shortcut
    repair code during the update into this version. Running this once after the
    new executable starts closes that rollout gap.
    """
    if os.name != "nt":
        return False

    exe_path = exe_path or sys.executable
    app_dir = app_dir or os.path.dirname(exe_path)
    if not exe_path or not os.path.exists(exe_path):
        logger.warning(f"[아이콘] 바로가기 자가복구 건너뜀: 실행 파일 없음 ({exe_path})")
        return False

    env = os.environ.copy()
    env["WINCRO_UPDATE_APP_DIR"] = app_dir
    env["WINCRO_UPDATE_EXE"] = exe_path
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _shortcut_refresh_powershell_command(),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=creationflags,
            check=False,
        )
        if result.returncode == 0:
            logger.info("[아이콘] 바로가기/고정 아이콘 자가복구 완료")
            return True
        logger.warning(f"[아이콘] 바로가기 자가복구 실패: exit={result.returncode}")
    except subprocess.TimeoutExpired:
        logger.warning("[아이콘] 바로가기 자가복구 시간 초과")
    except Exception as exc:
        logger.warning(f"[아이콘] 바로가기 자가복구 예외: {exc}")
    return False


def build_shortcut_icon_refresh_batch(app_dir: str) -> str:
    """Return a batch snippet that refreshes existing WinCro shortcuts.

    The application files can update correctly while Windows keeps an old .lnk
    icon or pinned taskbar icon. Refreshing shortcuts in-place makes icon
    rollout deterministic without deleting user shortcuts.
    """
    app_dir = app_dir.replace('"', '')
    powershell_command = _shortcut_refresh_powershell_command(escape_for_cmd=True)
    return f"""
echo [아이콘] 바로가기 아이콘 갱신 중...
set "WINCRO_UPDATE_APP_DIR={app_dir}"
set "WINCRO_UPDATE_EXE=%WINCRO_UPDATE_APP_DIR%\\%NEW_EXE_NAME%"
if defined NEW_EXE_NAME (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "{powershell_command}" >nul 2>&1
)
"""


def classify_error(error: Exception) -> str:
    """업데이트 오류를 사용자 친화적 메시지로 분류합니다."""
    error_str = str(error)

    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return "릴리즈 파일을 찾을 수 없습니다 (404)"
        elif error.code == 403:
            return "접근 권한 없음 (403) - 잠시 후 재시도"
        elif error.code >= 500:
            return f"서버 오류 ({error.code}) - 잠시 후 재시도"
        return f"HTTP 오류 {error.code}"

    if isinstance(error, urllib.error.URLError):
        reason = str(error.reason)
        if "SSL" in reason or "CERTIFICATE" in reason.upper():
            return "SSL 인증서 오류 - VPN/방화벽 확인"
        if "Connection refused" in reason:
            return "연결 거부됨 - 네트워크 확인"
        if "Name or service not known" in reason:
            return "서버를 찾을 수 없음 - 인터넷 확인"
        if "timed out" in reason.lower():
            return "연결 시간 초과 - 네트워크 확인"
        return f"연결 오류: {reason[:30]}"

    # SSL 오류 체크 (ssl 모듈 지연 import)
    try:
        import ssl
        if isinstance(error, ssl.SSLError):
            return "SSL 보안 연결 실패 - VPN/방화벽 확인"
    except ImportError:
        pass

    if isinstance(error, ConnectionError):
        if "SSL" in error_str or "CERTIFICATE" in error_str.upper():
            return "SSL 인증서 오류 - 네트워크 확인 필요"
        if "timeout" in error_str.lower():
            return "서버 연결 시간 초과"
        return f"연결 실패: {error_str[:30]}"

    if isinstance(error, zipfile.BadZipFile):
        return "손상된 zip 파일 - 재시도 필요"

    if isinstance(error, OSError):
        if "No space" in error_str:
            return "디스크 공간 부족"
        if "Permission" in error_str:
            return "파일 접근 권한 없음"
        return f"파일 오류: {error_str[:30]}"

    if "SSL" in error_str or "CERTIFICATE" in error_str.upper():
        return "SSL 연결 실패 - 네트워크 설정 확인"

    return f"오류: {error_str[:35]}"
