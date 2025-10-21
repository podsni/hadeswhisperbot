from aiogram import Router

from .commands import router as commands_router
from .media import router as media_router
from .history import router as history_router


def build_router() -> Router:
    router = Router()
    router.include_router(commands_router)
    router.include_router(history_router)
    router.include_router(media_router)
    return router


__all__ = ["build_router"]
