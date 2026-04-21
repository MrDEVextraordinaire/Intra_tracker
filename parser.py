#!/usr/bin/env python3
"""
Parser Module
Handles parsing of 42 API data formats - host coordinates, timestamps, etc.
"""

import re
import logging
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# Host format: c{n}r{n}p{n} e.g., c1r3p12
HOST_PATTERN = re.compile(r"c(\d+)r(\d+)p(\d+)")


def parse_host(host: str) -> Optional[dict]:
    """
    Parse a host string like 'c1r3p12' into coordinates.
    
    Args:
        host: Host string from 42 API
    
    Returns:
        Dict with cluster, row, position, x, y or None if invalid
    """
    if not host:
        return None
    
    m = HOST_PATTERN.match(host)
    if not m:
        return None
    
    cluster = int(m.group(1))
    row = int(m.group(2))
    position = int(m.group(3))
    
    return {
        "cluster": cluster,
        "row": row,
        "position": position,
        # Euclidean mapping for proximity calculations
        "x": position,
        "y": row,
        # Original host string
        "host": host,
    }


def is_valid_host(host: str) -> bool:
    """Check if host string is valid format."""
    return bool(host and HOST_PATTERN.match(host))


def parse_timestamp(ts: str) -> datetime:
    """
    Parse ISO timestamp string to datetime.
    
    Handles formats like:
    - 2026-04-21T14:32:20.431Z
    - 2026-04-21T14:32:20.431+00:00
    """
    if not ts:
        return datetime.now(timezone.utc)
    
    # Replace Z with +00:00
    ts = ts.replace("Z", "+00:00")
    
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        log.warning(f"[PARSER] Failed to parse timestamp: {ts}")
        return datetime.now(timezone.utc)


def calc_session_duration(begin_at: str, end_at: str) -> float:
    """
    Calculate session duration in hours.
    
    Args:
        begin_at: Start timestamp
        end_at: End timestamp
    
    Returns:
        Duration in hours
    """
    begin = parse_timestamp(begin_at)
    end = parse_timestamp(end_at)
    
    delta = end - begin
    hours = delta.total_seconds() / 3600
    
    return max(0, hours)  # No negative durations


def is_proximate(loc1: dict, loc2: dict, row_tol: int = 1, pos_tol: int = 2) -> bool:
    """
    Check if two locations are physically proximate.
    
    Args:
        loc1: First location with coords (from parse_host)
        loc2: Second location with coords
        row_tol: Row tolerance (default 1)
        pos_tol: Position tolerance (default 2)
    
    Returns:
        True if locations are within tolerance
    """
    if not loc1 or not loc2:
        return False
    
    # Must be same cluster
    if loc1.get("cluster") != loc2.get("cluster"):
        return False
    
    # Check row proximity
    row_diff = abs(loc1.get("row", 0) - loc2.get("row", 0))
    if row_diff > row_tol:
        return False
    
    # Check position proximity
    pos_diff = abs(loc1.get("position", 0) - loc2.get("position", 0))
    if pos_diff > pos_tol:
        return False
    
    return True


def test_parser():
    """Test parser functions."""
    print("[TEST] Testing parser module...")
    
    # Test parse_host
    assert parse_host("c1r3p12") == {"cluster": 1, "row": 3, "position": 12, "x": 12, "y": 3, "host": "c1r3p12"}
    assert parse_host("c3r8p5") == {"cluster": 3, "row": 8, "position": 5, "x": 5, "y": 8, "host": "c3r8p5"}
    assert parse_host("invalid") is None
    assert parse_host("") is None
    assert parse_host(None) is None
    print("[TEST] parse_host OK")
    
    # Test is_valid_host
    assert is_valid_host("c1r1p1") == True
    assert is_valid_host("c10r20p30") == True
    assert is_valid_host("invalid") == False
    print("[TEST] is_valid_host OK")
    
    # Test is_proximate
    loc1 = parse_host("c1r3p12")
    loc2 = parse_host("c1r3p13")  # Same cluster, same row, adjacent position
    assert is_proximate(loc1, loc2) == True
    
    loc3 = parse_host("c1r5p12")  # Different row
    assert is_proximate(loc1, loc3) == False
    
    loc4 = parse_host("c2r3p12")  # Different cluster
    assert is_proximate(loc1, loc4) == False
    print("[TEST] is_proximate OK")
    
    # Test timestamp parsing
    ts1 = parse_timestamp("2026-04-21T14:32:20.431Z")
    assert ts1.tzinfo is not None  # Should have timezone
    print("[TEST] parse_timestamp OK")
    
    # Test duration calculation
    dur = calc_session_duration("2026-04-21T10:00:00Z", "2026-04-21T12:00:00Z")
    assert abs(dur - 2.0) < 0.01  # Should be ~2 hours
    print("[TEST] calc_session_duration OK")
    
    return True


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    print("=" * 40)
    print("Running parser tests...")
    print("=" * 40)
    
    try:
        test_parser()
        print("[TEST] ✓ All parser tests PASSED")
    except Exception as e:
        print(f"[TEST] ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("=" * 40)