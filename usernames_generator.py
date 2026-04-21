

import requests

UID    = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET = "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"
CAMPUS_ID = 75

token = requests.post("https://api.intra.42.fr/oauth/token", data={
    "grant_type": "client_credentials", "client_id": UID, "client_secret": SECRET
}).json()["access_token"]

headers = {"Authorization": f"Bearer {token}"}
logins, page = [], 1

while True:
    batch = requests.get(f"https://api.intra.42.fr/v2/campus/{CAMPUS_ID}/users",
        headers=headers, params={"page[size]": 100, "page[number]": page}).json()
    logins += [u["login"] for u in batch]
    if len(batch) < 100:
        break
    page += 1

print(logins)
