#!/usr/bin/env python3
"""
42 API Client Module
Handles OAuth authentication and paginated API requests.
"""

import os
import time
import logging
import requests
from typing import Any, Optional

log = logging.getLogger(__name__)

UID = "u-s4t2ud-e6f49bc063742142943e14daea72ec3cf332b8f8a347c1778e35e35ca8c69bd2"
SECRET = "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"
TOKEN_TTL = 7000
PAGE_SIZE = 100


class TokenManager:
    """Handles OAuth token refresh with TTL caching."""
    
    def __init__(self, uid: str = UID, secret: str = SECRET, ttl: int = TOKEN_TTL):
        self._uid = uid
        self._secret = secret
        self._ttl = ttl
        self._token: Optional[str] = None
        self._fetched: float = 0.0
        log.debug(f"[AUTH] TokenManager initialized (TTL={ttl}s)")
    
    @property
    def token(self) -> Optional[str]:
        return self._token
    
    def _refresh(self) -> str:
        """Fetch new OAuth token from API."""
        log.info("[AUTH] Refreshing OAuth token...")
        resp = requests.post(
            "https://api.intra.42.fr/oauth/token",
            timeout=10,
            data={
                "grant_type": "client_credentials",
                "client_id": self._uid,
                "client_secret": self._secret,
            }
        )
        resp.raise_for_status()
        body = resp.json()
        
        if "access_token" not in body:
            raise KeyError("No access_token in response")
        
        self._token = body["access_token"]
        self._fetched = time.time()
        log.info(f"[AUTH] Token acquired: {self._token[:16]}...")
        return self._token
    
    def headers(self) -> dict:
        """Get authorization headers, refreshing token if needed."""
        now = time.time()
        
        if not self._token or (now - self._fetched) > self._ttl:
            self._refresh()
        
        return {"Authorization": f"Bearer {self._token}"}


def paginate(
    url: str,
    auth: TokenManager,
    params: Optional[dict] = None,
    page_size: int = PAGE_SIZE,
    label: str = "records",
    delay: float = 0.55,
) -> list:
    """
    Fetch all pages from a 42 API endpoint.
    
    Args:
        url: API endpoint URL
        auth: TokenManager instance
        params: Query parameters (without pagination)
        page_size: Records per page
        label: Human-readable label for logging
        delay: Seconds between pages (rate limiting)
    
    Returns:
        List of all records fetched
    """
    params = dict(params or {})
    params["page[size]"] = page_size
    
    results = []
    page = 1
    
    # Clear proxies that might interfere
    for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        os.environ.pop(var, None)
    
    while True:
        params["page[number]"] = page
        
        try:
            resp = requests.get(
                url,
                headers=auth.headers(),
                params=params,
                timeout=20,
            )
        except requests.RequestException as e:
            log.warning(f"[PAGINATE] Request error: {e}")
            break
        
        # Handle rate limiting
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            log.warning(f"[PAGINATE] Rate limited, sleeping {wait}s")
            time.sleep(wait)
            continue
        
        # Handle auth errors
        if resp.status_code == 401:
            log.warning("[PAGINATE] 401 - forcing token refresh")
            auth._token = None
            auth._fetched = 0
            time.sleep(1)
            continue
        
        if resp.status_code != 200:
            log.warning(f"[PAGINATE] HTTP {resp.status_code}")
            break
        
        # Parse JSON response
        try:
            batch = resp.json()
        except ValueError:
            log.warning("[PAGINATE] Invalid JSON")
            break
        
        # Validate response is a list
        if not isinstance(batch, list):
            log.warning(f"[PAGINATE] Expected list, got {type(batch).__name__}")
            break
        
        if not batch:
            log.debug(f"[PAGINATE] Empty page {page} - complete")
            break
        
        results.extend(batch)
        
        x_total = resp.headers.get("X-Total")
        log.info(f"[PAGINATE] page {page}: +{len(batch)} {label} (total: {len(results)}/{x_total or '?'})")
        
        # Check if we're done - either batch is smaller than page size
        # OR we've reached the total (when X-Total is available)
        if len(batch) <= page_size:
            log.debug(f"[PAGINATE] Last page (batch {len(batch)} <= page_size {page_size})")
            break
        
        if x_total:
            total = int(x_total)
            if len(results) >= total:
                log.debug(f"[PAGINATE] Reached total ({len(results)} >= {total})")
                break
        
        page += 1
        time.sleep(delay)
    
    log.debug(f"[PAGINATE] Complete: {len(results)} {label}")
    return results


def test_token_manager():
    """Quick test of token fetching."""
    print("[TEST] Testing TokenManager...")
    auth = TokenManager()
    hdrs = auth.headers()
    assert "Bearer" in hdrs["Authorization"], "Missing Bearer token"
    assert len(auth.token) > 20, "Token too short"
    print(f"[TEST] Token OK: {auth.token[:20]}...")
    return True


def test_paginate_simple():
    """Quick test of pagination with a list-returning endpoint."""
    print("[TEST] Testing paginate with /campus/75/users...")
    auth = TokenManager()
    results = paginate(
        "https://api.intra.42.fr/v2/campus/75/users",
        auth,
        params={"page[size]": 5},  # Small page for quick test
        page_size=5,
        label="users"
    )
    assert isinstance(results, list), "Not a list"
    assert len(results) > 0, "No results"
    print(f"[TEST] Got {len(results)} users")
    # Check structure
    first = results[0]
    assert "login" in first, "Missing login field"
    print(f"[TEST] First user: {first.get('login')}")
    return True


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    
    print("=" * 40)
    print("Running api_client tests...")
    print("=" * 40)
    
    try:
        test_token_manager()
        print("[TEST] ✓ TokenManager test PASSED")
    except Exception as e:
        print(f"[TEST] ✗ TokenManager test FAILED: {e}")
        sys.exit(1)
    
    try:
        test_paginate_simple()
        print("[TEST] ✓ Paginate test PASSED")
    except Exception as e:
        print(f"[TEST] ✗ Paginate test FAILED: {e}")
        sys.exit(1)
    
    print("=" * 40)
    print("All tests PASSED!")
    print("=" * 40)