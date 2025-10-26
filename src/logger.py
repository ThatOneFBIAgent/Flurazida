# logger.py
import logging
import sys

# Base config for the entire bot
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Optional: Create a shortcut getter
def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for a given module/cog."""
    return logging.getLogger(name)
