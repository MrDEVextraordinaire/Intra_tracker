#!/usr/bin/env python3
"""Quick test: generate ranking table for specific students."""

import json
from datetime import datetime

# Config (copied from student_ranker.py)
PASS_SCORE = 100
TIER1_FLOOR = 70.0
TIER1_CEIL = 100.0
TIER2_CEIL = 69.99
ATTEMPT_DECAY = 0.78
GAMBLE_DECAY = 0.82
QUALITY_FLOOR = 0.45
WEEK_PENALTY = 10.0
TIER2_WEEK_P = 0.10
RANK_KEYS = ["rank_02", "rank_03", "rank_04", "rank_05"]
EXAM_HOUR = 10

def get_exam_datetime(date_str):
    d = datetime.fromisoformat(date_str)
    return d.replace(hour=EXAM_HOUR, minute=0, second=0, microsecond=0)

def score_single_exam(occurrences, earliest_dt=None):
    if not occurrences:
        return None
    occ = sorted(occurrences, key=lambda x: x["occurrence"])
    pass_idx = next((i for i, o in enumerate(occ) if o.get("score") and o.get("score") >= PASS_SCORE), None)
    if pass_idx is None:
        return None
    passing_occ = occ[pass_idx]
    n_attempts = pass_idx + 1
    prior = occ[:pass_idx]
    prior_scores = [o["score"] for o in prior]
    
    exam_dt = None
    weeks_late = 0
    date_str = passing_occ.get("date")
    if date_str:
        try:
            exam_dt = get_exam_datetime(date_str)
            if earliest_dt and exam_dt:
                weeks_late = max(0, (exam_dt - earliest_dt).days // 7)
        except:
            pass
    
    if n_attempts == 1:
        final = max(TIER1_FLOOR, TIER1_CEIL - (weeks_late * WEEK_PENALTY))
        return {"tier": 1, "final": round(final, 2), "weeks_late": weeks_late, "exam_dt": exam_dt, "n_attempts": 1}
    
    attempt_factor = ATTEMPT_DECAY ** (n_attempts - 1)
    drops = sum(1 for i in range(len(prior_scores) - 1) if prior_scores[i+1] < prior_scores[i])
    gamble_factor = GAMBLE_DECAY ** drops
    
    if prior_scores:
        prior_quality = sum(prior_scores) / (len(prior_scores) * PASS_SCORE)
        quality_factor = QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * prior_quality
    else:
        quality_factor = 1.0
    
    base = TIER2_CEIL * attempt_factor * gamble_factor * quality_factor
    delay_multiplier = max(0.70, 1.0 - (weeks_late * TIER2_WEEK_P))
    final = min(base * delay_multiplier, TIER2_CEIL)
    
    return {"tier": 2, "final": round(final, 2), "weeks_late": weeks_late, "exam_dt": exam_dt, "n_attempts": n_attempts}

# Load data
with open("data/exam_ranks.json") as f:
    raw = json.load(f)
with open("data/students.json") as f:
    students = json.load(f)

# Find earliest dates per cohort
earliest_dates_2024 = {k: None for k in RANK_KEYS}
earliest_dates_2025 = {k: None for k in RANK_KEYS}
for login, data in raw.items():
    pool_year = students.get(login, {}).get("pool_year")
    for k in RANK_KEYS:
        occ = data.get("exam_ranks", {}).get(k, [])
        for o in occ:
            score = o.get("score")
            if score and score >= PASS_SCORE and o.get("date"):
                try:
                    dt = get_exam_datetime(o["date"])
                    if pool_year == "2024":
                        if earliest_dates_2024[k] is None or dt < earliest_dates_2024[k]:
                            earliest_dates_2024[k] = dt
                    elif pool_year == "2025":
                        if earliest_dates_2025[k] is None or dt < earliest_dates_2025[k]:
                            earliest_dates_2025[k] = dt
                except:
                    pass

def get_earliest(login, k):
    pool_year = students.get(login, {}).get("pool_year")
    if pool_year == "2024":
        return earliest_dates_2024.get(k)
    return earliest_dates_2025.get(k)

print("Earliest dates (2024):", {k: v.date() if v else None for k, v in earliest_dates_2024.items()})
print("Earliest dates (2025):", {k: v.date() if v else None for k, v in earliest_dates_2025.items()})
print()

# Target students
targets = ["kraghib", "blidrissi", "mjabri", "gomar", "itemlali", "yramouch", "zael-ghm", "smakkass"]

print(f"{'Login':<12} {'Cohort':>6} {'R02 Score':>10} {'R02 Tier':>8} {'R02 Late':>8} {'R03 Score':>10} {'R03 Tier':>8} {'R03 Late':>8}")
print("-" * 90)

for login in targets:
    if login not in raw:
        print(f"{login}: NOT FOUND")
        continue
    
    data = raw[login]
    exam_ranks = data.get("exam_ranks", {})
    pool_year = students.get(login, {}).get("pool_year", "2025")
    
    row = [login, pool_year]
    for k in ["rank_02", "rank_03"]:
        occ = exam_ranks.get(k)
        if occ:
            r = score_single_exam(occ, get_earliest(login, k))
            if r:
                row.extend([r["final"], r["tier"], r["weeks_late"]])
            else:
                row.extend(["-", "-", "-"])
        else:
            row.extend(["-", "-", "-"])
    
    print(f"{row[0]:<12} {str(row[1]):>10} {str(row[2]):>8} {str(row[3]):>8} {str(row[4]):>10} {str(row[5]):>8} {str(row[6]):>8}")
