"""Shiplock: deterministic docs-vs-code release checks.

The public surface is small on purpose. Most callers want either the CLI
(``shiplock check`` / ``shiplock prompt``) or the two entry points re-exported
here: load a repo's config, then run the checks over it.
"""

from shiplock._config import Config, ConfigError, load_config
from shiplock._report import Finding, Notice, Report
from shiplock._checks import run_checks

__version__ = "0.0.1"

__all__ = [
    "Config",
    "ConfigError",
    "Finding",
    "Notice",
    "Report",
    "load_config",
    "run_checks",
    "__version__",
]
