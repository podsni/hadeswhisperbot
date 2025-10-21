from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware


class DependencyMiddleware(BaseMiddleware):
    """Inject static dependencies into handler context."""

    def __init__(self, **dependencies: Any) -> None:
        self._dependencies = dependencies

    async def __call__(  # type: ignore[override]
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        data.update(self._dependencies)
        return await handler(event, data)
