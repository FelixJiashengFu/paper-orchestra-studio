# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Scholarly search backends.

Sources (all return Semantic-Scholar-shaped dicts, so downstream code is unchanged):

  semantic_scholar  SEMANTIC_SCHOLAR_API_KEY (optional; header x-api-key)
  openalex          OPENALEX_EMAIL (polite pool) / OPENALEX_API_KEY (optional)
  crossref          CROSSREF_MAILTO (polite pool) / CROSSREF_PLUS_TOKEN (optional)
  arxiv             no key
  dblp              no key (computer science)
  europepmc         no key (life sciences / chemistry)
  pubmed            NCBI_API_KEY (optional; 3 -> 10 req/s), NCBI_EMAIL (optional)
  core              CORE_API_KEY (required; open-access full texts)

PO_SCHOLAR_SOURCES  comma list to choose/order sources, e.g.
                    "semantic_scholar,openalex,crossref,arxiv"
S2_MIN_INTERVAL_SEC minimum seconds between Semantic Scholar requests
                    (default 1.1: the key allows 1 req/s across ALL endpoints,
                    and this limit is shared across threads AND processes).
"""

import contextlib
import datetime
import html
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv  # type: ignore
from thefuzz import fuzz  # type: ignore

cur_dir = os.path.dirname(os.path.realpath(__file__))
load_dotenv(os.path.join(cur_dir, "../.env"))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


# Query parameters that carry credentials (OpenAlex / NCBI take the key in the URL).
_SECRET_PARAM = re.compile(r"(?i)\b((?:api_?key|key|token|access_token|plus_token)=)[^&\s'\"<>)]+")


def _redact(text) -> str:
    """Error text safe for the run log: credential query parameters masked."""
    return _SECRET_PARAM.sub(r"\1***", str(text))


S2_API_KEY = _env("SEMANTIC_SCHOLAR_API_KEY") or None
print(f"Semantic Scholar API key: {'set' if S2_API_KEY else 'not set'}")

_USER_AGENT = "PaperOrchestra/1.0 (academic literature search)"


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """Minimum-interval limiter, shared across threads and (optionally) processes.

    Callers reserve the next free slot under a short lock and then sleep
    outside it. Cross-process sharing uses a tiny lock file plus a stamp file
    (the last reserved slot) in the temp dir, so two simultaneous desktop runs
    still respect a per-key limit together. If the temp dir cannot be used,
    the limiter quietly falls back to in-process limiting.
    """

    MAX_BACKOFF = 60.0

    # Adaptive pacing: each 429 stretches the interval (up to SLOW_MAX x the
    # base); a run of successes brings it back down step by step.
    SLOW_FACTOR = 1.5
    SLOW_MAX = 4.0
    SPEEDUP_AFTER = 20

    def __init__(self, name: str, min_interval: float, cross_process: bool = False):
        self.name = name
        self.min_interval = max(0.0, float(min_interval))
        self.base_interval = self.min_interval
        self._ok_streak = 0
        self.cross_process = cross_process
        self._lock = threading.Lock()
        self._last = 0.0
        base = os.path.join(tempfile.gettempdir(), "paper_orchestra_ratelimit")
        self._lock_path = os.path.join(base, f"{name}.lock")
        self._stamp_path = os.path.join(base, f"{name}.stamp")
        if cross_process:
            try:
                os.makedirs(base, exist_ok=True)
            except OSError:
                self.cross_process = False

    def _acquire_file_lock(self, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                try:  # stale lock (crashed process); the lock is only held for a few ms
                    if time.time() - os.path.getmtime(self._lock_path) > 10:
                        os.remove(self._lock_path)
                        continue
                except OSError:
                    pass
            except PermissionError:
                # Windows reports a lock file that is pending deletion this way;
                # anywhere else it means the directory is not ours to write.
                if not os.path.exists(self._lock_path):
                    break
            except OSError:
                break
            time.sleep(0.02)
        # Unusable or stuck: stop sharing across processes rather than stall every call.
        self.cross_process = False
        return False

    def _release_file_lock(self) -> None:
        try:
            os.remove(self._lock_path)
        except OSError:
            pass

    def _read_stamp(self) -> float:
        try:
            with open(self._stamp_path, "r") as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def _write_stamp(self, value: float) -> None:
        try:
            with open(self._stamp_path, "w") as f:
                f.write(repr(value))
        except OSError:
            pass

    def _update_shared(self, update: Callable[[float], float]) -> float:
        """Applies `update(shared_last)` under the file lock (if usable); returns the new value."""
        if self.cross_process and self._acquire_file_lock():
            try:
                value = update(max(self._last, self._read_stamp()))
                self._write_stamp(value)
                return value
            finally:
                self._release_file_lock()
        return update(self._last)

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            slot = self._update_shared(lambda last: max(time.time(), last + self.min_interval))
            self._last = slot
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)

    def slow_down(self) -> None:
        """After a 429: wait longer between requests from now on."""
        with self._lock:
            self._ok_streak = 0
            base = self.base_interval or 0.1
            new = min(base * self.SLOW_MAX, max(self.min_interval, base) * self.SLOW_FACTOR)
            changed = new > self.min_interval + 1e-9
            if changed:
                self.min_interval = new
        if changed:
            print(f"      ⏳ {self.name}: slowing down to one request every {new:.1f}s")

    def note_success(self) -> None:
        """After a 200: after SPEEDUP_AFTER in a row, step back towards the base interval."""
        with self._lock:
            if self.min_interval <= self.base_interval:
                return
            self._ok_streak += 1
            if self._ok_streak >= self.SPEEDUP_AFTER:
                self._ok_streak = 0
                self.min_interval = max(self.base_interval, self.min_interval / 1.25)

    def backoff(self, seconds: float) -> None:
        """Pushes the next allowed slot into the future (after a 429), at most MAX_BACKOFF."""
        seconds = min(max(0.0, float(seconds)), self.MAX_BACKOFF)
        # Relative to the base interval, which every process sharing the stamp
        # adds back (a locally slowed-down interval must not shorten the pause).
        target = time.time() + seconds - self.base_interval
        with self._lock:
            self._last = self._update_shared(lambda last: max(last, target))


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


_LIMITERS = {
    "semantic_scholar": RateLimiter("semantic_scholar", _float_env("S2_MIN_INTERVAL_SEC", 1.1), cross_process=True),
    "openalex": RateLimiter("openalex", 0.12),
    "crossref": RateLimiter("crossref", 0.2),
    "arxiv": RateLimiter("arxiv", 3.0, cross_process=True),  # arXiv API terms: 1 request / 3 s
    "dblp": RateLimiter("dblp", 1.0, cross_process=True),
    "europepmc": RateLimiter("europepmc", 0.12),
    "pubmed": RateLimiter("pubmed", 0.11 if _env("NCBI_API_KEY") else 0.35, cross_process=True),
    "core": RateLimiter("core", 1.0, cross_process=True),
}


# After this many 429s in a row a source is skipped for a while, so a shared
# rate-limit pool (e.g. Semantic Scholar without a key) does not cost ~14 s on
# every single lookup.
_COOLDOWN_AFTER_429 = 3
_COOLDOWN_SEC = 60.0
# Likewise after this many connection errors / timeouts in a row (DNS failure,
# refused or blocked host): an unreachable source is skipped instead of costing
# every lookup three attempts plus their back-off sleeps.
_COOLDOWN_AFTER_NET_ERRORS = 3
_NET_COOLDOWN_SEC = 300.0
# Server errors (5xx) in a row: the service is struggling; give it a break.
_COOLDOWN_AFTER_5XX = 2
_5XX_COOLDOWN_SEC = 120.0
# A source that asks for a longer wait than this (Retry-After: 60) is put on
# cooldown for that long instead of holding up the lookup; the next source is
# asked meanwhile.
_MAX_INLINE_WAIT = 10.0
_rate_state: Dict[str, Dict[str, float]] = {}
_rate_state_lock = threading.Lock()


def _state(source: str) -> Dict[str, float]:
    return _rate_state.setdefault(source, {"streak": 0, "net": 0, "srv": 0, "until": 0.0, "why": ""})


def _cool_down(source: str, seconds: float, why: str, message: str) -> None:
    with _rate_state_lock:
        st = _state(source)
        st["until"] = max(st.get("until", 0.0), time.time() + seconds)
        st["why"] = why
    print(message)


def _note_status(source: str, status: int) -> None:
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0
    srv_cool = False
    with _rate_state_lock:
        st = _state(source)
        st["net"] = 0  # the host answered
        if status >= 500:
            st["srv"] = st.get("srv", 0) + 1
            if st["srv"] >= _COOLDOWN_AFTER_5XX:
                st["srv"] = 0
                srv_cool = True
        else:
            st["srv"] = 0
    if srv_cool:
        _cool_down(source, _5XX_COOLDOWN_SEC, f"skipped for {_5XX_COOLDOWN_SEC:.0f}s after repeated server errors",
                   f"      ⏸ {source} is overloaded (HTTP {status} again); skipping it for {_5XX_COOLDOWN_SEC:.0f}s")
    with _rate_state_lock:
        st = _state(source)
        if status != 429:
            st["streak"] = 0
            return
        st["streak"] += 1
        if st["streak"] >= _COOLDOWN_AFTER_429:
            st["streak"] = 0
            st["until"] = max(st.get("until", 0.0), time.time() + _COOLDOWN_SEC)  # never shortens one
            st["why"] = f"skipped for {_COOLDOWN_SEC:.0f}s after repeated HTTP 429"
            print(f"      ⏸ {source} keeps answering HTTP 429; skipping it for {_COOLDOWN_SEC:.0f}s")


def _note_net_error(source: str, err: Exception) -> bool:
    """Counts a connection error / timeout; True when the source is now skipped for a while."""
    if not isinstance(err, (requests.ConnectionError, requests.Timeout)):
        return False
    with _rate_state_lock:
        st = _state(source)
        st["net"] += 1
        if st["net"] < _COOLDOWN_AFTER_NET_ERRORS:
            return False
        st["net"] = 0
        st["until"] = max(st.get("until", 0.0), time.time() + _NET_COOLDOWN_SEC)
        st["why"] = f"skipped for {_NET_COOLDOWN_SEC:.0f}s after repeated connection errors"
    print(f"      ⏸ {source} is unreachable; skipping it for {_NET_COOLDOWN_SEC:.0f}s")
    return True


def _cooling_down(source: str) -> bool:
    with _rate_state_lock:
        return time.time() < _rate_state.get(source, {}).get("until", 0.0)


def _cooldown_reason(source: str) -> str:
    with _rate_state_lock:
        return str(_rate_state.get(source, {}).get("why") or "skipped for a while")


# --------------------------------------------------------------------------
# Time budget for a lookup phase
# --------------------------------------------------------------------------

_budget = {"deadline": 0.0, "minutes": 0.0, "announced": False}
_budget_lock = threading.Lock()


def start_time_budget(minutes: Optional[float] = None) -> None:
    """Starts the literature-lookup time budget (PO_ENRICH_TIME_BUDGET_MIN,
    default 15 minutes; 0 = no limit). Past it, every source is asked once
    and nobody waits on back-offs, so a struggling service cannot hold up the
    run. Calling it again while a budget runs keeps the first deadline."""
    if minutes is None:
        minutes = _float_env("PO_ENRICH_TIME_BUDGET_MIN", 15.0)
    with _budget_lock:
        if _budget["deadline"] and time.time() < _budget["deadline"]:
            return
        _budget["minutes"] = max(0.0, float(minutes))
        _budget["deadline"] = time.time() + _budget["minutes"] * 60 if _budget["minutes"] > 0 else 0.0
        _budget["announced"] = False


def reset_time_budget() -> None:
    with _budget_lock:
        _budget.update(deadline=0.0, minutes=0.0, announced=False)


def _over_budget() -> bool:
    with _budget_lock:
        if not _budget["deadline"] or time.time() < _budget["deadline"]:
            return False
        announce = not _budget["announced"]
        _budget["announced"] = True
    if announce:
        print(f"      ⏱ Literature lookups have run {_budget['minutes']:.0f} min; from now on each source "
              f"is asked once, without waiting (PO_ENRICH_TIME_BUDGET_MIN).")
    return True


# --------------------------------------------------------------------------
# Connectivity probes (tools/check_providers.py --scholar)
# --------------------------------------------------------------------------

# Per-thread record of the last HTTP outcome per source, kept only inside
# probe_mode(). Diagnostics only: the search functions keep returning [] on
# failure, as before.
_diag = threading.local()


def _note_http(source: str, status: Optional[int] = None, error: Optional[str] = None,
               elapsed: float = 0.0) -> None:
    try:
        last = getattr(_diag, "last", None)
        if last is None or not getattr(_diag, "probe", False):
            return
        prev = last.get(source) or {}
        last[source] = {
            "status": status,
            "error": error,
            "elapsed_ms": int(prev.get("elapsed_ms", 0) + elapsed * 1000),
        }
    except Exception:
        pass


def last_http_result(source: str) -> Optional[Dict]:
    """{"status", "error", "elapsed_ms"} of this thread's last request to `source`, or None."""
    return (getattr(_diag, "last", None) or {}).get(source)


@contextlib.contextmanager
def probe_mode():
    """One quick attempt per request (no retries) for connectivity checks in this thread.

    Requests still go through the per-source rate limiters. The diagnostics of
    last_http_result() start empty inside the block.
    """
    prev_probe, prev_last = getattr(_diag, "probe", False), getattr(_diag, "last", None)
    _diag.probe, _diag.last = True, {}
    try:
        yield
    finally:
        _diag.probe, _diag.last = prev_probe, prev_last


# "Come back later" answers: 202 (dblp queues a query while it is busy and asks
# the client to retry), 408, 429 and any 5xx. They are retried with backoff.
_TRANSIENT_STATUSES = (202, 408, 429)
# A connectivity probe retries a transient answer once, waiting at most this long.
_PROBE_MAX_WAIT = 3.0


def is_transient_status(status) -> bool:
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status in _TRANSIENT_STATUSES or status >= 500


def _http_get(source: str, url: str, *, params=None, headers=None, timeout=15, max_retries=3):
    """GET with per-source throttling and backoff on transient answers (202/408/429/5xx).
    Returns Response or None."""
    if _cooling_down(source):
        _note_http(source, error=_cooldown_reason(source))
        return None
    probing = getattr(_diag, "probe", False)
    if probing:
        max_retries, timeout = 2, min(timeout, 20)
    hurry = not probing and _over_budget()
    if hurry:
        max_retries = 1
    limiter = _LIMITERS[source]
    hdrs = {"User-Agent": _USER_AGENT}
    if headers:
        hdrs.update(headers)
    for attempt in range(max_retries):
        limiter.wait()
        t0 = time.time()
        try:
            rsp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        except requests.RequestException as e:
            # The message includes the request URL, query string and all.
            _note_http(source, error=_redact(e), elapsed=time.time() - t0)
            skipped = _note_net_error(source, e)
            if isinstance(e, requests.Timeout) and attempt + 1 < max_retries and not (skipped or probing):
                # A slow server, not a failure yet: shown like the 429 backoffs.
                print(f"      ⏳ {source} slow to answer (no reply in {timeout}s); "
                      f"trying again ({attempt + 2}/{max_retries})")
            else:
                print(f"      ⚠️ {source} request error: {_redact(e)}")
            if skipped or probing:
                break
            time.sleep(1.5 * (attempt + 1))
            continue
        _note_status(source, rsp.status_code)
        _note_http(source, status=rsp.status_code,
                   error=None if rsp.status_code == 200 else f"HTTP {rsp.status_code}",
                   elapsed=time.time() - t0)
        if rsp.status_code == 429:
            limiter.slow_down()
        elif rsp.status_code == 200:
            limiter.note_success()
        if is_transient_status(rsp.status_code):
            retry_after = rsp.headers.get("Retry-After")
            try:
                wait_s = float(retry_after) if retry_after else 2.0 * (2 ** attempt)
            except ValueError:
                wait_s = 2.0 * (2 ** attempt)
            wait_s = min(max(wait_s, 0.0), _PROBE_MAX_WAIT if probing else RateLimiter.MAX_BACKOFF)
            if _cooling_down(source):
                return None
            if not probing and (wait_s > _MAX_INLINE_WAIT or hurry or attempt + 1 >= max_retries):
                # Waiting here would hold up the whole lookup: let the source
                # rest that long and ask the next one now.
                if wait_s > _MAX_INLINE_WAIT or hurry:
                    rest = max(wait_s, _MAX_INLINE_WAIT)
                    _cool_down(source, rest, f"asked to wait {rest:.0f}s (HTTP {rsp.status_code})",
                               f"      ⏸ {source} HTTP {rsp.status_code}, asked to wait {rest:.0f}s; "
                               f"using the other sources meanwhile")
                    limiter.backoff(wait_s)
                return None
            print(f"      ⏳ {source} HTTP {rsp.status_code}; backing off {wait_s:.1f}s")
            limiter.backoff(wait_s)
            if _cooling_down(source):
                return None
            continue
        return rsp
    return None


# --------------------------------------------------------------------------
# Date helpers
# --------------------------------------------------------------------------


def _parse_cutoff(cutoff_date: Optional[str]):
    if not cutoff_date:
        return None
    try:
        parts = [int(x) for x in str(cutoff_date).split("-")[:2]]
        return datetime.datetime(parts[0], parts[1] if len(parts) > 1 else 12, 1)
    except Exception:
        return None


def _before_cutoff(pub_date: Optional[str], year, cutoff_date: Optional[str]) -> bool:
    cutoff_dt = _parse_cutoff(cutoff_date)
    if cutoff_dt is None:
        return True
    if pub_date:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                return datetime.datetime.strptime(str(pub_date)[: len(fmt) + 2], fmt) < cutoff_dt
            except ValueError:
                continue
    if year:
        try:
            y = int(year)
        except (TypeError, ValueError):
            return True
        return y < cutoff_dt.year or (y == cutoff_dt.year and cutoff_dt.month > 1)
    return True


def _filter_cutoff(items: List[Dict], cutoff_date: Optional[str]) -> List[Dict]:
    return [r for r in items if _before_cutoff(r.get("publicationDate"), r.get("year"), cutoff_date)]


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)  # JATS / HTML tags (Crossref, Europe PMC)
    return " ".join(html.unescape(text).split())


def _paper(title, authors, year=None, venue="", abstract="", citations=0, pub_date=None,
           doi=None, url=None, source="", journal=None) -> Dict:
    """Builds a Semantic-Scholar-shaped record."""
    return {
        "title": _clean_text(title),
        "authors": [{"name": a} for a in authors if a],
        "year": int(year) if str(year or "").isdigit() else None,
        "venue": venue or "",
        "abstract": _clean_text(abstract),
        "citationCount": citations or 0,
        "publicationDate": pub_date,
        "journal": journal or ({"name": venue} if venue else None),
        "doi": doi,
        "url": url,
        "source": source,
    }


# --------------------------------------------------------------------------
# Semantic Scholar
# --------------------------------------------------------------------------

_S2_FIELDS = "title,authors,venue,year,abstract,citationCount,journal,publicationDate,externalIds,url"


def _s2_headers() -> Dict[str, str]:
    key = _env("SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": key} if key else {}


def _s2_search_checked(query: str, limit: int = 10) -> Tuple[List[Dict], bool]:
    """(results, answered): answered is False when Semantic Scholar gave no HTTP 200
    (unreachable, rate-limited after the retries, key rejected, server error)."""
    rsp = _http_get(
        "semantic_scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": query, "limit": limit, "fields": _S2_FIELDS},
        headers=_s2_headers(),
    )
    if rsp is None or rsp.status_code != 200:
        if rsp is not None and rsp.status_code in (401, 403):
            print("      ⚠️ Semantic Scholar rejected the API key (HTTP %d)." % rsp.status_code)
        return [], False
    items = rsp.json().get("data", []) or []
    for r in items:
        r["source"] = "semantic_scholar"
        r["doi"] = (r.get("externalIds") or {}).get("DOI")
    return items, True


def s2_search(query: str, limit: int = 10) -> List[Dict]:
    return _s2_search_checked(query, limit)[0]


def s2_title_search(
    title_query: str, year_hint: int = None, cutoff_date: str = None
) -> Optional[Dict]:
    try:
        results = s2_search(title_query, limit=3)
        return _best_title_match(title_query, results, year_hint, cutoff_date)
    except Exception as e:
        print(f"      ⚠️ S2 Error for '{title_query}': {_redact(e)}")
        return None


def s2_keyword_search(query: str, limit: int = 10, cutoff_date: str = None) -> List[Dict]:
    """Keyword search against Semantic Scholar API for non-Gemini literature discovery."""
    try:
        return _filter_cutoff(s2_search(query, limit), cutoff_date)
    except Exception as e:
        print(f"      ⚠️ S2 Keyword Search Error for '{query}': {_redact(e)}")
        return []


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------


def _openalex_abstract(inv: Optional[Dict]) -> str:
    if not inv:
        return ""
    positions = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    return " ".join(w for _, w in sorted(positions))


def openalex_search(query: str, limit: int = 10) -> List[Dict]:
    params = {"search": query, "per-page": min(limit, 50)}
    if _env("OPENALEX_EMAIL"):
        params["mailto"] = _env("OPENALEX_EMAIL")
    if _env("OPENALEX_API_KEY"):
        params["api_key"] = _env("OPENALEX_API_KEY")
    rsp = _http_get("openalex", "https://api.openalex.org/works", params=params)
    if rsp is None or rsp.status_code != 200:
        return []
    out = []
    for w in rsp.json().get("results", []) or []:
        src = ((w.get("primary_location") or {}).get("source") or {})
        biblio = w.get("biblio") or {}
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        venue = src.get("display_name") or ""
        pages = "--".join(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p) or None
        out.append(_paper(
            w.get("title") or w.get("display_name"),
            [(a.get("author") or {}).get("display_name") for a in w.get("authorships", []) or []],
            year=w.get("publication_year"), venue=venue,
            abstract=_openalex_abstract(w.get("abstract_inverted_index")),
            citations=w.get("cited_by_count"), pub_date=w.get("publication_date"),
            doi=doi, url=w.get("id"), source="openalex",
            journal={"name": venue, "volume": biblio.get("volume"), "pages": pages} if venue else None,
        ))
    return out


# --------------------------------------------------------------------------
# Crossref
# --------------------------------------------------------------------------


def _crossref_date(item: Dict) -> Optional[str]:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            return "-".join(f"{int(p):02d}" if i else str(int(p)) for i, p in enumerate(parts))
    return None


def crossref_search(query: str, limit: int = 10) -> List[Dict]:
    params = {"query.bibliographic": query, "rows": min(limit, 50)}
    if _env("CROSSREF_MAILTO"):
        params["mailto"] = _env("CROSSREF_MAILTO")
    headers = {}
    if _env("CROSSREF_PLUS_TOKEN"):
        headers["Crossref-Plus-API-Token"] = f"Bearer {_env('CROSSREF_PLUS_TOKEN')}"
    rsp = _http_get("crossref", "https://api.crossref.org/works", params=params, headers=headers)
    if rsp is None or rsp.status_code != 200:
        return []
    out = []
    for it in (rsp.json().get("message") or {}).get("items", []) or []:
        title = (it.get("title") or [""])[0]
        if not title:
            continue
        pub_date = _crossref_date(it)
        venue = (it.get("container-title") or [""])[0]
        authors = [" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name")
                   for a in it.get("author", []) or []]
        out.append(_paper(
            title, authors, year=(pub_date or "")[:4], venue=venue,
            abstract=it.get("abstract"), citations=it.get("is-referenced-by-count"),
            pub_date=pub_date if pub_date and len(pub_date) == 10 else None,
            doi=it.get("DOI"), url=it.get("URL"), source="crossref",
            journal={"name": venue, "volume": it.get("volume"), "pages": it.get("page")} if venue else None,
        ))
    return out


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------


def arxiv_search(query: str, limit: int = 8) -> List[Dict]:
    rsp = _http_get(
        "arxiv", "https://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "start": 0, "max_results": limit, "sortBy": "relevance"},
    )
    if rsp is None or rsp.status_code != 200:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for entry in ET.fromstring(rsp.text).findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        if title_el is None or not title_el.text:
            continue
        pub_el = entry.find("atom:published", ns)
        pub_date = pub_el.text[:10] if pub_el is not None and pub_el.text else None
        summary_el = entry.find("atom:summary", ns)
        id_el = entry.find("atom:id", ns)
        doi_el = entry.find("arxiv:doi", ns)
        out.append(_paper(
            title_el.text,
            [(a.findtext("atom:name", default="", namespaces=ns)) for a in entry.findall("atom:author", ns)],
            year=(pub_date or "")[:4], venue="arXiv",
            abstract=summary_el.text if summary_el is not None else "",
            pub_date=pub_date, doi=doi_el.text if doi_el is not None else None,
            url=id_el.text if id_el is not None else None, source="arxiv",
        ))
    return out


def arxiv_keyword_search(query: str, max_results: int = 8, cutoff_date: str = None) -> List[Dict]:
    """Fallback keyword search using the public arXiv Atom API."""
    try:
        return _filter_cutoff(arxiv_search(query, max_results), cutoff_date)
    except Exception as e:
        print(f"      ⚠️ arXiv Search Error for '{query}': {_redact(e)}")
        return []


# --------------------------------------------------------------------------
# DBLP (computer science bibliography; no abstracts)
# --------------------------------------------------------------------------


def dblp_search(query: str, limit: int = 10) -> List[Dict]:
    rsp = _http_get("dblp", "https://dblp.org/search/publ/api",
                    params={"q": query, "format": "json", "h": min(limit, 100)})
    if rsp is None or rsp.status_code != 200:
        return []
    hits = (((rsp.json().get("result") or {}).get("hits") or {}).get("hit")) or []
    out = []
    for h in hits:
        info = h.get("info") or {}
        authors = (info.get("authors") or {}).get("author") or []
        if isinstance(authors, dict):
            authors = [authors]
        title = re.sub(r"\.$", "", info.get("title") or "")
        out.append(_paper(
            title, [re.sub(r"\s\d{4}$", "", a.get("text", "")) for a in authors],
            year=info.get("year"), venue=info.get("venue") if isinstance(info.get("venue"), str) else "",
            doi=info.get("doi"), url=info.get("ee") if isinstance(info.get("ee"), str) else info.get("url"),
            source="dblp",
        ))
    return out


# --------------------------------------------------------------------------
# Europe PMC
# --------------------------------------------------------------------------


def europepmc_search(query: str, limit: int = 10) -> List[Dict]:
    rsp = _http_get("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": query, "format": "json", "resultType": "core", "pageSize": min(limit, 100)})
    if rsp is None or rsp.status_code != 200:
        return []
    out = []
    for r in ((rsp.json().get("resultList") or {}).get("result")) or []:
        # fullName is "He K" (surname first); BibTeX would read "K" as the surname.
        authors = [
            " ".join(p for p in (a.get("firstName"), a.get("lastName")) if p)
            or a.get("collectiveName") or a.get("fullName")
            for a in ((r.get("authorList") or {}).get("author") or [])
        ]
        venue = ((r.get("journalInfo") or {}).get("journal") or {}).get("title") or r.get("journalTitle") or ""
        out.append(_paper(
            r.get("title"), authors, year=r.get("pubYear"), venue=venue,
            abstract=r.get("abstractText"), citations=r.get("citedByCount"),
            pub_date=r.get("firstPublicationDate"), doi=r.get("doi"), source="europepmc",
        ))
    return out


# --------------------------------------------------------------------------
# PubMed (NCBI E-utilities)
# --------------------------------------------------------------------------


def pubmed_search(query: str, limit: int = 10) -> List[Dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    common = {"tool": "paper_orchestra"}
    if _env("NCBI_API_KEY"):
        common["api_key"] = _env("NCBI_API_KEY")
    if _env("NCBI_EMAIL"):
        common["email"] = _env("NCBI_EMAIL")
    rsp = _http_get("pubmed", f"{base}/esearch.fcgi",
                    params={**common, "db": "pubmed", "term": query, "retmax": limit, "retmode": "json"})
    if rsp is None or rsp.status_code != 200:
        return []
    ids = ((rsp.json().get("esearchresult") or {}).get("idlist")) or []
    if not ids:
        return []
    rsp = _http_get("pubmed", f"{base}/efetch.fcgi",
                    params={**common, "db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    if rsp is None or rsp.status_code != 200:
        return []
    out = []
    for art in ET.fromstring(rsp.content).findall(".//PubmedArticle"):
        a = art.find(".//Article")
        if a is None:
            continue
        title = "".join(a.find("ArticleTitle").itertext()) if a.find("ArticleTitle") is not None else ""
        abstract = " ".join("".join(x.itertext()) for x in a.findall(".//Abstract/AbstractText"))
        authors = []
        for au in a.findall(".//AuthorList/Author"):
            name = " ".join(p for p in (au.findtext("ForeName"), au.findtext("LastName")) if p)
            authors.append(name or au.findtext("CollectiveName"))
        year = (
            a.findtext(".//Journal/JournalIssue/PubDate/Year")
            or a.findtext("ArticleDate/Year")
            or (re.match(r"\s*(\d{4})", a.findtext(".//Journal/JournalIssue/PubDate/MedlineDate") or "")
                or [None, ""])[1]
        )
        # The article's own ids only: ReferenceList entries carry ArticleIdList too.
        doi = None
        for eid in art.findall("./PubmedData/ArticleIdList/ArticleId"):
            if eid.get("IdType") == "doi" and eid.text:
                doi = eid.text.strip()
        if not doi:
            el = a.find("ELocationID[@EIdType='doi']")
            doi = el.text.strip() if el is not None and el.text else None
        pmid = art.findtext(".//PMID")
        out.append(_paper(
            title, authors, year=year, venue=a.findtext(".//Journal/Title") or "",
            abstract=abstract, doi=doi, source="pubmed",
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        ))
    return out


# --------------------------------------------------------------------------
# CORE (open access aggregator; key required)
# --------------------------------------------------------------------------


def core_search(query: str, limit: int = 10) -> List[Dict]:
    key = _env("CORE_API_KEY")
    if not key:
        return []
    rsp = _http_get("core", "https://api.core.ac.uk/v3/search/works",
                    params={"q": query, "limit": min(limit, 100)},
                    headers={"Authorization": f"Bearer {key}"})
    if rsp is None or rsp.status_code != 200:
        return []
    out = []
    for r in rsp.json().get("results", []) or []:
        venue = ""
        journals = r.get("journals") or []
        if journals and isinstance(journals[0], dict):
            venue = journals[0].get("title") or ""
        out.append(_paper(
            r.get("title"), [a.get("name") for a in r.get("authors", []) or []],
            year=r.get("yearPublished"), venue=venue or r.get("publisher") or "",
            abstract=r.get("abstract"), citations=r.get("citationCount"),
            pub_date=(r.get("publishedDate") or "")[:10] or None, doi=r.get("doi"),
            url=(r.get("links") or [{}])[0].get("url") if r.get("links") else None, source="core",
        ))
    return out


# --------------------------------------------------------------------------
# Multi-source orchestration
# --------------------------------------------------------------------------

SOURCES: Dict[str, Callable[[str, int], List[Dict]]] = {
    "semantic_scholar": s2_search,
    "openalex": openalex_search,
    "crossref": crossref_search,
    "arxiv": arxiv_search,
    "dblp": dblp_search,
    "europepmc": europepmc_search,
    "pubmed": pubmed_search,
    "core": core_search,
}
_ALIASES = {"s2": "semantic_scholar", "semanticscholar": "semantic_scholar", "epmc": "europepmc"}
_DEFAULT_ORDER = ["semantic_scholar", "openalex", "crossref", "arxiv", "dblp", "europepmc", "pubmed", "core"]


def enabled_sources() -> List[str]:
    raw = _env("PO_SCHOLAR_SOURCES")
    names = [s.strip().lower() for s in raw.split(",") if s.strip()] if raw else list(_DEFAULT_ORDER)
    names = [_ALIASES.get(n, n) for n in names]
    out = []
    for n in names:
        if n not in SOURCES:
            print(f"      ⚠️ Unknown scholar source '{n}' in PO_SCHOLAR_SOURCES (ignored)")
            continue
        if n == "core" and not _env("CORE_API_KEY"):
            continue
        if n not in out:
            out.append(n)
    return out


def _norm_title(t: str) -> str:
    """Case- and punctuation-insensitive key; keeps CJK and other non-Latin letters."""
    return re.sub(r"[\W_]+", "", (t or "").casefold())


def _title_score(title_query: str, r: Dict, year_hint=None) -> int:
    score = fuzz.ratio(title_query.lower(), (r.get("title") or "").lower())
    if year_hint and r.get("year") == year_hint:
        score += 10
    return score


def _best_title_match(title_query, results, year_hint=None, cutoff_date=None, threshold=70):
    best, best_score = None, 0
    for r in results or []:
        if not r.get("title"):
            continue
        if not _before_cutoff(r.get("publicationDate"), r.get("year"), cutoff_date):
            continue
        score = _title_score(title_query, r, year_hint)
        if score > best_score:
            best, best_score = r, score
    return best if best_score > threshold else None


def _same_paper(a: Dict, b: Dict) -> bool:
    """True when two records from different sources describe the same work."""
    doi_a, doi_b = (a.get("doi") or "").strip().lower(), (b.get("doi") or "").strip().lower()
    if doi_a and doi_b:
        return doi_a == doi_b
    ya, yb = a.get("year"), b.get("year")
    if ya and yb and abs(int(ya) - int(yb)) > 1:
        return False
    ta, tb = _norm_title(a.get("title")), _norm_title(b.get("title"))
    return bool(ta) and (ta == tb or fuzz.ratio(ta, tb) >= 95)


def title_lookup(
    title_query: str, year_hint: int = None, cutoff_date: str = None, require_abstract: bool = True,
    sources: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Finds metadata for a known title, trying each enabled source (or `sources`) in order.

    A hit without an abstract is kept as a fallback and, if a later source has the
    same paper (same DOI, or near-identical title and year) with an abstract, the
    abstract is merged in.
    """
    fallback = None
    for name in (enabled_sources() if sources is None else sources):
        if name == "dblp" and require_abstract and fallback is not None:
            continue  # DBLP never has abstracts
        try:
            hit = _best_title_match(title_query, SOURCES[name](title_query, 3 if name != "pubmed" else 2),
                                    year_hint, cutoff_date)
        except Exception as e:
            print(f"      ⚠️ {name} lookup error for '{title_query[:60]}': {_redact(e)}")
            continue
        if not hit:
            continue
        if hit.get("abstract") or not require_abstract:
            if fallback is None:
                return hit
            if _same_paper(fallback, hit):
                merged = dict(fallback)
                merged["abstract"] = hit["abstract"]
                merged["source"] = f"{fallback.get('source')}+{name}"
                return merged
            # A different work (a survey or "Correction to: ..." of the same
            # title): never borrow its abstract. Use it only if it matches the
            # query at least as well as the abstract-less record.
            if _title_score(title_query, hit, year_hint) >= _title_score(title_query, fallback, year_hint):
                return hit
            continue
        if fallback is None:
            fallback = hit
    return fallback


def enrich_lookup(title_query: str, year_hint: int = None, cutoff_date: str = None) -> Optional[Dict]:
    """Phase 1b metadata lookup for a candidate title.

    Semantic Scholar first, exactly as the original enrichment did: when it
    answers, its best match (or None) is the result, so the bibliography is the
    one the original code built. The other enabled sources are asked only when
    Semantic Scholar is disabled (PO_SCHOLAR_SOURCES) or could not answer.
    """
    sources = enabled_sources()
    if "semantic_scholar" in sources:
        try:
            results, answered = _s2_search_checked(title_query, 3)
        except Exception as e:
            print(f"      ⚠️ S2 Error for '{title_query}': {_redact(e)}")
            results, answered = [], False
        if answered:
            return _best_title_match(title_query, results, year_hint, cutoff_date)
    others = [n for n in sources if n != "semantic_scholar"]
    if not others:
        return None
    return title_lookup(title_query, year_hint, cutoff_date, sources=others)


def multi_keyword_search(
    query: str, limit: int = 10, cutoff_date: str = None, sources: Optional[List[str]] = None,
    per_source: int = 6,
) -> List[Dict]:
    """Queries every enabled source, de-duplicates by title (preferring records with abstracts)."""
    merged: Dict[str, Dict] = {}
    order: List[str] = []
    for name in sources or enabled_sources():
        try:
            hits = _filter_cutoff(SOURCES[name](query, per_source), cutoff_date)
        except Exception as e:
            print(f"      ⚠️ {name} search error for '{query[:60]}': {_redact(e)}")
            continue
        for h in hits:
            key = _norm_title(h.get("title"))
            if not key:
                continue
            if key not in merged:
                merged[key] = h
                order.append(key)
            elif not merged[key].get("abstract") and h.get("abstract"):
                merged[key] = {**merged[key], "abstract": h["abstract"]}
    return [merged[k] for k in order][:limit]
