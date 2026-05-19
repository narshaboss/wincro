from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from tkinter import messagebox

from ..utils.logger import get_logger


logger = get_logger(__name__)


class GameModeMapRuntime:
    """Owns map persistence and segment-switch side effects for GameModeDialog."""

    def __init__(self, owner):
        self._owner = owner

    def sanitize_segment_end_pos(self, game_map_ref, segment_idx: int):
        """Clear segment-only runtime markers before disk persistence."""
        owner = self._owner
        if game_map_ref is not None:
            owner._sanitize_segment_start_pos(game_map_ref, segment_idx)
        if game_map_ref is not None:
            owner._sanitize_segment_placeholder_target_tile(game_map_ref, segment_idx)
        if game_map_ref is not None and not owner._should_persist_segment_end(segment_idx):
            game_map_ref.end_pos = None

    def verify_saved_map_file(self, map_path: str, expected_passable: int = None) -> bool:
        """Validate the minimum structure of a saved map file."""
        try:
            if not map_path or not os.path.exists(map_path):
                return False
            if os.path.getsize(map_path) <= 2:
                return False
            with open(map_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            passable = data.get("passable", [])
            blocked = data.get("blocked", [])
            if not isinstance(passable, list) or not isinstance(blocked, list):
                return False
            if expected_passable is not None and len(passable) != int(expected_passable):
                return False
            return True
        except Exception:
            return False

    def log_map_anomaly(self, message: str):
        """Record map-load or segment-switch anomalies in logs and UI."""
        owner = self._owner
        try:
            logger.warning(f"[맵핑이상] {message}")
        except Exception:
            pass
        try:
            owner.after(0, lambda m=message: owner._append_log(f"⚠️ 맵핑이상: {m}"))
        except Exception:
            pass

    def bind_game_map_segment(self, game_map_ref, segment_idx: int):
        try:
            if game_map_ref is not None:
                setattr(game_map_ref, "_segment_idx", int(segment_idx))
        except Exception:
            pass

    def resolve_game_map_segment_idx(self, game_map_ref, fallback_idx: int) -> int:
        try:
            bound_idx = getattr(game_map_ref, "_segment_idx", None)
        except Exception:
            bound_idx = None
        if isinstance(bound_idx, int) and bound_idx >= 0:
            if bound_idx != fallback_idx:
                logger.info(f"[맵핑이상] 세그먼트 보정: {fallback_idx}->{bound_idx}")
            return bound_idx
        return fallback_idx

    def _persist_map_snapshot(
        self,
        *,
        segment_idx: int,
        game_map_ref,
        critical: bool,
        allow_during_switch: bool = False,
        emit_saved_ui_log: bool = True,
    ) -> str:
        owner = self._owner
        trace_t0 = time.time()
        segment_idx = self.resolve_game_map_segment_idx(game_map_ref, segment_idx)

        if getattr(owner, "_segment_switch_in_progress", False) and not allow_during_switch:
            logger.info("[맵핑] 전환 중 일반 맵 저장 건너뜀")
            return ""

        self.sanitize_segment_end_pos(game_map_ref, segment_idx)

        if getattr(owner, "_boss_segment_active", False):
            try:
                owner.after(0, lambda: owner._append_log("⚠️ 맵 저장 건너뜀: 보스 경유지 활성"))
            except Exception:
                pass
            return ""

        if owner._is_segment_map_locked(segment_idx):
            return ""

        if not getattr(game_map_ref, "passable", None):
            try:
                owner.after(0, lambda: owner._append_log("⚠️ 맵 저장 건너뜀: passable 비어있음"))
            except Exception:
                pass
            return ""

        acquire_deadline = time.monotonic() + (5.0 if critical else 1.5)
        acquired = False
        while time.monotonic() < acquire_deadline:
            acquired = owner._map_save_lock.acquire(timeout=0.2)
            if acquired:
                break

        if not acquired:
            try:
                owner.after(0, lambda: owner._append_log("⚠️ 맵 저장 건너뜀: 락 획득 실패"))
            except Exception:
                pass
            logger.warning(f"[맵핑] 맵 저장 락 획득 실패 ({'critical' if critical else 'normal'})")
            return ""

        if not getattr(game_map_ref, "passable", None):
            owner._map_save_lock.release()
            return ""

        try:
            seg_name = owner._get_segment_display_name(segment_idx)
            map_path = owner._get_segment_map_name(segment_idx)
            if critical:
                logger.info(
                    f"[전환추적] critical 맵 저장 시작: seg='{seg_name}' idx={segment_idx} path={map_path}"
                )
            Path(map_path).parent.mkdir(parents=True, exist_ok=True)
            if os.path.exists(map_path):
                try:
                    bak3 = map_path + ".bak3"
                    bak2 = map_path + ".bak2"
                    bak1 = map_path + ".bak1"
                    if os.path.exists(bak3):
                        os.remove(bak3)
                    if os.path.exists(bak2):
                        shutil.move(bak2, bak3)
                    if os.path.exists(bak1):
                        shutil.move(bak1, bak2)
                    shutil.copy2(map_path, bak1)
                except Exception as backup_error:
                    logger.debug(f"[맵핑] 롤링 백업 실패 (무시): {backup_error}")
            passable_count = len(game_map_ref.passable)
            game_map_ref.save(map_path)
            if critical and not self.verify_saved_map_file(map_path, expected_passable=passable_count):
                logger.warning(f"[맵핑] 저장 검증 실패 후 1회 재시도: {map_path}")
                game_map_ref.save(map_path)
                if not self.verify_saved_map_file(map_path, expected_passable=passable_count):
                    raise RuntimeError(f"맵 저장 검증 실패: {map_path}")
            logger.info(f"[맵핑] '{seg_name}' 맵 저장: {map_path}")
            if critical:
                logger.info(
                    f"[전환추적] critical 맵 저장 완료: seg='{seg_name}' idx={segment_idx} "
                    f"dt={time.time() - trace_t0:.3f}s path={map_path}"
                )
            if emit_saved_ui_log:
                try:
                    owner.after(
                        0,
                        lambda sn=seg_name, pc=passable_count, mp=map_path: owner._append_log(
                            f"💾 맵 저장: '{sn}' ({pc}타일) -> {mp}"
                        ),
                    )
                except Exception:
                    pass
            return map_path
        finally:
            owner._map_save_lock.release()

    def _load_segment_snapshot(self, *, segment_idx: int, game_map_ref, map_path: str, context_label: str):
        owner = self._owner
        seg_name = owner._get_segment_display_name(segment_idx)
        loaded_ok = game_map_ref.load(map_path)
        if not loaded_ok:
            self.log_map_anomaly(
                f"{context_label} 로드 실패: idx={segment_idx} seg='{seg_name}' path={map_path}"
            )

        loaded_start_before = tuple(game_map_ref.start_pos) if game_map_ref.start_pos is not None else None
        self.sanitize_segment_end_pos(game_map_ref, segment_idx)
        loaded_start_after = tuple(game_map_ref.start_pos) if game_map_ref.start_pos is not None else None
        connectivity_repaired = owner._repair_segment_map_connectivity_from_backups(
            game_map_ref,
            segment_idx,
            map_path,
        )

        if loaded_start_before != loaded_start_after:
            logger.warning(
                "[맵핑] '%s' start_pos 자동복구: %s -> %s",
                seg_name,
                loaded_start_before,
                loaded_start_after,
            )
        if connectivity_repaired:
            logger.warning(f"[맵핑] '{seg_name}' 연결 복구를 백업에서 적용")

        stats = game_map_ref.get_statistics()
        if stats["total_tiles"] <= 1:
            self.log_map_anomaly(
                f"{context_label} 결과 비정상: idx={segment_idx} seg='{seg_name}' "
                f"tiles={stats['total_tiles']} path={map_path}"
            )

        if loaded_start_before != loaded_start_after or connectivity_repaired:
            try:
                game_map_ref.save(map_path)
            except Exception as save_error:
                logger.error(f"[맵핑] '{seg_name}' {context_label} 자동복구 재저장 실패: {save_error}")

        return loaded_ok, stats, loaded_start_before, loaded_start_after, connectivity_repaired

    def auto_save_map(self, segment_idx: int = None, game_map_ref=None, critical: bool = False) -> str:
        """Persist the active map with lock protection and rolling backups."""
        owner = self._owner
        if segment_idx is None:
            segment_idx = getattr(owner, "_current_segment_idx", 0)
        if game_map_ref is None:
            game_map_ref = owner._game_map
        return self._persist_map_snapshot(
            segment_idx=segment_idx,
            game_map_ref=game_map_ref,
            critical=critical,
        )

    def backup_map(self):
        """Create a point-in-time backup for the current segment map."""
        owner = self._owner
        segment_idx = self.resolve_game_map_segment_idx(
            getattr(owner, "_game_map", None),
            getattr(owner, "_current_segment_idx", 0),
        )
        seg_name = owner._get_segment_display_name(segment_idx)
        map_path = owner._get_segment_map_name(segment_idx)
        backup_path = map_path.replace("_map.json", "_map.backup.json")

        if os.path.exists(map_path):
            shutil.copy2(map_path, backup_path)
            stats = owner._game_map.get_statistics()
            logger.info(f"[맵핑] '{seg_name}' 백업 저장: {backup_path} ({stats['total_tiles']}타일)")
            owner._has_map_backup = True
        else:
            owner._has_map_backup = False

    def restore_map(self):
        """Restore the current segment map from the backup snapshot."""
        owner = self._owner
        from ..player.game_map import GameMap
        from ..player.map_explorer import MapExplorer
        from ..player.simple_pathfinder import SimplePathfinder

        segment_idx = getattr(owner, "_current_segment_idx", 0)
        seg_name = owner._get_segment_display_name(segment_idx)
        map_path = owner._get_segment_map_name(segment_idx)
        backup_path = map_path.replace("_map.json", "_map.backup.json")

        if not os.path.exists(backup_path):
            messagebox.showwarning(
                "되돌리기",
                f"'{seg_name}' 맵의 백업 파일이 없습니다.\n맵핑을 아직 저장하지 않았거나 백업이 제거되었습니다.",
            )
            return

        if not messagebox.askyesno(
            "되돌리기",
            f"'{seg_name}' 맵을 백업 상태로 되돌릴까요?\n현재 맵 데이터가 백업으로 대체됩니다.",
        ):
            return

        owner._game_map = GameMap(name=getattr(owner._config, "name", None) or "autosave")
        self.bind_game_map_segment(owner._game_map, segment_idx)
        if owner._game_map.load(backup_path):
            owner._map_pathfinder = SimplePathfinder(owner._game_map)
            owner._map_explorer = MapExplorer(owner._game_map)
            owner._game_map.save(map_path)

            stats = owner._game_map.get_statistics()
            owner._update_mapping_status()
            messagebox.showinfo(
                "되돌리기",
                f"'{seg_name}' 복원 완료!\n이동가능 {stats['passable_tiles']}개\n"
                f"벽 {stats['blocked_tiles']}개\n임시벽 {stats.get('soft_blocked_tiles', 0)}개",
            )
            logger.info(f"[맵핑] '{seg_name}' 백업에서 복원: {stats['total_tiles']}타일")
        else:
            messagebox.showerror("되돌리기", "백업 파일 로드 실패")

    def switch_segment_map(self, new_segment_idx: int, skip_save: bool = False) -> bool:
        """Switch the active segment map while preserving the previous segment snapshot."""
        owner = self._owner
        from ..player.game_map import GameMap
        from ..player.map_explorer import MapExplorer
        from ..player.simple_pathfinder import SimplePathfinder

        if owner._stop_event.is_set():
            return False

        trace_t0 = time.time()
        owner._segment_switch_in_progress = True
        logger.info(
            f"[전환추적] 구간전환 시작: current_idx={getattr(owner, '_current_segment_idx', 0)} "
            f"new_idx={new_segment_idx} skip_save={skip_save}"
        )
        try:
            current_seg_idx = getattr(owner, "_current_segment_idx", 0)
            current_map_ref = getattr(owner, "_game_map", None)
            old_name = owner._get_segment_display_name(current_seg_idx)

            if not skip_save and not owner._is_segment_map_locked(current_seg_idx):
                try:
                    saved_path = self._persist_map_snapshot(
                        segment_idx=current_seg_idx,
                        game_map_ref=current_map_ref,
                        critical=True,
                        allow_during_switch=True,
                        emit_saved_ui_log=False,
                    )
                    if saved_path:
                        logger.info(
                            f"[전환추적] 구간전환 이전맵 저장: idx={current_seg_idx} "
                            f"seg='{old_name}' path={saved_path}"
                        )
                except Exception as save_error:
                    logger.error(f"[맵핑] 현재 맵 저장 실패: {save_error}")
                    logger.warning("[맵핑] 현재 구간 저장 실패 후에도 전환은 계속")
            elif skip_save or owner._is_segment_map_locked(current_seg_idx):
                logger.info("[맵핑] 전환 중 현재 구간 저장 건너뜀 (skip_save 또는 잠금)")

            new_name = owner._get_segment_display_name(new_segment_idx)
            next_map = GameMap(name=f"{getattr(owner._config, 'name', None) or 'autosave'}_{new_name}")
            self.bind_game_map_segment(next_map, new_segment_idx)

            map_path = owner._get_segment_map_name(new_segment_idx)
            if os.path.exists(map_path):
                logger.info(
                    f"[전환추적] 구간전환 맵 로드 시작: new_idx={new_segment_idx} seg='{new_name}' path={map_path}"
                )
                _, stats, _, _, _ = self._load_segment_snapshot(
                    segment_idx=new_segment_idx,
                    game_map_ref=next_map,
                    map_path=map_path,
                    context_label="구간전환",
                )
                logger.info(f"[맵핑] '{old_name}'->'{new_name}' 전환, 맵 로드: {stats['total_tiles']}타일")
                logger.info(
                    f"[전환추적] 구간전환 맵 로드 완료: old='{old_name}' new='{new_name}' "
                    f"tiles={stats['total_tiles']} dt={time.time() - trace_t0:.3f}s"
                )
            elif owner._uses_transient_local_map(new_segment_idx):
                logger.info(f"[맵핑] '{old_name}'->'{new_name}' 전환, 로컬 경유지용 휘발성 맵 사용")
                logger.info(
                    f"[전환추적] 구간전환 휘발성맵: old='{old_name}' new='{new_name}' "
                    f"dt={time.time() - trace_t0:.3f}s"
                )
            else:
                logger.info(f"[맵핑] '{old_name}'->'{new_name}' 전환, 새 맵 생성")
                logger.info(
                    f"[전환추적] 구간전환 새맵생성: old='{old_name}' new='{new_name}' "
                    f"dt={time.time() - trace_t0:.3f}s"
                )

            owner._current_segment_idx = new_segment_idx
            owner._runtime_reload_segment_idx = None
            owner._runtime_reload_cooldown_until = 0.0
            owner._mapping_segment_completion_committed_idx = None
            owner._start_registered = False
            owner._game_map = next_map
            owner._map_pathfinder = SimplePathfinder(owner._game_map)
            owner._map_explorer = MapExplorer(owner._game_map)
            return True
        finally:
            logger.info(
                f"[전환추적] 구간전환 종료: current_idx={getattr(owner, '_current_segment_idx', 0)} "
                f"dt={time.time() - trace_t0:.3f}s"
            )
            owner._segment_switch_in_progress = False

    def reload_current_segment_map_runtime(self, segment_idx: int) -> bool:
        """Reload the current segment from disk to clear stale runtime contamination."""
        owner = self._owner
        from ..player.game_map import GameMap
        from ..player.map_explorer import MapExplorer
        from ..player.simple_pathfinder import SimplePathfinder

        map_path = owner._get_segment_map_name(segment_idx)
        if not map_path or not os.path.exists(map_path):
            return False

        seg_name = owner._get_segment_display_name(segment_idx)
        try:
            fresh_map = GameMap(name=f"{getattr(owner._config, 'name', None) or 'autosave'}_{seg_name}")
            self.bind_game_map_segment(fresh_map, segment_idx)
            self._load_segment_snapshot(
                segment_idx=segment_idx,
                game_map_ref=fresh_map,
                map_path=map_path,
                context_label="런타임재로드",
            )
            owner._game_map = fresh_map
            owner._map_pathfinder = SimplePathfinder(owner._game_map)
            owner._map_explorer = MapExplorer(owner._game_map)
            owner._runtime_reload_segment_idx = segment_idx
            owner._runtime_reload_cooldown_until = time.time() + 3.0
            logger.warning(f"[맵핑] '{seg_name}' 런타임 맵 재로드")
            return True
        except Exception as reload_error:
            logger.error(f"[맵핑] '{seg_name}' 런타임 맵 재로드 실패: {reload_error}")
            return False
