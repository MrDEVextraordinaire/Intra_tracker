#!/usr/bin/env python3
"""Quick test: generate full ranking DataFrame."""
import json
import os
os.environ["MPLCONFIGDIR"] = "/goinfre/matplotlib_cache"

import pandas as pd

PASS_SCORE = 100
TIER1_CEIL = 100.0
TIER2_CEIL = 69.99
ATTEMPT_DECAY = 0.78
GAMBLE_DECAY = 0.82
QUALITY_FLOOR = 0.45
TIER1_DECAY = 0.90
TIER2_WEEK_P = 0.10
STAGNATION_DELTA = 15
STAGNATION_WEIGHT = 0.5
RANK_KEYS = ["rank_02", "rank_03", "rank_04", "rank_05"]
PISCINE = ['C Piscine Exam 00', 'C Piscine Exam 01', 'C Piscine Exam 02', 'C Piscine Final Exam']
PW = {'C Piscine Exam 00': 1, 'C Piscine Exam 01': 2, 'C Piscine Exam 02': 4, 'C Piscine Final Exam': 12}
RANK_WEIGHTS = {"rank_02": 1.0, "rank_03": 1.5, "rank_04": 2.0, "rank_05": 3.0, "rank_06": 5.0}
PISCINE_MAP = {'C Piscine Exam 00': 'c-piscine-exam-00', 'C Piscine Exam 01': 'c-piscine-exam-01', 'C Piscine Exam 02': 'c-piscine-exam-02', 'C Piscine Final Exam': 'c-piscine-final-exam'}
STREAK_STEP = 0.04
STREAK_T2_FACTOR = 0.4
MAX_STREAK_BONUS = 0.25
GAP_FLOOR = 0.30
MAX_GAP_WEEKS = 4

from datetime import datetime

def get_exam_datetime(date_str):
    return datetime.fromisoformat(date_str).replace(hour=10, minute=0, second=0, microsecond=0)

def score_single_exam(occurrences, earliest_dt=None):
    if not occurrences: return None
    occ = sorted(occurrences, key=lambda x: x["occurrence"])
    pass_idx = next((i for i, o in enumerate(occ) if o.get("score") and o.get("score") >= PASS_SCORE), None)
    if pass_idx is None: return None
    passing_occ, n_attempts, prior = occ[pass_idx], pass_idx + 1, occ[:pass_idx]
    prior_scores = [o["score"] for o in prior]
    exam_dt, weeks_late = None, 0
    if passing_occ.get("date"):
        try:
            exam_dt = get_exam_datetime(passing_occ["date"])
            if earliest_dt and exam_dt: weeks_late = max(0, (exam_dt - earliest_dt).days // 7)
        except: pass
    if n_attempts == 1: return {"tier": 1, "final": round(TIER1_CEIL * (TIER1_DECAY ** weeks_late), 4), "weeks_late": weeks_late, "exam_dt": exam_dt, "n_attempts": 1}
    attempt_factor = ATTEMPT_DECAY ** (n_attempts - 1)
    drops = sum(1 for i in range(len(prior_scores)-1) if prior_scores[i+1] < prior_scores[i])
    stagnation = sum(1 for i in range(len(prior_scores)-1) if 0 <= prior_scores[i+1]-prior_scores[i] < STAGNATION_DELTA)
    gamble_factor = GAMBLE_DECAY ** (drops + stagnation * STAGNATION_WEIGHT)
    if prior_scores:
        weights = [i+1 for i in range(len(prior_scores))]
        weighted_avg = sum(s*w for s,w in zip(prior_scores, weights)) / (sum(weights) * 100)
        best_prior = max(prior_scores) / 100
        prior_quality = 0.6 * best_prior + 0.4 * weighted_avg
        quality_factor = QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * prior_quality
    else: quality_factor = 1.0
    base = TIER2_CEIL * attempt_factor * gamble_factor * quality_factor
    delay_multiplier = 1.0 / (1.0 + weeks_late * TIER2_WEEK_P)
    return {"tier": 2, "final": round(base * delay_multiplier, 4), "weeks_late": weeks_late, "exam_dt": exam_dt, "n_attempts": n_attempts, "drops": drops, "stagnation": stagnation}

def compute_streak_multiplier(rank_results):
    streak, multiplier, last_pass_dt = 0, 1.0, None
    for k in RANK_KEYS:
        r = rank_results.get(k)
        if r is None: continue
        gap_factor = 1.0
        if last_pass_dt and r["exam_dt"]:
            gap_weeks = (r["exam_dt"] - last_pass_dt).days / 7
            gap_factor = max(GAP_FLOOR, 1.0 - ((gap_weeks - 1) / (MAX_GAP_WEEKS - 1)) * (1.0 - GAP_FLOOR))
        if r["tier"] == 1:
            streak += 1
            multiplier += STREAK_STEP * streak * gap_factor
        else:
            multiplier += STREAK_STEP * STREAK_T2_FACTOR * gap_factor
            streak = 0
        last_pass_dt = r["exam_dt"]
    return min(multiplier, 1.0 + MAX_STREAK_BONUS)

# Load data
with open("data/exam_ranks.json") as f: raw = json.load(f)
with open("data/students.json") as f: students = json.load(f)

# Find earliest dates per cohort
earliest_2024 = {k: None for k in RANK_KEYS}
earliest_2025 = {k: None for k in RANK_KEYS}
for login, data in raw.items():
    pool_year = students.get(login, {}).get("pool_year")
    for k in RANK_KEYS:
        for o in data.get("exam_ranks", {}).get(k, []):
            if o.get("score") and o.get("score") >= PASS_SCORE and o.get("date"):
                try:
                    dt = get_exam_datetime(o["date"])
                    if pool_year == "2024":
                        if earliest_2024[k] is None or dt < earliest_2024[k]: earliest_2024[k] = dt
                    elif pool_year == "2025":
                        if earliest_2025[k] is None or dt < earliest_2025[k]: earliest_2025[k] = dt
                except: pass

def get_earliest(login, k):
    pool_year = students.get(login, {}).get("pool_year", "2025")
    return earliest_2024.get(k) if pool_year == "2024" else earliest_2025.get(k)

# Build rows
rows = []
for login, data in raw.items():
    pool_year = students.get(login, {}).get("pool_year", "Unknown")
    row = {"user": login, "pool_year": pool_year}
    
    # Piscine
    piscine_raw = data.get("piscine_exams", {})
    for k in PISCINE:
        slug = PISCINE_MAP.get(k)
        if slug in piscine_raw:
            scores = [a.get('score', 0) for a in piscine_raw[slug] if a.get('score')]
            row[k] = max(scores) if scores else None
        else: row[k] = None
    valid_p = [k for k in PISCINE if row[k] is not None]
    if valid_p:
        row["piscine_avg"] = round(sum(row[k] * PW[k] for k in valid_p) / sum(PW[k] for k in valid_p), 4)
    else: row["piscine_avg"] = None
    
    # Ranks
    exam_ranks = data.get("exam_ranks", {})
    rank_results = {}
    for k in RANK_KEYS:
        r = score_single_exam(exam_ranks.get(k), get_earliest(login, k)) if exam_ranks.get(k) else None
        rank_results[k] = r
        if r:
            row[f"{k}_score"] = r["final"]
            row[f"{k}_tier"] = r["tier"]
            row[f"{k}_attempts"] = r["n_attempts"]
            row[f"{k}_weeks_late"] = r.get("weeks_late")
            row[f"{k}_drops"] = r.get("drops")
            row[f"{k}_stagnation"] = r.get("stagnation")
    
    # Rank aggregate
    valid_ranks = [k for k in RANK_KEYS if rank_results.get(k)]
    if valid_ranks:
        row["rank_score_raw"] = round(sum(rank_results[k]["final"] * RANK_WEIGHTS.get(k, 1) for k in valid_ranks) / sum(RANK_WEIGHTS.get(k, 1) for k in valid_ranks), 4)
    else: row["rank_score_raw"] = None
    
    streak_mult = compute_streak_multiplier(rank_results)
    row["streak_mult"] = round(streak_mult, 4)
    row["rank_score"] = round(row["rank_score_raw"] * streak_mult, 4) if row["rank_score_raw"] else None
    
    # Final
    p, r = row.get("piscine_avg"), row.get("rank_score")
    if p and r: row["final_score"] = round(p * 0.6 + min(r, 125) * 0.4, 4)
    elif p: row["final_score"] = round(p, 4)
    elif r: row["final_score"] = round(min(r, 125), 4)
    else: row["final_score"] = None
    
    rows.append(row)

df = pd.DataFrame(rows).set_index("user")
df.sort_values("final_score", ascending=False, inplace=True, na_position="last")

print(f"DataFrame: {df.shape[0]} users, {df.shape[1]} columns")
print(f"Pool years: {df['pool_year'].value_counts().to_dict()}")
print()
print("Top 15:")
print(df[['pool_year', 'piscine_avg', 'rank_score', 'final_score']].head(15))
