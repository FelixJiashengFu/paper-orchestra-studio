"""Deterministic offline stand-in for the scholarly search APIs (e2e harness only).

The pipeline's literature agent talks to Semantic Scholar / arXiv (and, in the
multi-source version, OpenAlex, Crossref, DBLP, Europe PMC, PubMed, CORE) through
``requests``. ``install()`` patches ``requests.sessions.Session.request`` - the
call every ``requests.get`` ends in - so that:

  * api.semanticscholar.org /graph/v1/paper/search -> S2-shaped JSON from FIXTURE_PAPERS
  * export.arxiv.org /api/query                    -> arXiv Atom XML from FIXTURE_PAPERS
  * the other scholarly hosts                      -> HTTP 404 (handled as "no hits")
  * 127.0.0.1 / localhost                          -> passed through untouched
  * anything else                                  -> requests.ConnectionError (no network)

Patching at the HTTP layer (instead of replacing search functions) feeds the
exact same records to the original single-source code (c68cd16) and to the new
multi-source code, and keeps the real parsers, cutoff filters and per-source
rate limiters of utils/scholar_utils.py in the loop.

Every intercepted request is appended to the JSONL file named by
PO_E2E_SCHOLAR_LOG (host, path, query, status, hits).

All records below are synthetic test fixtures (fictional authors, DOIs under
the 10.5555 test prefix). They do not describe real publications.
"""

import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
from xml.sax.saxutils import escape

FIXTURE_PAPERS = [
    {
        "id": "e2e0001",
        "title": "Temporal Transformers for Video Recognition",
        "authors": ["Ada Lindqvist", "Bruno Okafor"],
        "year": 2021,
        "date": "2021-06-01",
        "venue": "CVPR",
        "journal": None,
        "abstract": "We study transformer encoders that attend across frames for video recognition and report "
        "that factorised space-time attention is a strong baseline for action classification.",
        "doi": "10.5555/po-e2e.0001",
        "arxiv": "2101.00001",
        "citations": 420,
    },
    {
        "id": "e2e0002",
        "title": "Parameter-Efficient Adapters for Vision Backbones",
        "authors": ["Chen Wei", "Dana Moretti"],
        "year": 2022,
        "date": "2022-04-25",
        "venue": "ICLR",
        "journal": None,
        "abstract": "Small bottleneck adapters inserted into a frozen vision backbone recover most of the accuracy "
        "of full fine-tuning while training only a few percent of the parameters.",
        "doi": "10.5555/po-e2e.0002",
        "arxiv": "2202.00002",
        "citations": 210,
    },
    {
        "id": "e2e0003",
        "title": "Low-Rank Adaptation of Large Pretrained Models",
        "authors": ["Elif Demir"],
        "year": 2022,
        "date": "2022-02-10",
        "venue": "Journal of Machine Learning Research",
        "journal": {"name": "Journal of Machine Learning Research", "volume": "23", "pages": "1-24"},
        "abstract": "Weight updates of pretrained models are approximated with low-rank matrices, which reduces "
        "the number of trainable parameters and the memory needed for adaptation.",
        "doi": "10.5555/po-e2e.0003",
        "arxiv": "2202.00003",
        "citations": 980,
    },
    {
        "id": "e2e0004",
        "title": "A Survey of Efficient Video Understanding",
        "authors": ["Farah Nasser", "George Ito"],
        "year": 2023,
        "date": "2023-03-15",
        "venue": "ACM Computing Surveys",
        "journal": {"name": "ACM Computing Surveys", "volume": "55", "pages": "101-140"},
        "abstract": "This survey reviews token pruning, sparse attention and parameter-efficient tuning for video "
        "models and summarises their accuracy and compute trade-offs.",
        "doi": "10.5555/po-e2e.0004",
        "arxiv": "2303.00004",
        "citations": 75,
    },
    {
        "id": "e2e0005",
        "title": "Long-Range Temporal Modeling with Sparse Attention",
        "authors": ["Hana Kowalski"],
        "year": 2022,
        "date": "2022-11-28",
        "venue": "NeurIPS",
        "journal": None,
        "abstract": "Sparse attention patterns let video transformers model long temporal ranges at a cost that "
        "grows linearly with the number of frames.",
        "doi": "10.5555/po-e2e.0005",
        "arxiv": "2211.00005",
        "citations": 133,
    },
    {
        "id": "e2e0006",
        "title": "Kinetics-Style Benchmarks for Human Action Recognition",
        "authors": ["Ivan Petrov", "Julia Santos"],
        "year": 2019,
        "date": "2019-08-01",
        "venue": "ECCV",
        "journal": None,
        "abstract": "We describe a large benchmark of human action clips with a fixed train, validation and test "
        "split and top-1 accuracy as the main metric.",
        "doi": "10.5555/po-e2e.0006",
        "arxiv": "1908.00006",
        "citations": 1500,
    },
    {
        "id": "e2e0007",
        "title": "Decoupled Weight Decay for Adaptive Optimizers",
        "authors": ["Kai Brandt"],
        "year": 2019,
        "date": "2019-05-06",
        "venue": "ICLR",
        "journal": None,
        "abstract": "Decoupling weight decay from the gradient-based update of adaptive optimizers improves "
        "generalisation and simplifies hyper-parameter tuning.",
        "doi": "10.5555/po-e2e.0007",
        "arxiv": "1905.00007",
        "citations": 3100,
    },
    {
        "id": "e2e0008",
        "title": "Residual Networks Revisited for Video Backbones",
        "authors": ["Lena Fischer"],
        "year": 2020,
        "date": "2020-10-01",
        "venue": "ICCV",
        "journal": None,
        "abstract": "Residual convolutional networks remain competitive video backbones when trained with modern "
        "augmentation recipes.",
        "doi": "10.5555/po-e2e.0008",
        "arxiv": "2010.00008",
        "citations": 60,
    },
]

SCHOLAR_HOSTS_404 = (
    "api.openalex.org",
    "api.crossref.org",
    "dblp.org",
    "www.ebi.ac.uk",
    "eutils.ncbi.nlm.nih.gov",
    "api.core.ac.uk",
)

_STOP = {"with", "from", "that", "this", "their", "into", "using", "papers", "paper", "research",
         "report", "technical", "introducing", "find", "search", "identify", "highly", "cited", "establishing"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def search_corpus(query: str, limit: int) -> list:
    """Papers ranked by title-token overlap with the query (an exact title always ranks first)."""
    q_tokens, q_norm = _tokens(query), _norm(query)
    scored = []
    for p in FIXTURE_PAPERS:
        if q_norm and q_norm == _norm(p["title"]):
            scored.append((100, p["id"], p))
            continue
        overlap = len(q_tokens & _tokens(p["title"]))
        if overlap >= 2:
            scored.append((overlap, p["id"], p))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored[: max(0, int(limit or 0))]]


def _s2_record(p: dict) -> dict:
    return {
        "paperId": p["id"],
        "title": p["title"],
        "authors": [{"authorId": f"{p['id']}-a{i}", "name": a} for i, a in enumerate(p["authors"])],
        "venue": p["venue"],
        "year": p["year"],
        "abstract": p["abstract"],
        "citationCount": p["citations"],
        "journal": p["journal"],
        "publicationDate": p["date"],
        "externalIds": {"DOI": p["doi"], "ArXiv": p["arxiv"]},
        "url": f"https://www.semanticscholar.org/paper/{p['id']}",
    }


def _arxiv_feed(papers: list) -> str:
    entries = []
    for p in papers:
        authors = "".join(f"<author><name>{escape(a)}</name></author>" for a in p["authors"])
        entries.append(
            "<entry>"
            f"<id>http://arxiv.org/abs/{p['arxiv']}v1</id>"
            f"<published>{p['date']}T00:00:00Z</published>"
            f"<title>{escape(p['title'])}</title>"
            f"<summary>{escape(p['abstract'])}</summary>"
            f"{authors}"
            f"<arxiv:doi>{escape(p['doi'])}</arxiv:doi>"
            "</entry>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        "<title>fake arXiv query</title>" + "".join(entries) + "</feed>"
    )


def handle(url: str, params=None):
    """(status, content_type, body_text, hits) for a scholarly URL, or None if not a scholarly host."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    if isinstance(params, dict):
        query.update({k: v for k, v in params.items() if v is not None})
    if host == "api.semanticscholar.org":
        if "/paper/search" not in parsed.path:
            return 404, "application/json", "{}", 0
        hits = search_corpus(str(query.get("query", "")), int(query.get("limit", 10) or 10))
        body = {"total": len(hits), "offset": 0, "data": [_s2_record(p) for p in hits]}
        return 200, "application/json", json.dumps(body), len(hits)
    if host == "export.arxiv.org":
        q = str(query.get("search_query", ""))
        q = q[4:] if q.startswith("all:") else q
        hits = search_corpus(q, int(query.get("max_results", 8) or 8))
        return 200, "application/atom+xml", _arxiv_feed(hits), len(hits)
    if host in SCHOLAR_HOSTS_404 or host.endswith(".semanticscholar.org"):
        return 404, "application/json", '{"error": "offline e2e fixture"}', 0
    return None


_log_lock = threading.Lock()


def _log(entry: dict) -> None:
    path = os.environ.get("PO_E2E_SCHOLAR_LOG", "").strip()
    if not path:
        return
    entry = dict(entry, ts=round(time.time(), 3), pid=os.getpid())
    with _log_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _query_of(url: str, params) -> str:
    q = {k: v[-1] for k, v in parse_qs(urlparse(url).query).items()}
    if isinstance(params, dict):
        q.update(params)
    for k in ("query", "search_query", "search", "query.bibliographic", "q", "term"):
        if q.get(k):
            return str(q[k])[:200]
    return ""


def install() -> None:
    import requests
    from requests.models import Response
    from requests.structures import CaseInsensitiveDict

    session_cls = requests.sessions.Session
    if getattr(session_cls.request, "_po_e2e_fake", False):
        return
    original = session_cls.request

    def fake_request(self, method, url, params=None, **kwargs):
        host = (urlparse(str(url)).hostname or "").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            return original(self, method, url, params=params, **kwargs)
        result = handle(str(url), params)
        if result is None:
            _log({"host": host, "path": urlparse(str(url)).path, "blocked": True})
            raise requests.ConnectionError(f"network access to '{host}' is blocked by the PaperOrchestra e2e harness")
        status, ctype, body, hits = result
        _log({"host": host, "path": urlparse(str(url)).path, "query": _query_of(str(url), params),
              "status": status, "hits": hits})
        rsp = Response()
        rsp.status_code = status
        rsp._content = body.encode("utf-8")
        rsp.headers = CaseInsensitiveDict({"Content-Type": ctype})
        rsp.encoding = "utf-8"
        query = urlencode(params, doseq=True) if isinstance(params, dict) else ""
        rsp.url = str(url) + (("&" if "?" in str(url) else "?") + query if query else "")
        rsp.reason = "OK" if status == 200 else "Not Found"
        return rsp

    fake_request._po_e2e_fake = True
    session_cls.request = fake_request
