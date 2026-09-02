from __future__ import annotations

import os


def restrict_owner_only_fd(fd: int) -> None:
    """Apply owner-only mode where descriptor chmod is supported.

    CPython 3.12 exposes ``os.fchmod`` only on Unix. Windows access remains
    governed by the containing profile/install-root ACL, and the missing POSIX
    API must not abort a product stream reader.
    """
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(fd, 0o600)
