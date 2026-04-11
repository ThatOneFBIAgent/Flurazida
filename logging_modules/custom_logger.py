# logging_modules/custom_logger.py
# Optimized logger for Flurazide with environment-aware adaptive coloring
import logging
import inspect
import sys
import os

# Custom Log Levels
SUCCESS_LEVEL = 25
EVENT_LEVEL = 19
SUCCESSTRACE_LEVEL = 14
WARNINGTRACE_LEVEL = 13
TRACE_LEVEL = 12
DATABASE_LEVEL = 17
NETWORK_LEVEL = 18

# Initialize Levels
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")
logging.addLevelName(TRACE_LEVEL, "TRACE")
logging.addLevelName(EVENT_LEVEL, "EVENT")
logging.addLevelName(DATABASE_LEVEL, "DATABASE")
logging.addLevelName(SUCCESSTRACE_LEVEL, "S-TRACE")
logging.addLevelName(WARNINGTRACE_LEVEL, "W-TRACE")
logging.addLevelName(NETWORK_LEVEL, "NETWORK")

# Inject Level Constants into logging module
logging.SUCCESS_LEVEL = SUCCESS_LEVEL
logging.EVENT_LEVEL = EVENT_LEVEL
logging.SUCCESSTRACE_LEVEL = SUCCESSTRACE_LEVEL
logging.WARNINGTRACE_LEVEL = WARNINGTRACE_LEVEL
logging.TRACE_LEVEL = TRACE_LEVEL
logging.DATABASE_LEVEL = DATABASE_LEVEL
logging.NETWORK_LEVEL = NETWORK_LEVEL

# Environment Detection
IS_RAILWAY = os.environ.get("RAILWAY_PROJECT_ID") is not None
# We use ALPHA from extraconfig if available, otherwise default to False
try:
    from extraconfig import ALPHA as IS_ALPHA
except ImportError:
    IS_ALPHA = False

class AdaptiveFormatter(logging.Formatter):
    """
    Formatter that switches between rich ANSI colors (Alpha/Local)
    and basic console coloring/plain text (Prod/Host).
    """
    
    # Rich Colors (Alpha/Local PC)
    RICH_COLORS = {
        TRACE_LEVEL: "\033[90m",                    # gray
        logging.DEBUG: "\033[90m",                  # gray
        WARNINGTRACE_LEVEL: "\033[33m",             # yellow
        SUCCESSTRACE_LEVEL: "\033[32m",             # green
        logging.INFO: "\033[36m",                   # cyan
        DATABASE_LEVEL: "\033[35m",                 # purple
        EVENT_LEVEL: "\033[96m",                    # light blue
        NETWORK_LEVEL: "\033[0;38;2;41;28;255;49m", # dark blue
        SUCCESS_LEVEL: "\033[32m",                  # green
        logging.WARNING: "\033[33m",                # yellow
        logging.ERROR: "\033[31m",                  # red
        logging.CRITICAL: "\033[41;97m",            # red bg white text
    }
    
    # Basic Colors (Prod/Host/Railway) - Only bolding/colors for primary levels to save logic
    BASIC_COLORS = {
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[31m",
    }
    
    RESET = "\033[0m"

    def __init__(self, use_rich: bool):
        super().__init__()
        self.use_rich = use_rich
        self.color_map = self.RICH_COLORS if use_rich else self.BASIC_COLORS

    def format(self, record: logging.LogRecord) -> str:
        color = self.color_map.get(record.levelno, "")
        msecs = f"{record.msecs:03.0f}"

        # Contextual path truncation
        try:
            cwd = os.getcwd()
            module_path = record.pathname.replace(cwd, "").lstrip(os.sep)
        except Exception:
            module_path = record.pathname
            
        module_parts = module_path.split(os.sep)
        if len(module_parts) > 1:
            module_parts[-1] = os.path.splitext(module_parts[-1])[0]
        module_name = ".".join(part for part in module_parts if part and part != "__init__")

        formatted = f"[{msecs}ms] [{record.levelname:^8}] [{module_name}] {record.getMessage()}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                formatted += "\n" + record.exc_text

        if color:
            return f"{color}{formatted}{self.RESET}"
        return formatted

# Setup global handler
def setup_logging(alpha_mode: bool = False):
    handler = logging.StreamHandler(sys.stdout)
    # If on Railway, always use basic regardless of alpha token (to save on log overhead)
    # unless user explicitly wants it. Let's stick to rich only if NOT on Railway.
    use_rich = alpha_mode and not IS_RAILWAY
    handler.setFormatter(AdaptiveFormatter(use_rich=use_rich))
    
    # Set level based on alpha mode
    level = TRACE_LEVEL if alpha_mode else EVENT_LEVEL
    
    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True
    )

def get_logger(name=None) -> logging.Logger:
    """Returns a contextual logger based on the caller."""
    if not name:
        frame = inspect.stack()[1]
        module = inspect.getmodule(frame[0])
        if module and hasattr(module, '__name__') and module.__name__ not in ("__main__",):
            name = module.__name__
        else:
            path = frame.filename.replace(os.getcwd(), "").lstrip(os.sep)
            parts = path.split(os.sep)
            parts[-1] = os.path.splitext(parts[-1])[0]
            name = ".".join(parts)

    return logging.getLogger(name)

# Extension methods for Logger
def _make_logger_method(level):
    def method(self, message, *args, **kwargs):
        if self.isEnabledFor(level):
            kwargs.setdefault('stacklevel', 2)
            self._log(level, message, args, **kwargs)
    return method

logging.Logger.success = _make_logger_method(SUCCESS_LEVEL)
logging.Logger.trace = _make_logger_method(TRACE_LEVEL)
logging.Logger.event = _make_logger_method(EVENT_LEVEL)
logging.Logger.database = _make_logger_method(DATABASE_LEVEL)
logging.Logger.successtrace = _make_logger_method(SUCCESSTRACE_LEVEL)
logging.Logger.warningtrace = _make_logger_method(WARNINGTRACE_LEVEL)
logging.Logger.network = _make_logger_method(NETWORK_LEVEL)

# Final trigger
setup_logging(alpha_mode=IS_ALPHA)
