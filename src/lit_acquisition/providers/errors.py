"""Provider error types."""

from __future__ import annotations


class ProviderConfigError(RuntimeError):
    """Permanent provider failure caused by missing configuration.

    Callers must NOT retry these; they surface as ``CONFIG_MISSING`` warnings.
    """

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"CONFIG_MISSING:{provider}:{reason}")
