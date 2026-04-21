#!/usr/bin/env python3
"""
42 Campus Location Tracker
══════════════════════════
Two-phase tool:

  --seed   Fetch active cadets (main cursus, not blackholed) + their full
           location history. Piscine-only and blackholed students are
           filtered before any per-student API call is made.

  --poll   15-minute loop. One API call fetches ALL currently active
           students at once. Appends to per-student history + snapshots log.

  --once   Single poll then exit (use with cron).

File layout:
  data/
    students.json          ← active cadet index
    excluded.json          ← blackholed + piscine-only students (audit log)
    locations/<login>.json ← full location history per student
    snapshots.jsonl        ← append-only 15-min snapshots (all active students)
    state.json             ← last poll summary
"""

import re
import json
import os
import time
import logging
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class CursusUser:
    """Models the response from /v2/cursus/{cursus_id}/users"""
    id: int
    level: float
    grade: Optional[str]
    blackholed_at: Optional[str]
    begin_at: str
    end_at: Optional[str]
    user: Dict[str, Any]  # Contains login, displayname, etc.

    @property
    def login(self) -> str:
        return self.user.get("login", "unknown")

    @classmethod
    def from_dict(cls, data: dict):
        """Helper to create a class instance from raw JSON"""
        return cls(
            id=data.get("id"),
            level=data.get("level", 0.0),
            grade=data.get("grade"),
            blackholed_at=data.get("blackholed_at"),
            begin_at=data.get("begin_at"),
            end_at=data.get("end_at"),
            user=data.get("user", {})
        )

# ── Config ────────────────────────────────────────────────────────────────────

UID       = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET    = "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"
CAMPUS_ID = 75          # 1337.ma — change if deploying for a different campus

POLL_INTERVAL = 15 * 60 # 15 minutes in seconds
REQUEST_DELAY = 0.55    # pause between paginated requests (42 API: ~2 req/s safe)
PAGE_SIZE     = 100
TOKEN_TTL     = 7000    # 42 tokens live 7200s — refresh slightly early

DATA_DIR       = Path("data")
STUDENTS_FILE  = DATA_DIR / "students.json"
LOCATIONS_DIR  = DATA_DIR / "locations"
SNAPSHOTS_FILE = DATA_DIR / "snapshots.jsonl"
STATE_FILE     = DATA_DIR / "state.json"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Auth (auto-refresh) ───────────────────────────────────────────────────────

class TokenManager:
    def __init__(self):
        log.debug("[AUTH] TokenManager.__init__() called")
        self._token   = None
        self._fetched = 0.0
        log.debug(f"[AUTH] Initial state — _token={self._token!r}  _fetched={self._fetched!r}")
        log.debug(f"[AUTH] TOKEN_TTL={TOKEN_TTL!r}s  UID={UID[:8]!r}…  SECRET={'*' * len(SECRET)}")

    def headers(self) -> dict:
        now           = time.time()
        token_age     = now - self._fetched
        token_present = bool(self._token)
        ttl_exceeded  = token_age > TOKEN_TTL

        log.debug(
            f"[AUTH] headers() called — "
            f"token_present={token_present}  "
            f"token_age={token_age:.1f}s  "
            f"TOKEN_TTL={TOKEN_TTL!r}s  "
            f"ttl_exceeded={ttl_exceeded}"
        )

        if not self._token or ttl_exceeded:
            log.debug(
                f"[AUTH] Token refresh needed — "
                f"reason={'no token' if not self._token else f'TTL exceeded ({token_age:.1f}s > {TOKEN_TTL}s)'}"
            )
            self._refresh()
        else:
            log.debug(
                f"[AUTH] Token still valid — "
                f"{TOKEN_TTL - token_age:.1f}s remaining before expiry  "
                f"token_preview={self._token[:16]!r}…"
            )

        built_headers = {"Authorization": f"Bearer {self._token}"}
        log.debug(
            f"[AUTH] Returning headers — "
            f"keys={list(built_headers.keys())!r}  "
            f"token_preview={self._token[:16]!r}…"
        )
        return built_headers

    def _refresh(self):
        log.info("[AUTH] Refreshing OAuth token…")
        log.debug(
            f"[AUTH] _refresh() called — "
            f"previous token={'None' if not self._token else self._token[:16] + '…'}  "
            f"previous _fetched={self._fetched!r}  "
            f"age={time.time() - self._fetched:.1f}s"
        )

        token_url = "https://api.intra.42.fr/oauth/token"
        payload   = {
            "grant_type":    "client_credentials",
            "client_id":     UID,
            "client_secret": SECRET,
        }
        log.debug(
            f"[AUTH] POST {token_url!r}  "
            f"payload keys={list(payload.keys())!r}  "
            f"client_id={UID[:8]!r}…  client_secret=REDACTED"
        )

        try:
            resp = requests.post(token_url, timeout=10, data=payload)
            log.debug(
                f"[AUTH] Token response — "
                f"status={resp.status_code}  "
                f"elapsed={resp.elapsed.total_seconds():.3f}s  "
                f"headers={dict(resp.headers)!r}"
            )
            log.debug(f"[AUTH] Token response body (redacted): { {k: ('REDACTED' if 'token' in k.lower() or 'secret' in k.lower() else v) for k, v in resp.json().items()}!r}")
        except requests.RequestException as e:
            log.error(f"[AUTH] Token request failed: {type(e).__name__}: {e}")
            raise

        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            log.error(
                f"[AUTH] raise_for_status() failed — "
                f"status={resp.status_code}  body={resp.text[:500]!r}"
            )
            raise

        body = resp.json()
        log.debug(f"[AUTH] Token response JSON keys: {list(body.keys())!r}")

        if "access_token" not in body:
            log.error(f"[AUTH] 'access_token' missing from response body — keys present: {list(body.keys())!r}")
            raise KeyError("access_token not in token response")

        old_token     = self._token
        self._token   = body["access_token"]
        self._fetched = time.time()

        log.debug(
            f"[AUTH] Token updated — "
            f"old={old_token[:16] + '…' if old_token else 'None'}  "
            f"new={self._token[:16]!r}…  "
            f"_fetched={self._fetched!r}  "
            f"expires_in={body.get('expires_in')!r}s  "
            f"token_type={body.get('token_type')!r}"
        )
        log.info(f"[AUTH] Token acquired: {self._token[:16]}…")


log.debug("[AUTH] Instantiating global TokenManager…")
auth = TokenManager()
log.debug(f"[AUTH] Global auth object ready: {auth!r}")


# ── Pagination helper ─────────────────────────────────────────────────────────

def paginate(url: str, params: dict = None, label: str = "records") -> list:
    """
    Fetch all pages from a 42 API list endpoint.
    Handles 429 rate-limiting and invalid responses gracefully.
    """
    params = dict(params or {})
    params["page[size]"] = PAGE_SIZE
    page, results = 1, []

    log.debug(f"[PAGINATE] Starting pagination for url={url!r}  label={label!r}")
    log.debug(f"[PAGINATE] Initial params (before page[number] injection): {params!r}")
    log.debug(f"[PAGINATE] PAGE_SIZE={PAGE_SIZE!r}  REQUEST_DELAY={REQUEST_DELAY!r}")


    for var in ["http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"]:
        os.environ.pop(var, None)
    
    while True:
        params["page[number]"] = page
        log.debug(f"[PAGINATE] → Requesting page {page}  full params={params!r}")

        try:
            resp = requests.get(
                url,
                headers=auth.headers(),
                params=params,
                timeout=20,
            )
            log.debug(
                f"[PAGINATE] ← Response: status={resp.status_code}  "
                f"url_called={resp.url!r}  "
                f"elapsed={resp.elapsed.total_seconds():.3f}s  "
                f"headers={dict(resp.headers)!r}"
            )
        except requests.RequestException as e:
            log.warning(f"[PAGINATE] Request error (page {page}): {e} — stopping")
            log.debug(f"[PAGINATE] Exception type: {type(e).__name__}  args: {e.args!r}")
            break

        if resp.status_code == 401:
            log.warning("[PAGINATE] 401 Unauthorised — forcing token refresh")
            log.debug(f"[PAGINATE] 401 response body: {resp.text[:500]!r}")
            auth._fetched = 0
            time.sleep(1)
            continue

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            log.warning(f"[PAGINATE] Rate limited — sleeping {wait}s")
            log.debug(f"[PAGINATE] 429 response body: {resp.text[:500]!r}")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            log.warning(f"[PAGINATE] HTTP {resp.status_code} on {url} — stopping")
            log.debug(
                f"[PAGINATE] Non-200 response body: {resp.text[:1000]!r}  "
                f"response headers: {dict(resp.headers)!r}"
            )
            break

        log.debug(f"[PAGINATE] Raw response body (first 500 chars): {resp.text[:500]!r}")

        try:
            batch = resp.json()
        except ValueError as e:
            log.warning(f"[PAGINATE] Failed to parse response as JSON: {e} — stopping")
            log.debug(f"[PAGINATE] Raw text that failed JSON parse: {resp.text[:1000]!r}")
            break

        log.debug(
            f"[PAGINATE] Parsed JSON: type={type(batch).__name__}  "
            f"{'len=' + str(len(batch)) if isinstance(batch, (list, dict)) else 'value=' + repr(batch)}"
        )

        if not isinstance(batch, list):
            log.warning(
                f"[PAGINATE] Expected a JSON list but got {type(batch).__name__} — stopping. "
                f"Value: {batch!r}"
            )
            break

        if not batch:
            log.debug(f"[PAGINATE] Got empty list on page {page} — pagination complete")
            break

        log.debug(f"[PAGINATE] First item in batch: {batch[0]!r}")
        log.debug(f"[PAGINATE] Last  item in batch: {batch[-1]!r}")

        results.extend(batch)
        log.debug(f"[PAGINATE] results length after extend: {len(results)}")

        x_total    = resp.headers.get("X-Total")
        x_page     = resp.headers.get("X-Page")
        x_per_page = resp.headers.get("X-Per-Page")
        log.debug(
            f"[PAGINATE] Pagination headers — "
            f"X-Total={x_total!r}  X-Page={x_page!r}  X-Per-Page={x_per_page!r}"
        )

        batch_lt_page = len(batch) < PAGE_SIZE
        total_reached = bool(x_total and len(results) >= int(x_total))
        done = batch_lt_page or total_reached
        log.debug(
            f"[PAGINATE] Done check — "
            f"batch_size({len(batch)}) < PAGE_SIZE({PAGE_SIZE}) → {batch_lt_page}  |  "
            f"total_reached({len(results)} >= {x_total}) → {total_reached}  |  "
            f"done={done}"
        )

        log.debug(
            f"  page {page}: +{len(batch)} {label} "
            f"(total {len(results)}/{x_total or '?'})"
        )
        log.info(
            f"[PAGINATE] page {page}: fetched {len(batch)} {label} "
            f"(running total {len(results)}/{x_total or '?'})"
        )

        if done:
            log.debug(f"[PAGINATE] Stopping pagination after page {page}")
            break

        page += 1
        log.debug(f"[PAGINATE] Advancing to page {page} — sleeping {REQUEST_DELAY!r}s")
        time.sleep(REQUEST_DELAY)

    log.debug(
        f"[PAGINATE] Pagination complete — "
        f"url={url!r}  total_pages={page}  total_records={len(results)}"
    )
    return results


# ── Storage helpers ───────────────────────────────────────────────────────────

def load_json(path, default=None):
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return default if default is not None else {}

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")

def loc_file(login: str) -> Path:
    return LOCATIONS_DIR / f"{login}.json"


# ── Host coordinate parser ────────────────────────────────────────────────────

def parse_host(host: str) -> dict:
    """
    Standard parser for Rabat (c[cluster]r[row]p[pos]).
    """
    m = re.match(r"c(\d+)r(\d+)p(\d+)", host or "")
    if m:
        return {
            "cluster":  int(m.group(1)),
            "row":      int(m.group(2)),
            "position": int(m.group(3)),
            # Euclidean mapping for proximity checks
            "x": int(m.group(3)),
            "y": int(m.group(2))
        }
    return {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Student eligibility filter ────────────────────────────────────────────────

# Cursus IDs used by 42
CURSUS_PISCINE = 9   # C Piscine — introductory selection month
CURSUS_MAIN    = 21  # 42cursus  — the actual programme

def classify_student(cursus_user: dict) -> tuple[str, str]:
    """
    Evaluates a CursusUser object (from /v2/cursus/21/users).
    """
    bh = cursus_user.get("blackholed_at")
    
    if bh:
        try:
            bh_dt = datetime.fromisoformat(bh.replace("Z", "+00:00"))
            if bh_dt <= datetime.now(timezone.utc):
                days_ago = (datetime.now(timezone.utc) - bh_dt).days
                return "blackholed", f"blackholed_at={bh} ({days_ago}d ago)"
        except ValueError:
            pass

    # No need to check for cursus_id=21, the endpoint guarantees it.
    return "active", "enrolled in main cursus"


# ── Phase 1: Seed ─────────────────────────────────────────────────────────────

def seed(max_students: int = 0):
    """
    Fetch active cadets for this campus and pull their full location history.

    Two-stage filtering happens before any per-student API call is made:

      Stage 1 — API-level (filter[cursus_id]=21):
        The roster endpoint only returns students enrolled in the main 42
        cursus. Piscine-only students (~600+ on a large campus) never appear
        in the response, saving the bulk of unnecessary pagination.

      Stage 2 — client-level (classify_student):
        From the returned roster, blackholed students are detected via the
        blackholed_at field in their cursus_users entry and skipped before
        any location history call is made.

    Both exclusion sets are written to data/excluded.json for auditing.
    Safe to interrupt and re-run — students with an existing location file
    are skipped without making any API call.

    Args:
        max_students: if > 0, only seed this many active students (testing).
    """
    log.debug(f"[SEED] seed() called with max_students={max_students!r}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCATIONS_DIR.mkdir(parents=True, exist_ok=True)
    log.debug(f"[SEED] DATA_DIR={DATA_DIR!r} (exists={DATA_DIR.exists()})")
    log.debug(f"[SEED] LOCATIONS_DIR={LOCATIONS_DIR!r} (exists={LOCATIONS_DIR.exists()})")
    log.debug(f"[SEED] STUDENTS_FILE={STUDENTS_FILE!r}")
    log.debug(f"[SEED] CAMPUS_ID={CAMPUS_ID!r}  CURSUS_MAIN={CURSUS_MAIN!r}")

    # ── Stage 1: fetch roster filtered to main cursus only ───────────────────
    # filter[cursus_id]=21 drops everyone who never passed piscine.
    # On a ~988-student campus this typically cuts the list to ~350.
    log.info(
        f"[SEED] Fetching roster for campus {CAMPUS_ID} "
        f"(filter: cursus_id={CURSUS_MAIN} — main 42cursus only)…"
    )
    paginate_url    = f"https://api.intra.42.fr/v2/campus/{CAMPUS_ID}/users"
    paginate_params = {"filter[cursus_ids]": CURSUS_MAIN}
    log.debug(f"[SEED] paginate() → url={paginate_url!r}  params={paginate_params!r}")

    users = paginate(
    f"https://api.intra.42.fr/v2/cursus/{CURSUS_MAIN}/users",
    params={"filter[primary_campus_id]": CAMPUS_ID},
    label="students",
)

    log.debug(f"[SEED] paginate() returned type={type(users).__name__}  len={len(users)}")
    if not users:
        log.warning("[SEED] paginate() returned an EMPTY list — no users to process!")
    else:
        log.debug(f"[SEED] First user sample: {users[0]!r}")
        log.debug(f"[SEED] Last  user sample: {users[-1]!r}")
    log.info(f"[SEED] API returned {len(users)} students (piscine-only excluded).")

    # ── Stage 2: client-side filter — drop blackholed students ───────────────
    active_users  = []
    excluded_path = DATA_DIR / "excluded.json"
    log.debug(f"[SEED] Loading excluded list from {excluded_path!r}  (exists={excluded_path.exists()})")
    excluded = load_json(excluded_path, {})
    log.debug(f"[SEED] excluded list pre-loaded with {len(excluded)} entries: {list(excluded.keys())[:10]}…")

    counts = {"active": 0, "blackholed": 0, "piscine_only": 0}
    log.debug(f"[SEED] counts initialised: {counts!r}")

    # Inside Phase 1: seed() Stage 2 loop
    for idx, cu in enumerate(users):
        # /v2/cursus/21/users returns user directly, not nested in "user" key
        login = cu.get("login")
        if not login:
            login = cu.get("user", {}).get("login")

        if not login:
            log.warning(f"[SEED] cu[{idx}] has no 'login' field inside 'user' — skipping.")
            continue

        status, reason = classify_student(cu)
        
        if status != "active":
            if status not in counts:
                counts[status] = 0
            counts[status] = counts.get(status, 0) + 1
            if login not in excluded:
                excluded[login] = {
                    "login":       login,
                    "status":      status,
                    "reason":      reason,
                    "excluded_at": now_iso(),
                }
            continue

        counts["active"] = counts.get("active", 0) + 1
        active_users.append(cu)
    log.debug(f"[SEED] {login!r} marked active — active_users length now {len(active_users)}")

    log.debug(f"[SEED] Filter loop complete. Final counts: {counts!r}")
    log.debug(f"[SEED] active_users ({len(active_users)}): {[u.get('login') for u in active_users][:10]}…")
    log.debug(f"[SEED] excluded dict now has {len(excluded)} entries — saving to {excluded_path!r}")

    save_json(excluded_path, excluded)
    log.info(
        f"[SEED] After filtering: "
        f"{counts['active']} active, "
        f"{counts['blackholed']} blackholed, "
        f"{counts['piscine_only']} piscine-only  "
        f"→ {len(excluded)} total excluded (saved to data/excluded.json)"
    )

    # sanity check: counts["active"] must match the actual list we built
    if counts["active"] != len(active_users):
        log.error(
            f"[SEED] MISMATCH: counts['active']={counts['active']} "
            f"but len(active_users)={len(active_users)} — investigate classify_student()"
        )
    else:
        log.debug(f"[SEED] Sanity check passed: counts['active'] == len(active_users) == {len(active_users)}")

    # ── Build / merge student index (active cadets only) ─────────────────────
    log.debug(f"[SEED] Loading student index from {STUDENTS_FILE!r}  (exists={STUDENTS_FILE.exists()})")
    index = load_json(STUDENTS_FILE, {})
    log.debug(f"[SEED] Student index pre-loaded with {len(index)} entries: {list(index.keys())[:10]}…")

    new_entries = 0
    # Inside the index merge loop in seed()
    for cu in active_users:
        # API returns user directly at top level, not nested in "user"
        login = cu.get("login")
        if not login:
            login = cu.get("user", {}).get("login")
        
        if login in index:
            continue

        entry = {
            "id":            cu.get("id"),
            "login":         login,
            "display_name":  cu.get("displayname", login),
            "level":         cu.get("level", 0),
            "pool_year":     cu.get("pool_year"),
            "blackholed_at": cu.get("blackholed_at"),
            "added_at":      now_iso(),
        }
        index[login] = entry
        new_entries += 1

    log.debug(
        f"[SEED] Index merge complete — {new_entries} new entries added, "
        f"total now {len(index)}  (unchanged: {len(active_users) - new_entries})"
    )
    save_json(STUDENTS_FILE, index)
    log.info(f"[SEED] Active student index saved ({len(index)} entries).")

    # ── Pull location history per active student ──────────────────────────────
    targets = list(index.keys())
    log.debug(f"[SEED] Full targets list length: {len(targets)}  first 5: {targets[:5]!r}")

    if max_students > 0:
        targets = targets[:max_students]
        log.info(f"[SEED] --limit {max_students} applied → {len(targets)} targets")
        log.debug(f"[SEED] Truncated targets: {targets!r}")
    else:
        log.debug(f"[SEED] No --limit applied, processing all {len(targets)} targets")

    total          = len(targets)
    n_seeded       = 0
    n_skipped_file = 0
    log.debug(
        f"[SEED] Beginning location history loop — "
        f"total={total}  REQUEST_DELAY={REQUEST_DELAY!r}s"
    )

    for i, login in enumerate(targets, 1):
        lf = loc_file(login)
        log.debug(f"[SEED] [{i}/{total}] {login!r} — loc_file={lf!r}  exists={lf.exists()}")

        if lf.exists():
            log.info(f"[{i:>4}/{total}] {login:20s} — location file exists, skipping")
            n_skipped_file += 1
            log.debug(f"[SEED] n_skipped_file now {n_skipped_file}")
            continue

        log.info(f"[{i:>4}/{total}] {login:20s} — fetching location history…")
        loc_url = f"https://api.intra.42.fr/v2/users/{login}/locations"
        log.debug(f"[SEED] paginate() → url={loc_url!r}  (no extra params)")

        locs = paginate(loc_url, label="locations")
        log.debug(
            f"[SEED] paginate() returned {len(locs)} location records for {login!r}  "
            f"type={type(locs).__name__}"
        )

        if not locs:
            log.warning(
                f"[SEED] {login!r} has ZERO location records — "
                f"will write empty history (this may be normal for brand-new students)"
            )

        for j, loc in enumerate(locs):
            raw_host = loc.get("host", "")
            coords   = parse_host(raw_host)
            loc["_coords"] = coords
            log.debug(
                f"[SEED]   loc[{j}] id={loc.get('id')!r}  host={raw_host!r}  "
                f"→ _coords={coords!r}  "
                f"begin_at={loc.get('begin_at')!r}  end_at={loc.get('end_at')!r}"
            )

        payload = {
            "login":     login,
            "seeded_at": now_iso(),
            "history":   locs,
        }
        log.debug(
            f"[SEED] Writing payload to {lf!r}  "
            f"keys={list(payload.keys())!r}  history_length={len(locs)}"
        )
        save_json(lf, payload)

        # verify the file actually landed on disk
        if lf.exists():
            log.debug(f"[SEED] Confirmed {lf!r} exists on disk after save_json()")
        else:
            log.error(f"[SEED] save_json() silently failed — {lf!r} does NOT exist after write!")

        n_seeded += 1
        log.debug(f"[SEED] n_seeded now {n_seeded}")
        log.info(f"             → {len(locs)} location records written")

        log.debug(f"[SEED] Sleeping REQUEST_DELAY={REQUEST_DELAY!r}s before next request…")
        time.sleep(REQUEST_DELAY)

    # ── Final summary ─────────────────────────────────────────────────────────
    log.debug(
        f"[SEED] Location loop finished — "
        f"n_seeded={n_seeded}  n_skipped_file={n_skipped_file}  total={total}"
    )

    if n_seeded + n_skipped_file != total:
        log.error(
            f"[SEED] ACCOUNT MISMATCH: n_seeded({n_seeded}) + "
            f"n_skipped_file({n_skipped_file}) = {n_seeded + n_skipped_file} ≠ total({total}) — "
            f"some targets were neither seeded nor skipped, investigate loop logic"
        )
    else:
        log.debug(
            f"[SEED] Sanity check passed: n_seeded({n_seeded}) + "
            f"n_skipped_file({n_skipped_file}) == total({total})"
        )

    log.info(
        f"[SEED] Done — {n_seeded} seeded, {n_skipped_file} skipped (file existed), "
        f"{total} total targets processed."
    )
    log.debug(f"[SEED] seed() returning normally.")


# ── Phase 2: Poll ─────────────────────────────────────────────────────────────

def poll_once() -> dict:
    """
    Fetch all currently active students on campus in one paginated query
    (endpoint: /campus/{id}/locations?filter[active]=true).

    For each active session:
      - Appends the record to that student's per-student JSON history
        (only if the session ID hasn't been recorded yet).
      - Writes a timestamped snapshot entry to snapshots.jsonl.

    Returns the snapshot dict (useful for logging / testing).
    """
    LOCATIONS_DIR.mkdir(parents=True, exist_ok=True)
    polled_at = now_iso()
    log.info(f"[POLL] Fetching active locations at {polled_at}…")

    active_sessions = paginate(
        f"https://api.intra.42.fr/v2/campus/{CAMPUS_ID}/locations",
        params={"filter[active]": "true"},
        label="active sessions",
    )

    snapshot_students = []

    for session in active_sessions:
        login  = session.get("user", {}).get("login", "unknown")
        host   = session.get("host", "")
        coords = parse_host(host)

        entry = {
            "login":      login,
            "host":       host,
            "coords":     coords,           # {"cluster":1,"row":9,"position":12}
            "begin_at":   session.get("begin_at"),
            "session_id": session.get("id"),
        }
        snapshot_students.append(entry)

        # ── Append to per-student location file ───────────────────────────────
        lf          = loc_file(login)
        student_rec = load_json(lf, {
            "login":     login,
            "seeded_at": None,
            "history":   [],
        })

        # Deduplicate by session ID — don't double-record same session
        known_ids = {r.get("id") for r in student_rec["history"]}
        if session.get("id") not in known_ids:
            enriched              = dict(session)
            enriched["_coords"]   = coords
            enriched["_polled_at"] = polled_at
            student_rec["history"].append(enriched)
            save_json(lf, student_rec)

    # ── Write snapshot to rolling log ─────────────────────────────────────────
    snapshot = {
        "polled_at":    polled_at,
        "active_count": len(active_sessions),
        "students":     snapshot_students,
    }
    append_jsonl(SNAPSHOTS_FILE, snapshot)

    # ── Update state file ─────────────────────────────────────────────────────
    save_json(STATE_FILE, {
        "last_poll":    polled_at,
        "active_count": len(active_sessions),
        "active_logins": [s["login"] for s in snapshot_students],
    })

    log.info(
        f"[POLL] {len(active_sessions)} active students — "
        f"snapshot written to {SNAPSHOTS_FILE}"
    )
    return snapshot


def poll_loop(interval: int = POLL_INTERVAL):
    """Run poll_once() forever, sleeping `interval` seconds between runs."""
    log.info(f"[POLL] Starting loop — interval: {interval}s ({interval//60}m)")
    while True:
        try:
            poll_once()
        except Exception as e:
            log.error(f"[POLL] Unhandled error: {e}", exc_info=True)
        log.info(f"[POLL] Sleeping {interval}s…")
        time.sleep(interval)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="42 Campus Location Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python campus_tracker.py --seed              # seed all ~200 students
  python campus_tracker.py --seed --limit 10   # seed only 10 (testing)
  python campus_tracker.py --poll              # run the 15-min polling loop
  python campus_tracker.py --once              # single poll, then exit (cron)
  python campus_tracker.py --seed --poll       # seed then immediately start loop
        """,
    )
    parser.add_argument(
        "--seed",  action="store_true",
        help="Fetch all campus students + full location history",
    )
    parser.add_argument(
        "--poll",  action="store_true",
        help="Run the 15-minute polling loop (runs forever)",
    )
    parser.add_argument(
        "--once",  action="store_true",
        help="Poll once then exit (for cron jobs)",
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Seed only this many students (0 = all, useful for testing)",
    )
    args = parser.parse_args()

    if not any([args.seed, args.poll, args.once]):
        parser.print_help()
        return

    if args.seed:
        seed(max_students=args.limit)

    if args.poll:
        poll_loop(interval=args.interval)

    if args.once:
        poll_once()


if __name__ == "__main__":
    main()
