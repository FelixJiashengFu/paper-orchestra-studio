# PaperOrchestra Studio for Coder (coder.com) workspaces.
#
# Drop this file into a workspace template that already defines `coder_agent.main`.
# It makes Claude Code inside the workspace start with the PaperOrchestra Studio
# MCP servers registered and the plugin (skills + auditor agent) installed.
#
# The module inputs used here (`workdir`, `mcp`, `pre_install_script`) follow the
# registry.coder.com `claude-code` module README; pin `version` to the release
# your Coder deployment supports and check its README if an input was renamed.

locals {
  po_dir = "/home/coder/paper-orchestra-studio"
  po_ref = "main"
}

module "claude-code" {
  source   = "registry.coder.com/coder/claude-code/coder"
  version  = "4.2.8"
  agent_id = coder_agent.main.id
  workdir  = "/home/coder"

  # Clone or update the repository and install the plugin before Claude Code starts.
  pre_install_script = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    export PO_STUDIO_DIR="${local.po_dir}" PO_STUDIO_REF="${local.po_ref}"
    if [ ! -d "$PO_STUDIO_DIR/.git" ]; then
      git clone --depth 1 --branch "$PO_STUDIO_REF" https://github.com/FelixJiashengFu/paper-orchestra-studio.git "$PO_STUDIO_DIR"
    fi
    bash "$PO_STUDIO_DIR/plugins/coder/install-in-workspace.sh"
  EOT

  # Registered directly as well, so the tools are available even if the plugin
  # install step could not run (for example when Claude Code is installed later).
  mcp = <<-EOF
    {
      "mcpServers": {
        "paper-orchestra-studio": {
          "command": "python3",
          "args": ["${local.po_dir}/plugins/claude-desktop/cowork-plugin/server/paper_orchestra_mcp.py"]
        },
        "paper-orchestra-bridge": {
          "command": "node",
          "args": ["${local.po_dir}/plugins/claude-desktop/cowork-plugin/mcp/server.mjs"]
        }
      }
    }
  EOF
}
