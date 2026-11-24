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
Optional per-run reports around write_single_paper():

  usage_report.json                   always (also when the run fails), see utils/usage_tracker.py
  citation_audit.json / citation_audit.md   after a successful run (final PDF copied),
                                      see utils/citation_audit.py

Purely additive: the wrapped function's arguments, return value, exceptions
and outputs are unchanged, and a failing report only prints a warning.
"""

import functools
import inspect
import os

from utils import usage_tracker


def _warn(what: str, err: BaseException) -> None:
    try:
        print(f"Warning: {what} failed ({type(err).__name__}: {err}); the paper run is not affected.")
    except Exception:
        pass


def _base_dir(sig: inspect.Signature, args, kwargs):
    try:
        return sig.bind_partial(*args, **kwargs).arguments.get("base_dir")
    except Exception:
        return None


def _remove_stale_audit(base_dir: str) -> None:
    from utils.author_report import REPORT_JSON_NAME, REPORT_MD_NAME
    from utils.citation_audit import CITED_BIB_NAME, JSON_NAME, MD_NAME

    for name in (JSON_NAME, MD_NAME, CITED_BIB_NAME, REPORT_MD_NAME, REPORT_JSON_NAME):
        path = os.path.join(base_dir, name)
        if os.path.isfile(path):
            os.remove(path)


def run_author_report(base_dir: str) -> None:
    """Best-effort author delivery report (`report.md`). Never raises."""
    try:
        from utils.author_report import generate_author_report

        rep = generate_author_report(base_dir)
        print(f" >> Author report: {rep.get('summary', 'ok')} (report.md)")
    except Exception as e:
        _warn("author report", e)


def run_citation_audit(base_dir: str) -> None:
    """Best-effort citation audit of a finished run folder. Never raises."""
    try:
        from utils.citation_audit import audit_run

        audit = audit_run(base_dir)
        s = audit.get("summary", {})
        print(
            f" >> Citation audit: {s.get('cited', 0)} cited, {s.get('missing_in_bib', 0)} missing in .bib, "
            f"{s.get('uncited', 0)} uncited, {s.get('verified', 0)}/{s.get('bib_entries', 0)} verified "
            "(citation_audit.md)"
        )
    except Exception as e:
        _warn("citation audit", e)


def with_run_reports(func):
    """Decorator for write_single_paper(base_dir, ...)."""
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        base_dir = _base_dir(sig, args, kwargs)
        try:
            if base_dir and os.path.isdir(base_dir):
                _remove_stale_audit(base_dir)
        except Exception as e:
            _warn("removing an old citation audit", e)
        usage_tracker.begin_run(base_dir)  # the report is only written once the folder exists

        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            if result is True and base_dir:
                run_citation_audit(base_dir)
                run_author_report(base_dir)
            try:
                if usage_tracker.end_run(base_dir):
                    t = usage_tracker.report(base_dir)["totals"]
                    print(
                        f" >> Token usage: {t['calls']} calls ({t['errors']} failed), "
                        f"{t['input_tokens']} input / {t['output_tokens']} output tokens (usage_report.json)"
                    )
            except Exception as e:
                _warn("writing usage_report.json", e)

    return wrapper
