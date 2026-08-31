"""Small helpers shared by the agent and the backend."""

from focusguard_shared.utilities.logging_setup import (
    RedactingFilter,
    configure_logging,
    get_logger,
)
from focusguard_shared.utilities.paths import (
    app_data_dir,
    log_dir,
    model_cache_dir,
)
from focusguard_shared.utilities.urls import (
    coarse_path_prefix,
    minimise_url,
    normalise_domain,
)

__all__ = [
    "RedactingFilter",
    "app_data_dir",
    "coarse_path_prefix",
    "configure_logging",
    "get_logger",
    "log_dir",
    "minimise_url",
    "model_cache_dir",
    "normalise_domain",
]
