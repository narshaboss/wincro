"""
기존 plan 파일의 절대 경로를 상대 경로(파일명만)로 일괄 변환하는 스크립트

사용법:
    python convert_plans.py
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
PLANS_DIR = DATA_DIR / "plans"


def convert_path_to_filename(path):
    """절대 경로를 파일명만 추출"""
    if not path:
        return path
    return Path(path).name


def convert_rule(rule):
    """규칙 내의 모든 이미지 경로를 파일명만으로 변환"""
    # target_image
    if rule.get("target_image"):
        rule["target_image"] = convert_path_to_filename(rule["target_image"])

    # target_images (리스트)
    if rule.get("target_images"):
        rule["target_images"] = [convert_path_to_filename(p) for p in rule["target_images"] if p]

    # trigger_image
    if rule.get("trigger_image"):
        rule["trigger_image"] = convert_path_to_filename(rule["trigger_image"])

    # monitoring_final_image
    if rule.get("monitoring_final_image"):
        rule["monitoring_final_image"] = convert_path_to_filename(rule["monitoring_final_image"])

    # monitoring_watches 내의 image
    if rule.get("monitoring_watches"):
        for watch in rule["monitoring_watches"]:
            if watch.get("image"):
                watch["image"] = convert_path_to_filename(watch["image"])

    # children (재귀)
    if rule.get("children"):
        for child in rule["children"]:
            convert_rule(child)


def convert_plan_file(file_path):
    """plan 파일 변환"""
    print(f"변환 중: {file_path.name}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # initial_rules 변환
    for rule in data.get("initial_rules", []):
        convert_rule(rule)

    # monitoring_rules 변환
    for rule in data.get("monitoring_rules", []):
        convert_rule(rule)

    # 저장
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  완료!")


def main():
    if not PLANS_DIR.exists():
        print(f"plans 폴더가 없습니다: {PLANS_DIR}")
        return

    plan_files = list(PLANS_DIR.glob("*.json"))
    if not plan_files:
        print("변환할 plan 파일이 없습니다.")
        return

    print(f"총 {len(plan_files)}개 파일 변환 시작\n")

    for plan_file in plan_files:
        try:
            convert_plan_file(plan_file)
        except Exception as e:
            print(f"  오류: {e}")

    print(f"\n변환 완료!")


if __name__ == "__main__":
    main()
