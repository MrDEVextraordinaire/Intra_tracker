#!/usr/bin/env python3
"""
Exam score HTML scraper for 42 intra.
Handles both:
  - New React-style layout (projects.intra.42.fr/projects/.../projects_users/ID)
  - Old classic layout (team-item / team-mark format)
"""

import json
import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

REQUEST_DELAY = 0.4
SCORE_PATTERN = re.compile(r"\b(0|25|50|75|100)\b")


# ──────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────

def _parse_new_style(soup: BeautifulSoup) -> list[dict]:
    """
    Parse the React / Radix-UI collapsible layout.

    Structure we look for:
      <button ...>                         ← top-level attempt row
        <div class="...justify-between...">
          <a ...>Exam Rank 02</a>
          ...score / icon...
        </div>
      </button>
      <div ...>                            ← sub-attempts
        <div class="...justify-between...">
          <a ...>Exam Rank 02 #1</a>
          ...score / icon...
        </div>
      </div>
    """
    attempts = []

    # Each attempt row: both the summary button row and the inner sub-rows
    # share the pattern "flex flex-row justify-between" with a score at the end.
    rows = soup.find_all("div", class_=lambda c: c and "justify-between" in c)

    for row in rows:
        # Score is the last <p> or <div> that contains only a number
        score_tag = row.find(
            lambda t: t.name in ("p", "div")
            and t.get("class")
            and "text-xs" in " ".join(t.get("class", []))
        )
        if not score_tag:
            continue

        raw = score_tag.get_text(separator=" ", strip=True)
        m = SCORE_PATTERN.search(raw)
        if not m:
            continue
        score = int(m.group())

        # Passed? green-500 check = pass, red-500 x = fail
        passed: Optional[bool] = None
        if score_tag.find(class_=lambda c: c and "green-500" in c):
            passed = True
        elif score_tag.find(class_=lambda c: c and "red-500" in c):
            passed = False

        # Attempt label (e.g. "Exam Rank 02 #1")
        link = row.find("a")
        label = link.get_text(strip=True) if link else ""

        # Occurrence number from label (#N) or default to None
        occ_m = re.search(r"#(\d+)", label)
        occurrence = int(occ_m.group(1)) if occ_m else None

        attempts.append(
            {"occurrence": occurrence, "final_mark": score, "passed": passed, "label": label}
        )

    return attempts


def _parse_old_style(soup: BeautifulSoup) -> list[dict]:
    """
    Parse the classic Bootstrap/ERB layout.

    Structure:
      <div class="team-item list-group-item">
        <a class="team-header" href="#team-XXXXXXX">
          <div>
            <span class="team-name">...</span>
            <span class="team-mark pull-right">
              <small class="text-danger">50%</small>   ← or text-success for pass
            </span>
          </div>
        </a>
      </div>
    """
    attempts = []

    for item in soup.find_all("div", class_="team-item"):
        mark_span = item.find("span", class_="team-mark")
        if not mark_span:
            continue

        raw = mark_span.get_text(strip=True).replace("%", "")
        m = SCORE_PATTERN.search(raw)
        if not m:
            continue
        score = int(m.group())

        # text-success → passed, text-danger → failed
        small = mark_span.find("small")
        passed: Optional[bool] = None
        if small:
            cls = " ".join(small.get("class", []))
            if "success" in cls:
                passed = True
            elif "danger" in cls:
                passed = False

        # Team/attempt ID from the collapse href  (#team-XXXXXXX)
        header = item.find("a", class_="team-header")
        team_id = None
        if header:
            href = header.get("href", "")
            tid_m = re.search(r"team-(\d+)", href)
            team_id = int(tid_m.group(1)) if tid_m else None

        attempts.append(
            {"team_id": team_id, "final_mark": score, "passed": passed}
        )

    return attempts


def parse_exam_page(html: str) -> list[dict]:
    """Auto-detect layout and return list of attempt dicts."""
    soup = BeautifulSoup(html, "html.parser")

    # Detect old-style by presence of team-item class
    if soup.find("div", class_="team-item"):
        return _parse_old_style(soup)

    # Otherwise try new React style
    attempts = _parse_new_style(soup)
    return attempts


# ──────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────

def fetch_project_user_page(session: requests.Session, projects_user_id: int) -> Optional[str]:
    """Fetch the per-student project page (new-style URL)."""
    url = f"https://projects.intra.42.fr/projects_users/{projects_user_id}"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log.warning(f"Failed to fetch projects_user {projects_user_id}: {e}")
        return None


def fetch_project_teams_page(
    session: requests.Session, slug: str, student_login: str
) -> Optional[str]:
    """Fetch the classic team listing page for a project/student."""
    url = f"https://projects.intra.42.fr/projects/{slug}/mine"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log.warning(f"Failed to fetch teams page for {student_login}/{slug}: {e}")
        return None


# ──────────────────────────────────────────────
# Integration with existing exam_ranks.json data
# ──────────────────────────────────────────────

EXAM_PROJECTS = {
    "rank_02": "42next-exam-rank-02",
    "rank_03": "42next-exam-rank-03",
    "rank_04": "42next-exam-rank-04",
    "rank_05": "42next-exam-rank-05",
}


def enrich_from_api_data(
    session: requests.Session,
    login: str,
    api_attempts: list[dict],
    rank_key: str,
) -> list[dict]:
    """
    For attempts where final_mark is None/missing, try to fetch the HTML page
    using the projects_user URL embedded in the API response.

    api_attempts: list of attempt dicts already collected from the API,
                  each may contain a 'projects_user_url' key.
    """
    enriched = []
    for attempt in api_attempts:
        if attempt.get("final_mark") is not None:
            enriched.append(attempt)
            continue

        pu_url = attempt.get("projects_user_url")
        if not pu_url:
            enriched.append(attempt)
            continue

        log.debug(f"  HTML fallback for {login} {rank_key}: {pu_url}")
        try:
            r = session.get(pu_url, timeout=15)
            r.raise_for_status()
            parsed = parse_exam_page(r.text)
            if parsed:
                # Use the first matching attempt score
                attempt = {**attempt, "final_mark": parsed[0]["final_mark"],
                           "passed": parsed[0].get("passed")}
        except Exception as e:
            log.warning(f"HTML fallback failed for {login}: {e}")

        enriched.append(attempt)
        time.sleep(REQUEST_DELAY)

    return enriched


# ──────────────────────────────────────────────
# Standalone scrape from HTML file / URL
# ──────────────────────────────────────────────

def scrape_student_html(
    session: requests.Session,
    login: str,
    projects_user_ids: dict[str, int],   # rank_key → projects_user_id
) -> dict[str, list[dict]]:
    """
    Fetch and parse HTML exam pages for one student.
    projects_user_ids comes from the API (field 'id' in projects_users response).

    Returns: { "rank_02": [{"occurrence": 1, "final_mark": 50, "passed": False}, ...], ... }
    """
    results: dict[str, list[dict]] = {}

    for rank_key, pu_id in projects_user_ids.items():
        url = f"https://projects.intra.42.fr/projects_users/{pu_id}"
        log.info(f"  {login} / {rank_key}: {url}")

        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"  Fetch error: {e}")
            continue

        attempts = parse_exam_page(r.text)
        if attempts:
            results[rank_key] = attempts
            scores = [a["final_mark"] for a in attempts]
            log.info(f"    → scores: {scores}")
        else:
            log.info(f"    → no attempts found in HTML")

        time.sleep(REQUEST_DELAY)

    return results


# ──────────────────────────────────────────────
# Quick test / demo
# ──────────────────────────────────────────────

def _demo_parse():
    """Parse the two HTML snippets from the task description."""
    new_html = """
    <div><div data-state="open" class="w-full"><button type="button" aria-controls="radix-:r4r:" aria-expanded="true" data-state="open" class="w-full"><div class="flex flex-row justify-between hover:bg-gray-300 p-2"><div class="flex flex-row gap-1"><a class="text-legacy-main hover:underline" href="https://projects.intra.42.fr/projects/42next-exam-rank-02/projects_users/4820554" target="_blank" rel="noreferrer">Exam Rank 02</a>about 1 month ago</div><div class="text-xs flex flex-row items-center"><div class="text-green-500"> <svg></svg> </div>100</div></div></button><div data-state="open" id="radix-:r4r:"><div class="flex flex-col gap-2 border-l-2 pl-2 py-2"><div class="flex flex-row justify-between text-gray-400 hover:text-black hover:bg-gray-300 p-2"><p><a class="text-xs hover:underline" href="https://projects.intra.42.fr/projects/42next-exam-rank-02" target="_blank" rel="noreferrer">Exam Rank 02 #1</a> about 1 month ago</p><p class="text-xs flex flex-row items-center"><div class="text-green-500"> <svg></svg> </div>100</p></div><div class="flex flex-row justify-between text-gray-400 hover:text-black hover:bg-gray-300 p-2"><p><a class="text-xs hover:underline" href="https://projects.intra.42.fr/projects/42next-exam-rank-02" target="_blank" rel="noreferrer">Exam Rank 02 #0</a> about 2 months ago</p><p class="text-xs flex flex-row items-center"><div class="text-red-500"> <svg></svg> </div>50</p></div></div></div></div></div>
    """

    old_html = """
    <div class="team-item list-group-item">
    <a class="team-header" data-toggle="collapse" href="#team-7290585" aria-expanded="true">
    <div>
    <span class="team-name">itemlali's group</span>
    <span class="team-mark pull-right"><small class="text-danger">50%</small></span>
    </div>
    </a>
    </div>
    """

    print("=== New-style HTML ===")
    for a in parse_exam_page(new_html):
        print(" ", a)

    print("\n=== Old-style HTML ===")
    for a in parse_exam_page(old_html):
        print(" ", a)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    _demo_parse()