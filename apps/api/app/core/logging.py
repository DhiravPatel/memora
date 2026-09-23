"""Logging setup for the API process."""

from __future__ import annotations

from common.logging import configure_logging, get_logger, project_id_var, request_id_var
from common.settings import get_settings


def setup_logging() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)


__all__ = ["get_logger", "project_id_var", "request_id_var", "setup_logging"]
