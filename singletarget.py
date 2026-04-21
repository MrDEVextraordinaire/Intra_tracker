import requests
import json
import time
import os

UID    = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET = "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"
USERNAME = "mjabri"
ENDPOINT_TIMEOUT = 5  # seconds before giving up on an endpoint

ENDPOINTS = [
    f"users/{USERNAME}",
    f"users/{USERNAME}/projects_users",
    f"users/{USERNAME}/cursus_users",
    f"users/{USERNAME}/achievements",
    f"users/{USERNAME}/titles",
    f"users/{USERNAME}/skills",
    f"users/{USERNAME}/coalitions",
    f"users/{USERNAME}/locations",
    f"users/{USERNAME}/events_users",
    f"users/{USERNAME}/patroned",
    f"users/{USERNAME}/patroning",
    f"users/{USERNAME}/expertises_users",
    f"users/{USERNAME}/teams",
    f"users/{USERNAME}/corrections",
    f"users/{USERNAME}/scale_teams",
]

SPINNERS = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
SPIN_I = 0

def spin(msg):
    global SPIN_I
    print(f"\r  {SPINNERS[SPIN_I % len(SPINNERS)]}  {msg}    ", end="", flush=True)
    SPIN_I += 1

def save_key(output, key, value):
    if os.path.exists(output):
        with open(output) as f:
            data = json.load(f)
    else:
        data = {}
    data[key] = value
    with open(output, "w") as f:
        json.dump(data, f, indent=2)

output = f"{USERNAME}_full.json"

print("[DEBUG] Authenticating...")
auth_start = time.time()
token = requests.post("https://api.intra.42.fr/oauth/token", timeout=10, data={
    "grant_type": "client_credentials", "client_id": UID, "client_secret": SECRET
}).json()["access_token"]
print(f"[DEBUG] Token acquired in {time.time()-auth_start:.2f}s: {token[:20]}...")

headers = {"Authorization": f"Bearer {token}"}
total_records = 0
script_start = time.time()

print(f"\n[DEBUG] Starting extraction for '{USERNAME}'")
print(f"[DEBUG] {len(ENDPOINTS)} endpoints to query")
print(f"[DEBUG] Per-endpoint timeout: {ENDPOINT_TIMEOUT}s")
print(f"[DEBUG] Output file: {output}\n")
print("=" * 60)

for i, endpoint in enumerate(ENDPOINTS, 1):
    key = endpoint.split("/", 2)[-1] if "/" in endpoint[6:] else "profile"
    url = f"https://api.intra.42.fr/v2/{endpoint}"
    page, results = 1, []
    endpoint_start = time.time()
    timed_out = False

    print(f"\n[{i}/{len(ENDPOINTS)}] {endpoint}")

    while True:
        elapsed_total = time.time() - endpoint_start
        remaining = ENDPOINT_TIMEOUT - elapsed_total

        if remaining <= 0:
            print(f"\n  [TIMEOUT] {ENDPOINT_TIMEOUT}s exceeded — moving on ({len(results)} records saved so far)")
            timed_out = True
            break

        spin(f"page {page} — {len(results)} records — {remaining:.1f}s left...")
        req_start = time.time()

        try:
            response = requests.get(
                url, headers=headers,
                params={"page[size]": 100, "page[number]": page},
                timeout=(min(3, remaining), min(remaining, ENDPOINT_TIMEOUT))
            )
        except requests.exceptions.Timeout:
            print(f"\n  [TIMEOUT] Request timed out after {time.time()-req_start:.2f}s — moving on")
            timed_out = True
            break
        except requests.exceptions.RequestException as e:
            print(f"\n  [ERROR] {type(e).__name__}: {e} — moving on")
            timed_out = True
            break

        elapsed = time.time() - req_start
        print(f"\r  [DEBUG] page {page} — status={response.status_code} x-total={response.headers.get('X-Total','?')} took={elapsed:.2f}s endpoint_elapsed={time.time()-endpoint_start:.2f}s", end="")

        if response.status_code == 429:
            print(f"\n  [DEBUG] Rate limited — waiting 2s...")
            time.sleep(2)
            continue

        if response.status_code != 200:
            print(f"\n  [SKIP] {response.status_code} — {response.text[:120]}")
            break

        batch = response.json()

        if isinstance(batch, list):
            results += batch
            x_total = response.headers.get('X-Total', '?')
            print(f"\n  [DEBUG] page {page} — got {len(batch)} records (total: {len(results)}, api says: {x_total})")

            if len(batch) < 100:
                print(f"  [DEBUG] Last page reached")
                break
            if x_total != '?' and len(results) >= int(x_total):
                print(f"  [DEBUG] Fetched all {x_total} records")
                break
            page += 1
        else:
            print(f"\n  [DEBUG] Single object — keys: {list(batch.keys()) if isinstance(batch, dict) else type(batch)}")
            results = batch
            break

    endpoint_elapsed = time.time() - endpoint_start

    if isinstance(results, list):
        count = len(results)
        total_records += count
        status = "PARTIAL" if timed_out and count > 0 else "TIMEOUT(empty)" if timed_out else "OK"
        print(f"  [DEBUG] {status} — {count} records in {endpoint_elapsed:.2f}s")
    else:
        print(f"  [DEBUG] Done — single object in {endpoint_elapsed:.2f}s")

    print(f"  [DEBUG] Writing '{key}' to {output}...")
    save_key(output, key, results)
    size_kb = os.path.getsize(output) / 1024
    print(f"  [DEBUG] File is now {size_kb:.1f} KB")

print("\n" + "=" * 60)
print(f"[DEBUG] All endpoints done in {time.time()-script_start:.2f}s")
print(f"[DEBUG] Total records: {total_records}")
print(f"[DEBUG] Output: {output} ({os.path.getsize(output)/1024:.1f} KB)")