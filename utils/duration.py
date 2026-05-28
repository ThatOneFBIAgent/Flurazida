# Duration parser, returns seconds as an integer and takes in strings like "1h30m", "2d", "45s", etc.

import re
from typing import Optional

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

    # Match patterns like "10y", "10 yr", "10 years"
    pattern = r"(\d+)\s*([a-zA-Z]+)"
    matches = re.findall(pattern, s)

    if not matches:
        return None

    # Map units to seconds
    unit_map = {
        # Decades
        'dec': 315360000, 'decs': 315360000, 'decade': 315360000, 'decades': 315360000,
        # Years
        'y': 31536000, 'yr': 31536000, 'yrs': 31536000, 'year': 31536000, 'years': 31536000,
        # Months
        'mo': 2592000, 'mon': 2592000, 'mons': 2592000, 'month': 2592000, 'months': 2592000,
        # Weeks
        'w': 604800, 'wk': 604800, 'wks': 604800, 'week': 604800, 'weeks': 604800,
        # Days
        'd': 86400, 'dy': 86400, 'dys': 86400, 'day': 86400, 'days': 86400,
        # Hours
        'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
        # Minutes
        'm': 60, 'mn': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
        # Seconds
        's': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1
    } # I mean you surely won't need centuries or millenia for a discord bot?

    total_seconds = 0
    reconstructed = s

    for val_str, unit in matches:
        if unit not in unit_map:
            return None
        total_seconds += int(val_str) * unit_map[unit]
        # Remove matched pattern from reconstructed string to check for leftovers
        reconstructed = re.sub(rf"{val_str}\s*{unit}", "", reconstructed, count=1)

    # Clean up reconstructed string from ignorable fillers: whitespace, commas, "and"
    reconstructed = re.sub(r"[\s,]|and", "", reconstructed)
    if reconstructed:  # If there's still unrecognized content, invalid format
        return None

    return total_seconds