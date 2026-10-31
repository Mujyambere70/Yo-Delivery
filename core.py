"""core.py - small things every Yo Delivery module needs (kept apart to avoid circular imports)."""

from datetime import datetime


class ShopError(Exception):
    """An error message that can be shown to the customer."""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_dt(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
