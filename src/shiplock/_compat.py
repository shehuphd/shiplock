"""Python-version compatibility imports, defined once.

``tomllib`` is stdlib from 3.11; the ``tomli`` backport covers 3.10 alone.
Every module that parses TOML imports it from here, so the version guard has
one home instead of a copy per consumer.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

__all__ = ["tomllib"]
