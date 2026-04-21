#!/usr/bin/env python3
import requests
import json

# --- CONFIGURATION ---
UID       = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET    = "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"
LOGIN = "mjabri"  # Change this to the student you want to track

def main():
    # 1. Get OAuth Token
    print("[1/4] Authenticating with 42 API...")
    auth_resp = requests.post(
        "https://api.intra.42.fr/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": UID,
            "client_secret": SECRET
        },
        timeout=10
    )
    auth_resp.raise_for_status()
    token = auth_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Get Main Profile (Contains projects_users, cursus_users, skills)
    print(f"[2/4] Fetching profile data for {LOGIN}...")
    profile_resp = requests.get(
        f"https://api.intra.42.fr/v2/users/{LOGIN}",
        headers=headers,
        timeout=10
    )
    
    # 3. Get Recent Locations (For the Hibernation vs Cramming metric)
    print(f"[3/4] Fetching recent locations for {LOGIN}...")
    loc_resp = requests.get(
        f"https://api.intra.42.fr/v2/users/{LOGIN}/locations?page[size]=100",
        headers=headers,
        timeout=10
    )

    # 4. Get Recent Scale Teams (For the Correction Integrity / Network metric)
    print(f"[4/4] Fetching recent corrections for {LOGIN}...")
    scale_resp = requests.get(
        f"https://api.intra.42.fr/v2/users/{LOGIN}/scale_teams?page[size]=100",
        headers=headers,
        timeout=10
    )

    # Combine into a single dictionary
    payload = {
        "profile": profile_resp.json() if profile_resp.status_code == 200 else {},
        "locations": loc_resp.json() if loc_resp.status_code == 200 else [],
        "scale_teams": scale_resp.json() if scale_resp.status_code == 200 else []
    }

    # Save to file
    filename = f"{LOGIN}_raw_data.json"
    with open(filename, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\n✅ Success! Data saved to {filename}")
    print(f"You can now 'cat {filename}' and paste the output to the AI.")

if __name__ == "__main__":
    main()