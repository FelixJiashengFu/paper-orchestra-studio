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

"""Small file helpers for the optional run reports (stdlib only)."""

import datetime
import json
import os
import threading
from typing import Any


def now_iso() -> str:
    """Local time with its UTC offset, e.g. 2026-11-18T10:04:05+08:00."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_text(path: str, text: str) -> None:
    """Writes via a temp file + os.replace, so a reader never sees half a file.

    Falls back to a direct write when the replace is refused (e.g. on Windows
    while another process holds the target open).
    """
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
