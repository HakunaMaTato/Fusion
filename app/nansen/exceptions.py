from typing import Any


class NansenError(Exception):
    pass


class NansenAPIError(NansenError):
    def __init__(
        self,
        status: int,
        message: str,
        code: str | None = None,
        request_id: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Nansen API error {status} ({code}): {message}")
        self.status = status
        self.message = message
        self.code = code
        self.request_id = request_id
        self.body = body


class BudgetExceeded(NansenError):
    def __init__(self, used: int, budget: int) -> None:
        super().__init__(f"Daily credit budget exceeded: {used} used of {budget}")
        self.used = used
        self.budget = budget


class MissingFixture(NansenError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Replay mode: no fixture recorded at {path}")
        self.path = path
