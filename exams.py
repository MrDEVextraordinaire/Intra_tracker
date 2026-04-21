#!/usr/bin/env python3
"""
Exam Data Fetcher
Fetches exam results from 42 API and stores per-student.
"""

import logging
from datetime import datetime, timezone

from api_client import TokenManager, paginate
from models import CURSUS_PISCINE, CURSUS_MAIN, ExamResult
from storage import load_students, save_exams, load_exams, ensure_dirs

log = logging.getLogger(__name__)

REQUEST_DELAY = 1.0


def get_user_exams(auth: TokenManager, user_id: int) -> list:
    """Fetch all exams for a specific user."""
    url = f"https://api.intra.42.fr/v2/users/{user_id}/exams"
    params = {"page[size]": 100}
    return paginate(url, auth, params=params, label=f"user_{user_id}_exams", delay=REQUEST_DELAY)


def fetch_student_exams(auth: TokenManager, student: dict) -> dict:
    """
    Fetch exam results for a single student.
    
    Args:
        student: dict with 'id' and 'login' keys
    
    Returns:
        dict: {"login": str, "fetched_at": str, "exams": [...]}
    """
    user_id = student.get("id")
    login = student.get("login")
    
    if not user_id or not login:
        return None
    
    try:
        exams = get_user_exams(auth, user_id)
    except Exception as e:
        log.warning(f"[EXAM] Failed to fetch exams for {login}: {e}")
        return None
    
    if not exams:
        return {"login": login, "fetched_at": datetime.now(timezone.utc).isoformat(), "exams": []}
    
    exam_results = []
    for exam in exams:
        exam_results.append({
            "exam_id": exam.get("id"),
            "exam_name": exam.get("name", ""),
            "score": exam.get("score", 0.0),
            "total": exam.get("total", 100.0),
            "created_at": exam.get("created_at", ""),
            "updated_at": exam.get("updated_at", ""),
        })
    
    return {
        "login": login,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "exams": exam_results,
    }


def fetch_all_exam_results(auth: TokenManager, student_logins: list = None, max_students: int = 0) -> dict:
    """
    Fetch exam results for all students.
    
    Args:
        student_logins: List of logins to fetch (None = load from students.json)
        max_students: Limit number (0 = all)
    
    Returns:
        dict: {login: {"login": str, "fetched_at": str, "exams": [...]}}
    """
    students = load_students()
    
    if student_logins:
        to_fetch = [(s["login"], students.get(s["login"])) for s in [{ "login": l } for l in student_logins] if l in students]
    else:
        to_fetch = list(students.items())
    
    if max_students > 0:
        to_fetch = to_fetch[:max_students]
    
    log.info(f"[EXAM] Fetching exams for {len(to_fetch)} students...")
    
    all_results = {}
    for i, (login, student) in enumerate(to_fetch):
        if student is None:
            student = {"login": login, "id": None}
        
        if i % 20 == 0:
            log.info(f"[EXAM] Progress: {i+1}/{len(to_fetch)}")
        
        result = fetch_student_exams(auth, student)
        if result:
            all_results[login] = result
    
    log.info(f"[EXAM] Total students with exam data: {len(all_results)}")
    return all_results


def save_all_exam_results(results: dict) -> None:
    """Save exam results to per-student files."""
    for login, data in results.items():
        save_exams(login, data)
    log.info(f"[EXAM] Saved exam results for {len(results)} students")


def seed_exams(auth: TokenManager, max_students: int = 0) -> dict:
    """
    Fetch and save exam results for all students.
    
    Args:
        auth: TokenManager
        max_students: Limit number (0 = all)
    
    Returns:
        dict: Summary of fetch operation
    """
    ensure_dirs()
    
    # Fetch all exam results for students
    all_results = fetch_all_exam_results(auth, max_students=max_students)
    
    # Save to per-student files
    save_all_exam_results(all_results)
    
    total_exams = sum(len(r.get("exams", [])) for r in all_results.values())
    
    return {
        "students_fetched": len(all_results),
        "total_exams": total_exams,
    }


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    print("=" * 50)
    print("Exam Seeder")
    print("=" * 50)
    
    auth = TokenManager()
    result = seed_exams(auth)
    print(f"Result: {result}")