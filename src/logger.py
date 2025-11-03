import logging
import inspect
import sys
import os

# Custom auto-context color formatter
class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[90m",    # gray
        logging.INFO: "\033[36m",     # cyan
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",    # red
        logging.CRITICAL: "\033[41m", # red bg
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        msecs = f"{record.msecs:06.0f}"

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
    level=logging.INFO,
    handlers=[handler],
    force=True
)

# Smart auto-context logger getter
def get_logger(name=None) -> logging.Logger:
    """
    Returns a contextual logger based on the caller's filename or module.
    If name is omitted, it automatically infers the calling file/module hierarchy.
    """
    if not name:
        # Inspect call stack to find where get_logger() was invoked
        frame = inspect.stack()[1]
        module = inspect.getmodule(frame[0])
        if module and module.__name__ != "__main__":
            name = module.__name__
        else:
            # fallback to file-based path like "discord.gateway.shard"
            path = frame.filename.replace(os.getcwd(), "").lstrip(os.sep)
            parts = path.split(os.sep)
            parts[-1] = os.path.splitext(parts[-1])[0]
            name = ".".join(parts)

    return logging.getLogger(name)
