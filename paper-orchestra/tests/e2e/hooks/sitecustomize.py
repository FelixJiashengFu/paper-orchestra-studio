"""Loaded automatically by Python when tests/e2e/hooks is on PYTHONPATH (e2e harness only).

1. Runs the interpreter's own sitecustomize.py first (if any), so this file
   shadows nothing.
2. If PO_E2E_FAKE_SCHOLAR=1, installs the offline scholarly-API fake from
   po_e2e_fake_scholar.py into this process (the paper_writing_cli.py
   subprocess started by run_e2e.py). The pipeline code itself is not touched.
"""

import importlib.util
import os
import sys


def _chain_other_sitecustomize() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for entry in sys.path:
        try:
            if not entry or os.path.abspath(entry) == here:
                continue
            candidate = os.path.join(entry, "sitecustomize.py")
            if os.path.isfile(candidate):
                spec = importlib.util.spec_from_file_location("_po_e2e_chained_sitecustomize", candidate)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return
        except Exception as e:  # never break interpreter start-up
            sys.stderr.write(f"[e2e sitecustomize] chained sitecustomize failed: {e}\n")
            return


_chain_other_sitecustomize()

if os.environ.get("PO_E2E_FAKE_SCHOLAR") == "1" or os.environ.get("PO_E2E_INJECT_FAULTS") == "1":
    _here = os.path.dirname(os.path.abspath(__file__))
    try:
        sys.path.insert(0, _here)
        if os.environ.get("PO_E2E_FAKE_SCHOLAR") == "1":
            import po_e2e_fake_scholar

            po_e2e_fake_scholar.install()
        if os.environ.get("PO_E2E_INJECT_FAULTS") == "1":
            import po_e2e_faults

            po_e2e_faults.install()
    except Exception as e:
        sys.stderr.write(f"[e2e sitecustomize] could not install the e2e hooks: {e}\n")
        raise
    finally:
        try:
            sys.path.remove(_here)
        except ValueError:
            pass
