#!/usr/bin/env python3
"""Verify that every agent platform (the original five plus Muse) ships the same PaperOrchestra Studio feature set.

    python3 plugins/scripts/verify_platforms.py            # human-readable table
    python3 plugins/scripts/verify_platforms.py --json     # machine-readable report
    python3 plugins/scripts/verify_platforms.py --static   # skip launching servers

For each of Claude Code, Antigravity, Codex, Qoder, WorkBuddy and Muse this checks the
manifest, the MCP registration, the bundled files, the skills and agents, the
marketplace entry, and then starts both MCP servers exactly as the host would
(placeholders resolved to the bundle directory) and compares the tools,
resources and prompts they report against the shared source.

Exit status is 0 only when every check on every platform passes. What this
cannot prove is that a given host application loads the bundle; that part is a
manual smoke test described in plugins/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platforms as P  # noqa: E402
import sync_platforms  # noqa: E402

Check = Tuple[str, str, str]  # (name, "pass" | "fail" | "skip", detail)


def server_version() -> str:
    m = re.search(r'^SERVER_VERSION = "([^"]+)"', P.SHARED_SERVER.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else ""


def read_frontmatter(path: Path) -> Dict[str, str]:
    """Parse the top-level scalar keys of a YAML frontmatter block (folded scalars included)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out: Dict[str, str] = {}
    key: Optional[str] = None
    for line in m.group(1).split("\n"):
        top = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if top:
            key = top.group(1)
            val = top.group(2).strip()
            out[key] = "" if val in (">", ">-", "|", "|-") else val.strip("\"'")
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def rpc(command: List[str], cwd: Path, env: Dict[str, str], requests: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Run a stdio MCP server, send newline-delimited JSON-RPC requests, return responses by id."""
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(command, cwd=str(cwd), env=env, input=payload, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    out: Dict[int, Dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and isinstance(msg.get("id"), int):
            out[msg["id"]] = msg
    if not out:
        raise RuntimeError(f"no JSON-RPC reply (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return out


HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "verify_platforms", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
    {"jsonrpc": "2.0", "id": 4, "method": "prompts/list"},
]


def fingerprint(command: List[str], cwd: Path, env: Dict[str, str]) -> Dict[str, Any]:
    """What a host sees when it connects: server info, instructions, tools, resources, prompts, UI payloads."""
    r = rpc(command, cwd, env, HANDSHAKE)
    init = r[1]["result"]
    resources = [x["uri"] for x in r.get(3, {}).get("result", {}).get("resources", [])]
    fp: Dict[str, Any] = {
        "version": init.get("serverInfo", {}).get("version", ""),
        "instructions": bool(init.get("instructions")),
        "tools": sorted(t["name"] for t in r[2]["result"]["tools"]),
        "resources": sorted(resources),
        "prompts": sorted(x["name"] for x in r.get(4, {}).get("result", {}).get("prompts", [])),
    }
    ui_uris = [u for u in resources if u.startswith("ui://")]
    if ui_uris:
        reads = rpc(command, cwd, env, HANDSHAKE[:2] + [
            {"jsonrpc": "2.0", "id": 10 + i, "method": "resources/read", "params": {"uri": u}} for i, u in enumerate(ui_uris)
        ])
        sizes = {}
        for i, u in enumerate(ui_uris):
            res = reads.get(10 + i, {})
            contents = res.get("result", {}).get("contents", [])
            sizes[u] = len(contents[0].get("text", "")) if contents else -1
        fp["ui_bytes"] = sizes
    return fp


def resolve(value: str, plat: P.Platform, data_dir: Path) -> str:
    if plat.root_var:
        value = value.replace(plat.root_var, str(plat.root))
    for placeholder, replacement in plat.host_vars.items():
        value = value.replace(placeholder, replacement.replace("{data}", str(data_dir)))
    return re.sub(r"\$\{[A-Za-z_]\w*:-([^}]*)\}", r"\1", value)


def launch_spec(entry: Dict[str, Any], plat: P.Platform, data_dir: Path) -> Tuple[List[str], Path, Dict[str, str]]:
    command = [resolve(str(entry["command"]), plat, data_dir)] + [resolve(str(a), plat, data_dir) for a in entry.get("args", [])]
    cwd_raw = resolve(str(entry.get("cwd") or "."), plat, data_dir)
    cwd = Path(cwd_raw) if os.path.isabs(cwd_raw) else (plat.root / cwd_raw)
    env = dict(os.environ)
    env["PAPER_ORCHESTRA_PLUGIN_DATA"] = str(data_dir)
    env["PAPER_ORCHESTRA_DB"] = str(data_dir / "verify.db")
    for k, v in (entry.get("env") or {}).items():
        env[k] = resolve(str(v), plat, data_dir)
    unresolved = [x for x in command + [str(cwd)] if "${" in x]
    if unresolved:
        raise RuntimeError(f"unresolved placeholder in {unresolved}")
    return command, cwd, env


def load_servers(plat: P.Platform) -> Dict[str, Dict[str, Any]]:
    """The bundle's MCP servers as {key: {command, args, cwd?, env?}}, whatever the host's format."""
    doc = json.loads((plat.root / plat.mcp_config).read_text(encoding="utf-8"))
    if plat.mcp_format == "muse-manifest":
        out: Dict[str, Dict[str, Any]] = {}
        for entry in doc["capabilities"]["mcpServers"]:
            argv = entry["command"]
            if entry.get("transport", "stdio") != "stdio" or not isinstance(argv, list) or not argv:
                raise ValueError(f"MCP server {entry.get('id')!r} must be a stdio argv list")
            out[entry["id"]] = {"command": argv[0], "args": list(argv[1:])}
        return out
    return doc["mcpServers"]


def call_tool(command: List[str], cwd: Path, env: Dict[str, str], name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call one tool on a stdio MCP server and return its JSON payload."""
    r = rpc(command, cwd, env, HANDSHAKE[:2] + [
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": arguments}}])
    return json.loads(r[9]["result"]["content"][0]["text"])


def check_host_agent(plat: P.Platform, servers: Dict[str, Dict[str, Any]], static: bool, data_dir: Path) -> List[Check]:
    """A bundle that routes backend LLM calls to its host agent (Muse) must really turn that on,
    and every other bundle must leave it off."""
    checks: List[Check] = []
    entry = servers.get(plat.python_server_key)
    if entry is None:
        return checks
    if plat.host_agent:
        backend = (P.REPO_ROOT / "paper-orchestra" / "utils" / "providers.py").read_text(encoding="utf-8")
        ok = f'name="{plat.host_agent}"' in backend and (P.REPO_ROOT / "paper-orchestra" / "utils" / "agent_cli_utils.py").is_file()
        checks.append((f"backend has the `{plat.host_agent}` host-agent provider", "pass" if ok else "fail",
                       "paper-orchestra/utils/providers.py + agent_cli_utils.py"))
        try:
            man = json.loads((plat.root / plat.manifests[0]).read_text(encoding="utf-8"))
            listed = {e["id"]: e["path"] for e in man["capabilities"]["skills"]}
            on_disk = {p.name for p in (plat.root / "skills").iterdir() if (p / "SKILL.md").is_file()}
            bad = sorted(set(listed) ^ on_disk) + [i for i, rel in listed.items() if not (plat.root / rel).is_file()]
            families = set(man["capabilities"])
            ok = not bad and families == {"skills", "commands", "hooks", "mcpServers", "reminders"} \
                and man.get("schemaVersion") == 1 and man.get("compat", {}).get("manifestDir") == ".muse-plugin"
            checks.append(("manifest lists every skill (native Muse schema)", "pass" if ok else "fail",
                           f"{len(listed)} skills" if ok else f"mismatch: {bad or sorted(families)}"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append(("manifest lists every skill (native Muse schema)", "fail", repr(exc)))
    if static:
        return checks
    try:
        command, cwd, env = launch_spec(entry, plat, data_dir)
        if command[0] in ("python3", "python"):
            command[0] = sys.executable
        for name in ("PO_HOST_AGENT", "PO_HOST_AGENT_STRICT"):
            if name not in (entry.get("env") or {}):
                env.pop(name, None)  # what the bundle itself sets, not this shell
        info = call_tool(command, cwd, env, "po_check_environment", {}).get("hostAgent") or {}
        if plat.host_agent:
            ok = info.get("enabled") is True and info.get("name") == plat.host_agent and info.get("strict") is True
            checks.append((f"server starts in `{plat.host_agent}`-only mode (no other LLM API)", "pass" if ok else "fail", json.dumps(info, ensure_ascii=False)[:160]))
        else:
            ok = info.get("enabled") is False
            checks.append(("host-agent mode is off (providers come from .env)", "pass" if ok else "fail", "" if ok else json.dumps(info)[:160]))
    except Exception as exc:  # noqa: BLE001
        checks.append(("host-agent mode", "fail", f"{type(exc).__name__}: {exc}"))
    return checks


def check_platform(plat: P.Platform, baseline: Dict[str, Any], static: bool, data_dir: Path) -> List[Check]:
    checks: List[Check] = []

    def add(name: str, ok: Any, detail: str = "") -> None:
        checks.append((name, ok if isinstance(ok, str) else ("pass" if ok else "fail"), detail))

    version = server_version()

    # 1. Manifest(s)
    for rel in plat.manifests:
        path = plat.root / rel
        try:
            man = json.loads(path.read_text(encoding="utf-8"))
            ok = man.get("name") == P.PLUGIN_NAME and man.get("version") == version and bool(man.get("description"))
            add(f"manifest {rel}", ok, f"name={man.get('name')} version={man.get('version')} (server {version})")
        except (OSError, ValueError) as exc:
            add(f"manifest {rel}", False, str(exc))

    # 2. Bundled files identical to the shared source
    prefix = plat.root.relative_to(P.REPO_ROOT).as_posix() + "/"
    stale = [d for d in sync_platforms.drift() if d.split(": ", 1)[1].startswith(prefix)]
    add("bundle in sync with plugins/shared", not stale, "; ".join(stale[:3]) or "server, UI, bridge, skills, agents byte-identical")

    # 3. Skills
    want = set(P.shared_skill_names()) | set(plat.extra_skills)
    skills_dir = plat.root / "skills"
    have = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()} if skills_dir.is_dir() else set()
    add("skill set", have == want, f"{len(have)} skills" + (f"; missing {sorted(want - have)}" if want - have else "")
        + (f"; unexpected {sorted(have - want)}" if have - want else ""))
    bad = []
    for name in sorted(have):
        fm = read_frontmatter(skills_dir / name / "SKILL.md")
        if fm.get("name") != name or not (1 <= len(fm.get("description", "")) <= 1024):
            bad.append(name)
    add("skill frontmatter (name = folder, description 1-1024 chars)", not bad, ", ".join(bad))

    # 4. Agents (or the skill fallback where the host has no plugin subagents)
    if plat.agents == "none":
        add("auditor available as skill (host has no plugin subagents)", "po-submission-auditor" in have)
    else:
        missing = [n for n in P.shared_agent_names() if not (plat.root / "agents" / n).is_file()]
        fm_ok = all(read_frontmatter(plat.root / "agents" / n).get("name") for n in P.shared_agent_names() if n not in missing)
        add("subagents", not missing and fm_ok, ", ".join(P.shared_agent_names()))

    # 5. Marketplace entry
    if plat.marketplace:
        try:
            mk = json.loads((P.REPO_ROOT / plat.marketplace).read_text(encoding="utf-8"))
            entry = next(e for e in mk["plugins"] if e["name"] == P.PLUGIN_NAME)
            src = entry["source"]
            rel = src if isinstance(src, str) else src.get("path", "")
            ok = (P.REPO_ROOT / rel).resolve() == plat.root.resolve() and entry.get("version", version) == version
            add(f"marketplace {plat.marketplace}", ok, f"source={rel}")
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            add(f"marketplace {plat.marketplace}", False, repr(exc))

    # 6. MCP registration + live handshake
    try:
        servers = load_servers(plat)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        add(f"MCP config {plat.mcp_config}", False, repr(exc))
        return checks
    for kind, key in (("python", plat.python_server_key), ("bridge", plat.bridge_server_key)):
        label = f"MCP server `{key}` ({kind})"
        if key not in servers:
            add(label, False, f"not registered in {plat.mcp_config}")
            continue
        try:
            command, cwd, env = launch_spec(servers[key], plat, data_dir)
            script = next((Path(a) if os.path.isabs(a) else cwd / a for a in command[1:] if a.endswith((".py", ".mjs"))), None)
            if script is None or not script.is_file():
                raise RuntimeError(f"entry script not found: {script}")
            if kind == "python" and plat.python_entry and script.resolve() != (plat.root / plat.python_entry).resolve():
                raise RuntimeError(f"expected the launcher {plat.python_entry}, found {script.name}")
            if static:
                add(label, "pass", f"registered; entry {script.relative_to(P.REPO_ROOT).as_posix()} (not launched)")
                continue
            if command[0] in ("python3", "python"):
                command[0] = sys.executable  # test the bundle, not this machine's PATH spelling
            if shutil.which(command[0]) is None:
                add(label, "skip", f"`{command[0]}` is not installed on this machine")
                continue
            fp = fingerprint(command, cwd, env)
            ref = baseline[kind]
            diffs = [k for k in ref if fp.get(k) != ref[k]]
            ui_ok = all(v > 1000 for v in fp.get("ui_bytes", {}).values())
            detail = f"{len(fp['tools'])} tools, {len(fp['resources'])} resources, {len(fp['prompts'])} prompts"
            if fp.get("ui_bytes"):
                detail += f", {len(fp['ui_bytes'])} UI apps load"
            add(label, not diffs and ui_ok, detail if not diffs and ui_ok else f"differs from shared source in {diffs or 'UI payload'}")
        except Exception as exc:  # noqa: BLE001 - report any launch failure as a failed check
            add(label, False, f"{type(exc).__name__}: {exc}")
    checks.extend(check_host_agent(plat, servers, static, data_dir))
    return checks


def check_coder_recipe(baseline: Dict[str, Any], static: bool, data_dir: Path) -> List[Check]:
    """Coder (coder.com) has no plugin format of its own; verify the workspace recipe instead."""
    checks: List[Check] = []
    folder = P.PLUGINS_ROOT / "coder"
    script = folder / "install-in-workspace.sh"
    template = folder / "paper-orchestra.tf"
    bash = shutil.which("bash")
    if not script.is_file():
        checks.append(("install script", "fail", "plugins/coder/install-in-workspace.sh is missing"))
    elif b"\r\n" in script.read_bytes():
        checks.append(("install script", "fail", "has CRLF line endings; bash cannot run it"))
    elif bash and os.name != "nt":
        proc = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        checks.append(("install script syntax (bash -n)", "pass" if proc.returncode == 0 else "fail", proc.stderr.strip()[:200]))
    else:
        checks.append(("install script", "pass", "present (bash syntax check runs on Linux and macOS)"))
    try:
        text = template.read_text(encoding="utf-8")
        po_dir = re.search(r'po_dir\s*=\s*"([^"]+)"', text).group(1)
        block = re.search(r"mcp\s*=\s*<<-EOF\n(.*?)\n\s*EOF", text, re.DOTALL).group(1)
        servers = json.loads(block.replace("${local.po_dir}", po_dir))["mcpServers"]
    except (OSError, AttributeError, ValueError, KeyError) as exc:
        checks.append(("template mcp block", "fail", repr(exc)))
        return checks
    for kind, key in (("python", "paper-orchestra-studio"), ("bridge", "paper-orchestra-bridge")):
        label = f"template MCP server `{key}` ({kind})"
        try:
            entry = servers[key]
            target = P.REPO_ROOT / os.path.relpath(entry["args"][0], po_dir)
            if not target.is_file():
                raise RuntimeError(f"{entry['args'][0]} does not exist in the repository")
            command = [sys.executable if kind == "python" else entry["command"], str(target)]
            if static:
                checks.append((label, "pass", f"points at {target.relative_to(P.REPO_ROOT).as_posix()} (not launched)"))
            elif shutil.which(command[0]) is None:
                checks.append((label, "skip", f"`{command[0]}` is not installed on this machine"))
            else:
                env = dict(os.environ, PAPER_ORCHESTRA_PLUGIN_DATA=str(data_dir), PAPER_ORCHESTRA_DB=str(data_dir / "verify.db"))
                fp = fingerprint(command, target.parent, env)
                same = all(fp.get(k) == v for k, v in baseline[kind].items())
                checks.append((label, "pass" if same else "fail", f"{len(fp['tools'])} tools" if same else "differs from shared source"))
        except Exception as exc:  # noqa: BLE001
            checks.append((label, "fail", f"{type(exc).__name__}: {exc}"))
    return checks


def check_pane_mod(baseline: Dict[str, Any]) -> List[Check]:
    """The Claude Code sidebar pane (`/po-pane`) is a mod that drives the shared server's tools."""
    checks: List[Check] = []
    folder = P.PLUGINS_ROOT / "claude-code-pane"
    try:
        manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((folder / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        modules = [folder / "hooks" / m for m in hooks["modules"]]
        missing = [m.name for m in modules if not m.is_file()]
        types = folder / manifest.get("types", "types/index.d.ts")
        if missing or not types.is_file() or not manifest.get("name") or not manifest.get("version"):
            raise RuntimeError(f"incomplete mod (missing modules: {missing}, types present: {types.is_file()})")
        checks.append(("pane mod files", "pass", f"{manifest['name']} {manifest['version']}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("pane mod files", "fail", f"{type(exc).__name__}: {exc}"))
        return checks
    source = "\n".join(m.read_text(encoding="utf-8") for m in modules)
    server = re.search(r"const SERVER = '([^']+)'", source)
    expected = P.PLATFORMS["claude-code"].python_server_key
    ok = bool(server) and server.group(1) == expected
    checks.append(("pane targets the Claude Code MCP server key", "pass" if ok else "fail",
                   expected if ok else f"pane uses {server.group(1) if server else None!r}, plugin registers {expected!r}"))
    called = sorted(set(re.findall(r"callJson\(\$, '([a-z_0-9]+)'", source)))
    known = baseline.get("python", {}).get("tools")
    if known is None:
        known = re.findall(r'"(po_[a-z_0-9]+)"', P.SHARED_SERVER.read_text(encoding="utf-8"))
    names = {t["name"] if isinstance(t, dict) else t for t in known}
    gone = [t for t in called if t not in names]
    checks.append(("pane tools exist on the shared server", "fail" if gone or not called else "pass",
                   f"missing: {gone}" if gone else f"{len(called)} tools used"))
    return checks


def run(static: bool = False) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="po-verify-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp)
        env = dict(os.environ, PAPER_ORCHESTRA_PLUGIN_DATA=tmp, PAPER_ORCHESTRA_DB=str(data_dir / "verify.db"))
        baseline: Dict[str, Any] = {"python": {}, "bridge": {}}
        if not static:
            baseline["python"] = fingerprint([sys.executable, str(P.SHARED_SERVER)], P.SHARED_SERVER.parent, env)
            if shutil.which("node"):
                baseline["bridge"] = fingerprint(["node", str(P.BRIDGE_SRC_DIR / "server.mjs")], P.BRIDGE_SRC_DIR, env)
        report: Dict[str, Any] = {"version": server_version(), "baseline": baseline, "platforms": {}}
        for key, plat in P.PLATFORMS.items():
            checks = check_platform(plat, baseline, static, data_dir)
            report["platforms"][key] = {
                "label": plat.label,
                "ok": all(c[1] != "fail" for c in checks),
                "checks": [{"name": n, "status": s, "detail": d} for n, s, d in checks],
            }
        coder = check_coder_recipe(baseline, static, data_dir)
        report["platforms"]["coder"] = {
            "label": "Coder workspaces (deployment recipe for Claude Code / Codex)",
            "ok": all(c[1] != "fail" for c in coder),
            "checks": [{"name": n, "status": s, "detail": d} for n, s, d in coder],
        }
        pane = check_pane_mod(baseline)
        report["platforms"]["claude-code-pane"] = {
            "label": "Claude Code sidebar pane mod (/po-pane)",
            "ok": all(c[1] != "fail" for c in pane),
            "checks": [{"name": n, "status": s, "detail": d} for n, s, d in pane],
        }
    report["ok"] = all(p["ok"] for p in report["platforms"].values())
    report["skipped"] = sum(c["status"] == "skip" for p in report["platforms"].values() for c in p["checks"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify platform parity (five platforms plus Muse) for PaperOrchestra Studio plugins.")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--static", action="store_true", help="do not launch the MCP servers")
    ap.add_argument("--strict", action="store_true", help="treat skipped checks (missing python3/node) as failures")
    args = ap.parse_args()
    report = run(static=args.static)
    failed = not report["ok"] or (args.strict and report["skipped"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    marks = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}
    print(f"PaperOrchestra Studio plugin parity check (server {report['version']})")
    if report["baseline"]["python"]:
        b = report["baseline"]
        print(f"Shared source: {len(b['python']['tools'])} Python-server tools, {len(b['python']['resources'])} resources, "
              f"{len(b['python']['prompts'])} prompts, {len(b['bridge'].get('tools', []))} bridge tools, "
              f"{len(P.shared_skill_names())} skills\n")
    for key, plat in report["platforms"].items():
        print(f"[{'OK' if plat['ok'] else 'FAILED'}] {plat['label']} ({key})")
        for c in plat["checks"]:
            print(f"   {marks[c['status']]}  {c['name']}" + (f"  -  {c['detail']}" if c["detail"] else ""))
        print()
    total = sum(len(p["checks"]) for p in report["platforms"].values())
    bad = sum(c["status"] == "fail" for p in report["platforms"].values() for c in p["checks"])
    print(f"{total - bad - report['skipped']}/{total} checks passed, {bad} failed, {report['skipped']} skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
