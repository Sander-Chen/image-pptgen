"""PPTGen's thin, HTTP-only command-line control plane."""

from .client import PlatformError, PlatformUnavailable, PptgenClient

__all__ = ["PlatformError", "PlatformUnavailable", "PptgenClient"]

