"""Shared mail client — public API lives in client.py."""

from .client import MailClient, MailConfig

__all__ = ["MailClient", "MailConfig"]
