#!/usr/bin/env python3
"""
Exam Rank & Piscine Exam Fetcher
Fetches exam ranks and piscine exams from 42 API.

Logic:
  - Fetch /v2/users/{id}/projects_users for all students
  - For exam ranks with retries, fetch /v2/projects_users/{id} to get all attempt scores
  - Filter exam ranks (slug matches exam-rank-0n) and piscine exams (c-piscine-*)
  - Store all attempt data with real scores from teams

Output structure:
  {login: {exam_ranks: {rank_0n: [{occurrence, date, score, passed}]}, piscine_exams: {slug: [{date, score, passed}]}}}
"""

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from api_client import TokenManager, paginate
from storage import load_students, ensure_dirs

log = logging.getLogger(__name__)

REQUEST_DELAY = 0.4
DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "exam_ranks.json"

RANK_EXAM_PATTERN = re.compile(r"(?:42next-)?exam-rank-0*(\d+)")
PISCINE_EXAM_PREFIX = "c-piscine-"


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def parse_date(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return date_str[:10]


def normalize_rank_key(slug: str) -> str | None:
    match = RANK_EXAM_PATTERN.match(slug)
    if match:
        num = int(match.group(1))
        return f"rank_0{num}" if num < 10 else f"rank_{num}"
    return None


def is_piscine_exam(slug: str) -> bool:
    return slug.startswith(PISCINE_EXAM_PREFIX)


# ──────────────────────────────────────────────
# API Fetches
# ──────────────────────────────────────────────

def get_user_projects_users(auth: TokenManager, user_id: int) -> list:
    url = f"https://api.intra.42.fr/v2/users/{user_id}/projects_users"
    params = {"page[size]": 100}
    try:
        all_results = paginate(url, auth, params=params, label=f"user_{user_id}_projects", delay=REQUEST_DELAY)
        filtered = [
            r for r in all_results
            if "exam" in r.get("project", {}).get("slug", "").lower()
            or "piscine" in r.get("project", {}).get("slug", "").lower()
        ]
        return filtered
    except Exception as e:
        log.warning(f"Failed to fetch projects for user {user_id}: {e}")
        return []


def get_all_attempts(auth: TokenManager, projects_user_id: int) -> list[dict]:
    import requests
    import time
    url = f"https://api.intra.42.fr/v2/projects_users/{projects_user_id}"
    
    for attempt in range(3):
        try:
            headers = auth.headers()
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** attempt
                log.warning(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            teams = data.get("teams", [])
            attempts = []
            for team in teams:
                for user in team.get("users", []):
                    attempts.append({
                        "occurrence": user.get("occurrence", 0),
                        "score": team.get("final_mark", 0),
                        "validated": user.get("validated", False),
                        "team_id": team.get("id"),
                        "date": parse_date(team.get("created_at", "")),
                    })
            return attempts
        except Exception as e:
            log.warning(f"Failed to fetch attempts for {projects_user_id}: {e}")
            return []
    return []


# ──────────────────────────────────────────────
# Main Logic
# ──────────────────────────────────────────────

def fetch_student_data(auth: TokenManager, student: dict) -> dict | None:
    login = student.get("login")
    user_id = student.get("id")

    if not login or not user_id:
        return None

    try:
        projects = get_user_projects_users(auth, user_id)
    except Exception as e:
        log.warning(f"Failed to fetch projects for {login}: {e}")
        return None

    if not projects:
        return None

    exam_ranks: dict[str, list[dict]] = {}
    piscine_exams: dict[str, list[dict]] = {}
    ranks_seen: dict[str, dict] = {}  # rank_key -> {pu_id -> proj data} to handle duplicates

    for proj in projects:
        slug = proj.get("project", {}).get("slug", "")
        occurrence = proj.get("occurrence", 0)
        final_mark = proj.get("final_mark") or 0
        date = parse_date(proj.get("created_at", ""))
        passed = proj.get("validated?", False)
        pu_id = proj.get("id")

        rank_key = normalize_rank_key(slug)

        if rank_key:
            if rank_key not in ranks_seen:
                ranks_seen[rank_key] = {}
            ranks_seen[rank_key][pu_id] = {
                "occurrence": occurrence,
                "date": date,
                "score": final_mark if final_mark else 0,
                "passed": passed,
            }

        elif is_piscine_exam(slug):
            if slug not in piscine_exams:
                piscine_exams[slug] = []
            piscine_exams[slug].append({
                "date": date,
                "score": final_mark if final_mark else 0,
                "passed": passed,
            })

    for rank_key, pu_id_data in ranks_seen.items():
        all_attempts: list[dict] = []

        for pu_id, proj_data in pu_id_data.items():
            if proj_data["occurrence"] > 0:
                attempts = get_all_attempts(auth, pu_id)
                if attempts:
                    all_attempts.extend(attempts)
                    log.debug(f"  {login} {rank_key} (pu_id={pu_id}): got {len(attempts)} attempts from teams API")
            else:
                all_attempts.append({
                    "occurrence": proj_data["occurrence"],
                    "date": proj_data["date"],
                    "score": proj_data["score"],
                    "passed": proj_data["passed"],
                })

        if all_attempts:
            all_attempts_sorted = sorted(all_attempts, key=lambda x: x.get("date", ""))
            all_attempts_filtered = [a for a in all_attempts_sorted if a.get("score") is not None]
            exam_ranks[rank_key] = [
                {
                    "occurrence": i,
                    "date": a.get("date", ""),
                    "score": a.get("score", 0),
                    "passed": a.get("validated", False),
                }
                for i, a in enumerate(all_attempts_filtered)
            ]
            log.debug(f"  {login} {rank_key}: combined {len(all_attempts_filtered)} attempts")

    result = {
        "login": login,
        "exam_ranks": exam_ranks,
        "piscine_exams": piscine_exams,
    }

    has_retries = any(a.get("occurrence", 0) > 0 for attempts in exam_ranks.values() for a in attempts)
    log.info(f"  {login}: {len(exam_ranks)} rank exams, {len(piscine_exams)} piscine exams"
           + (" (retries)" if has_retries else ""))
    return result


def fetch_all_exam_data(auth: TokenManager, max_students: int = 0, login_filter: str | None = None) -> dict:
    students = load_students()
    ensure_dirs()

    to_fetch = list(students.items())
    if max_students > 0:
        to_fetch = to_fetch[:max_students]
    if login_filter:
        to_fetch = [(l, s) for l, s in to_fetch if l == login_filter]
        if not to_fetch:
            log.error(f"Student {login_filter} not found")
            return {}

    log.info(f"Fetching exam data for {len(to_fetch)} students...")

    results: dict[str, dict] = {}

    for i, (login, student) in enumerate(to_fetch):
        if i > 0 and i % 20 == 0:
            log.info(f"Progress: {i}/{len(to_fetch)}")

        result = fetch_student_data(auth, student)
        if result:
            results[login] = result

    log.info(f"Complete: {len(results)} students")

    if results:
        existing = {}
        if OUTPUT_FILE.exists():
            existing = json.loads(OUTPUT_FILE.read_text())
        merged = {**existing, **results}
        OUTPUT_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        log.info(f"Saved {len(results)} students to {OUTPUT_FILE} (total: {len(merged)})")

    return results


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch exam ranks and piscine exams")
    parser.add_argument("--limit", type=int, default=0, help="Limit students (0=all)")
    parser.add_argument("--login", type=str, nargs="+", help="One or more student logins")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    ensure_dirs()
    auth = TokenManager()
    
    if args.login:
        for login in args.login:
            fetch_all_exam_data(auth, login_filter=login)
    else:
        fetch_all_exam_data(auth, max_students=args.limit)


if __name__ == "__main__":
    main()