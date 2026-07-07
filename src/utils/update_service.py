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
import urllib.request
import urllib.error
from typing import Optional, Callable, Tuple
from datetime import datetime

from .logger import get_logger
from .config import get_config, save_config
from .app_identity import PRIMARY_EXECUTABLE_FILE, LEGACY_EXECUTABLE_ALIASES

logger = get_logger(__name__)

# 요청 헤더
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) TaskAssistant-Updater/1.0'}


def _get_ssl_context(verify: bool = True):
    """SSL 컨텍스트 생성 (SSL 모듈 로드 실패 시 None 반환)"""
    try:
        import ssl
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception as e:
        logger.warning(f"SSL 컨텍스트 생성 실패: {e}")
        return None


def ssl_fallback_connect(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 30,
    on_status: Optional[Callable[[str], None]] = None,
):
    """
    SSL 다중 폴백 방식으로 URL에 연결합니다.

    4가지 방법을 순서대로 시도:
    1. 기본 SSL
    2. SSL 검증 완화
    3. SSL 없음
    4. 프록시 핸들러

    Returns:
        urllib response 객체 또는 None
    """
    if headers is None:
        headers = _HEADERS

    response = None
    last_error = None

    methods = [
        ("기본 SSL", lambda: _get_ssl_context(verify=True)),
        ("SSL 검증 완화", lambda: _get_ssl_context(verify=False)),
        ("SSL 없음", lambda: None),
    ]

    for method_name, get_context in methods:
        try:
            logger.info(f"연결 시도: {method_name}")
            if on_status:
                on_status(f"서버 연결 중... ({method_name})")
            req = urllib.request.Request(url, headers=headers)
            ctx = get_context()
            if ctx:
                response = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            else:
                response = urllib.request.urlopen(req, timeout=timeout)
            logger.info(f"{method_name} 성공")
            return response
        except Exception as e:
            last_error = e
            logger.warning(f"{method_name} 실패: {e}")

    # 방법 4: 프록시 핸들러
    try:
        logger.info("연결 시도: 프록시 핸들러")
        if on_status:
            on_status("서버 연결 중... (프록시 핸들러)")
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers=headers)
        response = opener.open(req, timeout=timeout)
        logger.info("프록시 핸들러 성공")
        return response
    except Exception as e:
        last_error = e
        logger.error(f"프록시 핸들러 실패: {e}")

    if last_error:
        raise ConnectionError(f"서버 연결 실패: {last_error}") from last_error
    return None


def download_file(
    response,
    dest_path: str,
    chunk_size: int = 262144,
    on_progress: Optional[Callable[[int, int], None]] = None,
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
    downloaded = 0
    last_log_percent = -1

    with open(dest_path, 'wb') as f:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)

            if total_size > 0:
                percent = int(downloaded / total_size * 100)
                if percent // 10 > last_log_percent // 10:
                    last_log_percent = percent
                    logger.info(f"다운로드 진행: {percent}%")

            if on_progress:
                on_progress(downloaded, total_size)

    logger.info(f"다운로드 완료: {downloaded / (1024*1024):.1f} MB")
    return downloaded


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
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)

    logger.info("zip 파일 압축 해제 중...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
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

    found_dir, found_name = discovered[0]
    logger.info(f"exe 발견: {os.path.join(found_dir, found_name)}")
    return found_dir, found_name


def find_zip_asset(release_data: dict) -> Optional[dict]:
    """릴리즈 데이터에서 zip 에셋을 찾습니다."""
    assets = release_data.get("assets", [])
    for asset in assets:
        name = asset.get("name", "").lower()
        if name.endswith(".zip"):
            return asset
    return None


def save_update_config(version: str) -> None:
    """업데이트 정보를 설정에 저장합니다."""
    config = get_config()
    config.update.last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config.update.last_version = version
    save_config()


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
        "$names=@('업무지원도구','WinCro','WinCro 개발','작업도우미','결재 도우미','결제 도우미','결제도우미');"
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
        "$matchName=$names -contains $_.BaseName;"
        "$matchTarget=($target -and ($target.StartsWith($appDir,[StringComparison]::OrdinalIgnoreCase) -or ($legacyExe -contains $targetName)));"
        "if($matchName -or $matchTarget){"
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


def _create_unverified_ssl_context():
    """SSL 검증을 완화한 컨텍스트 생성 (레거시 호환용 - _get_ssl_context 사용 권장)"""
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except ImportError:
        return None
