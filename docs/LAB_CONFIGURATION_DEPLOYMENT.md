# Aplicação do configurador Linux ao servidor DA de laboratório

Entrega interna de 05/11/2026. Os dados e hashes da primeira aplicação abaixo são históricos;
a configuração atual e a correção de reaplicação constam na seção final. Fontes **simuladas**, somente leitura. Não há
cliente OPC UA integrado nem histórico/replay. Máquina 1 não foi acessada.
O protótipo C# e os builds Rust anteriores foram preservados.

## Fluxo e identidade

O backend exporta somente a máquina selecionada, incluindo seus módulos,
pastas com variáveis e Item IDs explícitos. Os nomes de módulo/pasta são
metadados; não regeneram Item IDs. A aplicação não envia as variáveis de
outras máquinas. Pastas vazias não criam tags.

O destino provisionado é **RB1PI2**, Windows 7 SP1 x86 (6.1.7601), endereço
10.247.140.166. O cadastro chama-se **RB1PI1** e não foi renomeado. O agente
confere hostname, endereço e identificador da máquina antes de aplicar; o
Linux também confere essa identidade antes da transferência e na resposta.
O painel informa a diferença entre nome cadastrado e hostname confirmado.

| Item ID publicado | Tipo COM | Valor simulado |
|---|---|---|
| RB1PI1.TESTE.TESTE1.DIGITAL | VT_BOOL | True |
| RB1PI1.TESTE.TESTE1.TESTE1 | VT_R8 | 15 |
| RB1PI1.TESTE.TESTE1.TESTE2 | VT_R8 | 28 |

Com `project.json` ativo, somente seu catálogo aparece; as tags fixas
`RustLab.*` pertencem ao modo legado sem arquivo de projeto.

## Canal e provisionamento

A aplicação usa OpenSSH/SCP, autenticação por chave existente e chave do host
previamente confiada. `StrictHostKeyChecking=yes`, `UpdateHostKeys=no`,
`BatchMode=yes`; não aceita automaticamente hosts desconhecidos. É um canal
SSH criptografado, não TLS: não foi desativada validação de certificados.
Não foram instalados agentes como serviço, nem alterados DCOM/firewall.

O provisionamento local fica em `.opc-bridge-management/agents.json`, ignorado
pelo Git. Exemplo estrutural, que deve ser adaptado pelo operador:

```json
{
  "schema_version": 1,
  "targets": {
    "<UUID da máquina cadastrada>": {
      "host": "<endereço cadastrado>",
      "username": "<conta SSH autorizada>",
      "identity_file": "/caminho/privado/chave-existente",
      "known_hosts_file": "/caminho/known_hosts-conferido",
      "expected_hostname": "<hostname Windows confirmado>",
      "agent_dir": "D:\\OPCB\\pasta-laboratorio",
      "diagnostic_port": 49321
    }
  }
}
```

A chave privada permanece fora do checkout, com permissões 0600. O HTTP não
aceita comandos SSH, credenciais ou caminhos fornecidos pela requisição.
O diretório de agente aceita apenas caminhos Windows simples, sem espaços ou
metacaracteres. No Windows, preparar nessa pasta `lab_agent.ps1`, executável,
`inbox/` e `identity.json`:

```json
{"machine_id":"<mesmo UUID>","expected_hostname":"<hostname>","expected_address":"<IP>","diagnostic_port":49321}
```

Neste laboratório usa-se a conta Administrator já disponível. Isso não é um
modelo de menor privilégio para produção. A pasta/provisionamento e a conta
SSH precisam ser protegidos pelo operador. Não há novo listener de rede do
agente: o diagnóstico Rust é somente `127.0.0.1:49321`, leitura de snapshot.
O script usa ExecutionPolicy Bypass somente no processo iniciado por SSH;
não altera política global nem aceita licenças.

O acesso web HTTP com Basic existente permanece como previamente solicitado:
essa autenticação web não cifra credenciais no transporte. A criptografia
SSH Linux→Windows não torna o acesso navegador→Linux criptografado. Para esse
acesso, usar túnel SSH ou implantar HTTPS com certificado validado em etapa
própria. Não houve alteração do proxy de acesso nesta entrega.

## Aplicar pela interface

1. Abra o configurador e selecione máquina, módulo ou pasta da máquina desejada.
2. Use **Verificar agente**; confira a identidade real mostrada.
3. Para alterar somente valores das variáveis existentes, mantenha o PI OPC
   Client conectado. Clique **Aplicar na máquina** e confirme. O agente valida
   revisão, identidade, tipos e estrutura; o servidor confirma a nova revisão
   antes de a interface mostrar **Aplicada**.
4. Atualizações apenas de valores não reiniciam o processo OPC DA e preservam
   grupos e handles. Use **Refresh** no PI OPC Client para observar o valor
   novo (a entrega por Advise não foi validada). Se houver timeout após o
   envio, o estado é indeterminado até consultar a revisão ativa; não repita
   às cegas.
5. Inclusões, exclusões, renomeações ou mudanças de tipo não são aceitas pelo
   protocolo de atualização ao vivo. Elas exigem a aplicação estrutural com
   parada do servidor e reconexão dos clientes.

Aplicações estruturais exigem reiniciar **somente o executável DA de
laboratório**. O agente solicita parada normal por arquivo marcador e recusa
continuar se o próprio servidor não parar em tempo hábil ou tiver múltiplas
instâncias. Não mata processos arbitrários. Os clientes precisam reconectar.
O caminho de atualização de valores é separado e não chama `StopOwn`.

O agente valida JSON/tipos e SHA-256 antes da troca atômica de `project.json`.
Inicia uma instância temporária, confere PID, catálogo completo, valor, tipo,
qualidade, timestamp e digest/revisão. Encerra-a normalmente antes de gravar o
recibo, evitando uma instância da sessão SSH ocupar a porta de diagnóstico
antes da ativação COM na sessão RDP. O recibo informa
`validated_waiting_client_activation`: configuração aplicada é diferente de
runtime continuamente em execução. O PI OPC Client ativa o servidor depois.

O estado **Aplicada** registra a última confirmação da configuração; não é um
heartbeat. Uma leitura web posterior requer snapshot atual correspondente ao
recibo. Sem runtime/SSH válido a API retorna indisponibilidade (503), sem
substituir por valores Good do simulador Linux. Alterar essa máquina torna a
configuração pendente; editar outra não invalida seu recibo.

Operações HTTP: GET `/api/v1/machines/{id}/deployment`, POST `.../probe` com
`{}`, POST `.../apply` com `{}` e `If-Match: <revisão>`. Apply retorna 202;
consultas posteriores acompanham aplicação/erro. Nenhum parâmetro remoto
arbitrário é permitido. Leituras usam as rotas já existentes.

## Valores, qualidade e continuidade

O modelo inicia sem valor Good (`0x20`, timestamp ausente). Amostra simulada
válida recebe o timestamp da geração no Windows e qualidade `0xC0`. Timestamp
inválido/ausente é explícito (`0x40`), nunca substituído silenciosamente pela
hora atual. Perda da fonte marca `0x08`, retendo valor e timestamp anteriores;
retorno só fica Good após nova amostra. Escrita retorna `0xC0040006`.
No diagnóstico/na API, FILETIME original é preservado; a apresentação ISO tem
resolução de microssegundos. Não há buffer histórico nem recuperação de eventos.

O Windows usa a última configuração em disco sem depender do Linux para
servir COM. Configuração inválida é recusada antes da troca. Falha posterior
na validação do novo runtime restaura o arquivo anterior; clientes precisam
reconectar para ativá-lo. Quando havia configuração anterior, ela também fica
em `last-good-project.json`. Recibos e estado Linux sobrevivem ao restart.
Isso não equivale a atualização transacional de clientes COM conectados.

## Executável, Registro e reversão neste laboratório

Pasta nova, sem sobrescrever builds anteriores:
`D:\OPCB\rust-da-agent-lab-20261105-build1`.

SHA-256 do `rust-da-com-lab.exe` (PE32 i386):
`f1465d9780a750e04cca2059fae6d10b581293b7518935f87e8dbbc197cb9bd0`.
SHA-256 do agente `lab_agent.ps1`:
`59956b1bff0ce91c8db813606de53a5ebb72623fa4acc3045644fe0a1e052428`.
SHA-256 do projeto aplicado, revisão 8:
`a51eb6ec94d287b49a5ae8b9bf964d8e9605c6dc3942b995fd132d490aaac850`.

Mudaram somente os valores padrão de `LocalServer32`, HKCU e HKLM em
`Software\Classes\CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32`,
para o executável acima (caminho entre aspas). ProgID/categorias existentes
não foram alterados. Antes da troca não havia PI Client/servidor de laboratório
ativos; nenhum processo preexistente foi encerrado nessa troca.

Backups exatos: `before-agent-hkcu.reg`, `before-agent-hklm.reg`; ambos guardam
`D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rust-da-com-lab.exe`.
Para reverter, fechar clientes e parar exclusivamente o servidor de laboratório,
conferindo PID/caminho; na mesma conta Windows, console elevado:

```bat
D:\OPCB\rust-da-agent-lab-20261105-build1\rollback-localserver.cmd
```

O script importa somente os dois backups de LocalServer32. Não remove outros
registros. Reabrir o cliente ativa o build anterior com seu catálogo anterior.
O painel Linux conserva um recibo histórico: após reversão, leituras atuais
serão recusadas porque não correspondem ao runtime aplicado. Não confundir o
recibo com confirmação de que o Registro continua apontando ao novo build.

Para reverter somente dados com o novo agente, reenviar uma configuração
válida anterior pelo configurador. Não sobrescrever o banco ou arquivos sem
backup; o banco anterior ao restart foi salvo pela API SQLite em diretório
privado ignorado pelo Git.

## Dependências e reprodução

Não houve instalação nova de software Windows. Ferramentas de desenvolvimento
open source já adotadas: Rust nightly-2026-11-03 (1.101.0-nightly, MIT/Apache-2.0),
MinGW GCC 13.2 POSIX (GPL e Runtime Library Exception), binutils, MinGW-w64.
Versões, fontes oficiais e verificações anteriores:
[RUST_DA_POC_FEASIBILITY.md](RUST_DA_POC_FEASIBILITY.md).

`serde 1.0.229`, `serde_json 1.0.151` e transitivas seguem Cargo.lock e
[dependency-audit.json](../windows/rust_da_project/dependency-audit.json), com
hashes e origens. Licenças completas em THIRD_PARTY_NOTICES.txt. `windows` e
`windows-core 0.62.2` são MIT/Apache-2.0; patches locais Win7 ficam no script
de build. Manter avisos e licenças em eventual distribuição. Não se presume
que a licença de uma crate cubra arquivos terceiros OPC.

O adaptador utiliza Ronbb/rust_opc no commit
`da44a99265f9706618bbe506c98f714f2aa46f0b`, wrapper MIT. A situação de IDLs OPC
Foundation e bindings derivados permanece específica e não esclarecida como
licença geral de redistribuição. Esta etapa é interna; não houve publicação.
O agente novo é código MIT; não inclui cópias dos IDLs. Softing não é usado.
Não foi feito novo parecer jurídico nem declarada resolução dessa dúvida.

O backend usa Python 3.14.4 (PSF, com avisos de terceiros da distribuição),
biblioteca padrão e SQLite 3.46.1. Canal local: OpenSSH existente, licença BSD
com avisos próprios. Destino já tinha Win32-OpenSSH 10.0.0.0 e PowerShell 2 /
.NET Framework 4.8. Esses últimos e Windows são componentes proprietários de
execução existentes; não foram classificados como open source nem instalados
por esta entrega. BCrypt é API de sistema Windows, não biblioteca copiada.
O PE importa bcrypt/ole32/oleaut32 e DLLs Win7 existentes; não importa combase
nem api-ms-win-core-winrt-error. Não foi copiada DLL de sistema.

```bash
PYTHONPATH=src python -m pytest -q
RUSTUP_HOME=/tmp/opc-rust-toolchain/rustup-home \
 CARGO_HOME=/tmp/opc-rust-toolchain/cargo-home \
 /tmp/opc-rust-toolchain/cargo-home/bin/cargo +nightly-2026-11-03 test \
 --offline --manifest-path windows/rust_da_project/Cargo.toml
# Mesma chamada para windows/rust_da_model/Cargo.toml
scripts/build_rust_da_com_lab.sh
```

O build isolado completo gerou o executável acima. Uma tentativa adicional de
rebuild excedeu a quota de disco; foi descartado somente seu target incompleto.
Não se afirma que essa tentativa tenha passado. Reutilizou-se o artefato
completo conferido, correspondente às alterações funcionais e executado no
Windows. Os testes Rust adicionados depois não alteram o código funcional.

## Evidências e limites

- Python: **104 passed, 1 skipped** (clang++ ausente no teste já existente).
- Rust Linux: **11 testes do modelo + 5 do projeto**, todos aprovados.
- Dois destinos **simulados**, distintos: isolamento, alteração independente,
  identidade incorreta, falhas, recibo divergente e persistência após restart.
- HTTP real em loopback: revisão, parâmetros proibidos, apply 202 e confirmação.
- Navegador Chromium 156: CRUD, leitura/monitoramento, Good/Bad/Good, persistência;
  painel real **Aplicada**, identidade RB1PI2, três variáveis; leitura no navegador
  pelo agente Windows confirmou True/15/28 com qualidade Boa.
- Agente Windows real: identidade, validação SHA-256 BCrypt, aplicação com ACK;
  configuração com valor incompatível recusada e recibo anterior inalterado.
- PI OPC Client **real, automatizado na sessão SSH 0**: cliente PID588,
  servidor PID2692, Flat exato com três itens, Refresh Cache padrão e Sync.
  Colunas inspecionadas: TRUE/15.000000/28.000000, VT_BOOL/VT_R8, Good,
  timestamp 05/11/2026 15:04:50 (formatação local do cliente), logs S_OK.
  O primeiro teste foi falso negativo do regex do helper; corrigiu-se o helper,
  não o servidor, e o teste completo passou. Nenhum Write/Advise do PI foi usado.
- Cliente COM entre processos: servidor PID4052/cliente PID5448, browse exato,
  Cache e Device, três leituras Good/S_OK e escrita negada em todos os itens.
  Tentativas de escrita foram somente do teste automatizado contra o simulador.
- Helpers encerraram à força somente seus processos verificados: cliente4692 /
  servidor5792 no teste inicial, cliente588 / servidor2692 no teste aprovado.
  Servidores de validação4820 e4052 pararam normalmente por marcador. O runtime
  temporário4388 para leitura web também parou normalmente; nenhum ficou ativo.

A GUI da sessão RDP do usuário ainda exige confirmação manual das mesmas três
variáveis. Dois Windows reais distintos não foram testados. Não foram validados
Windows XP, execução COM x64, PI Interface, produção, assinatura de binários,
duração prolongada ou recuperação histórica. Notificações contínuas permanecem
parciais; Refresh usa o caminho de callback pontual já existente. Para UA→DA,
falta integrar cliente UA com certificados confiados, mapeamento de status e
timestamp e testes de perda/retorno da origem, mantendo escrita negada.

## Arquivos desta entrega

| Arquivo | Finalidade |
|---|---|
| `src/opc_da_bridge/management/deployment.py` | Exportação por destino, SSH restrito, ACK, estados persistentes e leitura Windows |
| `src/opc_da_bridge/management/server.py`, `store.py`, `__main__.py` | Rotas, aplicação assíncrona e estado decorado sem alterar o esquema SQLite |
| `src/opc_da_bridge/management/static/index.html`, `app.js`, `styles.css` | Painel de aplicação, identidade real, erros e fontes identificadas |
| `windows/agent/lab_agent.ps1` | Validação e aplicação atômica no Windows, recibo e parada normal do próprio runtime |
| `windows/rust_da_project/src/lib.rs` | IDs explícitos, metadados de destino, digest e testes independentes |
| `windows/rust_da_com_lab/src/configured.rs`, `main.rs` | Carregar projeto, validar sem COM, snapshot com digest, teste COM configurado e parada por marcador |
| `tests/test_management_deployment.py`, `tests/test_management.py` | Isolamento de dois destinos simulados, falhas e rotas HTTP |
| `scripts/test_pi_opc_client_refresh.ps1` | Testar catálogo parametrizado, Refresh e Sync no PI Client |
| `README.md`, `docs/WEB_CONFIGURATOR.md`, este guia | Operação, resultados, limitações e reversão |

Dados locais de provisionamento/recibos/backups são privados e ignorados pelo
Git. Não foram commitados chaves, credenciais ou binários. Arquivos preexistentes
não relacionados, incluindo o esboço `windows/rust_da_agent/`, foram preservados.

Para repetir a validação automatizada do PI Client, fechar primeiro o cliente
RDP e todas as instâncias **somente deste servidor de laboratório**, conferindo
PID/caminho antes de qualquer encerramento. Executar no PowerShell x86:

```powershell
$d='D:\OPCB\rust-da-agent-lab-20261105-build1'
& "$d\test_pi_opc_client_refresh.ps1" -Registered `
 -ServerExecutable "$d\rust-da-com-lab.exe" `
 -ExpectedSha256 'f1465d9780a750e04cca2059fae6d10b581293b7518935f87e8dbbc197cb9bd0' `
 -BooleanItemId 'RB1PI1.TESTE.TESTE1.DIGITAL' `
 -ExpectedItemIds @('RB1PI1.TESTE.TESTE1.DIGITAL',
                   'RB1PI1.TESTE.TESTE1.TESTE1',
                   'RB1PI1.TESTE.TESTE1.TESTE2')
```

O helper fecha somente os processos que ele próprio iniciou e revalidou;
não executar sobre uma sessão de operador com trabalho não salvo. A saída
esperada termina em `REGISTERED_REFRESH_AND_SYNC_PASS`. O teste adicional COM
usa `rust-da-com-lab.exe --project-client-test` com instância de laboratório
ativa; esse teste tenta escrever valores simulados e exige BADRIGHTS em todos.


## Correção de reaplicação — 05/11/2026

O Windows identificado continua sendo RB1PI2 em 10.247.140.166, PowerShell 2.0
sobre CLR 2.0.50727.5420. O Linux guardava a revisão 12: `DIGITAL=True`,
`TESTE1=36` e `TESTE2=7`; os Item IDs originais permaneceram intactos.
O projeto e recibo antes da correção eram da revisão 8 (`TESTE1=15`, `TESTE2=28`).

O erro foi reproduzido em uma pasta de teste nova na própria máquina: origem
`project.json.tmp-test`, destino `project.json`, ambos caminhos absolutos na
mesma pasta, e terceiro argumento `$null`. PowerShell 2 passou esse argumento
como string vazia ao método `File.Replace` e o CLR levantou ArgumentException
`The path is not of a legal form`. O destino original continuou intacto.
Com caminho de backup válido a substituição funcionou. Uma chamada equivalente
em C# com null real também funcionou. Assim, a causa observada é a vinculação
do argumento pelo PowerShell legado, não os caminhos válidos dos dois arquivos.

`windows/agent/lab_agent.ps1` agora escreve arquivo temporário no mesmo diretório
e chama `File.Replace` com um caminho de backup único. O backup temporário é
removido apenas quando a operação termina com sucesso. O método continua
atômico para o arquivo ativo; arquivo parcial não é publicado. O cálculo do
SHA-256 do executável passou a usar um fluxo, porque a primeira tentativa após
a correção apresentou `OutOfMemoryException` ao processar o executável de
45 MB no processo x86. A aplicação registrou falha e restaurou o projeto anterior,
confirmado pelo SHA-256 `a51eb6ec94d287b49a5ae8b9bf964d8e9605c6dc3942b995fd132d490aaac850`
e pelo recibo anterior inalterado. `Clear` e `Close` foram usados por serem
disponíveis no CLR 2.0; o método `Dispose` não estava acessível ao PowerShell 2.

A revisão 12 foi reaplicada duas vezes pela API real. O agente confirmou três
variáveis e digest `7daaa01263c643239b6b25b53b4744f54d413ea17be1d871239280d4368aae39`.
O hash do agente corrigido é
`b32b2eb4e2111d8fae4c2bb1ff80e2d51d54671ed170c5fd7080ec3437b6d74e`.
Executável e `LocalServer32` permaneceram inalterados nesta correção.

Validações executadas:

- PowerShell 2 real: criação, duas substituições, arquivo bloqueado (falha com
  conteúdo anterior preservado) e recuperação após liberar o arquivo, usando
  `scripts/test_windows_agent_atomic.ps1` em uma pasta temporária isolada.
- Rejeição de valor incompatível pelo validador Rust no Windows, com recibo e
  configuração confirmados da revisão 12 inalterados.
- PI OPC Client 1.4.0.0 real, automatizado em sessão 0, PID 2096 com servidor
  PID 5144: Flat, adição das três tags, Refresh e Sync passaram. Resultados
  Good/S_OK e timestamp local 05/11/2026 15:30:03: True VT_BOOL, 36 VT_R8 e
  7 VT_R8. Não é observação manual da sessão RDP.
- Navegador real: recibo aplicado real; mensagem `Falhou` e texto de erro
  testados com resposta simulada. Teste automatizado confirma que o status
  permanece `applying` até receber ACK e falha conserva o recibo anterior.

O PI OPC Client anterior PID 4676 foi encerrado à força após confirmação do
caminho e sessão, pois CloseMainWindow não encerrou o processo. O servidor
PID 5416 encerrou normalmente por marcador do agente. O helper encerrou somente
seus próprios PIDs 2096 e 5144. Nenhum serviço, DCOM, firewall ou Registro foi
modificado. A máquina 1 não foi acessada.

Antes da troca do script do agente foram salvos `project.json`,
`last-good-project.json`, `receipt.json` e o agente original em:

`D:\OPCB\rust-da-agent-lab-20261105-build1\before-reapply-fix-ffa84a1f3a55414996222994a4e352a6`

Para reverter valores de forma coordenada, edite os valores desejados na web
e aplique novamente; para os valores anteriores, use TESTE1=15 e TESTE2=28.
Após aplicar, reconecte o cliente OPC DA para ativar a nova configuração.
Os backups acima permitem recuperar o estado de revisão 8 manualmente, mas
restaurar somente arquivos no Windows deixaria o recibo Linux inconsistente;
a reaplicação pela interface mantém ambos sincronizados. O agente anterior
também está no backup, mas contém a falha de reaplicação e não é recomendado.

## Atualização de valor sem reiniciar — implementação em validação

O agente envia uma revisão candidata ao endpoint de controle local do processo
DA (`127.0.0.1:49322`). O servidor valida que somente valores simulados e a
revisão mudaram, grava o documento por substituição atômica e troca o runtime
sob o mutex compartilhado. Recusa ou erro de persistência mantém o runtime
anterior. Perda da resposta após envio é marcada como **indeterminada** e deve
ser reconciliada consultando a revisão ativa. Esse caminho não chama `StopOwn`
nem reinicia o processo; o servidor continua independente do configurador.

O servidor mantém o projeto e o cache em `Arc<Mutex<ProjectRuntime>>`, compartilhado
pelas instâncias COM e seus grupos. Cada grupo mantém sua própria tabela de
handles e clientes. A atualização apenas dos valores dentro do mesmo runtime
pode preservar esses objetos. `Refresh2` implementa um callback sob demanda;
notificações contínuas de alteração ainda não foram demonstradas.

O servidor valida somente mudanças de valor e revisão da mesma máquina;
mudanças estruturais, revisões antigas e tipos incompatíveis são recusados
antes de alterar o runtime. Em caso de perda da resposta depois do envio, o
cliente Linux marca o estado como indeterminado até reconciliar a revisão.

Os testes automatizados de deployment/API, modelo Rust e runtime cobrem
confirmação de revisão, mudança de valor, concorrência, revisão antiga,
estrutura/tipo inválidos e erros de comunicação simulados. Eles não substituem
o teste COM no Windows com o PI OPC Client conectado. A validação final ainda
precisa registrar PID e handles antes/depois, executar Sync/Refresh e conferir
valor, tipo, qualidade e timestamp. `Refresh2` é sob demanda; Advise contínuo
permanece sem validação.

### Build hot-update staged em 06/11/2026

O executável x86 foi copiado para a pasta isolada
`D:\OPCB\rust-da-agent-lab-20261106-hotupdate-build1` sem sobrescrever o
build anterior. SHA-256 do `rust-da-com-lab.exe`:
`177e6f706765a1230bb585e4126a22e2d7c40d2eca85cace1e7b570a76207cb6`.
O agente PowerShell staged tem SHA-256
`62e5487e828cac8e39cc925786f8c71c3a25b84525db1243d26e4fa43d89c3db`.
Windows 7 Professional SP1 x86 e hostname `RB1PI2` foram confirmados.

`LocalServer32` em HKCU e HKLM agora aponta para o executável novo; os exports
anteriores estão na subpasta `backup` dessa pasta. O provisionamento Linux
para a máquina cadastrada `RB1PI1`/host confirmado `RB1PI2` também usa esse
novo `agent_dir`; backup com permissão 0600 em
`/var/tmp/opc-hot-update-management-backup-20261106/agents.json` permite voltar
ao diretório anterior. O processo anterior PID 4968 foi parado após verificar
executável e argumento `-Embedding`. O PI OPC Client PID 5632 não pôde ser
fechado pela sessão SSH; ele já não estava presente na consulta posterior.

Executado no RB1PI2 o script `verify_project_runtime.ps1` (SHA-256 local
`18bb0333f5b31aca92f0660da64ad13a7615fe2ec3569b0e488d4f8d89265e96`) contra
o novo executável. Self-test COM, browse das quatro tags simuladas, SyncRead
entre processos, retorno de tipos, valores, qualidade `0xC0`, FILETIME,
rejeição de escrita `0xC0040006`, degradação ruim com timestamp estável na
indisponibilidade simulada e recuperação Good passaram. O servidor temporário
PID 4636 foi encerrado pelo próprio script. Isso não comprova ainda o caminho
do agente hot-update em processo COM ativado pelo PI OPC Client. O usuário deve
reabrir o PI OPC Client na sessão RDP; a alteração de valor com a GUI conectada,
mesmo PID e mesmos handles ainda está pendente.

### Correção do POST fragmentado e aplicação sem reinício — 06/11/2026

O primeiro envio da revisão 18 falhou antes da validação do corpo. A causa foi
o `TcpStream` aceito por um `TcpListener` não bloqueante no Windows 7 continuar
não bloqueante: quando o POST chegava em segmentos separados, a leitura do
corpo retornava `WSAEWOULDBLOCK (10035)`. O servidor fechava a conexão; isso não
alterou a revisão 17 em memória nem o arquivo de configuração ativo.

A correção localizada em `windows/rust_da_com_lab/src/configured.rs` troca o
socket aceito para modo bloqueante antes de ler a requisição. Foi adicionado o
teste Windows `accepted_nonblocking_stream_reads_fragmented_authenticated_post`,
que reproduz cabeçalho e corpo enviados separadamente. O teste compilou e passou
no Windows 7 SP1 x86 (1 aprovado, 0 falhos). O novo executável x86 está em
`D:\OPCB\rust-da-agent-lab-20261106-hotupdate-build2\rust-da-com-lab.exe`,
SHA-256 `152a61852bbea39a0ad4d2acea79aa894b55486deed3ed8637eee13b0041d0d0`.
O build1 foi preservado. Os `LocalServer32` de HKCU/HKLM apontam para build2;
os exports `.reg` para voltar ao caminho anterior estão na subpasta `backup`
de build2.

Com o PI OPC Client conectado ao build2 e o grupo contendo os quatro itens,
a revisão 18 foi aplicada pela API de gerenciamento e confirmada pelo agente
como `hot_applied_server_preserved`. O valor de
`RB1PI1.TESTE.TESTE1.TESTE2` mudou de 19 para 7, sem alterar Item ID ou tipo.
O recibo confirmou o mesmo processo, PID 5780, executável/hash build2 e digest
de projeto `485f765f2e3099f05844cd57e598ff2e22d25ae8e14706d029e6c7a3d7c308c8`.
O snapshot autenticado do agente retornou TESTE2=`7.0`, `Double`, qualidade
Good (`192`) e timestamp de origem `2026-11-06T12:45:29.542831Z`; os demais
itens permaneceram TESTE1=`14.0`, DIGITAL=`false` e PRESSAO=`12.4`, todos Good.

Antes da aplicação, o log do PID 5780 mostrava Group1 (`server_handle=2`),
TESTE2 (`server_handle=4`, `client_handle=103`) e Refresh com valor `19.0`.
Após o envio, o mesmo PID registrou `HOT_UPDATE_ACK revision=18` e o digest
acima; não houve saída/recriação do grupo no log. O PID 5780 e o PI OPC Client
PID 5200 continuavam ativos após o ACK. O usuário então executou Refresh na GUI
sem desconectar: o log registra quatro callbacks de Refresh (transações 2–5),
todos `S_OK`; em cada um TESTE2 manteve `server_handle=4` e `client_handle=103`,
retornou `7.0`, qualidade `0xC0` e FILETIME de origem atualizado. O usuário
confirmou que a GUI exibiu Value `7` e Quality `Good`. Isso valida Refresh com
o grupo/handles da sessão já conectada. Advise/notificações contínuas não foram
testadas.

O usuário também executou Sync no mesmo grupo: o log registra `IOPCSyncIO::Read`
com `source=Cache`, quatro itens e retorno `S_OK`; TESTE2 retornou `7.0`,
qualidade `0xC0`, `FILETIME=134384449071350566` e resultado por item `S_OK`.
No adaptador COM, esse timestamp é colocado no campo `ftTimeStamp` de
`tagOPCITEMSTATE`. O usuário confirmou Value `7` e Quality `Good` na GUI, mas
observou que Sync não mostrou Timestamp. A captura da janela mostra as colunas
visíveis Tag, Value, Quality, Error e Type, sem coluna Timestamp; portanto a
evidência atual aponta para apresentação do PI OPC Client, enquanto a interface
COM retornou FILETIME. Ainda não foi confirmado se a coluna de timestamp está
oculta fora da área horizontal ou se esta tela não a apresenta.


### Telemetria stdin e intervalo (09/11/2026)

Perfis de destino atualizados podem declarar `runtime_transport: "ssh_stdin_v1"`.
Isso envia somente envelopes de telemetria pela entrada padrão de um único SSH
verificado. A aplicação do projeto continua por SCP/inbox, com validação e
recibo. O agente precisa da versão com RequestStdin/RequestJson; habilitar a
opção em um agente antigo falha, não deve ser anunciado como aplicado. O JSON
é serializado com escapes Unicode para não depender da code page do PowerShell2,
encaminhado pela pipeline em memória e limitado a 2 MiB. Não há payload em
argumentos de processo nem relaxamento de identidade/host key/token.

Na RB1PI2, a revisão 33 aplicou intervalo 5 s mantendo PID 2324 e o catálogo de
cinco itens. O agendamento é entre inícios de ciclos; uma leitura lenta nunca
se sobrepõe à próxima. O status de coleta inclui timings_ms. Veja a medição
antes/depois e os backups em OPC_DA_DISCOVERY_AND_RELAY.md. O runtime aguarda
amostra válida e não deve devolver Good fabricado para ocultar a inicialização.
