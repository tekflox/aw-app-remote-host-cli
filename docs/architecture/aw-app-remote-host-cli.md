---
repo: architecture
path: docs/architecture/aw-app-remote-host-cli.md
source: generated
edited: false
checksum: sha256:075cad79f3a828793bf741073fa60e0fdeb53068be954ea13c7fc68d74dfb394
---
# Remote Host CLI

- **repo**: aw-app-remote-host-cli
- **layer**: app
- **technologies**: python
- **health** (derived): planned

Access the remote hosts linked to your aw-workspace account: contributes an `aw-workspace-cli remote-hosts` command (status/exec/ps plus push/pull file transfer against the BYOD host linked via aw-remote-host) and an MCP server so an agent can do the same, both scoped to only this account's own linked hosts.

## Connections
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `remote_host_delete`
- `remote_host_download_file`
- `remote_host_exec_kill`
- `remote_host_exec_run`
- `remote_host_exec_start`
- `remote_host_exec_status`
- `remote_host_exec_wait`
- `remote_host_list_directory`
- `remote_host_list_hosts`
- `remote_host_list_processes`
- `remote_host_mkdir`
- `remote_host_read_file`
- `remote_host_stat`
- `remote_host_status`
- `remote_host_upload_file`
- `remote_host_write_file`

## Requirements
_none documented_
