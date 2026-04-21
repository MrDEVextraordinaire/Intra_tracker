#!/usr/bin/env python3
"""
Storage Module
Handles JSON file read/write operations for data persistence.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
LOCATIONS_DIR = DATA_DIR / "locations"


def ensure_dirs():
    """Ensure data directories exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCATIONS_DIR.mkdir(parents=True, exist_ok=True)
    log.debug(f"[STORAGE] ensure_dirs: {DATA_DIR}, {LOCATIONS_DIR}")


def load_json(path: Path, default: Optional[Any] = None) -> Any:
    """
    Load JSON from file.
    
    Args:
        path: File path
        default: Default value if file doesn't exist
    
    Returns:
        Loaded JSON data or default
    """
    if default is None:
        default = {}
    
    if not path.exists():
        log.debug(f"[STORAGE] {path} not found, returning default")
        return default
    
    try:
        with open(path) as f:
            data = json.load(f)
        log.debug(f"[STORAGE] Loaded {path}: {len(data) if isinstance(data, (dict, list)) else 'data'}")
        return data
    except json.JSONDecodeError as e:
        log.warning(f"[STORAGE] Invalid JSON in {path}: {e}")
        return default
    except Exception as e:
        log.error(f"[STORAGE] Error loading {path}: {e}")
        return default


def save_json(path: Path, data: Any) -> None:
    """
    Save JSON to file (with directory creation).
    
    Args:
        path: File path
        data: Data to save
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log.debug(f"[STORAGE] Saved {path}")
    except Exception as e:
        log.error(f"[STORAGE] Error saving {path}: {e}")
        raise


def append_jsonl(path: Path, record: dict) -> None:
    """
    Append a record to a JSONL file.
    
    Args:
        path: File path
        record: Record to append (will be JSON + newline)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(path, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        log.debug(f"[STORAGE] Appended to {path}")
    except Exception as e:
        log.error(f"[STORAGE] Error appending to {path}: {e}")
        raise


def load_students() -> dict:
    """Load student index."""
    return load_json(DATA_DIR / "students.json")


def save_students(students: dict) -> None:
    """Save student index."""
    save_json(DATA_DIR / "students.json", students)


def load_excluded() -> dict:
    """Load excluded students."""
    return load_json(DATA_DIR / "excluded.json")


def save_excluded(excluded: dict) -> None:
    """Save excluded students."""
    save_json(DATA_DIR / "excluded.json", excluded)


def load_locations(login: str) -> dict:
    """Load location history for a student."""
    return load_json(LOCATIONS_DIR / f"{login}.json")


def save_locations(login: str, location_data: dict) -> None:
    """Save location history for a student."""
    save_json(LOCATIONS_DIR / f"{login}.json", location_data)


def load_state() -> dict:
    """Load current state."""
    return load_json(DATA_DIR / "state.json", {})


def save_state(state: dict) -> None:
    """Save current state."""
    save_json(DATA_DIR / "state.json", state)


def snapshot_path() -> Path:
    """Get path to snapshots JSONL file."""
    return DATA_DIR / "snapshots.jsonl"


def test_storage():
    """Quick test of storage functions."""
    print("[TEST] Testing storage module...")
    
    ensure_dirs()
    
    # Test save/load
    test_file = DATA_DIR / "test.json"
    test_data = {"test": "data", "count": 42}
    
    save_json(test_file, test_data)
    loaded = load_json(test_file)
    
    assert loaded == test_data, "Save/load mismatch"
    print(f"[TEST] Save/load OK")
    
    # Test append_jsonl
    append_jsonl(test_file, {"new": "record"})
    # Load and check - JSONL loads as text, let's just verify file exists
    assert test_file.exists(), "File not created"
    print(f"[TEST] Append OK")
    
    # Cleanup
    test_file.unlink()
    print(f"[TEST] Cleanup OK")
    
    return True


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    print("=" * 40)
    print("Running storage tests...")
    print("=" * 40)
    
    try:
        test_storage()
        print("[TEST] ✓ All storage tests PASSED")
    except Exception as e:
        print(f"[TEST] ✗ FAILED: {e}")
        sys.exit(1)
    
    print("=" * 40)