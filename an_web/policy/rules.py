"""Policy rules for AN-Web — domain allow/deny, rate limiting, navigation scope."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse
import time


@dataclass
class PolicyRules:
    """
    AI action policy enforced BEFORE each action.

    Design principle: policy is not post-processing.
    All checks happen at action precondition, not aftermath.
    """

    allowed_domains: list[str] = field(default_factory=list)  # empty = allow all
    denied_domains: list[str] = field(default_factory=list)
    max_requests_per_minute: int = 120
    allow_file_download: bool = False
    allow_form_submission: bool = True
    require_approval_for: list[str] = field(default_factory=list)  # action names

    # Internal rate limiting state
    _request_timestamps: list[float] = field(default_factory=list, repr=False)

    @classmethod
    def default(cls) -> PolicyRules:
        return cls()

    @classmethod
    def strict(cls) -> PolicyRules:
        return cls(
            max_requests_per_minute=30,
            allow_file_download=False,
            require_approval_for=["submit", "navigate"],
        )

    def is_url_allowed(self, url: str) -> bool:
        if not url or url == "about:blank":
            return True
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False

        # Check deny list first
        for denied in self.denied_domains:
            if host == denied or host.endswith(f".{denied}"):
                return False

        # If allow list is specified, URL must match
        if self.allowed_domains:
            for allowed in self.allowed_domains:
                if host == allowed or host.endswith(f".{allowed}"):
                    return True
            return False

        return True

    def check_rate_limit(self) -> bool:
        now = time.time()
        cutoff = now - 60.0
        self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
        if len(self._request_timestamps) >= self.max_requests_per_minute:
            return False
        self._request_timestamps.append(now)
        return True

    def requires_approval(self, action_name: str) -> bool:
        return action_name in self.require_approval_for
