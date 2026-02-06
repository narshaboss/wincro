"""
영상 분석 모듈

- video_analyzer.py: 영상 분석
- action_extractor.py: 액션 추출
- template_matcher.py: 이미지 템플릿 매칭
"""

from .template_matcher import (
    TemplateMatcher,
    template_matcher,
    get_template_matcher,
    MatchResult,
    generate_text_mask,
    save_image_with_mask,
    generate_mask_for_existing_image,
)

from .action_extractor import (
    ActionExtractor,
    action_extractor,
    get_action_extractor,
)

from .video_analyzer import (
    VideoAnalyzer,
    video_analyzer,
    get_video_analyzer,
    AnalysisProgress,
    AnalysisResult,
)

__all__ = [
    # Template Matcher
    "TemplateMatcher",
    "template_matcher",
    "get_template_matcher",
    "MatchResult",
    "generate_text_mask",
    "save_image_with_mask",
    "generate_mask_for_existing_image",
    # Action Extractor
    "ActionExtractor",
    "action_extractor",
    "get_action_extractor",
    # Video Analyzer
    "VideoAnalyzer",
    "video_analyzer",
    "get_video_analyzer",
    "AnalysisProgress",
    "AnalysisResult",
]
