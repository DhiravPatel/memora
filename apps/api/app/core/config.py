"""API configuration.

Thin re-export so route modules depend on ``app.core.config`` rather than reaching into
the shared package directly.
"""

from __future__ import annotations

from common.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
