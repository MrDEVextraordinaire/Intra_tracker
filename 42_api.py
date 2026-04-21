import requests
import time
import json
import os
from userlist import USERNAMES

UID     = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET  = "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"
OUTPUT  = "results.json"

if os.path.exists(OUTPUT):
    with open(OUTPUT) as f:
        saved = json.load(f)
else:
    saved = {}

already_done = set(saved.keys())
remaining = [u for u in USERNAMES if u not in already_done]
print(f"[DEBUG] {len(already_done)} users already saved, {len(remaining)} remaining\n")

if not remaining:
    print("[DEBUG] All users already processed. Nothing to do.")
    exit()

token = requests.post("https://api.intra.42.fr/oauth/token", data={
    "grant_type": "client_credentials", "client_id": UID, "client_secret": SECRET
}).json()["access_token"]
print(f"[DEBUG] Token acquired: {token[:20]}...\n")

headers = {"Authorization": f"Bearer {token}"}

for username in remaining:
    print(f"\n{'='*60}")
    print(f"[DEBUG] Starting full data extraction for '{username}'")
    print(f"{'='*60}")

    # --- PAGINATED FETCH ---
    projects, page = [], 1
    x_total = None
    while True:
        while True:
            response = requests.get(
                f"https://api.intra.42.fr/v2/users/{username}/projects_users",
                headers=headers, params={"page[size]": 100, "page[number]": page}
            )
            if x_total is None:
                x_total = response.headers.get("X-Total", "?")
            print(f"[DEBUG] Page {page} | Status: {response.status_code} | X-Total: {x_total} | X-Page: {response.headers.get('X-Page', '?')} | Content-Length: {response.headers.get('Content-Length', '?')}")
            if response.status_code != 429:
                break
            print(f"[DEBUG] Rate limited — waiting 2s...")
            time.sleep(2)

        batch = response.json()
        print(f"[DEBUG] Page {page} returned {len(batch)} entries")

        if page == 1:
            print(f"[DEBUG] Sample raw entry from page 1:\n{json.dumps(batch[0], indent=2) if batch else 'EMPTY'}")

        projects += batch
        if len(batch) < 100:
            print(f"[DEBUG] Last page reached (batch < 100)")
            break
        page += 1

    print(f"\n[DEBUG] Total entries fetched: {len(projects)} (API reported X-Total: {x_total})")
    if x_total and x_total != '?' and len(projects) != int(x_total):
        print(f"[WARNING] Mismatch! Fetched {len(projects)} but X-Total says {x_total}")

    # --- DUMP ALL PROJECT NAMES for discovery ---
    all_names = sorted(set(p["project"]["name"] for p in projects))
    print(f"\n[DEBUG] All distinct project names ({len(all_names)}):")
    for n in all_names:
        print(f"         - {n}")

    # --- EXAM FILTERING ---
    exams = [p for p in projects if "exam" in p["project"]["name"].lower()]
    print(f"\n[DEBUG] Exam entries matched: {len(exams)}")

    # --- DUMP RAW EXAM ENTRIES ---
    print(f"\n[DEBUG] Raw exam entries (full detail):")
    for e in sorted(exams, key=lambda x: (x["project"]["name"], x["occurrence"])):
        print(f"  project : {e['project']['name']} (id={e['project']['id']})")
        print(f"  occurrence : {e['occurrence']}")
        print(f"  status     : {e['status']}")
        print(f"  final_mark : {e.get('final_mark')}")
        print(f"  validated? : {e.get('validated?')}")
        print(f"  created_at : {e.get('created_at')}")
        print(f"  updated_at : {e.get('updated_at')}")
        print()

    # --- GROUP & SUMMARIZE ---
    by_exam = {}
    for e in exams:
        by_exam.setdefault(e["project"]["name"], []).append(e)

    print(f"[DEBUG] Distinct exams grouped: {list(by_exam.keys())}")
    print(f"\n[DEBUG] Attempt count per exam:")
    for name, attempts in sorted(by_exam.items()):
        occurrences = sorted([a["occurrence"] for a in attempts])
        marks       = [a.get("final_mark") for a in sorted(attempts, key=lambda x: x["occurrence"])]
        statuses    = [a["status"] for a in sorted(attempts, key=lambda x: x["occurrence"])]
        print(f"  {name}")
        print(f"    occurrences : {occurrences}")
        print(f"    marks       : {marks}")
        print(f"    statuses    : {statuses}")

    # --- SAVE ---
    saved[username] = {}
    print(f"\n{'User':<15} | {'Exam':<30} | {'Attempts':<9} | Final Mark")
    print("-" * 70)
    for name, attempts in sorted(by_exam.items()):
        latest     = max(attempts, key=lambda x: x["occurrence"])
        mark       = latest.get("final_mark", None)
        true_count = latest["occurrence"] + 1          # <-- THE FIX
        saved[username][name] = {
            "attempts"   : true_count,
            "final_mark" : mark,
            "all_marks"  : [a.get("final_mark") for a in sorted(attempts, key=lambda x: x["occurrence"])]
        }
        print(f"[DEBUG]   '{name}' — occurrence={latest['occurrence']} → {true_count} real attempt(s), mark={mark}")
        print(f"{username:<15} | {name[:30]:<30} | {true_count:<9} | {mark}")

    with open(OUTPUT, "w") as f:
        json.dump(saved, f, indent=2)
    print(f"\n[DEBUG] Written to {OUTPUT}")