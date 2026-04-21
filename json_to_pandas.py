import os
os.environ["MPLCONFIGDIR"] = "/goinfre/itemlali/matplotlib_cache"

import json
import time
import threading
import pandas as pd
import dtale

RESULTS_FILE = "results.json"
POLL_SECONDS = 5

PISCINE = ['C Piscine Exam 00','C Piscine Exam 01','C Piscine Exam 02','C Piscine Final Exam']
PW      = {'C Piscine Exam 00':1,'C Piscine Exam 01':2,'C Piscine Exam 02':3,'C Piscine Final Exam':4}
RANKS   = ['Exam Rank 02','Exam Rank 03','Exam Rank 04','Exam Rank 05','Exam Rank 06']

def build_df(raw=None, users=None):
    if raw is None:
        with open(RESULTS_FILE) as f:
            raw = json.load(f)
    if users is None:
        users = list(raw.keys())
    rows = []
    for user in users:
        exams = raw[user]
        row = {"user": user}
        for k in PISCINE:
            row[k] = exams.get(k, {}).get("final_mark")
        sw   = sum(PW[k] for k in PISCINE if row[k] is not None)
        wsum = sum(row[k]*PW[k] for k in PISCINE if row[k] is not None)
        row["piscine_avg"] = wsum / sw if sw else None
        for k in RANKS:
            ex = exams.get(k)
            row[f"{k} mark"]     = ex["final_mark"] if ex else None
            row[f"{k} attempts"] = ex["attempts"]   if ex else None
        rank_scores = [(row[f"{k} mark"] or 0) / row[f"{k} attempts"] for k in RANKS if row.get(f"{k} attempts")]
        row["rank_score"] = sum(rank_scores) / len(rank_scores) if rank_scores else None
        p, r = row["piscine_avg"], row["rank_score"]
        row["final_score"] = (p*.5 + r*.5) if p and r else (p or r)
        rows.append(row)
    return pd.DataFrame(rows).set_index("user")

print("[DEBUG] Initial load...")
df = build_df()
print(f"[DEBUG] Initial dataframe shape: {df.shape}")
dtale.global_state.set_app_settings({"enable_custom_filters": True})
d = dtale.show(df, ignore_duplicate=True)
print(f"[DEBUG] dtale instance id: {d._data_id}")
print(f"[DEBUG] dtale running at: {d._url}")
print(f"[DEBUG] Open this URL manually in your browser: {d._url}/dtale/main/{d._data_id}")

def poll():
    loop_count = 0
    while True:
        loop_count += 1
        try:
            with open(RESULTS_FILE) as f:
                raw = json.load(f)
            
            # --- IMPROVED LOOKUP LOGIC ---
            # We must be absolutely sure we are checking the same 'column' the user sees
            current_df = d.data
            if "user" in current_df.columns:
                known_users = set(current_df["user"].astype(str))
            else:
                known_users = set(current_df.index.astype(str))

            file_keys = set(raw.keys())
            new_keys = list(file_keys - known_users)

            if new_keys:
                print(f"[LOOP {loop_count}] ACTION: Appending {new_keys}")
                fragment = build_df(raw, new_keys)

                # --- STERILE MERGE ---
                # 1. Reset everything to columns to avoid index-collision
                curr_reset = current_df.reset_index()
                frag_reset = fragment.reset_index()

                # 2. Kill D-Tale artifacts
                to_drop = ['level_0', 'index', 'Unnamed: 0']
                curr_reset = curr_reset.drop(columns=[c for c in to_drop if c in curr_reset.columns])
                frag_reset = frag_reset.drop(columns=[c for c in to_drop if c in frag_reset.columns])

                # 3. Concatenate
                updated_df = pd.concat([curr_reset, frag_reset], ignore_index=True, sort=False)
                
                # 4. Set index back to 'user'
                if "user" in updated_df.columns:
                    updated_df = updated_df.set_index("user")
                
                # 5. Push to D-Tale
                d.data = updated_df

                # --- NEW SANITY CHECK ---
                print(f"[SANITY] Verification - Last 5 users in DataFrame:")
                print(d.data.index[-5:].tolist()) # This confirms the Python object has the data
                
            else:
                pass

        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(POLL_SECONDS)

t = threading.Thread(target=poll, daemon=True)
t.start()
print(f"[DEBUG] Poll thread launched, is_alive={t.is_alive()}")

input("Press Enter to stop...\n")