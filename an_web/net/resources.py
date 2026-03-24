"""Resource type classification and loading policy."""
from __future__ import annotations

from enum import Enum, auto


class ResourceType(Enum):
    DOCUMENT = auto()
    SCRIPT = auto()
    STYLESHEET = auto()
    IMAGE = auto()
    FONT = auto()
    XHR = auto()
    FETCH = auto()
    WEBSOCKET = auto()
    MEDIA = auto()
    OTHER = auto()

    @classmethod
    def from_content_type(cls, content_type: str) -> ResourceType:
        ct = content_type.lower()
        if "javascript" in ct or "ecmascript" in ct:
            return cls.SCRIPT
        if "css" in ct:
            return cls.STYLESHEET
        if "html" in ct:
            return cls.DOCUMENT
        if "image" in ct:
            return cls.IMAGE
        if "font" in ct or "woff" in ct:
            return cls.FONT
        return cls.OTHER

    def should_load(self, config: LoadPolicy | None = None) -> bool:
        """Decide if this resource type should be loaded."""
        if config is None:
            config = LoadPolicy()
        return self in config.allowed_types


class LoadPolicy:
    """Controls which resource types are loaded."""

    # Default: load document + scripts + XHR/fetch only (AI-relevant)
    DEFAULT_ALLOWED = {
        ResourceType.DOCUMENT,
        ResourceType.SCRIPT,
        ResourceType.XHR,
        ResourceType.FETCH,
    }

    def __init__(self, allowed_types: set[ResourceType] | None = None) -> None:
        self.allowed_types = allowed_types or self.DEFAULT_ALLOWED.copy()

    def allow_all(self) -> None:
        self.allowed_types = set(ResourceType)

    def allow_styles(self) -> None:
        self.allowed_types.add(ResourceType.STYLESHEET)
