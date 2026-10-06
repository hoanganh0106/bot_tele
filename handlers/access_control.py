"""Global access checks applied before all customer-facing handlers."""

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from core.helpers import is_admin
from core.runtime import db


async def block_blocked_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silently stop every update sent by a customer on the access blocklist."""
    user = update.effective_user
    if not user or is_admin(user.id) or not db.is_customer_blocked(user.id):
        return

    # Close Telegram's loading spinner without revealing any bot content.
    if update.callback_query:
        await update.callback_query.answer()
    raise ApplicationHandlerStop
