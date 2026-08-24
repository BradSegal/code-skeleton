"""One stable actionable error contract shared by every public boundary."""

from __future__ import annotations


class AnatomizeError(ValueError):
    """Domain failure with a stable code, remediation, and process exit status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        remediation: str = "Inspect the input boundary and retry with a current valid artifact.",
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.exit_code = exit_code
