#!/usr/bin/env python3
"""
42 Campus Location Tracker CLI
Command-line interface for seeding and polling student locations.

Usage:
    python cli.py --seed              # One-time seed all students
    python cli.py --seed --limit 10    # Seed only 10 students (testing)
    python cli.py --poll              # Run forever, poll every 15 min
    python cli.py --once              # Single poll (good for cron)
"""

import argparse
import logging
import sys

from api_client import TokenManager
from tracker import seed_students, poll_once, poll_loop
from storage import ensure_dirs

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="42 Campus Location Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python cli.py --seed              # seed all ~400 students (one-time)
  python cli.py --seed --limit 10 # seed only 10 (testing)
  python cli.py --poll              # run forever (15-min intervals)
  python cli.py --once              # single poll (good for cron)
        """,
    )
    parser.add_argument(
        "--seed", action="store_true",
        help="Seed all students with location history (one-time)",
    )
    parser.add_argument(
        "--poll", action="store_true",
        help="Run polling loop forever (15-minute intervals)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Poll once then exit (for cron jobs)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit number of students to seed (0 = all)",
    )
    parser.add_argument(
        "--interval", type=int, default=900,
        help="Poll interval in seconds (default: 900 = 15 min)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    # Ensure directories exist
    ensure_dirs()
    
    # Check arguments
    if not any([args.seed, args.poll, args.once]):
        parser.print_help()
        return
    
    # Initialize API client
    log.info("[CLI] Initializing...")
    auth = TokenManager()
    
    # Run requested operation
    if args.seed:
        log.info(f"[CLI] Starting seed (limit={args.limit})...")
        result = seed_students(auth, max_students=args.limit)
        log.info(f"[CLI] Seed complete: {result}")
        print(f"Seed complete: {result}")
    
    if args.poll:
        log.info(f"[CLI] Starting poll loop (interval={args.interval}s)...")
        poll_loop(auth, interval=args.interval)
    
    if args.once:
        log.info("[CLI] Running single poll...")
        snapshot = poll_once(auth)
        print(f"Poll complete: {snapshot['active_count']} active students")


if __name__ == "__main__":
    main()