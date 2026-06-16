"""Handler registration."""

from telegram.ext import Application

from .admin import register_admin_handlers
from .common import register_common_handlers


def register_handlers(application: Application) -> None:
    """Wire every handler onto the application.

    Order matters: the guided-setup ConversationHandler is registered before
    the standalone command handlers so that its states take precedence while a
    setup conversation is active.
    """
    register_admin_handlers(application)
    register_common_handlers(application)
