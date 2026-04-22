# ── Tier boundaries & Penalty Knobs ──────────────────────────────────────────
PASS_SCORE    = 100
TIER1_FLOOR   = 70.0
TIER1_CEIL    = 100.0
TIER2_CEIL    = 69.99

ATTEMPT_DECAY = 0.78
GAMBLE_DECAY  = 0.82
QUALITY_FLOOR = 0.45
WEEK_PENALTY  = 10.0  # Points lost per week delayed (Tier 1)
TIER2_WEEK_P  = 0.10  # 10% penalty per week delayed (Tier 2)

def score_single_exam(occurrences: list[dict], earliest_cohort_dt: datetime | None) -> dict | None:
    if not occurrences:
        return None

    occ = sorted(occurrences, key=lambda x: x["occurrence"])
    pass_idx = next((i for i, o in enumerate(occ) if o.get("score", 0) >= PASS_SCORE), None)
    
    if pass_idx is None:
        return None

    passing_occ  = occ[pass_idx]
    n_attempts   = pass_idx + 1
    prior        = occ[:pass_idx]
    prior_scores = [o["score"] for o in prior]

    # Resolve date
    exam_dt = None
    date_str = passing_occ.get("date")
    if date_str:
        try:
            exam_dt = get_exam_datetime(date_str)
        except ValueError as e:
            print(f"[WARN] date parse failed: {e}")

    # Calculate Weeks Late
    weeks_late = 0
    if earliest_cohort_dt and exam_dt:
        weeks_late = max(0, (exam_dt - earliest_cohort_dt).days // 7)

    # ── TIER 1 ────────────────────────────────────────────────────────────────
    if n_attempts == 1:
        final = max(TIER1_FLOOR, TIER1_CEIL - (weeks_late * WEEK_PENALTY))
        return {
            "tier": 1,
            "final": round(final, 4),
            "weeks_late": weeks_late,
            "exam_dt": exam_dt,
            "n_attempts": 1,
        }

    # ── TIER 2 ────────────────────────────────────────────────────────────────
    attempt_factor = ATTEMPT_DECAY ** (n_attempts - 1)

    drops = sum(1 for i in range(len(prior_scores) - 1) if prior_scores[i + 1] < prior_scores[i])
    gamble_factor = GAMBLE_DECAY ** drops

    if prior_scores:
        prior_quality  = sum(prior_scores) / (len(prior_scores) * PASS_SCORE)
        quality_factor = QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * prior_quality
    else:
        quality_factor = 1.0

    base = TIER2_CEIL * attempt_factor * gamble_factor * quality_factor
    
    # Apply delay penalty to Tier 2 (caps at 30% reduction for 3+ weeks late)
    delay_multiplier = max(0.70, 1.0 - (weeks_late * TIER2_WEEK_P))
    final = min(base * delay_multiplier, TIER2_CEIL)

    return {
        "tier": 2,
        "final": round(final, 4),
        "weeks_late": weeks_late,
        "exam_dt": exam_dt,
        "n_attempts": n_attempts,
    }

# ── Updated DataFrame Builder Logic ──────────────────────────────────────────
# Inside build_df(), before processing users, find the earliest dates:

def build_df(raw: dict | None = None, users: list | None = None) -> pd.DataFrame:
    # ... [JSON load setup] ...

    # 1. Find the earliest cohort passing date for each rank
    earliest_dates = {k: None for k in RANK_KEYS}
    for data in raw.values():
        exam_ranks = data.get("exam_ranks", {})
        for k in RANK_KEYS:
            occ = exam_ranks.get(k, [])
            for o in occ:
                if o.get("score", 0) >= PASS_SCORE and o.get("date"):
                    dt = get_exam_datetime(o["date"])
                    if earliest_dates[k] is None or dt < earliest_dates[k]:
                        earliest_dates[k] = dt

    # 2. Process users using the earliest_dates
    # ... [Loop through users] ...
        for k in RANK_KEYS:
            occurrences = exam_ranks.get(k)
            # Pass earliest_dates[k] to score_single_exam
            r = score_single_exam(occurrences, earliest_dates[k]) if occurrences else None
            rank_results[k] = r
            # ... [Rest of assignments]
