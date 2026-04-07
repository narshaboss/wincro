from src.player.boss_state_machine import (
    APPROACH_MISS_THRESHOLD,
    APPROACH_PIXEL_MAX,
    APPROACH_TILE_MAX,
    APPROACH_VECTOR_HOLD_MISS_LIMIT,
    BossDetection,
    BossEvidenceBuffer,
    BossRuntimeState,
    CONTACT_PIXEL_MAX,
    CONTACT_TILE_MAX,
    MODE_APPROACHING,
    MODE_CHASING,
    MODE_PATROLLING,
    evaluate_approach_signal,
    evaluate_patrol_signal,
    get_detect_interval,
)


def _det(
    *,
    found=True,
    confidence=0.82,
    dx_px=96,
    dy_px=0,
    dx_tiles=2,
    dy_tiles=0,
    tile_dist=2,
    pixel_dist=96.0,
    char_found=True,
    anchor_valid=True,
    anchor_source="template",
    visual_found=True,
    ocr_fallback_used=False,
    match_source="binary",
    visual_source="visual",
):
    return BossDetection(
        found=found,
        confidence=confidence,
        dx_px=dx_px,
        dy_px=dy_px,
        dx_tiles=dx_tiles,
        dy_tiles=dy_tiles,
        tile_dist=tile_dist,
        pixel_dist=pixel_dist,
        char_found=char_found,
        anchor_valid=anchor_valid,
        anchor_source=anchor_source,
        roi_applied=True,
        match_source=match_source,
        visual_found=visual_found,
        ocr_fallback_used=ocr_fallback_used,
        visual_source=visual_source,
    )


def _chase_target(*_args):
    return (11, 19)


def test_detect_interval_uses_three_state_model():
    assert get_detect_interval(boss_mode=MODE_PATROLLING, boss_chasing=False, recent_steps=0) == 2
    assert get_detect_interval(boss_mode=MODE_CHASING, boss_chasing=True, recent_steps=0) == 1
    assert get_detect_interval(boss_mode=MODE_APPROACHING, boss_chasing=False, recent_steps=0) == 1


def test_no_detection_resets_to_patrol_and_clears_buffer():
    state = BossRuntimeState(
        mode=MODE_PATROLLING,
        buffer=BossEvidenceBuffer(hits=2, visual_hits=1, best_conf=0.81, last_seen_at=100.0),
    )

    decision = evaluate_patrol_signal(
        state,
        BossDetection(found=False),
        now=101.0,
        current_pos=(1, 19),
        chase_target_builder=_chase_target,
    )

    assert decision.event == "no_detection"
    assert decision.next_state.mode == MODE_PATROLLING
    assert decision.next_state.buffer.hits == 0
    assert decision.next_state.buffer.visual_hits == 0


def test_ocr_only_signal_stays_in_patrol_buffer():
    decision = evaluate_patrol_signal(
        BossRuntimeState(mode=MODE_PATROLLING),
        _det(
            confidence=0.91,
            tile_dist=4,
            pixel_dist=120.0,
            dx_tiles=2,
            match_source="ocr_text",
            visual_found=False,
            ocr_fallback_used=True,
            visual_source=None,
        ),
        now=101.0,
        current_pos=(3, 19),
        chase_target_builder=_chase_target,
    )

    assert decision.event == "candidate_hold"
    assert decision.next_state.mode == MODE_PATROLLING
    assert decision.next_state.chasing is False
    assert decision.next_state.buffer.hits == 1
    assert decision.next_state.buffer.visual_hits == 0


def test_screen_center_anchor_cannot_promote_to_chase():
    decision = evaluate_patrol_signal(
        BossRuntimeState(mode=MODE_PATROLLING),
        _det(
            confidence=0.88,
            tile_dist=3,
            pixel_dist=110.0,
            dx_tiles=2,
            dy_tiles=1,
            char_found=False,
            anchor_valid=True,
            anchor_source="screen_center",
        ),
        now=101.0,
        current_pos=(4, 19),
        chase_target_builder=_chase_target,
    )

    assert decision.event == "wait_char_anchor"
    assert decision.next_state.mode == MODE_PATROLLING
    assert decision.next_state.buffer.hits == 1


def test_strong_visual_signal_starts_chase():
    decision = evaluate_patrol_signal(
        BossRuntimeState(mode=MODE_PATROLLING),
        _det(
            confidence=0.83,
            tile_dist=5,
            pixel_dist=180.0,
            dx_px=140,
            dx_tiles=3,
        ),
        now=101.0,
        current_pos=(5, 19),
        chase_target_builder=_chase_target,
    )

    assert decision.event == "start_chase"
    assert decision.next_state.mode == MODE_CHASING
    assert decision.next_state.chasing is True
    assert decision.chase_target == (11, 19)


def test_buffered_visual_signal_promotes_on_second_hit():
    state = BossRuntimeState(
        mode=MODE_PATROLLING,
        buffer=BossEvidenceBuffer(
            hits=1,
            visual_hits=1,
            best_conf=0.66,
            last_seen_at=100.0,
            last_match_source="binary",
            last_visual_source="visual",
            last_tile_dist=6,
            last_dx_tiles=3,
            last_dy_tiles=0,
        ),
    )

    decision = evaluate_patrol_signal(
        state,
        _det(
            confidence=0.66,
            tile_dist=6,
            pixel_dist=220.0,
            dx_px=176,
            dx_tiles=4,
        ),
        now=101.0,
        current_pos=(5, 19),
        chase_target_builder=_chase_target,
    )

    assert decision.event == "start_chase"
    assert decision.next_state.mode == MODE_CHASING
    assert decision.next_state.chasing is True


def test_approach_range_starts_without_skill():
    decision = evaluate_patrol_signal(
        BossRuntimeState(mode=MODE_PATROLLING),
        _det(
            confidence=0.85,
            dx_px=-51,
            dy_px=-107,
            dx_tiles=-1,
            dy_tiles=-2,
            tile_dist=APPROACH_TILE_MAX,
            pixel_dist=118.6,
        ),
        now=101.0,
        current_pos=(3, 2),
        chase_target_builder=lambda *_: (2, 1),
    )

    assert decision.event == "start_approaching"
    assert decision.next_state.mode == MODE_APPROACHING
    assert decision.start_skill is False
    assert decision.next_state.contact_confirmed is False


def test_strict_contact_starts_skill_only_with_trusted_anchor():
    decision = evaluate_patrol_signal(
        BossRuntimeState(mode=MODE_PATROLLING),
        _det(
            confidence=0.86,
            dx_px=16,
            dy_px=56,
            dx_tiles=0,
            dy_tiles=1,
            tile_dist=CONTACT_TILE_MAX,
            pixel_dist=CONTACT_PIXEL_MAX - 1.0,
        ),
        now=101.0,
        current_pos=(5, 20),
        chase_target_builder=lambda *_: (5, 20),
    )

    assert decision.event == "start_approaching"
    assert decision.next_state.mode == MODE_APPROACHING
    assert decision.start_skill is True
    assert decision.next_state.contact_confirmed is True


def test_approach_keeps_direct_follow_until_contact():
    state = BossRuntimeState(mode=MODE_APPROACHING, appr_miss=0, skill_fired=False)

    decision = evaluate_approach_signal(
        state,
        detection_attempted=True,
        detection=_det(
            confidence=0.84,
            dx_px=18,
            dy_px=81,
            dx_tiles=0,
            dy_tiles=2,
            tile_dist=2,
            pixel_dist=82.0,
        ),
        now=101.0,
    )

    assert decision.event == "keep_approaching"
    assert decision.next_state.mode == MODE_APPROACHING
    assert decision.start_skill is False


def test_approach_miss_short_hold_then_rechase():
    state = BossRuntimeState(
        mode=MODE_APPROACHING,
        appr_miss=APPROACH_VECTOR_HOLD_MISS_LIMIT,
        skill_fired=False,
    )

    decision = evaluate_approach_signal(
        state,
        detection_attempted=True,
        detection=None,
        now=101.0,
    )

    assert decision.event == "rechase"
    assert decision.next_state.mode == MODE_CHASING
    assert decision.next_state.chasing is True


def test_post_skill_short_track_stays_in_approach():
    state = BossRuntimeState(
        mode=MODE_APPROACHING,
        skill_fired=True,
        contact_confirmed=True,
        last_contact_at=100.0,
    )

    decision = evaluate_approach_signal(
        state,
        detection_attempted=True,
        detection=_det(
            confidence=0.73,
            dx_px=48,
            dy_px=96,
            dx_tiles=1,
            dy_tiles=2,
            tile_dist=3,
            pixel_dist=140.0,
        ),
        now=101.0,
    )

    assert decision.event == "keep_approaching"
    assert decision.next_state.mode == MODE_APPROACHING


def test_kill_confirm_pending_requires_recent_contact_and_skill():
    state = BossRuntimeState(
        mode=MODE_APPROACHING,
        skill_fired=True,
        contact_confirmed=True,
        last_contact_at=100.0,
        appr_miss=APPROACH_MISS_THRESHOLD - 1,
    )

    decision = evaluate_approach_signal(
        state,
        detection_attempted=True,
        detection=None,
        now=101.0,
    )

    assert decision.event == "kill_confirm_pending"
    assert decision.next_state.mode == MODE_APPROACHING
