"""
WinCro 업데이트 및 녹화 파일 동기화 모듈

GitHub Release를 통한 자동 업데이트와 녹화 파일 공유 기능을 제공합니다.
"""

import json
import os
import ssl
import socket
import urllib.request
import urllib.error
from typing import Optional, Dict, List, Any
from pathlib import Path

from .logger import get_logger
from .config import PROJECT_ROOT, DATA_DIR

logger = get_logger(__name__)


def _urlopen_with_fallback(url: str, headers: dict, timeout: int = 10):
    """SSL 폴백을 포함한 URL 열기 - 4가지 방법 시도"""
    req = urllib.request.Request(url, headers=headers)
    last_error = None

    # 방법 1: 기본 SSL
    try:
        ctx = ssl.create_default_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except Exception as e1:
        last_error = e1
        logger.debug(f"SSL 방법 1 실패: {e1}")

    # 방법 2: SSL 검증 완화
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except Exception as e2:
        last_error = e2
        logger.debug(f"SSL 방법 2 실패: {e2}")

    # 방법 3: SSL 컨텍스트 없이
    try:
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as e3:
        last_error = e3
        logger.debug(f"SSL 방법 3 실패: {e3}")

    # 방법 4: 프록시 핸들러
    try:
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers=headers)
        return opener.open(req, timeout=timeout)
    except Exception as e4:
        last_error = e4
        logger.debug(f"SSL 방법 4 실패: {e4}")

    # 모든 방법 실패
    raise last_error

# 녹화 파일 저장 디렉토리
RECORDINGS_DIR = DATA_DIR / "recordings"


def check_for_update(repo: str, current_version: str) -> Optional[Dict[str, Any]]:
    """
    GitHub에서 새 버전 확인

    Args:
        repo: GitHub 저장소 (예: "username/repo")
        current_version: 현재 버전 (예: "1.0.0")

    Returns:
        업데이트 정보 딕셔너리 또는 None
        {
            "update_available": bool,
            "version": str,
            "release_data": dict
        }
    """
    try:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {
            'User-Agent': 'WinCro-Updater',
            'Accept': 'application/vnd.github.v3+json'
        }

        with _urlopen_with_fallback(api_url, headers, timeout=10) as response:
            data = json.loads(response.read().decode())

        latest_version = data.get("tag_name", "").lstrip("v")

        if _compare_versions(latest_version, current_version) > 0:
            return {
                "update_available": True,
                "version": latest_version,
                "release_data": data
            }
        else:
            return {
                "update_available": False,
                "version": current_version,
                "release_data": None
            }

    except urllib.error.HTTPError as e:
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
            'User-Agent': 'WinCro-Updater',
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
    try:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        save_path = RECORDINGS_DIR / filename

        # 이미 존재하면 건너뛰기
        if save_path.exists():
            logger.info(f"이미 존재하는 파일: {filename}")
            return True

        headers = {
            'User-Agent': 'WinCro-Updater',
            'Accept': 'application/octet-stream'
        }

        with _urlopen_with_fallback(url, headers, timeout=300) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0

            with open(save_path, 'wb') as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback and total_size > 0:
                        percent = int(downloaded / total_size * 100)
                        progress_callback(percent)

        logger.info(f"녹화 파일 다운로드 완료: {filename}")
        return True

    except Exception as e:
        logger.error(f"녹화 파일 다운로드 오류: {e}")
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
