# roll_logic.py
# Dice rolling logic for fun.py "roll" command

import random
import re
from typing import Dict, List, Tuple, Optional
from logger import get_logger

log = get_logger()


# Constants for dice limits
MAX_DICE = 100
MAX_SIDES = 1000


def parse_dice_expression(dice: str) -> Tuple[List[Dict], int]:
    """
    Parse a dice expression into groups.
    Returns (groups, total_dice_count)
    """
    # Tokenize into signed parts: supports +1d20, -2, +3d6k1!!p etc.
    sanitized = dice.replace(" ", "")

    # Normalize shorthand like "20" or "+20" into "1d20"
    simple_roll_match = re.fullmatch(r'([+-]?)(?:d)?(\d+)', sanitized, re.IGNORECASE)
    if simple_roll_match:
        sign = simple_roll_match.group(1) or ''
        sides = simple_roll_match.group(2)
        sanitized = f"{sign}1d{sides}"

    sanitized_orig = sanitized  # keep the full original (contains & modifiers) for later parsing of &N+Z
    # Remove &N±Z fragments BEFORE tokenizing so the numeric N inside them is not treated as a standalone constant.
    sanitized_no_amp = re.sub(r'&\d+[+-]\d+', '', sanitized)

    # Tokenize
    token_pattern = re.compile(r'([+-]?)(\d+[dD](?:!{1,2}(?:p)?)?\d+(?:[kK]\d+|D\d+)?|\d+)', re.IGNORECASE)
    tokens = token_pattern.findall(sanitized_no_amp)
    if not tokens:
        raise ValueError("Invalid format!")

    def parse_group(text):
        if text.isdigit():
            return {"type": "const", "value": int(text)}
        m = re.match(r'(?P<num>\d+)[dD](?P<explode>!{1,2}p?|!p?)?(?P<sides>\d+)(?P<kd>(?:[kK]\d+|D\d+))?', text)
        if not m:
            return None
        return {
            "type": "dice",
            "num": int(m.group("num")),
            "sides": int(m.group("sides")),
            "explode": m.group("explode") or "",
            "keepdrop": m.group("kd") or ""
        }

    groups = []
    total_dice_count = 0
    for sign, body in tokens:
        parsed = parse_group(body)
        if not parsed:
            raise ValueError(f"Couldn't parse token: {body}")
        parsed["sign"] = -1 if sign == "-" else 1
        groups.append(parsed)
        if parsed["type"] == "dice":
            total_dice_count += parsed["num"]

    # Limits
    if total_dice_count > MAX_DICE:
        raise ValueError(f"Too many dice in total! Limit: {MAX_DICE} dice.")
    for g in groups:
        if g["type"] == "dice" and g["sides"] > MAX_SIDES:
            raise ValueError(f"Die with too many sides! Limit: {MAX_SIDES} sides.")

    return groups, total_dice_count, sanitized_orig


def roll_die(sides: int, rng: random.Random) -> int:
    """Roll a single die."""
    return rng.randint(1, sides)


def resolve_explosions_for_die(first_roll: int, sides: int, explode_flag: str, rng: random.Random, max_depth: int = 100) -> Tuple[int, List[int], List[int]]:
    """
    Returns (value_contrib, chain_list, raw_chain)
    - chain_list: the values that will be shown/added (penetrating subtracts 1 on extras)
    - raw_chain: raw face values used to test for further explosions (used only internally)
    """
    chain_display = [first_roll]
    raw_chain = [first_roll]
    if not explode_flag:
        return first_roll, chain_display, raw_chain

    is_compound = explode_flag.startswith("!!")
    is_penetrate = "p" in explode_flag
    depth = 0

    if is_compound:
        # only chain if first == max
        if first_roll != sides:
            return first_roll, chain_display, raw_chain
        # roll until we break the chain
        while depth < max_depth:
            depth += 1
            nxt_raw = roll_die(sides, rng)
            raw_chain.append(nxt_raw)
            nxt_display = nxt_raw - 1 if is_penetrate else nxt_raw
            chain_display.append(nxt_display)
            if nxt_raw != sides:
                break
        return sum(chain_display), chain_display, raw_chain
    else:
        # normal explode (possibly penetrating)
        total = first_roll
        raw_last = first_roll
        while depth < max_depth and raw_last == sides:
            depth += 1
            nxt_raw = roll_die(sides, rng)
            raw_chain.append(nxt_raw)
            nxt_display = nxt_raw - 1 if is_penetrate else nxt_raw
            chain_display.append(nxt_display)
            total += nxt_display
            raw_last = nxt_raw
        return total, chain_display, raw_chain


def execute_roll(dice: str) -> Dict:
    """
    Execute a dice roll and return the results.
    Returns a dictionary with all roll data for formatting.
    """
    log.trace(f"Executing roll expression: {dice}")
    groups, total_dice_count, sanitized_orig = parse_dice_expression(dice)
    
    rng = random.Random()

    # Collect per-group and per-die data
    group_summaries = []
    flat_kept_entries = []  # flattened list of dicts for kept dice to apply global &-modifiers
    const_total = 0  # sum of constant numeric tokens (signed)
    footer_keepdrop = []

    for gi, g in enumerate(groups):
        if g["type"] == "const":
            const_total += g["sign"] * g["value"]
            group_summaries.append({
                "kind": "const",
                "label": f"{g['sign'] * g['value']}",
                "pre_keep_sum": g['sign'] * g['value'],
                "post_mod_sum": g['sign'] * g['value'],
                "details": None
            })
            continue

        per_die = []
        for _ in range(g["num"]):
            first = roll_die(g["sides"], rng)
            contrib, chain_display, raw_chain = resolve_explosions_for_die(first, g["sides"], g["explode"], rng)
            per_die.append({
                "raw_first": first,
                "pre_contrib": contrib,       # contribution BEFORE any & modifiers
                "chain_display": chain_display,
                "raw_chain": raw_chain
            })

        # apply keep/drop per group (operates on pre_contrib)
        kd = g["keepdrop"]
        if kd:
            # kd now is either like 'k2' / 'K2' or 'D2' (drop must be uppercase D)
            first_char = kd[0]
            if first_char in ('k', 'K'):
                typ = 'k'
            elif first_char == 'D':
                typ = 'D'
            else:
                typ = first_char.lower()
            n = int(kd[1:])

            sorted_by = sorted(per_die, key=lambda x: x["pre_contrib"], reverse=True)
            if typ == "k":
                kept = sorted_by[:n]
                dropped = sorted_by[n:]
                footer_keepdrop.append(f"{g['num']}d{g['sides']}k{n}")
            else:  # typ == "D"
                dropped = sorted_by[:n]
                kept = sorted_by[n:]
                footer_keepdrop.append(f"{g['num']}d{g['sides']}D{n}")
        else:
            kept = per_die
            dropped = []

        pre_keep_sum = sum(d["pre_contrib"] for d in kept)
        # Add entries to flat_kept_entries for global &-style modifiers (preserve order groups->dice)
        for d in kept:
            flat_kept_entries.append({
                "group_index": gi,
                "base_value": d["pre_contrib"],  # will be adjusted by &N later
                "chain_display": d["chain_display"],
                "raw_first": d["raw_first"]
            })

        group_summaries.append({
            "kind": "dice",
            "label": f"{g['sign'] if g['sign']<0 else ''}{g['num']}d{g['sides']}{g['explode']}{g['keepdrop']}",
            "pre_keep_sum": g['sign'] * pre_keep_sum,  # sign applied here; & modifiers applied globally later
            "post_mod_sum": None,  # to be filled after global modifiers applied
            "details": per_die,
            "sign": g["sign"]
        })

    # Detect legacy &N+Z modifiers anywhere in the sanitized input
    ampersand_matches = re.findall(r'&(\d+)([+-]\d+)', sanitized_orig)
    ampersand_notes = []
    if ampersand_matches:
        # apply each match in order found — each modifies the first N kept dice in flat_kept_entries
        for cnt_str, flat_mod_str in ampersand_matches:
            cnt = int(cnt_str)
            flat_mod = int(flat_mod_str)
            applied = 0
            for e in flat_kept_entries:
                if applied >= cnt:
                    break
                e["base_value"] += flat_mod
                applied += 1
            ampersand_notes.append(f"First {cnt} rolls: {flat_mod:+d}")

    # Now compute per-group post-mod sums from flat_kept_entries
    group_post_sums = {}
    for idx, gs in enumerate(group_summaries):
        if gs["kind"] == "const":
            group_post_sums[idx] = gs["post_mod_sum"]
            continue
        group_post_sums[idx] = 0

    for e in flat_kept_entries:
        gi = e["group_index"]
        sign = group_summaries[gi]["sign"]
        group_post_sums[gi] += sign * e["base_value"]

    # fill post_mod_sum into summaries
    for idx, gs in enumerate(group_summaries):
        if gs["kind"] == "const":
            gs["post_mod_sum"] = gs.get("post_mod_sum", gs["pre_keep_sum"])
            continue
        gs["post_mod_sum"] = group_post_sums.get(idx, 0)

    pre_mod_total = sum(gs["pre_keep_sum"] for gs in group_summaries)
    post_mod_total = sum((gs["post_mod_sum"] if gs["post_mod_sum"] is not None else gs["pre_keep_sum"]) for gs in group_summaries)

    log.successtrace(f"Roll complete: {dice} -> {post_mod_total}")

    return {
        "group_summaries": group_summaries,
        "footer_keepdrop": footer_keepdrop,
        "ampersand_notes": ampersand_notes,
        "const_total": const_total,
        "pre_mod_total": pre_mod_total,
        "post_mod_total": post_mod_total,
        "dice": dice
    }

# who the hell needs a dice roller THIS complicated???