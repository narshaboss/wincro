"""Analysis package with compatibility-preserving lazy exports."""

import sys
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING


_LAZY_EXPORTS = {
    "TemplateMatcher": (".template_matcher", "TemplateMatcher"),
    "template_matcher": (".template_matcher", "template_matcher"),
    "get_template_matcher": (".template_matcher", "get_template_matcher"),
    "MatchResult": (".template_matcher", "MatchResult"),
    "generate_text_mask": (".template_matcher", "generate_text_mask"),
    "save_image_with_mask": (".template_matcher", "save_image_with_mask"),
    "generate_mask_for_existing_image": (".template_matcher", "generate_mask_for_existing_image"),
    "ActionExtractor": (".action_extractor", "ActionExtractor"),
    "action_extractor": (".action_extractor", "action_extractor"),
    "get_action_extractor": (".action_extractor", "get_action_extractor"),
    "VideoAnalyzer": (".video_analyzer", "VideoAnalyzer"),
    "video_analyzer": (".video_analyzer", "video_analyzer"),
    "get_video_analyzer": (".video_analyzer", "get_video_analyzer"),
    "AnalysisProgress": (".video_analyzer", "AnalysisProgress"),
    "AnalysisResult": (".video_analyzer", "AnalysisResult"),
}

__all__ = list(_LAZY_EXPORTS)

_COLLIDING_EXPORTS = {"template_matcher", "action_extractor", "video_analyzer"}


class _LazyExportPackage(ModuleType):
    """Keep singleton exports stable after their same-named module is imported."""

    def __getattribute__(self, name: str):
        if name in _COLLIDING_EXPORTS:
            namespace = ModuleType.__getattribute__(self, "__dict__")
            current = namespace.get(name)
            if isinstance(current, ModuleType):
                _module_name, attribute_name = namespace["_LAZY_EXPORTS"][name]
                value = getattr(current, attribute_name)
                namespace[name] = value
                return value
        return ModuleType.__getattribute__(self, name)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


sys.modules[__name__].__class__ = _LazyExportPackage


if TYPE_CHECKING:
    from .action_extractor import ActionExtractor, action_extractor, get_action_extractor
    from .template_matcher import (
        MatchResult,
        TemplateMatcher,
        generate_mask_for_existing_image,
        generate_text_mask,
        get_template_matcher,
        save_image_with_mask,
        template_matcher,
    )
    from .video_analyzer import (
        AnalysisProgress,
        AnalysisResult,
        VideoAnalyzer,
        get_video_analyzer,
        video_analyzer,
    )
