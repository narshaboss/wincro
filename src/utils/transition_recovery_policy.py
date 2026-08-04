"""Shared policy rules for bounded screen-transition recovery.

The editor preview and RuleExecutor must use the same eligibility decision.
Keeping this logic here prevents a plan from looking protected in the UI while
the runtime silently follows a different rule set.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


TRANSITION_POLICY_AUTO = "auto"
TRANSITION_POLICY_FORCE_ON = "force_on"
TRANSITION_POLICY_FORCE_OFF = "force_off"
TRANSITION_POLICIES = {
    TRANSITION_POLICY_AUTO,
    TRANSITION_POLICY_FORCE_ON,
    TRANSITION_POLICY_FORCE_OFF,
}

# Auto mode is intentionally narrower than the manual recovery feature. These
# actions produce a bounded input that is safe to retry after a failed screen
# transition check.
AUTO_TRANSITION_SOURCE_TYPES = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "hotkey",
        "key_press",
        "type",
        "random_key_sequence",
    }
)


@dataclass(frozen=True)
class TransitionRecoveryEligibility:
    eligible: bool
    reason_code: str
    reason: str
    verification_source: str = ""


def normalize_transition_recovery_policy(value, legacy_enabled: bool = False) -> str:
    """Normalize policy while preserving plans saved before the policy field."""
    normalized = str(value or "").strip().lower()
    if normalized in TRANSITION_POLICIES:
        return normalized
    if bool(legacy_enabled):
        return TRANSITION_POLICY_FORCE_ON
    return TRANSITION_POLICY_AUTO


def transition_recovery_policy_for(item) -> str:
    return normalize_transition_recovery_policy(
        getattr(item, "transition_recovery_policy", ""),
        bool(getattr(item, "transition_recovery_enabled", False)),
    )


def transition_target_images(item) -> List[str]:
    """Return unique target images from either Action or AutomationRule."""
    images: List[str] = []
    seen = set()
    raw_images = [getattr(item, "target_image", None)]
    raw_images.extend(list(getattr(item, "target_images", None) or []))
    for image in raw_images:
        text = str(image or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        images.append(text)
    return images


def is_monitoring_transition_item(item) -> bool:
    return bool(
        getattr(item, "is_monitoring_mode", False)
        or list(getattr(item, "monitoring_watches", None) or [])
    )


def transition_recovery_structural_exclusion(
    source,
    ancestor_exclusion: Optional[TransitionRecoveryEligibility] = None,
) -> Optional[TransitionRecoveryEligibility]:
    """Return contexts where the normal RuleExecutor recovery path cannot run."""
    if ancestor_exclusion is not None:
        return ancestor_exclusion
    if is_monitoring_transition_item(source):
        return TransitionRecoveryEligibility(
            False,
            "source_monitoring",
            "모니터링 액션",
        )
    if str(getattr(source, "action_type", "") or "") == "auto_list":
        return TransitionRecoveryEligibility(
            False,
            "source_auto_list",
            "자동 목록 처리 액션",
        )
    if bool(getattr(source, "click_until_image_disappears", False)):
        return TransitionRecoveryEligibility(
            False,
            "source_until_disappears",
            "사라질 때까지 반복 액션",
        )
    if bool(getattr(source, "repeat_from_auto_list_quantity", False)):
        return TransitionRecoveryEligibility(
            False,
            "source_auto_list_repeat",
            "자동 목록 수량 반복 액션",
        )
    return None


def _descendant_transition_recovery_exclusion(
    item,
) -> Optional[TransitionRecoveryEligibility]:
    if is_monitoring_transition_item(item):
        return TransitionRecoveryEligibility(
            False,
            "ancestor_monitoring",
            "모니터링 내부 액션",
        )
    if str(getattr(item, "action_type", "") or "") == "auto_list":
        return TransitionRecoveryEligibility(
            False,
            "ancestor_auto_list",
            "자동 목록 처리 내부 액션",
        )
    if bool(getattr(item, "click_until_image_disappears", False)):
        return TransitionRecoveryEligibility(
            False,
            "ancestor_until_disappears",
            "사라질 때까지 반복 내부 액션",
        )
    return None


def build_transition_recovery_context_exclusions(items: Iterable) -> dict:
    """Map object identity to structural exclusions inherited from parent actions."""
    exclusions = {}

    def visit(children, inherited_exclusion=None) -> None:
        for item in children or []:
            if not bool(getattr(item, "enabled", True)):
                continue
            if inherited_exclusion is not None:
                exclusions[id(item)] = inherited_exclusion
            child_exclusion = inherited_exclusion or _descendant_transition_recovery_exclusion(
                item
            )
            visit(getattr(item, "children", None) or [], child_exclusion)

    visit(items)
    return exclusions


def evaluate_transition_verification_target(
    next_item,
    next_target_images: Optional[Sequence[str]] = None,
) -> TransitionRecoveryEligibility:
    """Validate that presence of the next image really means transition success."""
    if next_item is None:
        return TransitionRecoveryEligibility(False, "next_missing", "다음 실행 액션 없음")
    if not bool(getattr(next_item, "enabled", True)):
        return TransitionRecoveryEligibility(False, "next_disabled", "다음 액션 비활성")
    if is_monitoring_transition_item(next_item):
        return TransitionRecoveryEligibility(
            False,
            "next_monitoring",
            "다음 액션이 모니터링 액션",
        )
    if bool(getattr(next_item, "skip_on_not_found", False)):
        return TransitionRecoveryEligibility(
            False,
            "next_optional",
            "다음 액션이 미감지 건너뛰기",
        )
    if bool(getattr(next_item, "click_until_image_disappears", False)):
        return TransitionRecoveryEligibility(
            False,
            "next_absence_success",
            "다음 액션은 이미지가 없어도 정상 상태",
        )

    images = [
        str(image or "").strip()
        for image in (
            next_target_images
            if next_target_images is not None
            else transition_target_images(next_item)
        )
        if str(image or "").strip()
    ]
    if not images:
        return TransitionRecoveryEligibility(
            False,
            "next_image_missing",
            "다음 액션에 확인 이미지 없음",
        )
    return TransitionRecoveryEligibility(
        True,
        "next_image",
        "다음 액션 이미지로 화면 전환 확인",
        images[0],
    )


def evaluate_auto_transition_recovery(
    source,
    next_item=None,
    next_target_images: Optional[Sequence[str]] = None,
    context_exclusion: Optional[TransitionRecoveryEligibility] = None,
) -> TransitionRecoveryEligibility:
    """Decide whether auto policy may safely protect one source action."""
    if not bool(getattr(source, "enabled", True)):
        return TransitionRecoveryEligibility(False, "source_disabled", "비활성 액션")

    structural_exclusion = transition_recovery_structural_exclusion(
        source,
        context_exclusion,
    )
    if structural_exclusion is not None:
        return structural_exclusion

    action_type = str(getattr(source, "action_type", "") or "").strip()
    if action_type not in AUTO_TRANSITION_SOURCE_TYPES:
        return TransitionRecoveryEligibility(
            False,
            "source_type",
            "자동 복구 대상이 아닌 액션 유형",
        )
    if bool(getattr(source, "skip_on_not_found", False)):
        return TransitionRecoveryEligibility(
            False,
            "source_optional",
            "이미지 미감지 시 건너뛰는 선택 액션",
        )

    verify_mode = str(
        getattr(source, "transition_verify_mode", "next_action") or "next_action"
    ).strip()
    if verify_mode == "custom_image":
        custom_image = str(
            getattr(source, "transition_verify_image", "") or ""
        ).strip()
        if not custom_image:
            return TransitionRecoveryEligibility(
                False,
                "custom_image_missing",
                "직접 확인 이미지가 설정되지 않음",
            )
        return TransitionRecoveryEligibility(
            True,
            "custom_image",
            "직접 확인 이미지 사용",
            custom_image,
        )

    return evaluate_transition_verification_target(
        next_item,
        next_target_images,
    )


def effective_transition_recovery_enabled(
    source,
    *,
    plan_auto_enabled: bool,
    next_item=None,
    next_target_images: Optional[Sequence[str]] = None,
    context_exclusion: Optional[TransitionRecoveryEligibility] = None,
) -> bool:
    if transition_recovery_structural_exclusion(source, context_exclusion) is not None:
        return False
    policy = transition_recovery_policy_for(source)
    if policy == TRANSITION_POLICY_FORCE_OFF:
        return False
    if policy == TRANSITION_POLICY_FORCE_ON:
        return True
    if not bool(plan_auto_enabled):
        return False
    return evaluate_auto_transition_recovery(
        source,
        next_item,
        next_target_images,
        context_exclusion,
    ).eligible


def flatten_enabled_transition_items(
    items: Iterable,
    parent_step: str = "",
) -> List[Tuple[object, str]]:
    """Flatten the enabled tree using the same parent-disable semantics as runtime."""
    flattened: List[Tuple[object, str]] = []
    visible_index = 0
    for item in items or []:
        if not bool(getattr(item, "enabled", True)):
            continue
        visible_index += 1
        step = f"{parent_step}-{visible_index}" if parent_step else str(visible_index)
        flattened.append((item, step))
        flattened.extend(
            flatten_enabled_transition_items(
                getattr(item, "children", None) or [],
                step,
            )
        )
    return flattened
