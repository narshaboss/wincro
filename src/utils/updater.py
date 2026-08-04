"""
WinCro 업데이트 및 녹화 파일 동기화 모듈

GitHub Release를 통한 자동 업데이트와 녹화 파일 공유 기능을 제공합니다.
"""

import json
import os
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, List, Any
from pathlib import Path

from .logger import get_logger
from .config import PROJECT_ROOT, DATA_DIR
from .json_utils import dump_json_file, load_json_file
from .update_service import ssl_fallback_connect

logger = get_logger(__name__)

# 업데이트 체크 캐시 (API 제한 방지)
UPDATE_CACHE_FILE = DATA_DIR / "update_cache.json"
UPDATE_CACHE_DURATION = 0  # 캐시 비활성화 (항상 새로 확인)


def _urlopen_with_fallback(url: str, headers: dict, timeout: int = 10, overall_timeout: int = 15):
    """Open an update URL without weakening certificate verification."""
    return ssl_fallback_connect(
        url,
        headers=headers,
        timeout=max(1, min(int(timeout), int(overall_timeout))),
    )

# 녹화 파일 저장 디렉토리
RECORDINGS_DIR = DATA_DIR / "recordings"


def _load_update_cache() -> Optional[Dict[str, Any]]:
    """캐시된 업데이트 정보 로드"""
    try:
        if UPDATE_CACHE_FILE.exists():
            return load_json_file(UPDATE_CACHE_FILE)
    except Exception:
        pass
    return None



def _is_cache_usable(cache: Optional[Dict[str, Any]], current_version: str) -> bool:
    """Use cache only when it matches this version and is not from the future."""
    if not cache:
        return False
    try:
        cache_timestamp = float(cache.get("timestamp", 0))
    except (TypeError, ValueError):
        return False
    if cache.get("current_version", "") != current_version:
        return False
    cache_age = time.time() - cache_timestamp
    if cache_age < 0:
        logger.warning("Ignoring update cache with future timestamp")
        return False
    return cache_age < UPDATE_CACHE_DURATION

def _save_update_cache(result: Dict[str, Any], current_version: str):
    """업데이트 결과 캐시 저장"""
    try:
        cache_data = {
            "timestamp": time.time(),
            "current_version": current_version,
            "result": result
        }
        UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        dump_json_file(UPDATE_CACHE_FILE, cache_data)
    except Exception as e:
        logger.debug(f"캐시 저장 실패: {e}")


def check_for_update(repo: str, current_version: str, force: bool = False) -> Optional[Dict[str, Any]]:
    """
    GitHub에서 새 버전 확인

    Args:
        repo: GitHub 저장소 (예: "username/repo")
        current_version: 현재 버전 (예: "1.0.0")
        force: True면 캐시 무시하고 강제 확인

    Returns:
        업데이트 정보 딕셔너리 또는 None
        {
            "update_available": bool,
            "version": str,
            "release_data": dict,
            "cached": bool (캐시된 결과인지)
        }
    """
    # 캐시 확인 (force가 아니면)
    if not force and UPDATE_CACHE_DURATION > 0:
        cache = _load_update_cache()
        if _is_cache_usable(cache, current_version):
            cache_age = time.time() - float(cache.get("timestamp", 0))
            result = cache.get("result", {})
            result["cached"] = True
            remaining_min = int((UPDATE_CACHE_DURATION - cache_age) / 60)
            logger.info(f"Using cached update info ({remaining_min} min remaining)")
            return result

    try:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {
            'User-Agent': 'TaskAssistant-Updater',
            'Accept': 'application/vnd.github.v3+json'
        }

        with _urlopen_with_fallback(api_url, headers, timeout=10) as response:
            data = json.loads(response.read().decode())

        latest_version = data.get("tag_name", "").lstrip("v")

        if _compare_versions(latest_version, current_version) > 0:
            result = {
                "update_available": True,
                "version": latest_version,
                "release_data": data,
                "cached": False
            }
        else:
            result = {
                "update_available": False,
                "version": current_version,
                "release_data": None,
                "cached": False
            }

        # 결과 캐시 저장
        _save_update_cache(result, current_version)
        return result

    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning("GitHub API 제한으로 업데이트 확인을 건너뜁니다. 잠시 후 다시 시도합니다.")
        elif 500 <= e.code < 600:
            logger.warning(f"GitHub API 일시 오류: {e.code}")
        else:
            logger.error(f"GitHub API 오류: {e.code}")
        return None
    except Exception as e:
        error_str = str(e)
        if "SSL" in error_str or "CERTIFICATE" in error_str.upper():
            logger.error(f"SSL 연결 오류 (모든 방법 실패): {e}")
        else:
            logger.error(f"업데이트 확인 오류: {e}")
        return None


def _compare_versions(v1: str, v2: str) -> int:
    """버전 비교 (v1 > v2: 1, v1 == v2: 0, v1 < v2: -1)"""
    try:
        parts1 = [int(x) for x in v1.split(".")]
        parts2 = [int(x) for x in v2.split(".")]

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


def get_remote_recordings(repo: str) -> List[Dict[str, Any]]:
    """
    GitHub Release에서 공유된 녹화 파일 목록 가져오기

    Args:
        repo: GitHub 저장소

    Returns:
        녹화 파일 정보 리스트
    """
    try:
        # recordings 태그가 붙은 릴리즈 찾기
        api_url = f"https://api.github.com/repos/{repo}/releases"
        headers = {
            'User-Agent': 'TaskAssistant-Updater',
            'Accept': 'application/vnd.github.v3+json'
        }

        with _urlopen_with_fallback(api_url, headers, timeout=10) as response:
            releases = json.loads(response.read().decode())

        recordings = []
        for release in releases:
            tag = release.get("tag_name", "")
            if tag.startswith("recordings-"):
                for asset in release.get("assets", []):
                    name = asset.get("name", "")
                    if name.endswith(".mp4") or name.endswith(".json"):
                        recordings.append({
                            "name": name,
                            "size": asset.get("size", 0),
                            "download_url": asset.get("browser_download_url"),
                            "release_tag": tag,
                            "created_at": asset.get("created_at")
                        })

        return recordings

    except Exception as e:
        logger.error(f"원격 녹화 파일 목록 조회 오류: {e}")
        return []


def download_recording(url: str, filename: str, progress_callback=None) -> bool:
    """
    녹화 파일 다운로드

    Args:
        url: 다운로드 URL
        filename: 저장할 파일명
        progress_callback: 진행률 콜백 함수 (percent: int)

    Returns:
        성공 여부
    """
    temp_path = None
    try:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        save_path = RECORDINGS_DIR / filename

        # 이미 존재하면 건너뛰기
        if save_path.exists():
            logger.info(f"이미 존재하는 파일: {filename}")
            return True

        # 임시 파일에 다운로드 (불완전한 파일 방지)
        temp_path = RECORDINGS_DIR / f"{filename}.tmp"

        headers = {
            'User-Agent': 'TaskAssistant-Updater',
            'Accept': 'application/octet-stream'
        }

        with _urlopen_with_fallback(url, headers, timeout=60, overall_timeout=60) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0

            with open(temp_path, 'wb') as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback and total_size > 0:
                        percent = int(downloaded / total_size * 100)
                        progress_callback(percent)

        # 다운로드 완료 후 최종 파일명으로 이동
        temp_path.rename(save_path)
        temp_path = None  # 성공적으로 이동했으므로 정리 불필요

        logger.info(f"녹화 파일 다운로드 완료: {filename}")
        return True

    except Exception as e:
        logger.error(f"녹화 파일 다운로드 오류: {e}")
        # 임시 파일 정리
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False


def sync_recordings(repo: str, progress_callback=None) -> Dict[str, Any]:
    """
    원격 녹화 파일과 동기화 (새 파일만 다운로드)

    Args:
        repo: GitHub 저장소
        progress_callback: 진행률 콜백 (current: int, total: int, filename: str)

    Returns:
        동기화 결과
        {
            "downloaded": int,
            "skipped": int,
            "failed": int,
            "files": list
        }
    """
    result = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "files": []
    }

    try:
        remote_files = get_remote_recordings(repo)
        total = len(remote_files)

        for i, file_info in enumerate(remote_files):
            filename = file_info["name"]
            local_path = RECORDINGS_DIR / filename

            if progress_callback:
                progress_callback(i + 1, total, filename)

            if local_path.exists():
                result["skipped"] += 1
                continue

            if download_recording(file_info["download_url"], filename):
                result["downloaded"] += 1
                result["files"].append(filename)
            else:
                result["failed"] += 1

        logger.info(f"녹화 동기화 완료: {result['downloaded']}개 다운로드, {result['skipped']}개 건너뜀")
        return result

    except Exception as e:
        logger.error(f"녹화 동기화 오류: {e}")
        return result


def get_local_recordings() -> List[Dict[str, Any]]:
    """
    로컬 녹화 파일 목록 가져오기

    Returns:
        녹화 파일 정보 리스트
    """
    recordings = []

    try:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

        for file_path in RECORDINGS_DIR.iterdir():
            if file_path.suffix in [".mp4", ".json"]:
                recordings.append({
                    "name": file_path.name,
                    "path": str(file_path),
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime
                })

        return sorted(recordings, key=lambda x: x["modified"], reverse=True)

    except Exception as e:
        logger.error(f"로컬 녹화 목록 조회 오류: {e}")
        return []


def upload_recording_info() -> str:
    """
    녹화 파일 업로드 안내 메시지 반환

    GitHub Release에 파일을 업로드하려면 gh CLI 또는 웹에서 직접 해야 합니다.
    """
    return """녹화 파일 업로드 방법:

1. GitHub 웹사이트에서 업로드:
   - https://github.com/{repo}/releases/new 접속
   - Tag: recordings-YYYYMMDD 형식으로 생성
   - 녹화 파일(.mp4, .json) 드래그앤드롭
   - Publish release

2. gh CLI 사용 (설치 필요):
   gh release create recordings-YYYYMMDD 녹화파일.mp4 녹화파일.json

업로드 후 다른 컴퓨터에서 '녹화 동기화' 버튼을 누르면 자동으로 다운로드됩니다."""
