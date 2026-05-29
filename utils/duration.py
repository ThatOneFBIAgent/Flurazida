# Duration parser, returns seconds as an integer and takes in strings like "1h30m", "2d", "45s", etc.

import re
from typing import Optional

# Map units to seconds
UNIT_MAP = {
    'dec': 315576000, 'decs': 315576000, 'decade': 315576000, 'decades': 315576000,          # Decades
    'y': 31557600, 'yr': 31557600, 'yrs': 31557600, 'year': 31557600, 'years': 31557600,     # Years
    'mo': 2629800, 'mon': 2629800, 'mons': 2629800, 'month': 2629800, 'months': 2629800,     # Months
    'w': 604800, 'wk': 604800, 'wks': 604800, 'week': 604800, 'weeks': 604800,               # Weeks
    'd': 86400, 'dy': 86400, 'dys': 86400, 'day': 86400, 'days': 86400,                      # Days
    'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,                         # Hours
    'm': 60, 'mn': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,                   # Minutes
    's': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1                                   # Seconds
} # I mean you surely won't need centuries or millenia for a discord bot?

# Regex parsing
_SEGMENT = r"\d+\s*[a-zA-Z]+"
_SEPARATOR = r"[\s,]*(and[\s,]*)?"
_FULL_PATTERN = re.compile(
    rf"^{_SEPARATOR}({_SEGMENT}{_SEPARATOR})+$"
)
_TOKEN = re.compile(r"(\d+)\s*([a-zA-Z]+)")

def parse_duration(duration_str: str) -> Optional[int]:
    """
    Parses a highly flexible duration string and returns total seconds.
    Supports units like:
    - Decades: dec, decs, decade, decades
    - Years: y, yr, yrs, year, years
    - Months: mo, mon, mons, month, months (approximated as 30 days)
    - Weeks: w, wk, wks, week, weeks
    - Days: d, dy, dys, day, days
    - Hours: h, hr, hrs, hour, hours
    - Minutes: m, mn, min, mins, minute, minutes
    - Seconds: s, sec, secs, second, seconds

    Returns None if the duration is completely invalid or has unparsed content.
    """
    if not duration_str:
        return None

    # Normalize input
    s = duration_str.lower().strip()

    if not _FULL_PATTERN.match(s):
        return None
    
    total = 0
    for val_str, unit in _TOKEN.findall(s):
        if unit not in UNIT_MAP:
            return None
        total += int(val_str) * UNIT_MAP[unit]

    return total