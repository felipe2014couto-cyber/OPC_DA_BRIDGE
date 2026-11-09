# Validação local com PI OPC Client

**Estado em 2026-11-05:** o build `refresh-build1` foi testado com o próprio
PI OPC Client 1.4.0.0: o comando Refresh com Cache no padrão retornou os dois
itens com valor, qualidade e timestamp. A ativação pelo Registro do laboratório
e a perda/recuperação da fonte simulada também foram testadas. Os relatos de
builds anteriores abaixo são históricos; veja o resultado mais recente no fim.

## Estado verificado na RB1PI2

Consulta remota somente de leitura em `RB1PI2` (`10.247.140.166`) confirmou
Windows 7 SP1 x86 (`6.1.7601`). O executável encontrado é:

```text
D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\OPCClient.exe
FileVersion: 1.4.0.0
```

Na consulta inicial não havia processo `OPCClient.exe` em execução. O usuário
depois confirmou que abriu o cliente manualmente e viu os dois servidores
RSLinx listados. `query session` mostrou uma sessão RDP ativa (ID 2), mas a
conexão SSH executa comandos fora dessa área de trabalho e não oferece
captura/controle gráfico; a confirmação da interface foi feita pelo usuário.
Para abrir novamente na sessão RDP, use um PowerShell interativo nessa área de
trabalho:

```powershell
& 'D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\OPCClient.exe'
```

O Registro contém `OpcEnum.exe`, `OPC.Automation.1`, `opcproxy.dll` e proxies
OPC Core Components; esses são componentes de suporte, não servidores de
simulação.

### Servidores locais encontrados

A listagem das categorias COM OPC DA 1.0 e 2.0 encontrou somente:

| Nome/ProgID | CLSID | Servidor associado | Decisão |
|---|---|---|---|
| `RSLinx OPC Server` | `{A05BB6D5-2F8A-11D1-9BB0-080009D01446}` | `D:\AppSW\ROCKWE~1\RSLinx\RSLINX.EXE`; `RSOPCCON.DLL` | Não conectar: servidor Rockwell, sem indicação de que exponha apenas dados simulados. |
| `RSLinx Remote OPC Server` | `{A05BB6D6-2F8A-11D1-9BB0-080009D01446}` | `D:\AppSW\ROCKWE~1\RSLinx\RSLINX.EXE` | Não conectar: não é simulador identificado. |

Não foram encontrados servidores nas categorias DA 3.0, nem outro servidor
simulado registrado nas categorias DA 1.0/2.0. Nenhuma conexão COM com RSLinx,
ABB ou PI Interface foi iniciada; nenhum valor foi escrito.

## Recursos documentados do PI OPC Client

A documentação OSIsoft descreve conectar ao nó local (`Localhost`), listar e
conectar ao servidor OPC, criar um grupo, abrir o diálogo de browse/adicionar
itens, listar a árvore, selecionar itens e observá-los no grupo. A janela
Polling Group exibe valor, timestamp e qualidade. A ferramenta também pode
exercitar leitura síncrona, refresh e advise/inscrição. Esses recursos são
descritos pela documentação do PI OPC Client; ainda não foram observados na
GUI desta máquina.

Referências: [PI Interface for OPC DA User Guide](https://cdn.osisoft.com/interfaces/3424/PI_OPCInt_2.5.0.9a.pdf),
[PI System Administration training, atividade PI OPC Client](https://osicdn.blob.core.windows.net/learningcontent/pdfs/PI%20System%20Administration.pdf).

## Situação do protótipo COM Rust

O build6 foi encontrado pelo PI OPC Client como `RustDaBridge.Lab.1`; o
cliente criou `Group1` com taxa de 1000 ms. Ao navegar, mostrou “This server
does not support browsing.” O grupo ficou com zero itens e ainda não houve
leitura pelo PI client. Essa observação é referente ao build6 atualmente
registrado.

Foi compilado um build separado com `IOPCBrowseServerAddressSpace` e
`IEnumString` e o teste COM de browse passou no RB1PI2. O novo executável está
em `D:\OPCB\rust-da-com-lab-20261104-browse-build2\rust-da-com-lab-browse.exe`.
O PI OPC Client ainda não o testou. Consulte
[`RUST_DA_COM_LAB.md`](RUST_DA_COM_LAB.md) para hash, interfaces e resultado.
Notificações assíncronas continuam ausentes.

### Caminho registrado para testar browse

Após autorização, somente o valor padrão `LocalServer32` do CLSID foi atualizado
em HKCU e HKLM para:

```text
"D:\OPCB\rust-da-com-lab-20261104-browse-build2\rust-da-com-lab-browse.exe"
```

Os valores anteriores estão preservados em
`D:\OPCB\rust-da-com-lab-20261104-browse-build2\hkcu-build6-before-browse-build2.reg`
e `D:\OPCB\rust-da-com-lab-20261104-browse-build2\hklm-build6-before-browse-build2.reg`.
Para reverter, execute como administrador
`D:\OPCB\rust-da-com-lab-20261104-browse-build2\rollback-to-build6.cmd`, que
importa somente esses dois exports de `LocalServer32`. O executável build6
permanece intacto.

Reabra o PI OPC Client pela sessão RDP. Confirme no Gerenciador de Tarefas que
o processo iniciado pelo cliente aponta para o build de browse. Então:

1. Na sessão RDP da RB1PI2, abra o PI OPC Client pelo caminho acima.
2. Mantenha `Localhost` como nó e use **Connect to node**.
3. Selecione somente o ProgID de laboratório Rust e use **Connect to OPC
   Server**. Confirme estado `RUNNING`.
4. Use **Add Group** e **Create**.
5. Abra **Browse OPC Server, Add Tags** e pressione **List**. O servidor anuncia
   um endereço plano; adicione somente:
   `RustLab.Numeric.Double` e `RustLab.Boolean`.
6. Na janela de polling do grupo, confira valor, tipo apresentado, qualidade e
   timestamp. Use refresh/leitura síncrona. Advise/notificação assíncrona ainda
   não está implementada e não deve ser considerada aprovada.
7. Não use funções de output/write. O servidor deve rejeitar escritas, mas o
   teste permanece de leitura.
8. Quando houver um controle de fonte simulada no servidor, interrompa somente
   essa fonte e confirme qualidade ruim; religue-a e confirme que dados só
   voltam a Good depois de uma nova amostra.

O procedimento não requer selecionar RSLinx, iniciar PI Interface ou alterar
DCOM/firewall/serviços. Use apenas leitura e refresh síncrono; notificações
assíncronas não estão implementadas.

## Diagnóstico de leitura síncrona no PI OPC Client 1.4.0.0 (2026-11-05)

O manual que acompanha a instalação foi consultado em
`D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\PI_OPCClient.docx`, capítulo 9,
“Reading Data”, páginas 25–26. Ele distingue três controles: `Cache/Device`
define a origem; `SyncRead` executa leitura síncrona; `AsyncRead` executa
leitura assíncrona. O próprio manual descreve a seleção do servidor conectado,
do grupo que contém os itens e, então, o clique em `SyncRead`. A seção também
classifica `Refresh` como operação assíncrona. A documentação de PI confirma
que o PI OPC Client serve para testar sync read, refresh e advise
([PI Interface for OPC DA User Guide](https://cdn.osisoft.com/interfaces/3424/PI_OPCInt_2.5.0.9a.pdf)).

Na observação de 2026-11-05, o PI client (PID 4240, sessão 2) ativou o build7
(PID 5148, sessão 2), criou `DummyGroupABC` e `Group1`, navegou em Flat e
adicionou `RustLab.Boolean` e `RustLab.Numeric.Double`; os dois resultados
`ADD_ITEM_RESULT` foram `S_OK`, com handles 1 e 2 e tipos canônicos `VT_BOOL`
e `VT_R8`. O log atualizado termina nessas adições: não contém
`READ_REQUEST`/`READ_RESULT`. Como o método de leitura do serviço registra
`READ_REQUEST` antes de chamar o modelo, a leitura síncrona ainda não chegou
ao caminho de dados do servidor nesta tentativa. O build7 continua registrado
em HKCU e HKLM; nenhum valor do Registro foi alterado nesta investigação e os
dois processos foram deixados em execução.

A mensagem observada anteriormente, “OPC DA v2.05a doesn't support AsyncRead
from Cache”, é compatível com o uso de `AsyncRead` junto à opção `Cache`; ela
não comprova uma falha de `IOPCSyncIO::Read`. O build7 não implementa as
interfaces assíncronas, portanto não use `AsyncRead`, `Refresh` ou `Advise`
para este teste.

Procedimento para a próxima tentativa na GUI da sessão RDP:

1. Na janela principal, selecione `RustDaBridge.Lab.1` em Connected Servers.
2. Selecione `Group1` na lista Groups (não apenas a janela/linha de itens).
3. Na lista Items/Points do grupo, confirme as duas tags e selecione uma ou
   ambas.
4. Use `Cache/Device` para escolher `Device`, depois acione o botão
   explicitamente chamado `SyncRead` na seção/toolbar do grupo. Selecione
   `Cache` em uma segunda tentativa, mas continue usando `SyncRead`.
5. Não use `AsyncRead`, `Refresh`, `Advise` nem qualquer comando de escrita.

Após o clique, verificar no log ao lado do executável:

- `READ_REQUEST source=Device` ou `source=Cache` confirma que
  `IOPCSyncIO::Read` chegou ao serviço.
- `READ_RESULT` deve aparecer uma vez por item, com `result=S_OK`, valor,
  qualidade e FILETIME. Se não surgir `READ_REQUEST`, registrar qual botão foi
  acionado, qual grupo estava selecionado e qualquer mensagem do cliente.
- Se surgir `READ_REQUEST` mas não houver `READ_RESULT`, preservar o trecho
  completo do log e o HRESULT/mensagem mostrados pela GUI; isso indicaria uma
  falha dentro do caminho de leitura do servidor a investigar separadamente.

O teste COM entre processos de diagnóstico já executou AddItems e Sync Read
com sucesso, mas não substitui esse teste do PI OPC Client. Até agora, o PI
client provou conexão, browse e AddItems; ainda não provou leitura.

### Build8: diagnóstico no limite de `IOPCSyncIO::Read`

O build8 mantém as mesmas tags e lógica e adiciona logs antes e depois do
limite COM: `IOPCSyncIO::Read ENTER` inclui origem DA (`1` Cache, `2` Device),
quantidade e ponteiros nulos; `IOPCSyncIO::Read RETURN` informa HRESULT. O
cross-process test em RB1PI2 passou: viu a chamada entrar com `source=2`, o
serviço leu os dois itens e a interface retornou `S_OK` por lote; os resultados
por item também foram `S_OK`, Good, com valor e FILETIME. Isso não confirma o
comportamento do PI OPC Client.

O manual local especifica a sequência servidor conectado → selecionar o grupo
que contém os itens → escolher a fonte em `Cache/Device` → clicar
`SyncRead`. `Refresh` e `AsyncRead` são operações distintas e assíncronas. A
janela **OPC Server Properties**, disponível após conectar ao servidor, lista
as interfaces do servidor e grupo; verificar `IOPCSyncIO` ajuda a separar uma
falha de QueryInterface de um comando de leitura não acionado.

O `LocalServer32` em HKCU e HKLM aponta agora ao build8. Os backups que
restauram build7 estão junto ao executável em
`D:\OPCB\rust-da-com-lab-20261105-diag-build8` (`rollback-before-build8-hkcu.reg`
e `rollback-before-build8-hklm.reg`). O PI OPC Client PID 4240 não pôde ser
fechado por SSH: não tinha janela principal e `taskkill` sem força foi
recusado. Não foi encerrado à força. O processo de laboratório build7 foi
encerrado após conferência do caminho e argumento, mas o cliente ainda não
ativou build8; o teste de leitura na GUI está pendente.

## PI OPC Client real: SyncRead validado (2026-11-05)

O cliente pôde ser controlado por mensagens Win32 numa instância diagnóstica
na sessão SSH 0. A árvore foi enumerada e somente `RustDaBridge.Lab.1` foi
selecionado para conexão. O próprio diálogo **OPC Server Properties** reportou
`IOPCSyncIO: Yes (Required)` nas colunas DA 1.0a e DA 2.05a. Um grupo ativo
com DA v2.05a e taxa padrão de 1000 ms recebeu apenas as duas tags simuladas
via Flat/List/Select All/Add Selected. Não foi conectado nenhum RSLinx, ABB ou
PI Interface.

O comando de leitura real é o controle `Sync` ID **1028** da janela principal;
o seletor `Cache/Device` é ID **1029**, com texto `Dev` para Device. A lista
de **itens** é controle 1002; a lista de **grupos** é 1003. Selecionar o grupo
e acionar Sync com zero linhas de itens selecionadas leu os dois itens.
Selecionar ambos os itens também leu os dois. Assim, falta de seleção das
linhas dos itens não explica, por si só, o relato original. O manual instalado
chama o comando de `SyncRead`; Refresh/AsyncRead são outros comandos.

| Evidência do teste reproduzível | Resultado |
|---|---|
| Cliente real | `OPCClient.exe` 1.4.0.0, PID 5988, sessão 0 |
| Servidor correspondente | PID 5952, caminho confirmado `D:\OPCB\rust-da-com-lab-20261105-diag-build8\rust-da-com-lab-diag.exe` |
| Interface vista pelo PI Client | `IOPCSyncIO: Yes (Required)` em ambas as versões DA da janela Properties |
| Seleção antes de Sync | 1 grupo, 2 itens, 0 linhas de itens selecionadas |
| Chamada observada | `COM_SYNCIO ... ENTER source=2 count=2`, depois `READ_REQUEST source=Device` |
| `RustLab.Boolean` | PI UI `FALSE`, `VT_BOOL`, `Good - 11000000`; servidor `Bool(false)`, quality `0xC0`, per-item `S_OK` |
| `RustLab.Numeric.Double` | PI UI `61.500000`, `VT_R8`, `Good - 11000000`; servidor `F64(61.5)`, quality `0xC0`, per-item `S_OK` |
| Timestamp de origem nos resultados do mesmo PID | FILETIME `134383586988495406` para ambos; não foi inferido a partir das colunas da GUI |
| HRESULT da interface | `IOPCSyncIO::Read RETURN hresult=S_OK` |
| Repetição por anexação | Helper anexado à mesma instância do PI Client repetiu Sync com sucesso |

Esses eventos foram extraídos somente das linhas acrescentadas durante cada
invocação e tiveram o PID e o caminho do servidor verificados. As leituras
antigas do PID 4104 não foram utilizadas. O teste comprova a operação pelo PI
Client real, além do cliente Rust de testes; não é uma captura da sessão RDP.
Nenhuma alteração no binário ou no Registro foi necessária nesta rodada. A
causa exata de o clique anterior na sessão RDP não ter disparado o método
continua sem observação direta. Falha permanente de disponibilidade/QI ou de
marshaling de `IOPCSyncIO` não se reproduziu no cliente real testado.

### Helper para acionar e conferir o comando na sessão RDP

O script original do projeto
[`scripts/test_pi_opc_client_sync.ps1`](../scripts/test_pi_opc_client_sync.ps1)
foi copiado para
`D:\OPCB\pi-client-ui-20261105\test_pi_opc_client_sync.ps1`. Usa apenas o
Windows PowerShell/.NET já instalado para diagnóstico Win32. Esses componentes
existentes do Windows são proprietários. Nenhuma ferramenta foi instalada; o
gateway continua compilado pelo toolchain Rust isolado adotado. O helper não
faz chamadas OPC diretamente: envia o comando da interface do PI Client e lê
as células dele. Não escreve no Registro nem recompila o servidor.
O SHA-256 do helper no checkout e no RB1PI2 confere:
`f540a93e0095d145eff9562d1ac897b47959d9e976deb5e27f4f2aaeae8d1bee`.

Na sessão RDP, abra novamente PI OPC Client, conecte somente ao servidor Rust,
crie/selecione um grupo ativo e adicione apenas as duas tags. Execute neste
mesmo desktop um PowerShell x86:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\OPCB\pi-client-ui-20261105\test_pi_opc_client_sync.ps1
```

O parâmetro de ExecutionPolicy vale somente para esse processo. O modo padrão
anexa ao PI Client da mesma sessão, verifica versão/caminho, servidor
selecionado, interface, grupo e os dois Item IDs, seleciona Device e aciona o
handler real de Sync. Ele deixa o cliente e os valores abertos. Deve terminar
com `PI_CLIENT_SYNC_PASS` e imprimir as células Value/Quality e os resultados
do mesmo PID do servidor. Se o estado não for o esperado, aborta antes de ler.
Ele não usa Write, Advise, Refresh ou AsyncRead.

Para repetir o teste completo numa sessão SSH isolada, use
`-StartDiagnosticClient`. Esse modo abre sua própria instância, navega Flat,
adiciona somente as tags permitidas e encerra apenas o cliente e os novos
servidores de laboratório que criou, após conferir PID/caminho/sessão. O modo
de anexação e o de criação foram ambos executados no RB1PI2 e passaram.

### Encerramentos e estado final

Foram encerrados à força, após conferência de identidade, o PI Client
diagnóstico bloqueado PID 5744, os clientes próprios PIDs 6012 e 5988 ao fim
dos testes, e os servidores Rust de laboratório PIDs 2192, 3576, 5512, 6100,
5264, 2348, 4620, 4268, 5000 e 5952. Outros clientes temporários terminaram ao
fechar suas sessões SSH. A instância antiga PID 2836, sessão 2, foi encerrada
somente depois de confirmar ausência de qualquer `OPCClient.exe`, caminho
build8 e argumento `-Embedding`. Os PIDs podem ser reutilizados posteriormente;
essa lista descreve apenas os processos conferidos e encerrados nesta execução.
Na última consulta não havia PI Client nem servidor Rust de laboratório ativo.
HKCU/HKLM continuam apontando para build8, sem alteração nesta investigação.

## Refresh padrão no PI OPC Client (2026-11-05)

No build8, um clique no controle **Refresh** (ID 1025, setas circulares) foi
reproduzido no PI Client e resultou em “No ConnectionPointContainer found.”
O botão não é equivalente a Sync: o manual instalado descreve Refresh como
requisição assíncrona que devolve os dados por callback. Na especificação DA
2.05, `IOPCAsyncIO2::Refresh2` solicita `IOPCDataCallback::OnDataChange` para
os itens ativos e requer uma conexão de callback. [OPC DA 2.05, §4.5.6.3](https://www.advosol.com/OpcSpecs/opcda205_cust.pdf)

O build `refresh-build1` adiciona `IConnectionPointContainer`, um ponto de
conexão `IOPCDataCallback` e `Refresh2` para os itens ativos. O cliente PI
estabelece e encerra a conexão COM de callback automaticamente ao usar
Refresh; não é preciso clicar no botão Advise nem mudar Cache/Device. A conexão
usa `IConnectionPoint::Advise` internamente, exigido pelo protocolo, e não
habilita notificações contínuas. O fluxo continua somente leitura.

No teste do binário registrado, o PI Client PID 2196 iniciou pelo COM o servidor
PID 4212, selecionou as duas tags e clicou Refresh com Cache no padrão. O
cliente exibiu `FALSE` (`VT_BOOL`) e `37.500000` (`VT_R8`), ambos
`Good - 11000000`, com timestamp local `11/05/26 10:57:34`. O log do PID 4212
registra `REFRESH_REQUEST source=Cache count=2`, `READ_RESULT`/`S_OK` por item
e `REFRESH_CALLBACK ... result=S_OK`. O PI Client e o servidor temporários
foram encerrados após a verificação. O teste ocorreu na sessão SSH 0, usando
os controles do PI Client real, e não representa ainda um clique manual na RDP.

Um teste adicional com class object temporário no mesmo Windows 7 x86 fez quatro
Refresh seguidos: Good → Bad → Bad → Good após controlar apenas a fonte
simulada. Os dois Refresh durante a interrupção mantiveram o mesmo FILETIME
`134383604383527451`; a recuperação retornou timestamp mais recente após uma
nova amostra. Três operações de conexão e desconexão de callback foram
observadas no primeiro teste de perda/retorno; o teste com repetição conferiu
também timestamp estável. Sync foi testado como regressão e retornou S_OK.
Nenhuma operação Write ou clique no botão Advise foi feita.

O executável está em
`D:\OPCB\rust-da-com-lab-20261105-refresh-build1\rust-da-com-lab.exe`, SHA-256
`619cb824f3c2d365a09df01a89f433642f48afe61d194af842283e63ba6dea1a`.
Após guardar os valores anteriores e confirmar que não havia processos PI ou
servidores Rust ativos, apenas os valores padrão `LocalServer32` do CLSID de
laboratório em HKCU e HKLM foram alterados para esse executável. Build8 não foi
sobrescrito. Para reverter ao build8, feche o cliente e o servidor Rust e
importe os dois arquivos no diretório do build Refresh:

```bat
reg import "D:\OPCB\rust-da-com-lab-20261105-refresh-build1\rollback-before-refresh-hkcu.reg"
reg import "D:\OPCB\rust-da-com-lab-20261105-refresh-build1\rollback-before-refresh-hklm.reg"
```

SHA-256 dos backups: HKCU
`677f9cda1b176bcca9bb6da2e95a5e4feae8b4de2bf7ec8cee8763a0cbd6013c`;
HKLM `a9dc1060c8de72f4ce9149b22010db2c19fc07fb3634c0dfa5d62f5c47f995a2`.

Para verificar manualmente, abra novamente o PI OPC Client, conecte a
`RustDaBridge.Lab.1` e selecione o grupo que contém as duas tags. Com Cache no
estado padrão, clique **Refresh**, o botão de setas circulares à esquerda de
Sync. Depois que o grupo e os itens estiverem criados, cada novo clique em
Refresh deve atualizar Value, Quality e Timestamp. Se o grupo não tiver sido
salvo pelo cliente, será necessário criá-lo e adicionar as tags uma vez após
reabrir o cliente. A validação física desse clique na sessão RDP permanece
pendente até confirmação do usuário.

O teste reproduzível está em
[`scripts/test_pi_opc_client_refresh.ps1`](../scripts/test_pi_opc_client_refresh.ps1).
No RB1PI2 foi copiado para
`D:\OPCB\pi-client-ui-20261105\test_pi_opc_client_refresh_registered.ps1`.
Sem argumentos, testa um servidor temporário e usa um marcador de fonte
simulada somente na pasta isolada; com `-Registered`, testa a ativação normal
e Refresh sem criar o marcador. O script não testa Write nem o botão Advise.
Ele exige ausência prévia de servidor de laboratório e de PI Client na mesma
sessão, confere executável/hash, e encerra somente os processos de teste.

## Remoção do aviso de shutdown (2026-11-05)

O aviso de conexão era exibido porque o objeto servidor não expunha o ponto
de conexão `IOPCShutdown` da OPC DA 2.05. O build
`D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rust-da-com-lab.exe`
(SHA-256 `622fdc4d1e6755f761d77062778ef7feef08a82c231ac3cff35daa459a2ecc5e`)
agora aceita a inscrição de shutdown no objeto servidor. Testei a conexão com
o PI OPC Client 1.4.0.0 tanto por class object volátil quanto pela ativação COM
registrada: o aviso não apareceu, o log registrou `SHUTDOWN_CALLBACK_CONNECT`,
e Refresh exibiu as duas tags. No teste registrado: `FALSE`/`VT_BOOL` e
`45.500000`/`VT_R8`, ambas Good, timestamp `11/05/26 11:16:54`; Sync também
passou. O teste volátil repetiu Good → Bad → Bad → Good e confirmou timestamps
de origem estáveis durante a indisponibilidade simulada.

O servidor ainda não chama `IOPCShutdown::ShutdownRequest` antes de encerrar,
e a enumeração dos connection points de shutdown continua `E_NOTIMPL`. O
callback é aceito e mantido; as notificações de dados contínuas também seguem
fora do escopo. Assim, o aviso de conexão foi removido, mas o ciclo completo
de shutdown ainda não foi validado.

Os valores padrão `LocalServer32` em HKCU e HKLM agora apontam para o build
shutdown. Os backups do valor anterior, que aponta para refresh-build1, estão
em `D:\OPCB\rust-da-com-lab-20261105-shutdown-build1`:

```bat
reg import "D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rollback-before-shutdown-hkcu.reg"
reg import "D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rollback-before-shutdown-hklm.reg"
```

Hashes dos backups: HKCU
`23c69f97c7ad718c7ec52d794d3dec8f488d1bbbbe20bc9d35c3fb4d67aa18cd`;
HKLM `887c78306fea408fba16eb7ee5221a64eab00990469954082c71558c21e5829e`.
O script `scripts/test_pi_opc_client_refresh.ps1` agora falha se o aviso
reaparecer e confirma a inscrição de shutdown. A validação automatizada ocorreu
na sessão SSH 0; reabra o PI OPC Client na sessão RDP para confirmar visualmente
o mesmo resultado nessa sessão. O teste não usou Write, ABB, RSLinx nem PI
Interface.

## Refresh / Sync / Async / Advise — build final de 09/11/2026

O build registrado nesta etapa é
`D:\OPCB\rust-da-async-20261109-build3\rust-da-com-lab.exe`, SHA-256
`7f9c7b730ec9359bbaaf65a41116e06212c769abd8ecd378c7024ef4f6d4bc12`.
O ensaio COM entre processos passou, incluindo leitura da origem Kepware real
de laboratório. A confirmação visual dos quatro botões no PI OPC Client deve
ser anotada separadamente; os logs dos processos de fixture não comprovam a GUI.

1. Na RDP da RB1PI2, abrir PI OPC Client e conectar somente a
   `RustDaBridge.Lab.1` em Localhost. Criar um grupo ativo DA v2.05a, 1000 ms.
2. Adicionar `RB1PI1.TESTE.TESTE1.TESTE2` e
   `RB1PI1.TESTE.TESTE1.DIGITAL`, por browse ou IDs manuais. Aguardar o primeiro
   ciclo completo do relay (intervalo configurado mais latência SSH).
3. Com Cache no padrão, clicar **Refresh**. Os itens devem permanecer visíveis
   com os valores da origem, Good, timestamp e tipos VT_R8/VT_BOOL. Clicar
   **Sync** e conferir os mesmos campos.
4. Selecionar **Device** e clicar **Async**. Em DA 2.05a, Async Read usa Device;
   o próprio PI Client impede AsyncRead de Cache. Para Cache assíncrono, usar
   Refresh (Refresh2). Implementar Async Read no servidor não muda esse controle
   do aplicativo. Essa distinção também consta no manual instalado do fornecedor
   (`PI_OPCInt.pdf`, PI Interface for OPC DA 2.6.15 User Guide, descrição de
   leituras assíncronas para servidores v2.0); nenhuma PI Interface foi iniciada.
5. Clicar **Advise**. Deve haver uma notificação inicial e novas notificações
   quando valor/qualidade/erro mudarem. Valor estável ou apenas novo timestamp
   não implica incremento contínuo de atualizações. Não usar Write.
6. Manter a conexão enquanto o relay coleta. Conferir que o mesmo PID do servidor
   continua atendendo. O runtime só expõe o último valor recebido, sem histórico.

Registrar valores e campos da GUI e correlacionar com o novo PID do servidor:
`COM_SYNCIO`/`READ_RESULT` para Sync, `REFRESH_REQUEST`/`REFRESH_CALLBACK` para
Refresh, `ASYNC_READ_REQUEST`/`ASYNC_READ_CALLBACK` para Async e
`CALLBACK_ENABLE`/`ADVISE_CALLBACK` (transaction=0) para Advise. O HRESULT
`0x80010105` no callback é falha no lado cliente e não deve ser descrito como
sucesso apenas porque READ_RESULT retornou S_OK.

A origem LB3PI1 teve UTC comprovado por comparação Automation/FILETIME nativo.
O agente preserva agora essa data em UTC, com qualidade Good. Não aplicar essa
política por analogia a outras origens. Se um timestamp continuar ausente, o
novo adaptador retorna E_FAIL por item em COM e preserva a ausência no cache,
sem inventar a hora atual. A visualização desse erro específico no PI Client
continua sendo um teste próprio de interoperabilidade.

### Confirmação visual de Refresh na RDP — 09/11/2026, 13:08 (UTC−3)

O operador enviou uma captura do PI OPC Client conectado ao build3 na RB1PI2,
Group1 com um item, TESTE2=1.000000, Good, VT_R8 e timestamp 11/09/26 13:08:39.
O log do PID 2324 confirma READ_RESULT S_OK, qualidade 0xc0, FILETIME não zero e
REFRESH_CALLBACK S_OK. É confirmação visual de TESTE2/Refresh, não confirmação
visual de DIGITAL ou dos outros três controles.

O aviso inicial 0x800706BA ocorreu antes da primeira amostra: START em FILETIME
134387140984311652, primeiro READ_RESULT de falha em 134387141115819883 e
primeiro SOURCE_SAMPLES_ACK em 134387141129079907. A primeira confirmação levou
14,477 s após a abertura; a leitura que falhou ocorreu 1,326 s antes dela. Após
a chegada, o mesmo PID 2324 passou a devolver Good/S_OK; não houve reinício
para recuperar. O status inicial ainda usa o erro genérico de origem
indisponível, portanto pode produzir um diálogo se o usuário ler antes do
primeiro ciclo. Isso é uma limitação de inicialização identificada e não deve
ser descrita como falha persistente do RPC ou como valor inicial confirmado.
Não foram feitas mudanças de código ou Registro para esta observação.
