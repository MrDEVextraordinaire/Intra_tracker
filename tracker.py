#!/usr/bin/env python3
"""
Tracker Module
Main logic for campus tracking - seed and poll operations.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from api_client import TokenManager, paginate, PAGE_SIZE
from storage import (
    DATA_DIR, LOCATIONS_DIR,
    load_students, save_students,
    load_excluded, save_excluded,
    load_locations, save_locations,
    load_state, save_state,
    append_jsonl, snapshot_path, ensure_dirs,
)
from parser import parse_host, parse_timestamp
from models import CAMPUS_ID, CURSUS_MAIN

log = logging.getLogger(__name__)

POLL_INTERVAL = 15 * 60  # 15 minutes
REQUEST_DELAY = 0.55


def now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def get_campus_users(auth: TokenManager, campus_id: int = CAMPUS_ID, cursus_id: int = CURSUS_MAIN) -> list:
    """
    Fetch all users for a campus in a specific cursus.
    
    Uses /cursus/{cursus_id}/users with filter on primary_campus_id.
    """
    url = f"https://api.intra.42.fr/v2/cursus/{cursus_id}/users"
    params = {"filter[primary_campus_id]": campus_id}
    
    log.info(f"[TRACKER] Fetching users for campus {campus_id}, cursus {cursus_id}...")
    users = paginate(url, auth, params=params, label="users", delay=REQUEST_DELAY)
    
    return users


def get_user_locations(auth: TokenManager, login: str) -> list:
    """Fetch location history for a single user."""
    url = f"https://api.intra.42.fr/v2/users/{login}/locations"
    locations = paginate(url, auth, label="locations", delay=REQUEST_DELAY)
    return locations


def get_active_sessions(auth: TokenManager, campus_id: int = CAMPUS_ID) -> list:
    """Fetch all currently active location sessions."""
    url = f"https://api.intra.42.fr/v2/campus/{campus_id}/locations"
    params = {"filter[active]": "true"}
    
    sessions = paginate(url, auth, params=params, label="active sessions", delay=REQUEST_DELAY)
    return sessions


def is_student_active(cursus_user: dict) -> bool:
    """Check if student is active (not blackholed)."""
    bh = cursus_user.get("blackholed_at")
    if not bh:
        return True
    
    try:
        bh_dt = datetime.fromisoformat(bh.replace("Z", "+00:00"))
        return bh_dt > datetime.now(timezone.utc)
    except ValueError:
        return True


def classify_student(user_data: dict) -> tuple[str, str]:
    """
    Classify a student as active or excluded.
    
    Returns:
        (status, reason) - status is "active" or "blackholed", reason explains why
    """
    bh = user_data.get("blackholed_at")
    
    if bh:
        try:
            bh_dt = datetime.fromisoformat(bh.replace("Z", "+00:00"))
            if bh_dt <= datetime.now(timezone.utc):
                days_ago = (datetime.now(timezone.utc) - bh_dt).days
                return "blackholed", f"blackholed_at={bh} ({days_ago}d ago)"
        except ValueError:
            pass
    
    return "active", "enrolled in main cursus"


def seed_students(auth: TokenManager, max_students: int = 0, limit: int = 0) -> dict:
    """
    Seed students and their location history.
    
    Args:
        auth: TokenManager instance
        max_students: Max students to process (0 = all)
        limit: Alias for max_students (for CLI compatibility)
    
    Returns:
        Summary dict with counts
    """
    if limit > 0:
        max_students = limit
    
    ensure_dirs()
    
    # Fetch campus users
    users = get_campus_users(auth)
    log.info(f"[SEED] API returned {len(users)} users")
    
    # Load existing data
    students = load_students()
    excluded = load_excluded()
    
    # Classify and filter students
    active_users = []
    excluded_count = 0
    
    for user in users:
        login = user.get("login")
        if not login:
            continue
        
        status, reason = classify_student(user)
        
        if status != "active":
            if login not in excluded:
                excluded[login] = {
                    "login": login,
                    "status": status,
                    "reason": reason,
                    "excluded_at": now_iso(),
                }
            excluded_count += 1
            continue
        
        active_users.append(user)
        
        # Add to students index
        if login not in students:
            students[login] = {
                "id": user.get("id"),
                "login": login,
                "display_name": user.get("displayname", login),
                "pool_year": user.get("pool_year"),
                "level": user.get("level", 0),
                "blackholed_at": user.get("blackholed_at"),
                "added_at": now_iso(),
            }
    
    # Save index files
    save_students(students)
    save_excluded(excluded)
    
    log.info(f"[SEED] {len(active_users)} active, {excluded_count} excluded")
    log.info(f"[SEED] Student index: {len(students)} entries")
    
    # Fetch location history for students
    targets = list(students.keys())
    if max_students > 0:
        targets = targets[:max_students]
    
    seeded = 0
    skipped = 0
    
    for i, login in enumerate(targets, 1):
        loc_file = LOCATIONS_DIR / f"{login}.json"
        
        if loc_file.exists():
            log.info(f"[{i:>4}/{len(targets)}] {login:20s} — already exists, skipping")
            skipped += 1
            continue
        
        log.info(f"[{i:>4}/{len(targets)}] {login:20s} — fetching location history...")
        
        locations = get_user_locations(auth, login)
        
        # Parse coordinates
        for loc in locations:
            coords = parse_host(loc.get("host", ""))
            loc["_coords"] = coords
        
        # Save
        save_locations(login, {
            "login": login,
            "seeded_at": now_iso(),
            "history": locations,
        })
        
        log.info(f"             → {len(locations)} locations")
        seeded += 1
        
        time.sleep(REQUEST_DELAY)
    
    log.info(f"[SEED] Done: {seeded} seeded, {skipped} skipped")
    
    return {
        "total_api_users": len(users),
        "active_users": len(active_users),
        "excluded": excluded_count,
        "students_indexed": len(students),
        "seeded": seeded,
        "skipped": skipped,
    }


def poll_once(auth: TokenManager) -> dict:
    """
    Poll active students once.
    
    Returns:
        Snapshot dict
    """
    polled_at = now_iso()
    log.info(f"[POLL] Polling at {polled_at}...")
    
    sessions = get_active_sessions(auth)
    log.info(f"[POLL] {len(sessions)} active students")
    
    snapshot_students = []
    
    for session in sessions:
        login = session.get("user", {}).get("login", "unknown")
        host = session.get("host", "")
        coords = parse_host(host)
        
        entry = {
            "login": login,
            "host": host,
            "coords": coords,
            "begin_at": session.get("begin_at"),
            "session_id": session.get("id"),
        }
        snapshot_students.append(entry)
        
        # Append to student's location history
        loc_file = LOCATIONS_DIR / f"{login}.json"
        
        if loc_file.exists():
            data = load_locations(login)
            history = data.get("history", [])
            
            # Check for duplicates by session ID
            known_ids = {loc.get("id") for loc in history if isinstance(loc, dict)}
            if session.get("id") not in known_ids:
                enriched = dict(session)
                enriched["_coords"] = coords
                enriched["_polled_at"] = polled_at
                history.append(enriched)
                data["history"] = history
                save_locations(login, data)
    
    # Write snapshot
    snapshot = {
        "polled_at": polled_at,
        "active_count": len(sessions),
        "students": snapshot_students,
    }
    
    append_jsonl(snapshot_path(), snapshot)
    
    # Update state
    save_state({
        "last_poll": polled_at,
        "active_count": len(sessions),
        "active_logins": [s["login"] for s in snapshot_students],
    })
    
    log.info(f"[POLL] Snapshot written, {len(sessions)} active")
    
    return snapshot


def poll_loop(auth: TokenManager, interval: int = POLL_INTERVAL):
    """Run poll_once() forever."""
    log.info(f"[POLL] Starting loop - interval: {interval}s ({interval//60}m)")
    
    while True:
        try:
            poll_once(auth)
        except Exception as e:
            log.error(f"[POLL] Error: {e}", exc_info=True)
        
        log.info(f"[POLL] Sleeping {interval}s...")
        time.sleep(interval)


def test_seed_small():
    """Test seed with 2 students."""
    print("[TEST] Testing seed with 2 students...")
    ensure_dirs()
    
    auth = TokenManager()
    result = seed_students(auth, max_students=2)
    
    print(f"[TEST] Seed result: {result}")
    assert result["seeded"] == 2, f"Expected 2 seeded, got {result['seeded']}"
    assert result["students_indexed"] > 0
    print("[TEST] Seed test PASSED")
    return True


def test_poll_once():
    """Test poll once."""
    print("[TEST] Testing poll_once...")
    ensure_dirs()
    
    auth = TokenManager()
    snapshot = poll_once(auth)
    
    print(f"[TEST] Snapshot: {snapshot['active_count']} active")
    assert "polled_at" in snapshot
    assert "active_count" in snapshot
    assert "students" in snapshot
    print("[TEST] Poll test PASSED")
    return True


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    print("=" * 40)
    print("Running tracker tests...")
    print("=" * 40)
    
    try:
        test_seed_small()
    except Exception as e:
        print(f"[TEST] ✗ Seed test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("=" * 40)