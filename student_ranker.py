import os
os.environ["MPLCONFIGDIR"] = "/goinfre/matplotlib_cache"

import json
import time
import threading
from datetime import datetime, timedelta

import pandas as pd
import dtale
import dtale.global_state


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

RESULTS_FILE = "data/exam_ranks.json"
POLL_SECONDS = 5

# ── Piscine ───────────────────────────────────────────────────────────────────
PISCINE = [
    'C Piscine Exam 00',
    'C Piscine Exam 01',
    'C Piscine Exam 02',
    'C Piscine Final Exam',
]
# Exponential: Final ~63%, Exam02 ~21%, 01 ~10%, 00 ~5%
PW = {
    'C Piscine Exam 00':    1,
    'C Piscine Exam 01':    2,
    'C Piscine Exam 02':    4,
    'C Piscine Final Exam': 12,
}

# ── Rank exams ────────────────────────────────────────────────────────────────
RANK_KEYS = ["rank_02", "rank_03", "rank_04", "rank_05", "rank_06"]

# Higher ranks weighted more in the aggregate
RANK_WEIGHTS = {
    "rank_02": 1.0,
    "rank_03": 1.5,
    "rank_04": 2.0,
    "rank_05": 3.0,
    "rank_06": 5.0,
}

# ── Tier boundaries ───────────────────────────────────────────────────────────
# Tier 1 (first attempt): [70, 100]   — weeks_late penalty fills the 30-point band
# Tier 2 (retries):       [0,  69.99] — penalties fill the range, hard cap
PASS_SCORE  = 100
TIER1_FLOOR = 70.0
TIER1_CEIL  = 100.0
TIER2_CEIL  = 69.99

# ── Tier 2 penalty knobs ──────────────────────────────────────────────────────
# attempt_factor:  0.78^(n-1)  →  2 att=0.78, 3=0.61, 4=0.47, 5=0.37
# gamble_factor:   0.82^(drops + stagnation × 0.5)
#                 drops = score decreases, stagnation = improvement < 15 points
# quality_factor:  [0.45, 1.0] →  wider range than old [0.70, 1.0]
# tier1_decay:    0.90^weeks_late - exponential decay for tier 1
# tier2_week_p:   hyperbolic decay: 1 / (1 + weeks × 0.10)
ATTEMPT_DECAY = 0.78
GAMBLE_DECAY  = 0.82
QUALITY_FLOOR = 0.45
TIER1_DECAY   = 0.90
TIER2_WEEK_P  = 0.10
STAGNATION_DELTA = 15    # points of improvement below which = stagnation
STAGNATION_WEIGHT = 0.5  # stagnation counts at half the rate of drops

# ── Streak (multiplicative, not additive) ─────────────────────────────────────
# Applied to rank_score as a multiplier in [1.0, 1.25]
# Tier 1 pass: full nudge = STREAK_STEP × streak_length × gap_factor
# Tier 2 pass: partial nudge = STREAK_STEP × 0.4 × streak_length × gap_factor
# No attempt: preserve streak (neutral)
STREAK_STEP      = 0.04
STREAK_T2_FACTOR = 0.4    # Tier 2 gets 40% of full streak bonus
MAX_STREAK_BONUS = 0.25
GAP_FLOOR        = 0.30   # min gap factor (waiting 4+ weeks = 30% of potential)
MAX_GAP_WEEKS    = 4

# ── Final score weights ───────────────────────────────────────────────────────
PISCINE_W = 0.40
RANK_W    = 0.60

# ── Calendar ──────────────────────────────────────────────────────────────────
EXAM_WEEKDAY  = 2    # Wednesday
EXAM_HOUR     = 10   # 10:00 — exam starts


# ─────────────────────────────────────────────────────────────────────────────
# CALENDAR HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_exam_datetime(date_str: str) -> datetime:
    """Parse YYYY-MM-DD exam date and pin to Wednesday 10:00."""
    d = datetime.fromisoformat(date_str)
    return d.replace(hour=10, minute=0, second=0, microsecond=0)


# ─────────────────────────────────────────────────────────────────────────────
# PER-EXAM SCORING
# ─────────────────────────────────────────────────────────────────────────────


def score_single_exam(occurrences: list[dict], earliest_cohort_dt: datetime | None = None) -> dict | None:
    """
    Score one rank exam for one user.

    Each occurrence dict expected keys:
        occurrence        int    0-based attempt index
        date              str    "YYYY-MM-DD" Wednesday the exam was taken
        score             int    score on that attempt

    Returns a result dict or None if PASS_SCORE was never reached.

    ── TIER 1: first attempt pass ────────────────────────────────────────────
    Score range [TIER1_FLOOR, TIER1_CEIL] = [70.0, 100.0].
    Weeks_late (how late they took exam vs earliest cohort) fills the 30-point band.
    No retrier ever reaches 70.0 due to TIER2_CEIL = 69.99 hard cap.

    ── TIER 2: passed after retries ─────────────────────────────────────────
    Score range [0, TIER2_CEIL] = [0, 69.99].
    Three compounding penalty factors (all multiplicative):

      attempt_factor  = ATTEMPT_DECAY ^ (n-1)
                        More attempts = exponentially lower score.
                        Steeper than before: 0.78 vs old 0.85.

      gamble_factor   = GAMBLE_DECAY ^ drops
                        Each score DROP in prior attempts signals gambling.
                        Steeper: 0.82 vs old 0.93.
                        e.g. [75, 50, 100]: 1 drop → ×0.82
                             [75, 50, 40, 100]: 2 drops → ×0.67

      quality_factor  = QUALITY_FLOOR + (1 - QUALITY_FLOOR) * prior_quality
                        Range [0.45, 1.0] vs old [0.70, 1.0].
                        Wider floor gives much more separation at the low end.
                        All-25s prior: factor=0.56  All-75s prior: factor=0.84

    Weeks_late penalty applied to tier 2 as percentage reduction.
    """
    if not occurrences:
        return None

    occ = sorted(occurrences, key=lambda x: x["occurrence"])

    pass_idx = next(
        (i for i, o in enumerate(occ) if o.get("score") and o.get("score") >= PASS_SCORE),
        None,
    )
    if pass_idx is None:
        return None  # never passed

    passing_occ  = occ[pass_idx]
    n_attempts   = pass_idx + 1
    prior        = occ[:pass_idx]
    prior_scores = [o["score"] for o in prior]

    # ── Resolve exam datetime and weeks_late ─────────────────────────────────
    exam_dt = None
    weeks_late = 0

    date_str = passing_occ.get("date")
    if date_str:
        try:
            exam_dt = get_exam_datetime(date_str)
            if earliest_cohort_dt and exam_dt:
                weeks_late = max(0, (exam_dt - earliest_cohort_dt).days // 7)
        except ValueError as e:
            print(f"[WARN] date parse failed: {e}")

    # ── TIER 1 ────────────────────────────────────────────────────────────────
    if n_attempts == 1:
        final = TIER1_CEIL * (TIER1_DECAY ** weeks_late)
        return {
            "tier":        1,
            "final":       round(final, 4),
            "weeks_late":  weeks_late,
            "exam_dt":     exam_dt,
            "n_attempts":  1,
        }

    # ── TIER 2 ───────────────────────────────────────────────────────────────

    # 1. Attempt decay
    attempt_factor = ATTEMPT_DECAY ** (n_attempts - 1)

    # 2. Gamble penalty: drops + stagnation as continuous signal
    # drops = score decreases, stagnation = improvement < STAGNATION_DELTA
    drops = sum(
        1 for i in range(len(prior_scores) - 1)
        if prior_scores[i + 1] < prior_scores[i]
    )
    stagnation = sum(
        1 for i in range(len(prior_scores) - 1)
        if 0 <= prior_scores[i + 1] - prior_scores[i] < STAGNATION_DELTA
    )
    gamble_factor = GAMBLE_DECAY ** (drops + stagnation * STAGNATION_WEIGHT)

    # 3. Prior quality: weighted recency (recent attempts matter more)
    # Blend: 0.6 × max(prior) + 0.4 × weighted_avg
    if prior_scores:
        weights = [i + 1 for i in range(len(prior_scores))]
        weighted_avg = sum(s * w for s, w in zip(prior_scores, weights)) / (sum(weights) * PASS_SCORE)
        best_prior = max(prior_scores) / PASS_SCORE
        prior_quality = 0.6 * best_prior + 0.4 * weighted_avg
        quality_factor = QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * prior_quality
    else:
        quality_factor = 1.0

    # Combine against tier 2 ceiling
    base = TIER2_CEIL * attempt_factor * gamble_factor * quality_factor

    # Apply weeks_late penalty to Tier 2 (hyperbolic decay - no hard floor)
    delay_multiplier = 1.0 / (1.0 + weeks_late * TIER2_WEEK_P)
    final = base * delay_multiplier

    return {
        "tier":           2,
        "final":          round(final, 4),
        "base":           round(base, 4),
        "weeks_late":     weeks_late,
        "exam_dt":        exam_dt,
        "n_attempts":     n_attempts,
        "prior_scores":   prior_scores,
        "drops":          drops,
        "attempt_factor": round(attempt_factor, 4),
        "gamble_factor":  round(gamble_factor, 4),
        "quality_factor": round(quality_factor, 4),
    }

    # ── TIER 2 ────────────────────────────────────────────────────────────────

    # 1. Attempt decay
    attempt_factor = ATTEMPT_DECAY ** (n_attempts - 1)

    # 2. Gamble penalty: count score drops in the sequence of prior attempts
    drops = sum(
        1 for i in range(len(prior_scores) - 1)
        if prior_scores[i + 1] < prior_scores[i]
    )
    gamble_factor = GAMBLE_DECAY ** drops

    # 3. Prior quality: how high were prior scores on average?
    if prior_scores:
        prior_quality  = sum(prior_scores) / (len(prior_scores) * PASS_SCORE)
        quality_factor = QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * prior_quality
    else:
        quality_factor = 1.0

    # Combine against tier 2 ceiling
    base = TIER2_CEIL * attempt_factor * gamble_factor * quality_factor

    # Eagerness nudges within tier 2 (25% weight)
    final = base * (1.0 - EAGER_WEIGHT) + base * EAGER_WEIGHT * eagerness
    final = min(final, TIER2_CEIL)  # hard cap — never reaches tier 1

    return {
        "tier":           2,
        "final":          round(final, 4),
        "base":           round(base, 4),
        "eagerness":      round(eagerness, 4),
        "exam_dt":        exam_dt,
        "n_attempts":     n_attempts,
        "prior_scores":   prior_scores,
        "drops":          drops,
        "attempt_factor": round(attempt_factor, 4),
        "gamble_factor":  round(gamble_factor, 4),
        "quality_factor": round(quality_factor, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STREAK MULTIPLIER
# ─────────────────────────────────────────────────────────────────────────────

def compute_streak_multiplier(rank_results: dict) -> float:
    """
    Returns a multiplier in [1.0, 1.0 + MAX_STREAK_BONUS] applied to rank_score.

    Every rank exam contributes to the multiplier:
    - Tier 1 pass: full nudge = STREAK_STEP × streak_length × gap_factor
    - Tier 2 pass: partial nudge = STREAK_STEP × 0.4 × streak_length × gap_factor
    - No attempt: preserve streak (neutral, no nudge)

    gap_factor measures how quickly the student moved on to the next rank:
      1 week  (immediate) → gap_factor = 1.0 (full bonus)
      4+ weeks            → gap_factor = GAP_FLOOR = 0.30

    Ranks not yet attempted do NOT break the streak.
    Tier 2 passes still add partial bonus but reset streak length.

    Examples (all 1 week apart):
      R02✓ (T1)         → multiplier = 1.04
      R02✓(T1) R03✓(T1)  → multiplier = 1.12
      R02✓(T1) R03✓(T2)  → multiplier = 1.04 + 1.04×0.4 = 1.456 → capped at 1.25
    """
    streak       = 0
    multiplier   = 1.0
    last_pass_dt = None

    for k in RANK_KEYS:
        r = rank_results.get(k)
        if r is None:
            continue  # not attempted — preserve streak

        # Calculate gap factor
        gap_factor = 1.0
        if last_pass_dt is not None and r["exam_dt"] is not None:
            gap_weeks = (r["exam_dt"] - last_pass_dt).days / 7
            gap_factor = max(
                GAP_FLOOR,
                1.0 - ((gap_weeks - 1) / (MAX_GAP_WEEKS - 1)) * (1.0 - GAP_FLOOR)
            )

        if r["tier"] == 1:
            streak += 1
            multiplier += STREAK_STEP * streak * gap_factor
        else:
            # Tier 2: partial nudge, reset streak
            multiplier += STREAK_STEP * STREAK_T2_FACTOR * gap_factor
            streak = 0

        last_pass_dt = r["exam_dt"]

    return min(multiplier, 1.0 + MAX_STREAK_BONUS)


# ─────────────────────────────────────────────────────────────────────────────
# DATAFRAME BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_df(raw: dict | None = None, users: list | None = None) -> pd.DataFrame:
    if raw is None:
        with open(RESULTS_FILE) as f:
            raw = json.load(f)
    if users is None:
        users = list(raw.keys())

    # Load students for cohort info
    with open("data/students.json") as f:
        students = json.load(f)

    # Find earliest cohort passing date for each rank per cohort
    earliest_dates_2024 = {k: None for k in RANK_KEYS}
    earliest_dates_2025 = {k: None for k in RANK_KEYS}
    for login, data in raw.items():
        pool_year = students.get(login, {}).get("pool_year")
        exam_ranks = data.get("exam_ranks", {})
        for k in RANK_KEYS:
            occ = exam_ranks.get(k, [])
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
                    except ValueError:
                        pass

    def get_earliest(login, k):
        pool_year = students.get(login, {}).get("pool_year", "2025")
        if pool_year == "2024":
            return earliest_dates_2024.get(k)
        return earliest_dates_2025.get(k)

    rows = []

    for login in users:
        data = raw[login]
        pool_year = students.get(login, {}).get("pool_year", "Unknown")
        row  = {"user": login, "pool_year": pool_year}

        # ── Piscine ───────────────────────────────────────────────────────────
        piscine_raw = data.get("piscine_exams", {})
        
        # Map PISCINE names to slug keys in new format
        piscine_map = {
            'C Piscine Exam 00': 'c-piscine-exam-00',
            'C Piscine Exam 01': 'c-piscine-exam-01',
            'C Piscine Exam 02': 'c-piscine-exam-02',
            'C Piscine Final Exam': 'c-piscine-final-exam',
        }
        
        for k in PISCINE:
            slug = piscine_map.get(k)
            if slug and slug in piscine_raw:
                attempts = piscine_raw[slug]
                # Get highest score
                scores = [a.get('score', 0) for a in attempts if a.get('score')]
                row[k] = max(scores) if scores else None
            else:
                row[k] = None

        valid_p = [k for k in PISCINE if row[k] is not None]
        if valid_p:
            sw   = sum(PW[k] for k in valid_p)
            wsum = sum(row[k] * PW[k] for k in valid_p)
            row["piscine_avg"] = round(wsum / sw, 4)
        else:
            row["piscine_avg"] = None

        # ── Rank exams ────────────────────────────────────────────────────────
        exam_ranks   = data.get("exam_ranks", {})
        rank_results = {}

        for k in RANK_KEYS:
            occurrences = exam_ranks.get(k)
            r = score_single_exam(occurrences, get_earliest(login, k)) if occurrences else None
            rank_results[k] = r

            # Per-rank debug columns visible in dtale
            row[f"{k}_score"]          = r["final"]              if r else None
            row[f"{k}_tier"]           = r["tier"]               if r else None
            row[f"{k}_attempts"]       = r["n_attempts"]          if r else None
            row[f"{k}_weeks_late"]    = r.get("weeks_late")      if r else None
            row[f"{k}_drops"]          = r.get("drops")           if r else None
            row[f"{k}_attempt_factor"] = r.get("attempt_factor")  if r else None
            row[f"{k}_gamble_factor"]  = r.get("gamble_factor")   if r else None
            row[f"{k}_quality_factor"] = r.get("quality_factor")  if r else None

        # ── Rank aggregate (difficulty-weighted) ──────────────────────────────
        valid_ranks = [k for k in RANK_KEYS if rank_results.get(k) is not None]
        if valid_ranks:
            rw   = sum(RANK_WEIGHTS[k] for k in valid_ranks)
            wsum = sum(rank_results[k]["final"] * RANK_WEIGHTS[k] for k in valid_ranks)
            row["rank_score_raw"] = round(wsum / rw, 4)
        else:
            row["rank_score_raw"] = None

        # ── Streak multiplier ─────────────────────────────────────────────────
        streak_mult        = compute_streak_multiplier(rank_results)
        row["streak_mult"] = round(streak_mult, 4)
        row["rank_score"]  = (
            round(row["rank_score_raw"] * streak_mult, 4)
            if row["rank_score_raw"] is not None else None
        )

        # ── Final score ───────────────────────────────────────────────────────
        # piscine_avg : [0, 100]
        # rank_score  : [0, ~125] (base [0,100] × max multiplier 1.25)
        # We clamp rank_score to 125 before weighting so the scale stays sane.
        p = row["piscine_avg"]
        r = row["rank_score"]

        if p is not None and r is not None:
            row["final_score"] = round(p * PISCINE_W + min(r, 125.0) * RANK_W, 4)
        elif p is not None:
            row["final_score"] = round(p, 4)
        elif r is not None:
            row["final_score"] = round(min(r, 125.0), 4)
        else:
            row["final_score"] = None

        rows.append(row)

    df = pd.DataFrame(rows).set_index("user")
    
    # Reorder: key scores first
    key_cols = ["pool_year", "piscine_avg", "rank_score_raw", "streak_mult", "rank_score", "final_score"]
    other_cols = [c for c in df.columns if c not in key_cols]
    df = df[key_cols + other_cols]
    
    df.sort_values("final_score", ascending=False, inplace=True, na_position="last")
    return df


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--no-dtale":
        df = build_df()
        print(f"[INIT] DataFrame: {df.shape[0]} users, {df.shape[1]} columns")
        print(f"Pool years: {df['pool_year'].value_counts().to_dict()}")
        print(df[['pool_year', 'final_score']].head(20))
    else:
        print("[INIT] Loading data...")
        df = build_df()
        print(f"[INIT] DataFrame: {df.shape[0]} users, {df.shape[1]} columns")

        dtale.global_state.set_app_settings({"enable_custom_filters": True})
        d = dtale.show(df, ignore_duplicate=True)

        print(f"[INIT] dtale id  : {d._data_id}")
        print(f"[INIT] URL       : {d._url}/dtale/main/{d._data_id}")

        import time
        def poll():
            loop = 0
            while True:
                loop += 1
                try:
                    with open(RESULTS_FILE) as f:
                        raw = json.load(f)
                    current_df = d.data
                    known_users = set(current_df.index.astype(str)) if current_df is not None else set()
                    new_logins = list(set(raw.keys()) - known_users)
                    if new_logins:
                        print(f"[POLL {loop}] New users: {new_logins}")
                        fragment = build_df(raw, new_logins)
                        curr = current_df.reset_index()
                        frag = fragment.reset_index()
                        stale = ["level_0", "index", "Unnamed: 0"]
                        curr.drop(columns=[c for c in stale if c in curr.columns], inplace=True)
                        frag.drop(columns=[c for c in stale if c in frag.columns], inplace=True)
                        updated = pd.concat([curr, frag], ignore_index=True, sort=False)
                        if "user" in updated.columns:
                            updated = updated.set_index("user")
                            updated = updated[~updated.index.duplicated(keep='last')]
                        updated.sort_values("final_score", ascending=False, inplace=True, na_position="last")
                        d.data = updated
                        print(f"[POLL {loop}] Total: {d.data.shape[0]}")
                except Exception as e:
                    print(f"[POLL {loop}] ERROR: {e}")
                time.sleep(POLL_SECONDS)

        t = threading.Thread(target=poll, daemon=True)
        t.start()
        print(f"[INIT] Poll thread alive: {t.is_alive()}")

        input("\nPress Enter to stop...\n")
