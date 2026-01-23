"""
WinCro 데이터베이스 관리 모듈

SQLite 데이터베이스 연결, 쿼리, CRUD 작업을 담당합니다.
"""

import sqlite3
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from ..utils.logger import get_logger
from ..utils.config import DATA_DIR
from .models import Sequence, ActionTemplate, ExecutionLog, Recording, Action

logger = get_logger(__name__)

# 데이터베이스 파일 경로
DB_PATH = DATA_DIR / "wincro.db"


class DatabaseManager:
    """
    데이터베이스 관리자 클래스

    SQLite 데이터베이스에 대한 모든 작업을 처리합니다.
    스레드 안전을 위해 Lock을 사용합니다.
    """

    _instance: Optional['DatabaseManager'] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> 'DatabaseManager':
        """싱글톤 인스턴스 생성 (스레드 안전)"""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """데이터베이스 관리자 초기화"""
        if not self._initialized:
            self._db_path = DB_PATH
            self._ensure_database()
            self._initialized = True

    def _ensure_database(self) -> None:
        """데이터베이스 파일 및 테이블 생성"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            self._create_tables(conn)

    @contextmanager
    def _get_connection(self):
        """데이터베이스 연결 컨텍스트 매니저"""
        conn = None
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.error(f"데이터베이스 연결 실패: {e}")
            raise

        try:
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"데이터베이스 오류: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """테이블 생성"""
        cursor = conn.cursor()

        # 시퀀스 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                actions TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                last_run TEXT,
                run_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                version TEXT DEFAULT '1.0'
            )
        ''')

        # 액션 템플릿 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS action_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER,
                step_order INTEGER,
                action_type TEXT NOT NULL,
                target_image_path TEXT,
                parameters TEXT DEFAULT '{}',
                FOREIGN KEY (sequence_id) REFERENCES sequences(id) ON DELETE CASCADE
            )
        ''')

        # 실행 로그 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER,
                sequence_name TEXT,
                started_at TEXT,
                ended_at TEXT,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                ai_interventions INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                completed_steps INTEGER DEFAULT 0,
                skipped_steps INTEGER DEFAULT 0,
                failed_steps INTEGER DEFAULT 0,
                execution_time_ms INTEGER DEFAULT 0,
                notes TEXT,
                FOREIGN KEY (sequence_id) REFERENCES sequences(id) ON DELETE SET NULL
            )
        ''')

        # 녹화 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                video_path TEXT NOT NULL,
                input_log_path TEXT,
                duration_seconds REAL DEFAULT 0,
                fps INTEGER DEFAULT 30,
                resolution_width INTEGER DEFAULT 1920,
                resolution_height INTEGER DEFAULT 1080,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                analyzed INTEGER DEFAULT 0,
                ai_analyzed INTEGER DEFAULT 0,
                sequence_id INTEGER,
                automation_plan_id TEXT,
                FOREIGN KEY (sequence_id) REFERENCES sequences(id) ON DELETE SET NULL
            )
        ''')

        # 기존 테이블에 새 컬럼 추가 (마이그레이션)
        migrations = [
            ('recordings', 'ai_analyzed', 'INTEGER DEFAULT 0'),
            ('recordings', 'automation_plan_id', 'TEXT'),
            ('recordings', 'locked', 'INTEGER DEFAULT 0'),
        ]
        for table, column, col_type in migrations:
            try:
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
                logger.debug(f"마이그레이션: {table}.{column} 컬럼 추가됨")
            except sqlite3.OperationalError:
                pass  # 이미 존재하는 컬럼

        # 인덱스 생성
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sequences_name ON sequences(name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sequences_created ON sequences(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_execution_logs_sequence ON execution_logs(sequence_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_execution_logs_status ON execution_logs(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_recordings_created ON recordings(created_at)')

        conn.commit()
        logger.info("데이터베이스 테이블 초기화 완료")

    # ========== 시퀀스 CRUD ==========

    def create_sequence(self, sequence: Sequence) -> int:
        """
        새 시퀀스 생성

        Args:
            sequence: 생성할 시퀀스 객체

        Returns:
            int: 생성된 시퀀스 ID
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO sequences (name, description, actions, created_at, updated_at, tags, version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                sequence.name,
                sequence.description,
                json.dumps([a.to_dict() for a in sequence.actions], ensure_ascii=False),
                now,
                now,
                json.dumps(sequence.tags, ensure_ascii=False),
                sequence.version,
            ))

            sequence_id = cursor.lastrowid
            logger.info(f"시퀀스 생성: {sequence.name} (ID: {sequence_id})")
            return sequence_id

    def get_sequence(self, sequence_id: int) -> Optional[Sequence]:
        """
        시퀀스 조회

        Args:
            sequence_id: 시퀀스 ID

        Returns:
            Optional[Sequence]: 시퀀스 객체 또는 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM sequences WHERE id = ?', (sequence_id,))
            row = cursor.fetchone()

            if row:
                return Sequence.from_dict(dict(row))
            return None

    def get_all_sequences(self, order_by: str = "created_at", desc: bool = True) -> List[Sequence]:
        """
        모든 시퀀스 조회

        Args:
            order_by: 정렬 기준 컬럼
            desc: 내림차순 여부

        Returns:
            List[Sequence]: 시퀀스 목록
        """
        # SQL 인젝션 방지: 화이트리스트 검증 및 매핑 사용
        valid_columns = {
            "id": "id",
            "name": "name",
            "created_at": "created_at",
            "last_run": "last_run",
            "run_count": "run_count"
        }

        # 유효한 컬럼만 사용 (화이트리스트에 없으면 기본값)
        safe_order_by = valid_columns.get(order_by, "created_at")
        safe_direction = "DESC" if desc else "ASC"

        # 정적 쿼리 구성 (파라미터화 불가한 ORDER BY를 위해 검증된 값만 사용)
        query_map = {
            ("id", "ASC"): 'SELECT * FROM sequences ORDER BY id ASC',
            ("id", "DESC"): 'SELECT * FROM sequences ORDER BY id DESC',
            ("name", "ASC"): 'SELECT * FROM sequences ORDER BY name ASC',
            ("name", "DESC"): 'SELECT * FROM sequences ORDER BY name DESC',
            ("created_at", "ASC"): 'SELECT * FROM sequences ORDER BY created_at ASC',
            ("created_at", "DESC"): 'SELECT * FROM sequences ORDER BY created_at DESC',
            ("last_run", "ASC"): 'SELECT * FROM sequences ORDER BY last_run ASC',
            ("last_run", "DESC"): 'SELECT * FROM sequences ORDER BY last_run DESC',
            ("run_count", "ASC"): 'SELECT * FROM sequences ORDER BY run_count ASC',
            ("run_count", "DESC"): 'SELECT * FROM sequences ORDER BY run_count DESC',
        }

        query = query_map.get((safe_order_by, safe_direction),
                              'SELECT * FROM sequences ORDER BY created_at DESC')

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            return [Sequence.from_dict(dict(row)) for row in rows]

    def update_sequence(self, sequence: Sequence) -> bool:
        """
        시퀀스 업데이트

        Args:
            sequence: 업데이트할 시퀀스 객체

        Returns:
            bool: 성공 여부
        """
        if sequence.id is None:
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE sequences SET
                    name = ?,
                    description = ?,
                    actions = ?,
                    updated_at = ?,
                    tags = ?,
                    version = ?
                WHERE id = ?
            ''', (
                sequence.name,
                sequence.description,
                json.dumps([a.to_dict() for a in sequence.actions], ensure_ascii=False),
                datetime.now().isoformat(),
                json.dumps(sequence.tags, ensure_ascii=False),
                sequence.version,
                sequence.id,
            ))

            logger.info(f"시퀀스 업데이트: {sequence.name} (ID: {sequence.id})")
            return cursor.rowcount > 0

    def delete_sequence(self, sequence_id: int) -> bool:
        """
        시퀀스 삭제

        Args:
            sequence_id: 삭제할 시퀀스 ID

        Returns:
            bool: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM sequences WHERE id = ?', (sequence_id,))
            success = cursor.rowcount > 0

            if success:
                logger.info(f"시퀀스 삭제: ID {sequence_id}")
            return success

    def search_sequences(self, query: str) -> List[Sequence]:
        """
        시퀀스 검색

        Args:
            query: 검색어

        Returns:
            List[Sequence]: 검색 결과
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            search_term = f"%{query}%"
            cursor.execute('''
                SELECT * FROM sequences
                WHERE name LIKE ? OR description LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC
            ''', (search_term, search_term, search_term))
            rows = cursor.fetchall()

            return [Sequence.from_dict(dict(row)) for row in rows]

    def update_sequence_run_stats(
        self,
        sequence_id: int,
        success: bool
    ) -> None:
        """
        시퀀스 실행 통계 업데이트

        Args:
            sequence_id: 시퀀스 ID
            success: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            if success:
                cursor.execute('''
                    UPDATE sequences SET
                        run_count = run_count + 1,
                        success_count = success_count + 1,
                        last_run = ?
                    WHERE id = ?
                ''', (now, sequence_id))
            else:
                cursor.execute('''
                    UPDATE sequences SET
                        run_count = run_count + 1,
                        failure_count = failure_count + 1,
                        last_run = ?
                    WHERE id = ?
                ''', (now, sequence_id))

    # ========== 실행 로그 CRUD ==========

    def create_execution_log(self, log: ExecutionLog) -> int:
        """
        실행 로그 생성

        Args:
            log: 실행 로그 객체

        Returns:
            int: 생성된 로그 ID
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO execution_logs (
                    sequence_id, sequence_name, started_at, ended_at, status,
                    error_message, ai_interventions, total_steps, completed_steps,
                    skipped_steps, failed_steps, execution_time_ms, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                log.sequence_id,
                log.sequence_name,
                log.started_at.isoformat() if log.started_at else None,
                log.ended_at.isoformat() if log.ended_at else None,
                log.status,
                log.error_message,
                log.ai_interventions,
                log.total_steps,
                log.completed_steps,
                log.skipped_steps,
                log.failed_steps,
                log.execution_time_ms,
                log.notes,
            ))

            return cursor.lastrowid

    def update_execution_log(self, log: ExecutionLog) -> bool:
        """
        실행 로그 업데이트

        Args:
            log: 업데이트할 로그 객체

        Returns:
            bool: 성공 여부
        """
        if log.id is None:
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE execution_logs SET
                    ended_at = ?,
                    status = ?,
                    error_message = ?,
                    ai_interventions = ?,
                    completed_steps = ?,
                    skipped_steps = ?,
                    failed_steps = ?,
                    execution_time_ms = ?,
                    notes = ?
                WHERE id = ?
            ''', (
                log.ended_at.isoformat() if log.ended_at else None,
                log.status,
                log.error_message,
                log.ai_interventions,
                log.completed_steps,
                log.skipped_steps,
                log.failed_steps,
                log.execution_time_ms,
                log.notes,
                log.id,
            ))

            return cursor.rowcount > 0

    def get_execution_logs(
        self,
        sequence_id: Optional[int] = None,
        limit: int = 100
    ) -> List[ExecutionLog]:
        """
        실행 로그 조회

        Args:
            sequence_id: 특정 시퀀스의 로그만 조회 (None이면 전체)
            limit: 최대 조회 수

        Returns:
            List[ExecutionLog]: 실행 로그 목록
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if sequence_id:
                cursor.execute('''
                    SELECT * FROM execution_logs
                    WHERE sequence_id = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                ''', (sequence_id, limit))
            else:
                cursor.execute('''
                    SELECT * FROM execution_logs
                    ORDER BY started_at DESC
                    LIMIT ?
                ''', (limit,))

            rows = cursor.fetchall()
            return [ExecutionLog.from_dict(dict(row)) for row in rows]

    def delete_old_logs(self, days: int = 30) -> int:
        """
        오래된 실행 로그 삭제

        Args:
            days: 보관 일수

        Returns:
            int: 삭제된 로그 수
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM execution_logs
                WHERE started_at < datetime('now', ?)
            ''', (f'-{days} days',))

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"{deleted}개의 오래된 실행 로그 삭제")
            return deleted

    # ========== 녹화 CRUD ==========

    def create_recording(self, recording: Recording) -> int:
        """
        녹화 정보 생성

        Args:
            recording: 녹화 객체

        Returns:
            int: 생성된 녹화 ID
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO recordings (
                    name, video_path, input_log_path, duration_seconds,
                    fps, resolution_width, resolution_height, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                recording.name,
                recording.video_path,
                recording.input_log_path,
                recording.duration_seconds,
                recording.fps,
                recording.resolution_width,
                recording.resolution_height,
                datetime.now().isoformat(),
            ))

            recording_id = cursor.lastrowid
            logger.info(f"녹화 저장: {recording.name} (ID: {recording_id})")
            return recording_id

    def get_recording(self, recording_id: int) -> Optional[Recording]:
        """
        녹화 정보 조회

        Args:
            recording_id: 녹화 ID

        Returns:
            Optional[Recording]: 녹화 객체 또는 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recordings WHERE id = ?', (recording_id,))
            row = cursor.fetchone()

            if row:
                return Recording.from_dict(dict(row))
            return None

    def get_all_recordings(self) -> List[Recording]:
        """
        모든 녹화 조회

        Returns:
            List[Recording]: 녹화 목록
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recordings ORDER BY created_at DESC')
            rows = cursor.fetchall()

            return [Recording.from_dict(dict(row)) for row in rows]

    def update_recording_analyzed(
        self,
        recording_id: int,
        sequence_id: int
    ) -> bool:
        """
        녹화 분석 완료 상태 업데이트

        Args:
            recording_id: 녹화 ID
            sequence_id: 생성된 시퀀스 ID

        Returns:
            bool: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE recordings SET
                    analyzed = 1,
                    sequence_id = ?
                WHERE id = ?
            ''', (sequence_id, recording_id))

            return cursor.rowcount > 0

    def update_recording_ai_analyzed(
        self,
        recording_id: int,
        automation_plan_id: str
    ) -> bool:
        """
        녹화 AI 자동화 분석 완료 상태 업데이트

        Args:
            recording_id: 녹화 ID
            automation_plan_id: 자동화 계획 ID

        Returns:
            bool: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE recordings SET
                    ai_analyzed = 1,
                    automation_plan_id = ?
                WHERE id = ?
            ''', (automation_plan_id, recording_id))

            logger.info(f"녹화 AI 분석 완료: ID {recording_id}, Plan: {automation_plan_id}")
            return cursor.rowcount > 0

    def get_recording_by_video_path(self, video_path: str) -> Optional[Recording]:
        """
        비디오 경로로 녹화 조회

        Args:
            video_path: 비디오 파일 경로

        Returns:
            Optional[Recording]: 녹화 객체 또는 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recordings WHERE video_path = ?', (video_path,))
            row = cursor.fetchone()

            if row:
                return Recording.from_dict(dict(row))
            return None

    def get_recording_by_plan_id(self, plan_id: str) -> Optional[Recording]:
        """
        자동화 계획 ID로 녹화 조회

        Args:
            plan_id: 자동화 계획 ID

        Returns:
            Optional[Recording]: 녹화 객체 또는 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recordings WHERE automation_plan_id = ?', (plan_id,))
            row = cursor.fetchone()

            if row:
                return Recording.from_dict(dict(row))
            return None

    def update_recording_name(self, recording_id: int, name: str) -> bool:
        """
        녹화 이름 업데이트

        Args:
            recording_id: 녹화 ID
            name: 새 이름

        Returns:
            bool: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE recordings SET name = ? WHERE id = ?
            ''', (name, recording_id))

            logger.info(f"녹화 이름 변경: ID {recording_id} -> {name}")
            return cursor.rowcount > 0

    def update_recording_locked(self, recording_id: int, locked: bool) -> bool:
        """
        녹화 잠금 상태 업데이트

        Args:
            recording_id: 녹화 ID
            locked: 잠금 여부

        Returns:
            bool: 성공 여부
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE recordings SET locked = ? WHERE id = ?
            ''', (1 if locked else 0, recording_id))

            logger.info(f"녹화 잠금 상태 변경: ID {recording_id} -> {'잠금' if locked else '해제'}")
            return cursor.rowcount > 0

    def delete_recording(self, recording_id: int, delete_files: bool = True) -> bool:
        """
        녹화 삭제 (연관된 시퀀스와 파일도 함께 삭제)

        Args:
            recording_id: 삭제할 녹화 ID
            delete_files: 실제 파일도 삭제할지 여부

        Returns:
            bool: 성공 여부
        """
        import os

        # 먼저 녹화 정보 조회
        recording = self.get_recording(recording_id)
        if not recording:
            return False

        # 연관된 시퀀스 삭제
        if recording.sequence_id:
            self.delete_sequence(recording.sequence_id)
            logger.info(f"연관 시퀀스 삭제: ID {recording.sequence_id}")

        # 실제 파일 삭제
        if delete_files:
            # 비디오 파일 삭제
            if recording.video_path and os.path.exists(recording.video_path):
                try:
                    os.remove(recording.video_path)
                    logger.info(f"비디오 파일 삭제: {recording.video_path}")
                except Exception as e:
                    logger.warning(f"비디오 파일 삭제 실패: {e}")

            # 입력 로그 파일 삭제
            if recording.input_log_path and os.path.exists(recording.input_log_path):
                try:
                    os.remove(recording.input_log_path)
                    logger.info(f"입력 로그 삭제: {recording.input_log_path}")
                except Exception as e:
                    logger.warning(f"입력 로그 삭제 실패: {e}")

            # 템플릿 이미지와 자동화 계획(플랜)은 삭제하지 않음
            # - 이미지: "미사용 이미지 정리" 버튼으로 별도 관리
            # - 플랜: 실행 탭에서 별도 관리

        # DB에서 녹화 삭제
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM recordings WHERE id = ?', (recording_id,))
            success = cursor.rowcount > 0

            if success:
                logger.info(f"녹화 삭제 완료: {recording.name} (ID: {recording_id})")
            return success

    # ========== 유틸리티 ==========

    def get_statistics(self) -> Dict[str, Any]:
        """
        전체 통계 조회

        Returns:
            Dict: 통계 정보
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 시퀀스 통계
            cursor.execute('SELECT COUNT(*) as count FROM sequences')
            sequence_count = cursor.fetchone()['count']

            # 실행 통계
            cursor.execute('''
                SELECT
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_runs,
                    SUM(ai_interventions) as total_ai_interventions
                FROM execution_logs
            ''')
            run_stats = cursor.fetchone()

            # 녹화 통계
            cursor.execute('SELECT COUNT(*) as count FROM recordings')
            recording_count = cursor.fetchone()['count']

            return {
                "sequence_count": sequence_count,
                "recording_count": recording_count,
                "total_runs": run_stats['total_runs'] or 0,
                "success_runs": run_stats['success_runs'] or 0,
                "success_rate": (
                    (run_stats['success_runs'] / run_stats['total_runs'] * 100)
                    if run_stats['total_runs'] else 0
                ),
                "total_ai_interventions": run_stats['total_ai_interventions'] or 0,
            }

    def vacuum(self) -> None:
        """데이터베이스 최적화"""
        with self._get_connection() as conn:
            conn.execute('VACUUM')
            logger.info("데이터베이스 최적화 완료")


# 전역 데이터베이스 관리자 인스턴스
db_manager = DatabaseManager()


def get_db() -> DatabaseManager:
    """데이터베이스 관리자 헬퍼 함수"""
    return db_manager
