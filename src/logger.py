import logging
import inspect
import sys
import os

# Define custom log levels
SUCCESS_LEVEL = 25  # Between INFO (20) and WARNING (30)
TRACE_LEVEL = 5     # Below DEBUG (10)
EVENT_LEVEL = 15    # Below INFO (20)

logging.EVENT_LEVEL = EVENT_LEVEL
logging.SUCCESS_LEVEL = SUCCESS_LEVEL
logging.TRACE_LEVEL = TRACE_LEVEL

logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")
logging.addLevelName(TRACE_LEVEL, "TRACE")
logging.addLevelName(EVENT_LEVEL, "EVENT")

# Custom auto-context color formatter
class ColoredFormatter(logging.Formatter):
    COLORS = {
        TRACE_LEVEL: "\033[90m",      # gray
        logging.DEBUG: "\033[90m",     # gray
        logging.INFO: "\033[36m",      # cyan
        EVENT_LEVEL: "\033[96m",      # light blue
        SUCCESS_LEVEL: "\033[32m",  # green text
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[41;97m",  # red bg with white text
    } # you CAN have other logs with bgs, but it's only recommended for critical errors since it's eye-catching
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        msecs = f"{record.msecs:03.0f}"

        # Auto-truncate file path to relative module hierarchy
        module_path = record.pathname.replace(os.getcwd(), "").lstrip(os.sep)
        module_parts = module_path.split(os.sep)

        # Collapse to something like "cogs.music.queue"
        if len(module_parts) > 1:
            module_parts[-1] = os.path.splitext(module_parts[-1])[0]
        module_name = ".".join(part for part in module_parts if part and part != "__init__")

        formatted = f"[{msecs}ms] [{record.levelname:^8}] [{module_name}] {record.getMessage()}"
        return f"{color}{formatted}{self.RESET}"

# Base logger setup
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())

logging.basicConfig(
    level=EVENT_LEVEL,
    handlers=[handler],
    force=True
)

# Smart auto-context logger getter
def get_logger(name=None) -> logging.Logger:
    """
    Returns a contextual logger based on the caller's filename or module.
    If name is omitted, it automatically infers the calling file/module hierarchy.
    Ergo: get_logger() from src/CloudflarePing.py yields a logger named "src.CloudflarePing"
    """
    if not name:
        # Inspect call stack to find where get_logger() was invoked
        frame = inspect.stack()[1]
        module = inspect.getmodule(frame[0])
        if module and hasattr(module, '__name__') and module.__name__ not in ("__main__",):
            name = module.__name__
        else:
            # fallback to file-based path like "discord.gateway.shard"
            path = frame.filename.replace(os.getcwd(), "").lstrip(os.sep)
            parts = path.split(os.sep)
            parts[-1] = os.path.splitext(parts[-1])[0]
            name = ".".join(parts)

    return logging.getLogger(name)

# Add convenience methods to Logger class
def success(self, message, *args, **kwargs):
    """Log a success message with green background."""
    if self.isEnabledFor(SUCCESS_LEVEL):
        self._log(SUCCESS_LEVEL, message, args, **kwargs)

def trace(self, message, *args, **kwargs):
    """Log a trace message (even more verbose than debug)."""
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kwargs)

def event(self, message, *args, **kwargs):
    """Log an event message (even more verbose than debug)."""
    if self.isEnabledFor(EVENT_LEVEL):
        self._log(EVENT_LEVEL, message, args, **kwargs)

# Attach custom methods to Logger class
logging.Logger.success = success
logging.Logger.trace = trace
logging.Logger.event = event
