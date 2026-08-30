---
repo: architecture
path: docs/architecture/aw-app-remote-host-cli.md
source: generated
edited: false
checksum: sha256:adf919101e098efb90b314678005fb31d52780ba9849d808adcfff32ae5fab75
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
- `remote_host_firewall_add_rule`
- `remote_host_firewall_list`
- `remote_host_firewall_remove_rule`
- `remote_host_firewall_set_lockdown`
- `remote_host_firewall_status`
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
### Um id já resolvido não custa consulta nenhuma
- Given o caminho quente é um agente passando --host vindo de uma chamada anterior de hosts, ou a saída deste módulo realimentada nele mesmo
- When a referência é testada contra a forma que mint_host_credential produz, um token_hex(8) (repos/aw-app-remote-host-cli/remote_host_cli_app/hosts.py::looks_like_id:37, usado em resolve_host_ref:47)
- Then o que tem cara de id passa direto e a operação inteira continua sendo uma única chamada HTTP, e um id desconhecido é repassado em vez de recusado localmente — o backend devolve 404 para um id fora da conta de qualquer forma, e esse erro é melhor que um que este cliente inventaria, porque descreve o que o servidor realmente sabe em vez de um palpite local
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-remote-host-cli/tests/test_hosts.py` (passing)

### Entre linhas duplicadas do mesmo host, a que está conectada ganha
- Given um workspace pode ter mais de uma linha de host, porque um re-link deixa a anterior para trás
- When a referência casa com mais de uma linha (repos/aw-app-remote-host-cli/remote_host_cli_app/hosts.py:82-85)
- Then quando exatamente uma delas está de fato conectada, é ela — e com duas conectadas, ou com todas offline, a resolução RECUSA em vez de escolher (tests/test_hosts.py::test_two_connected_matches_refuse_to_guess:111 e test_all_matches_offline_still_refuses_rather_than_picking:123). Adivinhar aqui manda um comando para a máquina errada, que é um erro sem desfazer; a duplicata obsoleta é desempate seguro, empate real não é
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-remote-host-cli/tests/test_hosts.py` (passing)

### Referência desconhecida responde listando o que existe
- Given alguém digita um slug ou hostname que não casa com host nenhum
- When a resolução falha (repos/aw-app-remote-host-cli/remote_host_cli_app/hosts.py::resolve_host_ref:47, com o texto montado por _describe:41)
- Then o erro enumera os hosts disponíveis em vez de só dizer que não achou, e uma referência vazia é tratada como erro de programação e não como consulta que falhou — a distinção importa: string vazia significa que quem chamou não passou o argumento, e mandá-la para uma busca produziria "host não encontrado" para um bug que não é de configuração. O casamento por hostname ignora maiúsculas, e slug ganha de hostname de outra máquina
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-remote-host-cli/tests/test_hosts.py` (passing)

### Quem já listou os hosts não paga uma segunda ida ao servidor
- Given um chamador que acabou de listar hosts e agora quer resolver uma referência dentro daquela mesma lista
- When a lista já obtida é repassada pelo parâmetro opcional hosts (repos/aw-app-remote-host-cli/remote_host_cli_app/hosts.py::resolve_host_ref:47, assinatura com hosts=None)
- Then a resolução usa o que veio e não busca de novo (tests/test_hosts.py::test_a_caller_that_already_listed_hosts_avoids_a_second_round_trip:82) — o parâmetro é opcional de propósito, para que o caminho simples continue simples e só quem se importa com a ida extra precise pensar nela. Numa CLI que fala com um backend remoto por túnel, cada round trip é latência que a pessoa sente
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-remote-host-cli/tests/test_hosts.py` (passing)

### App não configurado e erro do host remoto chegam como conteúdo de erro MCP
- Given as sete tools MCP podem ser chamadas com o app ainda não configurado, ou contra um host que devolve erro
- When a chamada delega para o dispatch e a exceção é convertida (repos/aw-app-remote-host-cli/mcp_server/server.py, via tests/test_mcp_server.py::test_not_configured_surfaces_as_mcp_error_content:116 e test_remote_host_error_surfaces_as_mcp_error_content:128)
- Then os dois viram conteúdo de erro na resposta MCP, tool desconhecida também (test_unknown_tool_returns_error:139), e o tools/list anuncia exatamente sete — um servidor MCP que levanta exceção derruba a sessão do cliente inteira, e "não configurado" é o estado mais provável logo depois de instalar, ou seja, exatamente quando alguém está experimentando pela primeira vez
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-remote-host-cli/tests/test_mcp_server.py` (passing)
