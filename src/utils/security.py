"""
WinCro 보안 모듈

API 키 암호화, 민감한 데이터 보호 기능을 제공합니다.
cryptography 라이브러리를 사용하여 Fernet 대칭키 암호화를 구현합니다.
"""

import os
import base64
import hashlib
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# 키 파일 경로
PROJECT_ROOT = Path(__file__).parent.parent.parent
KEY_FILE = PROJECT_ROOT / "data" / ".keyfile"


class SecurityManager:
    """
    보안 관리자 클래스

    API 키와 민감한 데이터의 암호화/복호화를 담당합니다.
    머신 고유 식별자를 기반으로 키를 생성하여 다른 컴퓨터에서
    설정 파일을 복사해도 복호화할 수 없도록 합니다.
    """

    def __init__(self):
        """보안 관리자 초기화"""
        self._fernet: Optional[Fernet] = None
        self._ensure_key()

    def _get_machine_id(self) -> bytes:
        """
        머신 고유 식별자를 생성합니다.

        Windows 환경에서 머신 GUID를 기반으로 고유 ID를 생성합니다.
        이를 통해 설정 파일이 다른 컴퓨터로 복사되어도
        API 키를 복호화할 수 없습니다.

        Returns:
            bytes: 머신 고유 식별자
        """
        # Windows 머신 GUID 사용
        machine_id = ""

        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography"
            )
            try:
                machine_id = winreg.QueryValueEx(key, "MachineGuid")[0]
            finally:
                winreg.CloseKey(key)
        except (OSError, FileNotFoundError, PermissionError, ImportError):
            # 레지스트리 접근 실패 시 사용자명과 컴퓨터명 조합
            machine_id = f"{os.getenv('COMPUTERNAME', 'WINCRO')}-{os.getenv('USERNAME', 'USER')}"

        return machine_id.encode('utf-8')

    def _generate_key(self, salt: bytes) -> bytes:
        """
        PBKDF2를 사용하여 암호화 키를 생성합니다.

        Args:
            salt: 솔트 바이트

        Returns:
            bytes: 생성된 암호화 키
        """
        machine_id = self._get_machine_id()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )

        key = base64.urlsafe_b64encode(kdf.derive(machine_id))
        return key

    def _ensure_key(self) -> None:
        """암호화 키가 존재하는지 확인하고, 없으면 생성합니다."""
        if KEY_FILE.exists():
            # 기존 솔트 로드
            with open(KEY_FILE, 'rb') as f:
                salt = f.read()
        else:
            # 새 솔트 생성
            salt = os.urandom(16)
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(KEY_FILE, 'wb') as f:
                f.write(salt)

        key = self._generate_key(salt)
        self._fernet = Fernet(key)

    def encrypt(self, data: str) -> str:
        """
        문자열 데이터를 암호화합니다.

        Args:
            data: 암호화할 문자열

        Returns:
            str: 암호화된 문자열 (base64 인코딩)
        """
        if not data:
            return ""

        if self._fernet is None:
            self._ensure_key()

        encrypted = self._fernet.encrypt(data.encode('utf-8'))
        return base64.urlsafe_b64encode(encrypted).decode('utf-8')

    def decrypt(self, encrypted_data: str) -> Optional[str]:
        """
        암호화된 문자열을 복호화합니다.

        Args:
            encrypted_data: 암호화된 문자열

        Returns:
            Optional[str]: 복호화된 문자열, 실패 시 None
        """
        if not encrypted_data:
            return None

        if self._fernet is None:
            self._ensure_key()

        try:
            decoded = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted = self._fernet.decrypt(decoded)
            return decrypted.decode('utf-8')
        except (InvalidToken, ValueError, TypeError) as e:
            # InvalidToken: 잘못된 암호화 토큰
            # ValueError: base64 디코딩 실패
            # TypeError: 잘못된 입력 타입
            return None

    def encrypt_api_key(self, api_key: str) -> str:
        """
        OpenAI API 키를 암호화합니다.

        Args:
            api_key: 암호화할 API 키

        Returns:
            str: 암호화된 API 키
        """
        return self.encrypt(api_key)

    def decrypt_api_key(self, encrypted_key: str) -> Optional[str]:
        """
        암호화된 OpenAI API 키를 복호화합니다.

        Args:
            encrypted_key: 암호화된 API 키

        Returns:
            Optional[str]: 복호화된 API 키, 실패 시 None
        """
        return self.decrypt(encrypted_key)

    def validate_api_key_format(self, api_key: str) -> bool:
        """
        OpenAI API 키 형식을 검증합니다.

        Args:
            api_key: 검증할 API 키

        Returns:
            bool: 유효한 형식인지 여부
        """
        if not api_key:
            return False

        # OpenAI API 키는 'sk-'로 시작하고 일정 길이 이상
        if not api_key.startswith('sk-'):
            return False

        if len(api_key) < 40:
            return False

        return True

    def hash_data(self, data: str) -> str:
        """
        데이터의 SHA-256 해시를 생성합니다.

        Args:
            data: 해시할 데이터

        Returns:
            str: 해시 문자열 (hex)
        """
        return hashlib.sha256(data.encode('utf-8')).hexdigest()

    def secure_delete_key(self) -> bool:
        """
        키 파일을 안전하게 삭제합니다.

        Returns:
            bool: 삭제 성공 여부
        """
        try:
            if KEY_FILE.exists():
                # 파일을 랜덤 데이터로 덮어쓴 후 삭제
                with open(KEY_FILE, 'wb') as f:
                    f.write(os.urandom(64))
                KEY_FILE.unlink()
            self._fernet = None
            return True
        except (IOError, OSError, PermissionError):
            return False


# 전역 보안 관리자 인스턴스
security_manager = SecurityManager()


def encrypt_api_key(api_key: str) -> str:
    """API 키 암호화 헬퍼 함수"""
    return security_manager.encrypt_api_key(api_key)


def decrypt_api_key(encrypted_key: str) -> Optional[str]:
    """API 키 복호화 헬퍼 함수"""
    return security_manager.decrypt_api_key(encrypted_key)


def validate_api_key(api_key: str) -> bool:
    """API 키 검증 헬퍼 함수"""
    return security_manager.validate_api_key_format(api_key)
