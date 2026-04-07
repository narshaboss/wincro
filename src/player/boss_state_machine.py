from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable


MODE_EXPLORING = "exploring"
MODE_PATROLLING = "patrolling"
MODE_CANDIDATE_HOLD = "candidate_hold"  # compatibility only; no longer used as runtime mode
MODE_CHASING = "chasing"
MODE_APPROACHING = "approaching"
MODE_KILL_CONFIRM_PENDING = "kill_confirm_pending"  # compatibility/event marker
MODE_LOOT_PENDING = "loot_pending"
MODE_COMPLETED = "completed"

NONCOMBAT_MODES = {
    MODE_EXPLORING,
    MODE_PATROLLING,
    MODE_CHASING,
}

APPROACH_TILE_MAX = 2
APPROACH_PIXEL_MAX = 128
CONTACT_TILE_MAX = 1
CONTACT_PIXEL_MAX = 64
APPROACH_VECTOR_HOLD_MISS_LIMIT = 2
POST_SKILL_TRACK_TILE_MAX = 3
POST_SKILL_TRACK_PIXEL_MAX = 160.0
CHASE_STICKY_TILE_MAX = 8
CHASE_STICKY_PIXEL_MAX = 320.0
CHASE_STICKY_CONF_MIN = 0.65
VISUAL_CHASE_CONF_MIN = 0.70
PATROL_BUFFER_PROMOTE_HITS = 2
PATROL_BUFFER_TIMEOUT_S = 1.5
CHASE_MISS_THRESHOLD = 8
KILL_CONFIRM_CONTACT_TTL_S = 6.0
APPROACH_MISS_THRESHOLD = 10
TRUSTED_CONTACT_ANCHOR_SOURCES = {"template", "recent_char"}


@dataclass(frozen=True)
class BossDetection:
    found: bool = False
    confidence: float = 0.0
    dx_px: int = 0
    dy_px: int = 0
    dx_tiles: int = 0
    dy_tiles: int = 0
    tile_dist: int = 0
    pixel_dist: float = 0.0
    char_found: bool = False
    anchor_valid: bool = False
    anchor_source: str | None = None
    roi_applied: bool = False
    match_source: str | None = None
    visual_found: bool = False
    ocr_fallback_used: bool = False
    visual_source: str | None = None


@dataclass(frozen=True)
class BossEvidenceBuffer:
    hits: int = 0
    visual_hits: int = 0
    best_conf: float = 0.0
    last_seen_at: float = 0.0
    last_match_source: str | None = None
    last_visual_source: str | None = None
    last_tile_dist: int = 0
    last_dx_tiles: int = 0
    last_dy_tiles: int = 0


BossCandidateState = BossEvidenceBuffer


@dataclass(frozen=True)
class BossTrackerState:
    mode: str = MODE_EXPLORING
    chasing: bool = False
    chase_miss: int = 0
    appr_miss: int = 0
    steps: int = 0
    item_force_loot: bool = False
    contact_confirmed: bool = False
    skill_fired: bool = False
    last_contact_at: float = 0.0
    last_skill_at: float = 0.0
    buffer: BossEvidenceBuffer = field(default_factory=BossEvidenceBuffer)
    noise_block_signature: tuple[int, int, int] | None = None
    noise_block_until: float = 0.0


BossRuntimeState = BossTrackerState


@dataclass(frozen=True)
class BossDecision:
    next_state: BossTrackerState
    event: str
    boss_visible: bool = False
    start_skill: bool = False
    clear_detect_hints: bool = False
    arm_noise_cooldown: bool = False
    noise_signature: tuple[int, int, int] | None = None
    noise_cooldown_seconds: float = 0.0
    chase_target: tuple[int, int] | None = None


def make_noise_signature(dx_px, dy_px, tile_dist) -> tuple[int, int, int] | None:
    try:
        dx_i = int(dx_px or 0)
        dy_i = int(dy_px or 0)
        td_i = int(tile_dist or 0)
    except Exception:
        return None
    return (
        max(-12, min(12, int(round(dx_i / 96.0)))),
        max(-12, min(12, int(round(dy_i / 96.0)))),
        min(24, max(0, td_i)),
    )


def has_trusted_contact_anchor(detection: BossDetection) -> bool:
    if bool(detection.char_found):
        return True
    return bool(detection.anchor_valid) and str(detection.anchor_source or "") in TRUSTED_CONTACT_ANCHOR_SOURCES


def get_detect_interval(*, boss_mode: str, boss_chasing: bool, recent_steps: int) -> int:
    if boss_mode == MODE_APPROACHING or boss_chasing:
        return 1
    return 2


def clip_chase_delta(delta_tiles, step_limit: int) -> int:
    try:
        delta = int(delta_tiles or 0)
    except Exception:
        return 0
    if delta == 0:
        return 0
    sign = 1 if delta > 0 else -1
    return sign * min(abs(delta), max(1, int(step_limit or 1)))


def build_incremental_chase_target(
    current_x: int,
    current_y: int,
    dx_tiles,
    dy_tiles,
    tile_dist,
    *,
    is_passable: Callable[[int, int], bool],
    start_pos: tuple[int, int] | None,
) -> tuple[int, int]:
    try:
        dx = int(dx_tiles or 0)
        dy = int(dy_tiles or 0)
    except Exception:
        return current_x, current_y

    step_limit = 1 if int(tile_dist or 0) <= 2 else 2
    step_dx = clip_chase_delta(dx, step_limit)
    step_dy = clip_chase_delta(dy, step_limit)
    if step_dx == 0 and step_dy == 0:
        if abs(dx) >= abs(dy) and dx != 0:
            step_dx = 1 if dx > 0 else -1
        elif dy != 0:
            step_dy = 1 if dy > 0 else -1
        else:
            return current_x, current_y

    target_x = current_x + step_dx
    target_y = current_y + step_dy
    if is_passable(target_x, target_y) and (start_pos is None or (target_x, target_y) != start_pos):
        return target_x, target_y

    prefer_offsets = [
        (0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
        (1, 1), (-1, 1), (1, -1), (-1, -1),
    ]
    for radius in range(1, 3):
        for off_x, off_y in prefer_offsets:
            cand_x = target_x + (off_x * radius)
            cand_y = target_y + (off_y * radius)
            if (cand_x, cand_y) == (current_x, current_y):
                continue
            if not is_passable(cand_x, cand_y):
                continue
            if start_pos is not None and (cand_x, cand_y) == start_pos:
                continue
            return cand_x, cand_y
    return current_x, current_y


def normalize_state(state: BossTrackerState) -> BossTrackerState:
    if state.mode in NONCOMBAT_MODES:
        return replace(
            state,
            item_force_loot=False,
            contact_confirmed=False,
            skill_fired=False,
            last_contact_at=0.0,
            last_skill_at=0.0,
        )
    return state


def _clear_buffer(state: BossTrackerState) -> BossTrackerState:
    return replace(state, buffer=BossEvidenceBuffer())


def _update_buffer(state: BossTrackerState, detection: BossDetection, *, now: float) -> BossTrackerState:
    prev = state.buffer
    timed_out = prev.last_seen_at > 0.0 and (now - float(prev.last_seen_at)) > PATROL_BUFFER_TIMEOUT_S
    if timed_out:
        prev = BossEvidenceBuffer()

    visual_hit = bool(detection.visual_found)
    new_buffer = BossEvidenceBuffer(
        hits=int(prev.hits or 0) + 1,
        visual_hits=(int(prev.visual_hits or 0) + 1) if visual_hit else 0,
        best_conf=max(float(prev.best_conf or 0.0), float(detection.confidence or 0.0)),
        last_seen_at=float(now),
        last_match_source=str(detection.match_source or ""),
        last_visual_source=str(detection.visual_source or ""),
        last_tile_dist=int(detection.tile_dist or 0),
        last_dx_tiles=int(detection.dx_tiles or 0),
        last_dy_tiles=int(detection.dy_tiles or 0),
    )
    return replace(state, buffer=new_buffer)


def _reset_to_patrol(
    state: BossTrackerState,
    *,
    keep_buffer: bool = False,
) -> BossTrackerState:
    next_state = replace(
        state,
        mode=MODE_PATROLLING,
        chasing=False,
        chase_miss=0,
        appr_miss=0,
        steps=0,
        item_force_loot=False,
        contact_confirmed=False,
        skill_fired=False,
        last_contact_at=0.0,
        last_skill_at=0.0,
        buffer=state.buffer if keep_buffer else BossEvidenceBuffer(),
    )
    return normalize_state(next_state)


def _reset_to_chase(state: BossTrackerState) -> BossTrackerState:
    return normalize_state(
        replace(
            state,
            mode=MODE_CHASING,
            chasing=True,
            chase_miss=0,
            appr_miss=0,
            steps=state.steps,
            buffer=BossEvidenceBuffer(),
            item_force_loot=False,
            contact_confirmed=False,
            skill_fired=False,
            last_contact_at=0.0,
            last_skill_at=0.0,
        )
    )


def _start_approach_state(state: BossTrackerState, *, now: float, contact_confirmed: bool) -> BossTrackerState:
    return normalize_state(
        replace(
            state,
            mode=MODE_APPROACHING,
            chasing=False,
            chase_miss=0,
            appr_miss=0,
            steps=state.steps,
            buffer=BossEvidenceBuffer(),
            contact_confirmed=bool(contact_confirmed),
            skill_fired=False,
            last_contact_at=now if contact_confirmed else 0.0,
            last_skill_at=0.0,
        )
    )


def _build_start_chase_decision(
    state: BossTrackerState,
    detection: BossDetection,
    *,
    current_pos: tuple[int, int],
    chase_target_builder: Callable[[int, int, int, int, int], tuple[int, int]],
) -> BossDecision:
    chase_target = chase_target_builder(
        current_pos[0],
        current_pos[1],
        detection.dx_tiles,
        detection.dy_tiles,
        detection.tile_dist,
    )
    next_state = _reset_to_chase(state)
    return BossDecision(
        next_state=next_state,
        event="start_chase",
        boss_visible=True,
        chase_target=chase_target,
    )


def evaluate_boss_frame(
    state: BossTrackerState,
    detection: BossDetection,
    *,
    now: float,
    current_pos: tuple[int, int],
    chase_target_builder: Callable[[int, int, int, int, int], tuple[int, int]],
) -> BossDecision:
    state = normalize_state(state)
    if state.mode == MODE_APPROACHING:
        return evaluate_approach_signal(state, detection_attempted=True, detection=detection, now=now)

    if not detection.found:
        return BossDecision(next_state=_reset_to_patrol(state, keep_buffer=False), event="no_detection")

    trusted_anchor = has_trusted_contact_anchor(detection)
    signature = make_noise_signature(detection.dx_px, detection.dy_px, detection.tile_dist)
    if (
        state.noise_block_signature is not None
        and signature == state.noise_block_signature
        and now < float(state.noise_block_until or 0.0)
    ):
        return BossDecision(next_state=state, event="noise_cooldown")

    if detection.tile_dist > 20:
        next_state = replace(
            _reset_to_patrol(state, keep_buffer=False),
            noise_block_signature=signature,
            noise_block_until=now + 3.0,
        )
        return BossDecision(
            next_state=next_state,
            event="ui_noise",
            clear_detect_hints=True,
            arm_noise_cooldown=True,
            noise_signature=signature,
            noise_cooldown_seconds=3.0,
        )

    if detection.ocr_fallback_used and not detection.visual_found:
        next_state = _update_buffer(_reset_to_patrol(state, keep_buffer=False), detection, now=now)
        return BossDecision(next_state=next_state, event="candidate_hold")

    if not trusted_anchor:
        next_state = _update_buffer(_reset_to_patrol(state, keep_buffer=False), detection, now=now)
        return BossDecision(next_state=next_state, event="wait_char_anchor")

    if detection.tile_dist <= CONTACT_TILE_MAX and detection.pixel_dist <= CONTACT_PIXEL_MAX:
        return BossDecision(
            next_state=_start_approach_state(state, now=now, contact_confirmed=True),
            event="start_approaching",
            boss_visible=True,
            start_skill=True,
        )

    if detection.tile_dist <= APPROACH_TILE_MAX and detection.pixel_dist <= APPROACH_PIXEL_MAX:
        return BossDecision(
            next_state=_start_approach_state(state, now=now, contact_confirmed=False),
            event="start_approaching",
            boss_visible=True,
            start_skill=False,
        )

    if (
        state.chasing
        and trusted_anchor
        and detection.tile_dist <= CHASE_STICKY_TILE_MAX
        and detection.pixel_dist <= CHASE_STICKY_PIXEL_MAX
        and float(detection.confidence or 0.0) >= CHASE_STICKY_CONF_MIN
    ):
        return _build_start_chase_decision(
            state,
            detection,
            current_pos=current_pos,
            chase_target_builder=chase_target_builder,
        )

    buffered_state = _update_buffer(_reset_to_patrol(state, keep_buffer=True), detection, now=now)
    visual_hits = int(buffered_state.buffer.visual_hits or 0)
    strong_visual = bool(
        detection.visual_found
        and float(detection.confidence or 0.0) >= VISUAL_CHASE_CONF_MIN
    )
    if strong_visual or visual_hits >= PATROL_BUFFER_PROMOTE_HITS:
        return _build_start_chase_decision(
            buffered_state,
            detection,
            current_pos=current_pos,
            chase_target_builder=chase_target_builder,
        )

    return BossDecision(next_state=buffered_state, event="candidate_hold")


def evaluate_patrol_signal(
    state: BossTrackerState,
    detection: BossDetection,
    *,
    now: float,
    current_pos: tuple[int, int],
    chase_target_builder: Callable[[int, int, int, int, int], tuple[int, int]],
) -> BossDecision:
    return evaluate_boss_frame(
        state,
        detection,
        now=now,
        current_pos=current_pos,
        chase_target_builder=chase_target_builder,
    )


def evaluate_approach_signal(
    state: BossTrackerState,
    *,
    detection_attempted: bool,
    detection: BossDetection | None,
    now: float,
) -> BossDecision:
    state = normalize_state(state)
    if detection is not None and detection.found:
        trusted_anchor = has_trusted_contact_anchor(detection)
        strict_contact = bool(
            trusted_anchor
            and detection.tile_dist <= CONTACT_TILE_MAX
            and detection.pixel_dist <= CONTACT_PIXEL_MAX
        )
        if strict_contact:
            next_state = replace(
                state,
                mode=MODE_APPROACHING,
                chasing=False,
                appr_miss=0,
                contact_confirmed=True,
                last_contact_at=now,
            )
            return BossDecision(
                next_state=next_state,
                event="keep_approaching",
                boss_visible=True,
                start_skill=not state.skill_fired,
            )

        if trusted_anchor and (
            detection.tile_dist <= APPROACH_TILE_MAX
            and detection.pixel_dist <= APPROACH_PIXEL_MAX
        ):
            next_state = replace(
                state,
                mode=MODE_APPROACHING,
                chasing=False,
                appr_miss=0,
                contact_confirmed=False,
            )
            return BossDecision(next_state=next_state, event="keep_approaching", boss_visible=True, start_skill=False)

        if (
            state.skill_fired
            and trusted_anchor
            and detection.tile_dist <= POST_SKILL_TRACK_TILE_MAX
            and detection.pixel_dist <= POST_SKILL_TRACK_PIXEL_MAX
        ):
            next_state = replace(
                state,
                mode=MODE_APPROACHING,
                chasing=False,
                appr_miss=0,
            )
            return BossDecision(next_state=next_state, event="keep_approaching", boss_visible=True, start_skill=False)

        if trusted_anchor:
            return BossDecision(next_state=_reset_to_chase(state), event="rechase", boss_visible=True)

    next_miss = int(state.appr_miss or 0)
    if detection_attempted:
        next_miss += 1
    next_state = replace(state, appr_miss=next_miss)

    if not state.skill_fired:
        if next_miss <= APPROACH_VECTOR_HOLD_MISS_LIMIT:
            return BossDecision(next_state=next_state, event="approach_miss")
        return BossDecision(next_state=_reset_to_chase(state), event="rechase")

    if next_miss < APPROACH_MISS_THRESHOLD:
        return BossDecision(next_state=next_state, event="approach_miss")

    kill_ready = bool(
        float(state.last_contact_at or 0.0) > 0.0
        and ((now - float(state.last_contact_at or 0.0)) <= KILL_CONFIRM_CONTACT_TTL_S)
    )
    if kill_ready:
        return BossDecision(next_state=replace(next_state, chasing=False), event="kill_confirm_pending")

    return BossDecision(next_state=_reset_to_patrol(state, keep_buffer=False), event="kill_hold_reset")
