#!/usr/bin/env python3
"""Single source of truth for the five supported agent platforms.

Imported by sync_platforms.py, verify_platforms.py and install.py. Standard
library only, so it runs wherever the MCP server itself runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

PLUGINS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGINS_ROOT.parent
SHARED = PLUGINS_ROOT / "shared"

PLUGIN_NAME = "paper-orchestra-studio"
SHARED_SERVER = SHARED / "mcp_server" / "paper_orchestra_mcp.py"
SHARED_UI_DIR = SHARED / "ui_dist"
SHARED_SKILLS_DIR = SHARED / "skills"
SHARED_AGENTS_DIR = SHARED / "agents"
UI_FILES = ("workbench.html", "tool-tree.html")

# The loopback bridge to the running Studio desktop app (Node, zero deps).
# integrations/qoder-plugin/mcp is where it was first shipped and stays canonical.
BRIDGE_SRC_DIR = REPO_ROOT / "integrations" / "qoder-plugin" / "mcp"
BRIDGE_FILES = ("server.mjs", "studio.mjs", "tools.mjs")

# Antigravity ships hand-tuned wording for the original six skills (its MCP
# server is namespaced differently); those are kept as they are.
ANTIGRAVITY_TUNED_SKILLS = (
    "po-compute-memory",
    "po-figure-studio",
    "po-literature-graph",
    "po-paper-pipeline",
    "po-research-studio",
    "po-submission-latex",
)


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    root: Path                 # plugin bundle root inside the repo
    manifests: Tuple[str, ...]  # manifest paths relative to root (first is primary)
    mcp_config: str            # MCP registration file relative to root
    root_var: str              # placeholder the host expands to the plugin root ("" = relative cwd)
    python_server_key: str
    bridge_server_key: str
    agents: str                # "full" | "minimal" (name+description only) | "none"
    marketplace: str = ""      # marketplace file relative to the repo root
    tuned_skills: Tuple[str, ...] = ()
    extra_skills: Tuple[str, ...] = ()  # platform-only skills that must be preserved
    host_vars: Dict[str, str] = field(default_factory=dict)  # other placeholders -> test value


PLATFORMS: Dict[str, Platform] = {
    "claude-code": Platform(
        key="claude-code",
        label="Claude Code / Claude Desktop Cowork",
        root=PLUGINS_ROOT / "claude-desktop" / "cowork-plugin",
        manifests=(".claude-plugin/plugin.json",),
        mcp_config=".mcp.json",
        root_var="${CLAUDE_PLUGIN_ROOT}",
        python_server_key="paper-orchestra-studio",
        bridge_server_key="paper-orchestra-bridge",
        agents="full",
        marketplace=".claude-plugin/marketplace.json",
    ),
    "antigravity": Platform(
        key="antigravity",
        label="Google Antigravity",
        root=PLUGINS_ROOT / "antigravity",
        manifests=("plugin.json",),
        mcp_config="mcp_config.json",
        root_var="${PLUGIN_ROOT}",
        python_server_key="paper_orchestra",
        bridge_server_key="paper_orchestra_bridge",
        agents="minimal",
        tuned_skills=ANTIGRAVITY_TUNED_SKILLS,
        host_vars={"${PLUGIN_DATA}": "{data}"},
    ),
    "codex": Platform(
        key="codex",
        label="OpenAI Codex",
        root=PLUGINS_ROOT / "codex",
        manifests=(".codex-plugin/plugin.json",),
        mcp_config=".mcp.json",
        root_var="",
        python_server_key="paper_orchestra",
        bridge_server_key="paper_orchestra_studio",
        agents="none",
        marketplace=".agents/plugins/marketplace.json",
    ),
    "qoder": Platform(
        key="qoder",
        label="Qoder",
        root=REPO_ROOT / "integrations" / "qoder-plugin",
        manifests=(".qoder-plugin/plugin.json",),
        mcp_config=".mcp.json",
        root_var="${QODER_PLUGIN_ROOT}",
        python_server_key="paper_orchestra",
        bridge_server_key="paper_orchestra_studio",
        agents="full",
        tuned_skills=("paper-orchestra-studio",),
        host_vars={"${QODER_NODE_RUNTIME}": "node"},
    ),
    "workbuddy": Platform(
        key="workbuddy",
        label="WorkBuddy / CodeBuddy",
        root=PLUGINS_ROOT / "workbuddy",
        manifests=(".codebuddy-plugin/plugin.json",),
        mcp_config=".mcp.json",
        root_var="${CODEBUDDY_PLUGIN_ROOT}",
        python_server_key="paper-orchestra-studio",
        bridge_server_key="paper-orchestra-bridge",
        agents="full",
        marketplace=".codebuddy-plugin/marketplace.json",
        host_vars={"${CODEBUDDY_PLUGIN_DATA}": "{data}"},
    ),
}

# Not one of the five agent platforms, but it embeds the same server and UI.
EXTRA_SERVER_TARGETS = (PLUGINS_ROOT / "claude-desktop" / "desktop-extension",)


def shared_skill_names() -> Tuple[str, ...]:
    return tuple(sorted(p.name for p in SHARED_SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file()))


def shared_agent_names() -> Tuple[str, ...]:
    return tuple(sorted(p.name for p in SHARED_AGENTS_DIR.glob("*.md")))
