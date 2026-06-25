from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HarnessActionResult:
    action: str
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    logs: list[str] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "requested": self.requested,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "ok": self.ok,
            "logs": self.logs,
        }
