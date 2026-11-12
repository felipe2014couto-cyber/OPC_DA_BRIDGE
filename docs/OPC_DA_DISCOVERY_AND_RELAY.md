# Descoberta OPC DA e coleta para o espelho Windows

> **Atualização de 06/11/2026:** a subseção “Coleta contínua Linux → fonte Rust
> da RDP ainda não funciona” abaixo registra a tentativa COM anterior. A causa
> foi confirmada e um transporte de snapshot restrito ao servidor de laboratório
> foi validado posteriormente; veja as atualizações de snapshot e autenticação
> ao final deste documento.

Entrega de laboratório em 06/11/2026. Esta etapa usa origem **OPC DA** e destino
**OPC DA**; a integração OPC UA permanece futura. A máquina 1 de produção não
foi acessada. O servidor registrado que atende ao PI Client foi preservado.

## Estado confirmado ao final da investigação

- Descoberta autenticada Linux → RB1PI2 funciona; não ativa candidatos.
- **Leitura Automation real na RDP passou**, versão group-sync-v3: TESTE2=7
  (VT_R8), DIGITAL=false (VT_BOOL), ambos Good/192 e S_OK. O timestamp foi
  comparado com o log nativo e diferiu apenas -0,384 ms, sem deslocamento de fuso.
- O servidor registrado permaneceu no PID 5780. Não houve troca de executável,
  Registro, serviço Windows, DCOM ou firewall. A máquina 1 não foi acessada.
- **Coleta contínua Linux → fonte Rust da RDP ainda não funciona**: o teste SSH
  criou outra instância, que saiu por conflito na porta 49321, e o Connect
  expirou. Não confundir sucesso na sessão RDP com sucesso por SSH.
- O novo runtime receptor foi validado separadamente com pacotes sintéticos.
  Não houve ensaio PI GUI do fluxo completo nem leitura ABB. Antes de ativar
  uma origem, validar o contexto COM do seu agente; não alterar DCOM ou remover
  a proteção de porta apenas para esconder essa diferença de contexto.

## Componentes e comunicação

```text
Navegador → configurador Linux
                 ├─ SSH verificado → agente de origem Windows
                 │                     ├─ Registro local: descoberta de candidatos
                 │                     └─ OPC.Automation.1 → servidor/itens selecionados
                 └─ SSH verificado → agente do destino Windows
                                       └─ HTTP somente loopback + token local
                                           → cache compartilhado do servidor Rust DA
                                               → clientes OPC DA
```

O IP do formulário não concede acesso ao Registro remoto. Ele seleciona uma
entrada previamente autorizada em `source-agents.json`. O Linux conecta via
OpenSSH, exige chave de host conhecida e chave de usuário existente, e confirma
hostname, endereço e, quando provisionada, arquitetura do processo. Não aceita
automaticamente chaves desconhecidas. Origem e destino usam TCP 22 já existente;
o novo fluxo não abre portas OPC/DCOM entre máquinas. Nenhum serviço, firewall,
DCOM ou Registro Windows foi alterado.

O agente de origem é `windows/agent/opc_da_source_agent.ps1`. Executa localmente
no Windows, enumera `CLSID/Implemented Categories` DA 1.0/2.0/3.0 e o marcador
legado `ProgID/OPC`, na view do **seu processo PowerShell**. A lista informa
ProgID, CLSID e view; a existência de registro não comprova disponibilidade. Não
conecta a candidatos durante descoberta nem usa heurística de nome contendo OPC.
A escolha é explícita e vinculada ao resultado persistido da busca/identidade.

Para ler, o agente confere uma allowlist local de ProgIDs e o CLSID registrado,
cria seu próprio grupo e usa `OPCItems.AddItems` seguido de
`OPCGroup.SyncRead` com fonte Device (2), individualmente somente nos itens
solicitados. Os arrays têm um elemento e limite inferior 1; handles e HRESULTs
são obtidos das saídas de cada operação. Essa versão evita depender do
IDispatch do OPCItem, que retornou E_NOINTERFACE no teste RDP. Não chama escrita nem conecta DCOM remoto. Cria uma sessão COM
transitória por ciclo, desconecta ao concluir e mantém um watchdog de 10 s em
um processo PowerShell filho. No timeout, encerra somente esse filho criado
pelo agente. O SSH tem limite de 20 s. Isso limita bloqueios, mas ainda não é
um coletor COM persistente otimizado para grandes cargas.

O relay Linux inicia coleta apenas para máquinas com configuração aplicada
confirmada, módulos OPC DA selecionados e agentes de origem provisionados.
Há até 128 itens por módulo e até 7 KiB por pedido de leitura (limite da linha
de comando no Windows; pedidos maiores devem ser divididos em módulos); intervalo padrão de 1 s, mínimo de 1 s. Configurações
já salvas mantêm seu intervalo explícito. O intervalo
é a pausa entre ciclos completos; inclui-se também a latência das operações.
Falhas recebem espera progressiva, limitada a 60 s. O relay atual percorre as
origens serialmente: não houve ensaio de capacidade com muitas origens lentas.

## Provisionamento explícito da origem

Não provisionar ou copiar arquivos para PIMS_TD1_PB2 sem autorização específica.
Antes de um ensaio real, conferir conta, fingerprint SSH, arquitetura, permissões
COM e caminhos. A conta precisa ler o Registro e usar a Automation local; o
software não concede essas permissões nem altera DCOM.

Na pasta privada do agente Windows, colocar `opc_da_source_agent.ps1` e
`source-identity.json`. O arquivo `automation_dispatch.ps1` é diagnóstico opcional:
a versão de leitura por grupo não depende dele nem de delegates nativos próprios.

```json
{
  "expected_hostname": "SOURCE-LAB",
  "expected_address": "192.0.2.10",
  "allowed_progids": ["Example.Simulator.1"],
  "timestamp_policy": "reject_unspecified"
}
```

No diretório de dados do configurador Linux, criar `source-agents.json` fora do
Git, com permissão 0600. O exemplo está desabilitado até autorização:

```json
{
  "schema_version": 1,
  "sources": {
    "192.0.2.10": {
      "enabled": false,
      "host": "192.0.2.10",
      "expected_hostname": "SOURCE-LAB",
      "expected_address": "192.0.2.10",
      "expected_process_architecture": 32,
      "username": "lab_reader",
      "agent_dir": "D:\\OPCB\\source-lab",
      "identity_file": "/private/existing-key",
      "known_hosts_file": "/private/verified-known-hosts",
      "powershell_executable": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    }
  }
}
```

Em Windows x64 com Automation x86, usar o executável **SysWOW64** do PowerShell.
Em Windows 7 x86, usar System32 ou `powershell.exe`. Não deduzir arquitetura da
Automation a partir da arquitetura do SO. A máquina 1 foi anteriormente
identificada com Automation x86; isso não foi revalidado nesta entrega.
Proteger a pasta/script/allowlist contra alterações por contas não autorizadas.
Não copiar chave privada para o Windows nem para o repositório.

## Cadastrar e aplicar

1. Na máquina de destino cadastrada, criar/editar um módulo e escolher origem
   **OPC DA**. Informar IP, clicar **Buscar servidores** e selecionar um
   candidato retornado. A interface mostra a identidade confirmada, ausência
   de candidatos e falhas; preserva os campos em caso de erro.
2. Criar uma pasta. Na variável, preencher Nome, **Item ID de origem** exato,
   **Item ID OPC DA publicado**, tipo e descrição. O valor inicial simulado fica
   oculto para origem OPC DA e jamais é usado como amostra real.
3. Aplicar ao destino selecionado. O exportador inclui somente seus itens e
   registra explicitamente módulo de origem/Item ID de origem por variável.
4. A confirmação de configuração não é confirmação de comunicação com a origem.
   O painel de coleta e as amostras indicam o estado real. A UI distingue
   simulação, fonte OPC DA e último valor conhecido com qualidade ruim.
5. No cliente DA, usar browse, AddItems, Sync ou Refresh. Notificações contínuas
   por Advise não foram implementadas/validadas nesta etapa.

O destino precisa do **novo executável e agente** desta entrega. O build2
anterior continua registrado na RB1PI2 e não conhece os vínculos de origem.
Aplicar um projeto OPC DA a um build anterior deve falhar na validação, mantendo
sua configuração anterior. Antes de atualizar executável/LocalServer32, copiar
projeto/identidade/recibo atuais para uma nova pasta, conferir hashes e salvar os
dois valores de LocalServer32 com a reversão. Não registrar diretamente a pasta
de testes abaixo: ela contém projeto/identidade de fixture, não a configuração
do usuário.

Mudanças estruturais são recusadas enquanto o processo está ativo; inclusão,
remoção, renomeação, troca de tipo ou vínculo de origem exigem fechar os clientes
e encerrar normalmente o servidor do laboratório antes de aplicar. Sem instância
ativa, o agente valida e prepara a configuração por substituição atômica, com
backup e restauração em falha. Alterações só de valores simulados continuam
usando o hot-update validado. Amostras reais são atualizações do cache, sem
reescrever o projeto, reiniciar o processo ou recriar grupos/handles.

## Contrato de amostras e falhas

O canal autenticado local oferece `/v1/source/session` e `/v1/source/samples`.
Cada requisição vincula destino, revisão, SHA-256 da configuração e módulo de
origem. A sessão é concedida pelo servidor; uma nova sessão invalida a anterior.
Sequências antigas/duplicadas e pacotes de outro destino/revisão são recusados.
O lote precisa corresponder exatamente aos itens da origem; tipos e valores
são validados antes da troca transacional do cache compartilhado.

Os tipos publicados continuam `VT_R8`/Double e `VT_BOOL`/Boolean. Por padrão,
o tipo efetivamente retornado pelo COM precisa coincidir estritamente com o
VARIANT/CLR esperado (`System.Double`/VT_R8 e `System.Boolean`/VT_BOOL). Uma
única conversão autorizada e explícita nesta etapa é origem `System.Single`/
VT_R4 para publicação Double/VT_R8; ela promove o valor finito, registra os
dois VARTYPEs e preserva a associação de Item IDs. Na leitura por grupo,
`canonical_vartype=null`: não se declara uma consulta canônica que não foi
executada. O tipo bruto é identificado por
`type_evidence=returned_VARIANT_CLR_type`; para a conversão aprovada, a
evidência é `COM_VARIANT_VT_R4_explicitly_converted_to_VT_R8`. Outros tipos
recebem erro explícito, sem conversão deduzida do nome. Qualidade DA é preservada como palavra
numérica; timestamps válidos são transportados como FILETIME, sem substituição
por relógio atual. HRESULT disponível da leitura/conexão é mantido por item.
O agente exige política explícita para `DateTime` sem Kind em
`source-identity.json`: `reject_unspecified` (padrão seguro, timestamp ausente),
`utc` ou `local`. O resultado informa a política. UTC/Local com Kind explícito
são respeitados. Datas ausentes, não representáveis ou locais inválidas/ambíguas
ficam ausentes; não há substituição por relógio atual. Ausência de timestamp
rebaixa Good para Uncertain. A política **precisa ser conferida com o ABB** antes
de habilitar coleta real; o resultado do laboratório Rust não comprova a
semântica da Automation no fornecedor. A conversão DATE/DateTime observada
arredonda para milissegundos, portanto não preserva todos os 100 ns do FILETIME
nativo do servidor. FILETIME no protocolo não recupera a precisão perdida.

Itens com HRESULT de falha mantêm o último valor/timestamp no cache com qualidade
Bad. A apresentação COM retorna erro por item; não afirma que esse último valor
é atual. O timeout/queda de transporte marca Bad; se o Linux parar, o próprio
runtime expira a fonte após `max(30, 3 × intervalo)` segundos, usando tempo
monotônico. Novos projetos/fontes iniciam sem Good. Recuperação exige nova
amostra válida. A configuração e o catálogo permanecem locais; não há histórico,
replay nem recuperação integral de eventos durante interrupções.

## Dependências e procedência

- Novo código de descoberta/relay: biblioteca padrão Python no Linux e
  PowerShell/.NET existentes no Windows. Nenhum pacote ou compilador novo foi
  instalado. O script de origem usa reflection COM; não compila helper C#.
- Windows PowerShell/.NET, COM e OPC Automation são componentes existentes do
  ambiente, não ferramentas open source. A DLL Automation não é redistribuída;
  presença, licença e arquitetura devem ser verificadas em cada origem.
  No RB1PI2 foram observados PowerShell 2.0, CLR 2.0.50727.5420 e
  System.Web.Extensions **3.5.0.0** (serializador JSON); .NET 2 isolado sem esse
  assembly não é uma dependência suficiente. OPCDAAuto.dll 2.02.5.30 já estava
  instalado. Nenhum desses runtimes foi instalado/redistribuído nesta etapa.
- Servidor Rust: toolchain isolado `nightly-2026-11-03`, alvo
  `i686-win7-windows-gnu`, MinGW já auditado no relatório de viabilidade;
  dependências Rust existentes, sem novo crate. Não há imports `combase.dll` ou
  WinRT no PE32 gerado. Windows XP/x64 COM/produção não foram homologados.
- Referência independente OPC-Bridge: commit
  `19491fd010b671409ae6754004466923ac4ae4a4`, metadata **Proprietary**, sem licença
  OSS indicada no repositório. Usada somente para análise; nenhum código foi
  copiado. A referência tem CATIDs diferentes dos utilizados no Registro DA do
  laboratório e substitui timestamps ausentes pelo relógio atual; essas escolhas
  não foram adotadas. Fonte:
  [metadata](https://github.com/felipe2014couto-cyber/OPC-Bridge/blob/19491fd010b671409ae6754004466923ac4ae4a4/pyproject.toml),
  [adaptador](https://github.com/felipe2014couto-cyber/OPC-Bridge/blob/19491fd010b671409ae6754004466923ac4ae4a4/src/opc_bridge/adapters/da.py).
- As dúvidas de licença dos IDLs/bindings OPC Foundation do protótipo existente
  permanecem. Não houve publicação/redistribuição. O modelo é independente e a
  fronteira COM continua isolada. Não considerar licença MIT do wrapper como
  autorização automática sobre arquivos de terceiros.

## Validação registrada

- Suíte Python completa: **126 aprovados, 1 ignorado**, em 10,56 s.
- Dois testes de navegador Chromium 156.0.8078.4: busca vazia/múltiplos candidatos,
  seleção explícita, erro preservando formulário, IDs distintos e ausência de
  fallback simulado; o fluxo simulado anterior também passou, incluindo
  persistência, monitoramento e edição/exclusão.
- 13 testes do runtime Rust passaram no Linux **e** no Windows 7 SP1 x86.
  Cobrem tipo/qualidade/timestamp, lotes transacionais, isolamento, sessão,
  sequência antiga, falha, expiração e recuperação; não são testes COM.
- Descoberta real na RB1PI2: hostname/endereço/view x86 confirmados; candidato
  RustDaBridge.Lab.1 identificado por CATID DA 2.0, sem conectar a terceiros.
- Build PE32 x86 e self-test executados no Windows 7. Executável na pasta nova
  `D:\OPCB\rust-da-source-runtime-20261106-build1`, SHA-256
  `08c86e57bc19bee35fd8d21075a4054c2e2f2d171951e5a87720f9fbdf3d2838`.
  O harness independente de 13 testes tem SHA-256
  `ab10d1b538bbe39a94bdd3c87cd75e2de3bd656f9f8a427dd64ac761270d3f18`.
- Teste COM entre processos, sem Registro, com CLSID transitório exclusivo
  `{71E7A018-B13B-4E24-9461-5C36A7E60952}` e porta loopback 49326: servidor
  PID 3628, cliente PID 5916; o mesmo grupo/handles leu 7 por Cache e 42 por
  Device após a atualização. Ambas as tags retornaram Good, FILETIME e S_OK.
  Revisão/identidade/seq/tipo inválidos foram recusados; erro por item reteve o
  último estado; expiração e recuperação passaram. Origem desse teste: **pacotes
  sintéticos**, não ABB nem Automation. O servidor temporário encerrou normalmente. Os handles foram preservados entre
  as duas leituras do mesmo cliente/grupo.
- Sessão e ACK de amostras também foram exercitados pelo canal real
  Linux→SSH→agente→loopback no receptor temporário PID 1400, depois encerrado
  normalmente. Isso valida o transporte de fixture, não a fonte ABB.
- Tentativa de leitura Automation em outra sessão SSH: o servidor de laboratório
  tentou ativar outra instância e encontrou a porta 49321 ocupada pela instância
  da RDP. Nenhuma leitura foi alegada. O helper
  `scripts/test_opc_da_source_rdp.ps1` está preparado para testar a Automation
  dentro da sessão RDP com somente duas tags simuladas; a execução group-sync-v3
  passou conforme a comparação detalhada abaixo. Nova tentativa SSH após as
  correções continuou sem leitura: COM_CONNECT_BEGIN, com_timeout e instância
  4540 abortando com erro 10048 na porta 49321. Ela saiu naturalmente; somente
  o worker PowerShell criado pelo próprio watchdog foi encerrado no timeout.
  Servidor 5780 e PI Client 5112 permaneceram ativos. A causa comprovada da
  falha dessa ativação é o conflito de porta; a diferença de logon/sessão deve
  ser tratada no desenho do agente antes de validar coleta contínua.
- O PI OPC Client atual permaneceu conectado ao build anterior. O teste anterior
  de hot-update simulado 19→7 com PID/handles preservados continua válido.
  **Não foi validado no PI Client o novo caminho de origem real.**

Os testes COM precisam compartilhar o logon: classes transitórias registradas
numa sessão SSH não foram encontradas pelo cliente de outra sessão. O helper
PowerShell mantém servidor/cliente no mesmo logon e não altera DCOM para contornar
esse comportamento.

## Reproduzir e reverter

Build isolado, sem downloads nem registro COM:

```bash
OPC_RUST_BUILD_PARENT="$PWD/.offline" \
CARGO_TARGET_DIR="$PWD/.offline/discovery-com-target" \
  bash scripts/build_rust_da_com_lab.sh

PYTHONPATH=src python3 -m pytest -q
NODE_PATH=/tmp/opc-management-browser-tools/node_modules \
OPC_BROWSER_EXECUTABLE=/caminho/do/chromium-existente \
  node scripts/test_opc_da_discovery_browser.cjs
```

`test_rust_da_source_runtime.ps1` exige o fixture isolado, executável/hash
conferidos e nenhuma instância daquele caminho ativa. O wrapper Linux
`test_rust_da_source_runtime.py --target arquivo-privado.json --executable exe-local`
executa o helper na pasta indicada por `agent_dir`; o helper deve ser copiado
para essa pasta. Ele cria/encerra somente seus próprios processos, não registra
COM e não escreve via OPC. O fixture usa identidade
`discovery-lab-destination`, duas tags DiscoveryLab.*, vínculos source-lab e
porta 49326; não usar um projeto real neste teste.

As mudanças de código anteriores estão preservadas em
`.offline/discovery-before-20261106` com hashes. Para reverter o protótipo Windows
registrado, nenhuma mudança é necessária: seu executável/LocalServer32 continuam
anteriores. Para retirar os testes isolados, confirmar ausência dos processos
exclusivos; preservar logs/hashes antes de remover manualmente suas pastas.
Não restaurar um banco de configuração enquanto a aplicação estiver gravando;
reversão do backend deve combinar código e backup SQLite compatível, preservando
cadastros posteriores para revisão. O catálogo de descoberta corrompido é
preservado e a busca é recusada; a interface e a configuração principal continuam
utilizáveis.

Para validar com a máquina 1, falta autorização específica para leitura da
identidade/Registro e para transferir/executar o agente em pasta isolada, além de
conferir conta/arquitetura Automation e timestamps ABB. Não foi instalado agente
nem iniciada conexão com ABB, RSLinx ou PI Interface nesta entrega.

## Disponibilização no Linux

O configurador foi reiniciado com autorização existente somente para esse serviço.
Antes da troca, foi criado backup SQLite consistente e dos arquivos de implantação
em `.opc-bridge-management/backups/before-opc-da-discovery-20261106T152120Z`.
A revisão 19 e o estado de aplicação anterior permaneceram preservados.
Foi provisionada **somente** RB1PI2 como origem de laboratório, com script/allowlist
isolados em `D:\OPCB\rust-da-source-agent-20261106-discovery-build2`; a allowlist
permite conectar somente a RustDaBridge.Lab.1. A máquina 1 não está provisionada.
O teste manual na RDP usa a pasta build1 preservada. Os dois scripts de origem
foram sincronizados na correção descrita abaixo; seus backups individuais foram
preservados.

A descoberta pela API em execução retornou três candidatos da RB1PI2/view x86:
RSLinx OPC Server, RSLinx Remote OPC Server e RustDaBridge.Lab.1. Nenhuma
conexão foi feita aos servidores RSLinx; `availability_tested=false` em todos.
Após os guardas finais, os 61 testes relevantes de API/deployment/fontes passaram.
O hash do agente antes da correção de coleções era
`86b0e21e4899c13ced4d450defce650982abaab050783036af3f8bc7a1c39dd7`.
HKCU e HKLM LocalServer32 continuam no hotupdate-build2; o PI Client PID 5200
continua usando o servidor PID 5780. O receptor isolado foi encerrado por marcador,
sem força. Não houve alteração no Registro Windows.

### Correção do teste Automation na RDP — 2026-11-06

O primeiro helper retornou `source_error` após `COM_CONNECTED`, antes de
criar o grupo. As funções PowerShell devolviam diretamente o resultado da
reflexão; o pipeline enumerava OPCGroups vazio e devolvia `$null`. A correção
usa a vírgula unária para preservar coleções/RCWs em `ComGet` e `ComCall`.
`scripts/test_opc_da_com_helpers.ps1` reproduziu o retorno antigo vazio e
validou a correção no **PowerShell 2.0 do RB1PI2**, com coleção vazia, não vazia,
array vazio retornado por método, propriedade escalar e máscara dos parâmetros
por referência. Não é apresentado como teste COM.

A DLL instalada foi consultada somente por metadados: `C:\Windows\system32\OPCDAAuto.dll`,
FileVersion/ProductVersion `2.02.5.30`, CompanyName `OPC Foundation`. É componente
preexistente, não foi instalado, copiado ou classificado como ferramenta open source;
a consulta de versão não esclarece direitos de redistribuição.

O segundo teste real na RDP confirmou `COM_GROUP_CREATED` e inclusão dos dois
itens. Falhou na propriedade `CanonicalDataType`, HRESULT `0x80131604`,
antes de chamar `Read`. A ausência dessa propriedade deixou de impedir a leitura:
o agente valida o VARIANT retornado como descrito acima. O teste independente no
mesmo PowerShell 2.0 passou para Double, Boolean, rejeição de inteiro/string/null.
A terceira execução RDP atingiu `item_Read`, mas falhou antes da invocação COM.
O teste do helper exato reproduziu `TypeNotFound`: o alias `[short]` não existe
nesse PowerShell 2.0. A substituição por `[Int16]` passou no teste que exercita
a preparação completa dos argumentos e a sobrecarga de reflexão no CLR instalado.
A quarta execução RDP passou a preparação, mas `Type.InvokeMember(Read)`
retornou `DotNetMethodTargetInvocation`/`0x80131604` em `item_Read_invoke`.
Foi substituída pela chamada direta Automation
`OPCItem.Read([Int16]2, [ref]valor, [ref]qualidade, [ref]timestamp)`.
O teste do helper exato com um ScriptMethod simulado passou no PowerShell 2.0:
valida fonte Device, tipo Int16, uma única chamada e os três parâmetros de saída.
Esse teste é um **mock**, não valida o marshaling COM real.
A quinta execução RDP retornou `MethodNotFound` em `item_Read_dispatch`,
ainda sem amostras válidas. Foi acrescentado diagnóstico restrito ao tipo CLR,
indicador de RCW e nomes de membros do objeto retornado por AddItem.
A hipótese de incompatibilidade de reflexão não foi confirmada como causa única;
nenhuma correção no servidor COM foi feita com base nela.
A sexta execução mostrou um RCW `System.__ComObject`, mas somente membros CLR
básicos visíveis ao PowerShell, sem `Read`. Foi preparada uma ponte genérica
`automation_dispatch.ps1` que chama os métodos padrão de IDispatch diretamente,
sem precisar dos bindings OPC nem de geração/instalação de assemblies em disco.
Usa Reflection.Emit em memória e `oleaut32!VariantClear` preexistente no Windows;
esta implementação é própria, MIT, limitada explicitamente a x86 nesta etapa.
Argumentos VARIANT seguem a ordem inversa do contrato Microsoft; saídas usam
VARIANT por referência; HRESULT/scode são preservados e os textos EXCEPINFO não
são expostos. Os recursos de memória são liberados em `finally`.

O teste real dessa ponte no RB1PI2 usou apenas um `Scripting.Dictionary` privado
em memória: Count, Exists, argumento string, retorno Double e HRESULT de nome
inexistente passaram. Não é validação OPC. `ComRead` foi ligado à ponte e seu
contrato de entrada/saída foi coberto por mock no PowerShell 2.0.

Uma reprodução independente com `FileSystemObject` e um arquivo descartável do
próprio laboratório comprovou outro problema: a reflexão original falhava com
DISP_E_TYPEMISMATCH ao receber wrappers PSObject; a mesma operação funcionou
desembrulhando `PSObject.BaseObject` do alvo e dos argumentos. Os helpers
`ComGet`/`ComCall` foram normalizados. O teste de regressão
`scripts/test_opc_da_com_reflection.ps1` reproduz o erro antigo e valida leitura
do arquivo próprio e consulta de propriedade, sem OPC. Essa evidência não é
apresentada como confirmação de que seja a única causa da falha OPC.
Respostas de leitura dessa versão identificam `agent_revision=dispatch-unwrapped-v1`.
A execução RDP `7dfc86ffb89649b8838506ef46817b3d` confirmou a versão
`dispatch-unwrapped-v1`, mas o acesso ao IDispatch do OPCItem retornou
**0x80004002 / E_NOINTERFACE** para os dois itens. Isso é evidência da fronteira
Automation testada; não significa que o IOPCSyncIO do servidor esteja ausente.
Uma extensão experimental da ponte nativa para arrays por referência não passou
no teste COM próprio em memória (processo de teste encerrou com código 6).
Não foi ativada; o teste foi preservado em diretório privado como não validado.

A versão **group-sync-v1** elimina esse caminho: usa o marshaler CLR existente
com ParameterModifier em AddItems/SyncRead, arrays 1-based e erros por item.
Testes no PowerShell 2.0 passaram para saída CLR por referência, handles, Double,
Boolean false, qualidade, timestamp, erro no AddItems sem tentativa de Read,
erro no SyncRead e recusa de array inválido. São mocks/CLR, não leitura OPC real.
A regressão de wrappers COM com arquivo próprio também passou. Os processos
PI Client 5200 e servidor 5780 permaneceram na sessão 2.
A execução RDP `6f118d618473408fac78edd25ab293f3` da group-sync-v1 chegou a
AddItems, mas retornou `0x80020005` (DISP_E_TYPEMISMATCH). A versão
**group-sync-v2** inicializa ServerHandles/Errors como arrays Int32 e Values
como array Variant antes de passá-los por referência. Os testes do contrato
passaram novamente no PowerShell 2.0, incluindo os tipos dessas saídas.
Se houver novo erro de tipo, o agente pode consultar `ITypeInfo` da Automation
instalada e retornar nomes, flags e tipos dos parâmetros, sem gerar bindings ou
copiar IDLs. Essa inspeção opcional foi validada com um Scripting.Dictionary
local; não é confirmação dos metadados OPC até a execução RDP.

Os próprios arquivos experimentais da ponte nativa de arrays foram preservados
com sufixo `.unvalidated-<uuid>.disabled` na pasta build1. A ponte ativa manteve
apenas a implementação previamente testada e ganhou a inspeção ITypeInfo;
o caminho normal de leitura por grupo usa exclusivamente o marshaler CLR.
Não houve instalação, registro COM ou reinício do servidor. A tentativa de
quarentena por Rename-Item encontrou uma diferença do PowerShell 2.0
(`-LiteralPath` ausente nesse cmdlet); foi concluída com File.Move, conferindo
os hashes nas duas pastas após a operação parcial. Os backups de cada etapa
permanecem preservados.
A execução **RDP real** `332cea928ab940a5a036996d09154b41` da group-sync-v2
leu as duas tags através da Automation instalada, não de um mock:

| Item de origem | Valor | Tipo observado | Qualidade | HRESULT |
|---|---:|---|---|---|
| RB1PI1.TESTE.TESTE1.TESTE2 | 7 | Double / VT_R8 (5) | 192 / Good | 0 / S_OK |
| RB1PI1.TESTE.TESTE1.DIGITAL | false | Boolean / VT_BOOL (11) | 192 / Good | 0 / S_OK |

O log do PID **5780** confirmou IOPCSyncIO::Read, Device, um item por chamada,
HRESULT S_OK para ambos e os client handles Automation 112079016/112079224.
A sessão PI estava no PID **5112**, sessão 2; não foi reiniciada pelo agente.
Essas leituras pertencem ao helper Automation, **não** a um clique na GUI PI.
O grupo temporário foi removido na desconexão do próprio agente; o processo
5780 permaneceu ativo.

A comparação detectou um erro de fuso de **três horas** na interpretação antiga:
FILETIME nativo `134384616655268912` (18:01 UTC) tornou-se
`134384724655270000` (21:01 UTC). A Automation entregou DATE UTC com Kind
Unspecified. A diferença adicional de 1088 ticks é arredondamento de 108,8 us
para milissegundos no DATE/DateTime disponível, não um novo relógio de origem.
A versão **group-sync-v3** adiciona a política explícita acima. Somente as duas
pastas de origem simulada RB1PI2 foram configuradas como `utc`, com backups de
source-identity.json preservados. Não foi alterado fuso/relógio do Windows.
Testes no PowerShell 2.0 passaram para utc, local, rejeição sem política,
UTC com Kind explícito, data ausente/inválida e ausência de fallback para agora.
Os **17 testes Python de fontes passaram** após reconhecer o novo código de
configuração inválida. A execução **group-sync-v3** `4bffc857c1d94f85a88dd557c1422c16` passou.
Ambos os itens tiveram FILETIME do agente `134384620599420000`, correspondente a
**2026-11-06T18:07:39.942Z**. Os READ_RESULT correspondentes do PID 5780 tinham
FILETIME `134384620599423840`: diferença de **-3840 ticks = -0,384 ms** para ambos,
compatível com o arredondamento observado. Valores/tipos/qualidade/HRESULT
continuaram 7/VT_R8 e false/VT_BOOL, 192/Good, S_OK. A comparação automatizada por
client handle passou (`TIMESTAMP_COMPARISON_PASS`). Não é validação da GUI PI;
foi leitura real do helper em outro processo na sessão RDP.
O configurador web Linux foi reiniciado sozinho com autorização existente e
preservou a revisão 19. Nenhum serviço Windows foi alterado.
 `ok:true` de transporte **não** confirma amostras válidas.

Hash sincronizado/conferido nas pastas build1 e build2:
`b734e34f5f046f74c5f422bd0c3d965a6b8223830c82c07baee2694db27663ec`.
SHA-256 do diagnóstico opcional `automation_dispatch.ps1`:
`0e83ea3d45b878a38f479057962def3de6d3018c5da9e647320fab95161a620f`.
Backups da versão imediatamente anterior:

- build1: `opc_da_source_agent.before-timestamp-policy-b461c126cb9e4f818ee1bbe5e0e605ec.ps1`;
- build2: `opc_da_source_agent.before-timestamp-policy-644b592d2b9b47cda1a289c5cba0e2ff.ps1`.

Backups de identidade antes da política UTC: build1
`source-identity.before-utc-94146ae4d58f4236942cf6e7a8d4df77.json`; build2
`source-identity.before-utc-3e79591fc96c43f38659066e418c6681.json`.
Restaurar somente essa identidade mantendo group-sync-v3 restaura o padrão
`reject_unspecified`, com timestamp ausente/Uncertain para datas sem Kind.
Não restaurar indiscriminadamente o agente antigo e tratar o timestamp como
validado: ele contém o erro de interpretação local já documentado. Desabilitar
primeiro a origem no provisionamento se for necessário voltar a esse código.
O servidor COM registrado não depende desses scripts e não precisa de reversão
no Registro para desativar o ensaio de origem.

Para reverter arquivos, copie o backup escolhido para outro arquivo na mesma pasta e
use `[IO.File]::Replace(copia, agente, novoBackup)` com **três caminhos reais**;
não passe backup nulo no .NET do Windows 7. Não é necessário reiniciar o servidor
COM: o agente é executado por consulta. Não houve troca de executável/Registro
nem encerramento dos PIDs 5200/5780. O configurador Linux foi reiniciado sozinho,
preservando a revisão 19. Os **63 testes Python relevantes passaram**; tentativas
anteriores foram impedidas por quota de `/tmp` e sockets bloqueados no sandbox,
resolvidas com temporários isolados no checkout e execução autorizada em loopback.

Referências primárias: [retorno e enumeração PowerShell](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_return),
[mapeamento VARIANT/CLR](https://learn.microsoft.com/en-us/dotnet/framework/interop/default-marshalling-for-objects).
[contrato IDispatch::Invoke](https://learn.microsoft.com/en-us/windows/win32/api/oaidl/nf-oaidl-idispatch-invoke).
Nenhum código dessas páginas foi incorporado.

### Arquivos desta etapa

- `management/sources.py`: provisionamento/identidade, descoberta e relay com retry.
- `management/store.py`, `server.py`, `deployment.py`, `__main__.py`: configuração
  de origem, rotas, ciclo do relay, exportação por destino e confirmação separada.
- `management/static/app.js`: formulários de origem/variável, busca e estado real;
  proteção contra foco inicial tardio que desviava digitação entre campos.
- `windows/agent/opc_da_source_agent.ps1`: Registro e Automation locais, somente leitura.
- `windows/agent/automation_dispatch.ps1`: ponte genérica IDispatch em memória,
  independente de IDLs OPC; testes em `test_automation_dispatch.ps1` e
  `test_opc_da_com_reflection.ps1`, além do contrato em `test_opc_da_com_helpers.ps1`.
- `windows/agent/lab_agent.ps1`: encaminhamento autenticado de amostras ao runtime;
  recusa estrutural com servidor ativo, preparação fria validada sem instância ativa.
- `windows/rust_da_project/src/lib.rs`: vínculos e cache de fontes, validação de
  lotes, sessão/seq, qualidade/erro/timestamp, expiração e recuperação.
- `windows/rust_da_com_lab/src/configured.rs`, `main.rs`: endpoints locais de
  amostras e cliente/classe transitória de teste sem registro persistente.
- `scripts/build_rust_da_com_lab.sh`: diretório de build configurável e isolado.
- `tests/test_management_sources.py`, `scripts/test_opc_da_discovery_browser.cjs`,
  `test_opc_da_source_rdp.ps1`, `test_opc_da_com_helpers.ps1`,
  `test_rust_da_source_runtime.ps1`/`.py`: testes
  com agentes falsos, navegador, RDP e COM real com entrada sintética.
- `scripts/test_management_browser.cjs`: teste simulado adaptado ao novo formulário.
- `.gitignore`, `README.md`, `WEB_CONFIGURATOR.md` e este documento: proteção de
  artefatos privados e instruções. Os demais arquivos/alterações locais foram preservados.

## Transporte de snapshot do Rust DA de laboratório — 2026-11-06

### Causa confirmada da falha COM por SSH

A falha antiga foi causada pela diferença de sessão Windows, embora a conta fosse
a mesma. O servidor Rust DA estava no PID **5780**, sessão RDP **2**, com
`127.0.0.1:49321`; o PI OPC Client era PID **5112**, também na sessão 2. SSH
iniciava PowerShell na sessão **0**. A ativação LocalServer COM nessa sessão fez
RPCSS iniciar outra instância. O processo temporário PID 4540 não conseguiu
vincular a porta 49321 (Winsock 10048, já em uso) e o Connect expirou. Não houve
falha comprovada da leitura OPC DA da instância na sessão RDP. Servidor e cliente
RDP permaneceram ativos.

### Transporte e limites

Para o **servidor Rust de laboratório**, foi adicionado um caminho separado de
leitura: o agente Windows executa `GET /v1/snapshot` em
`127.0.0.1:49321` através do PowerShell remoto SSH. Ele lê o cache tipado do
processo existente, sem ativar COM, abrir portas de rede ou alterar o cliente. O
uso exige transporte explicitamente habilitado e valida PID do snapshot contra
WMI, caminho e SHA-256 do executável, ownership do listener loopback, hostname,
`machine_id`, projeto e allowlist exata dos dois Item IDs.

Falha de transporte, identidade incompatível, fonte indisponível ou amostra
inválida não produz qualidade Good; a recuperação exige uma nova amostra válida.
O servidor mantém o cache local se o Linux ficar indisponível. O endpoint HTTP
não tem autenticação própria; por isso fica exclusivamente em loopback e esse
caminho é apenas para laboratório local. Não usar com ABB/produção nem expor em
interface de rede.

Este transporte **não chama IOPCSyncIO::Read**. `hresult=0` representa sucesso do
GET (`hresult_scope=snapshot_http`), não HRESULT OPC. Tipo/VARTYPE são os dados
declarados no schema do snapshot, não uma leitura VARIANT Automation nova. As
amostras continuam simuladas: TESTE2=7.0/Double, DIGITAL=false/Boolean, DA
quality 192/Good, timestamp FILETIME vindo do servidor. Timestamp ausente ou
inválido permanece ausente, sem fallback para a hora atual. O teste Automation
real anterior na sessão RDP é evidência separada: SyncRead retornou VT_R8/7 e
VT_BOOL/false, ambos 192/Good e S_OK; não atribuir esse resultado ao snapshot.

No PowerShell 2.0/.NET do Windows 7, o desserializador JSON 3.5 apresenta números
como `System.Decimal`. O helper converte apenas tipos numéricos compatíveis para
Double com cultura invariável e continua rejeitando Boolean/string no item
numérico. A validação PowerShell 2.0 passou 16 checks.

### Provisionamento e reversão

O agente foi colocado em uma pasta nova, sem substituir builds anteriores:
`D:\OPCB\rust-da-source-agent-20261106-discovery-build4`. Nenhum Registro COM,
serviço Windows, firewall, DCOM ou processo do servidor foi alterado. O arquivo
privado `.opc-bridge-management/source-agents.json` (modo 0600) seleciona esse
agente e o transporte para RB1PI2. Backup integral anterior:
`.opc-bridge-management/backups/source-agents.before-snapshot-7236528723b142caa438808e2f5598c4.json`,
SHA-256 `7ebd55bf9cf8359b2a34cfd514fc650fc2ec96122486f2f9745ce2287b661c7f`.
O arquivo ativo tem SHA-256
`1631c996ab764e8785ed02666ccf2ce5011ec7167a0e4ae88ba953948d25946d`. Para
reverter apenas o provisionamento, restaurar o backup mantendo modo restrito:

```bash
install -m 600 \
  .opc-bridge-management/backups/source-agents.before-snapshot-7236528723b142caa438808e2f5598c4.json \
  .opc-bridge-management/source-agents.json
```

Teste do modelo no RB1PI2:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\OPCB\rust-da-source-agent-20261106-snapshot-validation-build3\test_opc_da_lab_snapshot.ps1
```

O teste de relay em fixture temporária pode ser reproduzido no Linux:

```bash
TMPDIR="$PWD/.offline/discovery-tests/pytest-tmp" PYTHONPATH=src \
  python3 scripts/test_opc_da_source_relay_live.py --host 10.247.140.166 \
  --duration-s 30 --interval-s 5
```

O navegador não foi necessário para a leitura. O status da aplicação é
recarregado do arquivo de provisionamento pelo backend; nenhuma configuração
persistente de módulos foi alterada nesta etapa.

O serviço `opc-bridge-management.service` foi reiniciado uma vez para carregar
o backend atualizado, conforme autorização anterior. Antes e depois, a revisão
persistida foi 19 e havia uma máquina cadastrada; após a inicialização, `/api/v1/health`
retornou `status=ok`, `mode=simulated` e `actual_agents_connected=0`. O reinício
foi somente do configurador Linux, não do servidor Rust nem do PI OPC Client.

### Resultados, testes e pendências

- O relay de teste obteve **3 ciclos em 30 s**, intervalo de 5 s, via SSH e
  snapshot do PID 5780/sessão 2. TESTE2=7.0/Double e DIGITAL=false/Boolean,
  quality 192, timestamps FILETIME válidos. Duas consultas adicionais usaram o
  provisionamento ativo. A fonte era simulada.
- Um teste de outage usou fixture com porta 49322 fechada e obteve
  `lab_snapshot_unavailable`; a consulta provisionada seguinte recuperou os
  dados. O servidor nunca foi interrompido.
- **20 testes Python de fontes passaram**; **16 checks PowerShell 2.0** passaram
  no Windows 7 x86. O teste de navegador não foi executado com sucesso porque o
  pacote Node `playwright` não está disponível (`MODULE_NOT_FOUND`); não foi
  instalado.
- A suíte Python completa passou após executar fora do sandbox de sockets:
  **131 aprovados, 1 ignorado**. Dentro do sandbox, os testes que criavam
  listeners locais foram bloqueados por `Operation not permitted`; não era uma
  falha funcional.
- PI OPC Client permaneceu aberto no PID 5112; não houve nova interação GUI. O
  fluxo de snapshot não foi validado visualmente no PI Client.
- O projeto persistente atual ainda possui apenas módulo simulado e nenhum
  `source_kind=opc_da` ativo. Os três ciclos foram executados com Store temporário
  e sink de teste; isso valida o relay isolado, não publicação persistente no
  dashboard. Para operação contínua visível, falta cadastrar/aplicar um módulo
  de origem separado e conferir os estados e valores pela interface.
- A UI exibe o método de snapshot como dado simulado e rotula sucesso HTTP
  separadamente de HRESULT OPC; essa apresentação não foi exercitada por
  navegador. OPC DA real genérico via SSH ainda tem a limitação de contexto COM
  entre sessão 0 e sessão interativa. A máquina 1, ABB, RSLinx e PI Interface
  não foram acessados.

Arquivos desta etapa: `windows/agent/lab_snapshot_transport.ps1` (validação e
leitura isoladas), `windows/agent/opc_da_source_agent.ps1` (roteamento explícito
do ProgID local), `src/opc_da_bridge/management/sources.py` (transporte/status),
`src/opc_da_bridge/management/static/app.js` (rótulos transparentes),
`tests/test_management_sources.py`, `scripts/test_opc_da_lab_snapshot.ps1` e
`scripts/test_opc_da_source_relay_live.py`.

Após os ensaios, foi removido do helper local um campo de diagnóstico redundante
que rotulava PID como sessão. O PID e a sessão verdadeiros estão na resposta
principal. Essa limpeza não altera o comportamento do transporte. A tentativa
inicial de sincronização usou uma identidade SSH diferente da provisionada; a
correção e a transferência concluída estão detalhadas na seção seguinte.

## Persistência e autenticação SSH — verificação adicional de 2026-11-06

### Falha de autenticação

A recusa `Permission denied (publickey,keyboard-interactive)` foi reproduzida
usando manualmente `/home/felipe/.ssh/id_ed25519`. Essa não é a identidade do
agente. O provisionamento privado da origem `10.247.140.166` seleciona o usuário
`Administrator`, a chave `/home/felipe/.ssh/opcbridge_lab_b_codex` e o arquivo
`/home/felipe/.ssh/known_hosts`. Ambos têm modo 0600; a chave configurada tem
fingerprint público `SHA256:lf8AbwFJgniK8C1Te+EmTk84wLu+yQSQF6tNmX131no`.
O SSH com essa identidade exata e host key já confiada retornou hostname
`RB1PI2`. Portanto, a falha anterior foi causada pelo uso manual da chave errada,
não por uma alteração comprovada no provisionamento. Nenhum segredo foi alterado
ou mostrado.

O helper atualizado foi enviado primeiro como arquivo temporário e substituído
atomicamente em `D:\OPCB\rust-da-source-agent-20261106-discovery-build4`, depois
de comparar hashes. Hash novo:
`db42e4a9b91ae7043b1de00f6f7da974f8ca1e020726a969e1d4446784fc58ed`. Backups
Windows preservados na mesma pasta:
`lab_snapshot_transport.ps1.before-pid-session-field-20261106.ps1` e
`lab_snapshot_transport.ps1.replace-backup-20261106.ps1`; ambos mantêm o hash
anterior `6f7fd3b9739ae2e087899d52a8e71ac688f8314e75f43cd0ae777fcfc7e3ea12`.
Reversão manual pelo PowerShell 2.0, somente se necessária: copie o backup para
outro arquivo temporário na mesma pasta e use
`[IO.File]::Replace(copia, caminho-ativo, backup-da-versao-removida)` com três
caminhos reais. Não houve reinício do agente, do servidor ou do cliente.

### Leitura após a sincronização e estado persistente

Após atualizar o helper, o teste do relay ficou conectado por **15 s**, intervalo
5 s, e completou **2 ciclos** em fixture temporária com sink de teste. O estado
reportado foi `connected`; a leitura confirmou apenas o snapshot simulado do
processo fonte. No ciclo final:

| Item de origem | Valor | Tipo declarado no snapshot | Qualidade DA | Timestamp de origem | HRESULT |
|---|---:|---|---:|---|---|
| `RB1PI1.TESTE.TESTE1.TESTE2` | 7.0 | Double / VARTYPE 5 | 192 / Good | FILETIME `134384669095541018` | 0, apenas HTTP (`snapshot_http`) |
| `RB1PI1.TESTE.TESTE1.DIGITAL` | false | Boolean / VARTYPE 11 | 192 / Good | FILETIME `134384669095541018` | 0, apenas HTTP (`snapshot_http`) |

Antes e depois, o processo era PID **5780**, caminho
`D:\OPCB\rust-da-agent-lab-20261106-hotupdate-build2\rust-da-com-lab.exe`,
argumento `-Embedding`, sessão **2**; listener `127.0.0.1:49321` permanecia
associado ao mesmo PID. PI OPC Client PID **5112**, caminho
`D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\OPCClient.exe`, sessão 2, também
continuou ativo. Nenhuma instância concorrente foi iniciada.

A configuração persistente do configurador permanece na revisão **19**, estado
de aplicação `applied`, modo `simulated`, uma máquina e módulo simulado. A API
persistente `/api/v1/samples` leu TESTE2=7.0/Double e DIGITAL=false/Boolean, com
qualidade 192/Good e timestamp `2026-11-06T19:33:15.190603Z`; a resposta
identificou `source_kind=simulated`, `origin=Agente Windows — fonte simulada`,
`source_vartype=null` e `server_pid=5780`. Isso é uma leitura do cache Windows
simulado usado pelo dashboard, **não** a integração persistente com o agente de
origem snapshot.

Não foi criada uma nova configuração `opc_da` no banco. A leitura somente via
snapshot funcionou no relay isolado, mas o snapshot de destino confirmou que os
quatro itens atualmente publicados têm `source_module_id=null`. O runtime exige
que exista o vínculo de módulo de origem, Item ID e revisão/digest ativos antes
de aceitar `source-session`/`source-samples`. Criar esse vínculo altera a
estrutura do projeto Windows; o agente de aplicação recusa alterações
estruturais com o servidor ativo. Salvar tal alteração no banco atual deixaria a
revisão persistente pendente e interromperia a experiência atual sem permitir
que a telemetria fosse aceita. A configuração atual foi preservada. Para concluir
o fluxo persistente será necessário preparar um projeto de laboratório separado
com backup/reversão e coordenar a aplicação estrutural enquanto clientes não
estiverem conectados, ou mudar deliberadamente o desenho para o dashboard guardar
amostras de origem no Linux sem encaminhá-las ao runtime Windows.

### Cliente fechado e navegador

Foi tentado fechamento normal do PID 5112 depois de confirmar caminho e sessão.
`CloseMainWindow()` retornou `false`; a consulta mostrou `MainWindowHandle=0`,
sem título de janela, e o processo permaneceu ativo/Responding. Não foi enviado
encerramento forçado. O usuário então fechou o PI OPC Client normalmente pela
sessão RDP. A consulta posterior não encontrou o PID 5112; o servidor PID 5780
continuou ativo na mesma sessão, mantendo o listener loopback.

Com o cliente fechado, o relay executou **2 novos ciclos em 15 s** (intervalo
5 s), status `connected`, com TESTE2=7.0/Double e DIGITAL=false/Boolean,
qualidade DA 192/Good e FILETIME `134384683979371161`. Novamente `hresult=0`
era apenas sucesso HTTP do snapshot. A leitura foi seguida por chamada da API
persistente, que retornou os valores Good simulados na revisão 19. O servidor
continuou no PID 5780, executável hotupdate-build2 e porta 49321; o cliente 5112
permaneceu ausente. Isso comprova disponibilidade do snapshot sem PI OPC Client,
mas não leitura COM nem preservação de handles quando o cliente está fechado.

Playwright estava disponível em `/tmp/opc-management-browser-tools/node_modules`,
mas o executável Chromium indicado pelo próprio pacote não existia em
`/home/felipe/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome`; a busca
também não encontrou Chromium/Chrome em `/usr/bin` ou `/opt`. O teste visual
terminou com `browserType.launch: Failed to launch chromium because executable
doesn't exist`. Nenhum navegador ou dependência foi instalada. A API foi testada
separadamente, mas não substitui inspeção visual. Passos manuais para conferir o
estado **atual simulado**: abra a interface no navegador executado no próprio
AUTOU3 em `http://127.0.0.1:8765`, selecione a conexão RB4 → máquina RB1PI1 →
módulo TESTE → pasta TESTE1, e leia TESTE2 e DIGITAL. A interface deve identificá-los
como dados simulados. Isso ainda não valida o rótulo de snapshot em um módulo
`opc_da` persistente, que não foi criado.

## Dashboard persistente sobre o snapshot local — 2026-11-06

### Causa do vínculo ausente e separação de catálogos

O módulo TESTE persistente continua simulado (`source_kind` ausente, tratado
como `simulated`) e seus quatro Item IDs já fazem parte do projeto aplicado na
RB1PI2. O exportador só inclui `source.module_id`, `source.item_id` e
`stale_after_s` quando o módulo é alterado para `source_kind=opc_da`. Esse
campo de origem participa do JSON/digest do projeto Windows; portanto, adicionar
o vínculo no catálogo ativo seria uma mudança estrutural que o agente não aceita
como hot update com o servidor ativo. Esse caminho exigiria parar/reconfigurar e
reconectar clientes. Não foi usado aqui.

Foi criado um perfil privado Linux, separado do banco e do projeto exportado,
em `.opc-bridge-management/lab-dashboard-snapshot.json`. Ele vincula apenas a
máquina de configuração `733cc9b5-7837-44f6-8ef8-a222b1ee6ddd` aos registros
existentes de `TESTE2` (Double) e `DIGITAL` (Boolean), usando os mesmos Item IDs
publicados. O host permitido é `10.247.140.166` / `RB1PI2`, o ProgID/CLSID é o
RustDaBridge.Lab.1 de laboratório, a porta do snapshot é 49321 e o intervalo do
relay é 5 s. A política privada do `source-agents.json` repete o ProgID, CLSID,
porta e a lista exata Item ID/tipo, e fixa o hostname, arquitetura x86 e
diretório do agente já verificado. O agente Windows continua validando
executável/hash/PID/sessão, listener loopback, projeto, identidade e allowlist
antes de responder.

Na primeira tentativa, o perfil foi recusado porque o `source-agents.json`
existente só declarava host/transporte; os dados detalhados de ProgID, CLSID,
porta e itens estavam no arquivo privado do agente Windows, não espelhados no
provisionamento Linux. Essa foi a causa da primeira resposta `invalid`. O bloco
local foi completado após backup, sem enfraquecer as verificações. Também foi
corrigido o caminho de erro para que um perfil inválido nunca caia no cache
Windows Good como se fosse a amostra snapshot; os IDs exatos da máquina
configurada agora retornam Bad/sem valor até a política ser válida. O log de
erro inicial foi preservado no histórico; emissões repetidas agora são limitadas.

O overlay é somente para exibição no dashboard. Ele não altera o banco,
`source_module_id`, os valores iniciais, a revisão, o JSON/digest ou os quatro
Item IDs exportados. Não associa nenhuma fonte real: o que aparece vem do cache
simulado do servidor Rust DA. Não é nova leitura `IOPCSyncIO::Read`, não é
integração OPC UA e não deve ser interpretado como dado do ABB. O sucesso HTTP
do snapshot não é exposto como HRESULT OPC; as amostras desse overlay não têm
campo `hresult`.

### Estado e validade das amostras

Ao abrir o configurador com o perfil ativo, o frontend carrega e consulta
automaticamente apenas os dois IDs vinculados, e mantém a consulta local à API
de amostras. O cache de relay fica em
`.opc-bridge-management/lab-dashboard-snapshot-cache.json`, com permissões 0600.
Após reiniciar o configurador, dados armazenados começam como Bad e sem
valor/timestamp atual até receber uma confirmação nova do snapshot. Falha de
comunicação ou amostra inválida limpa valor e timestamp e apresenta Bad; um
timestamp ausente/invalidável rebaixa Good para Uncertain sem inventar hora. Se
nenhuma confirmação chega por mais de `max(15 s, 3 × intervalo)`, a API também
responde indisponível/Bad, mesmo se a thread SSH ainda aguarda timeout. A
recuperação requer uma resposta nova válida.

O status do dashboard distingue “Snapshot de laboratório · Fonte simulada”,
“Conectado”, “Degradado” e “Indisponível”. Os detalhes mostram valor, tipo
declarado pelo schema do snapshot/VARTYPE, qualidade, timestamp de origem e
hora da última confirmação. Perfil inválido na primeira inicialização também
falha fechado para os dois Item IDs autorizados da máquina configurada; não cai
no cache Windows normal nem anuncia esse cache como snapshot. Eventos repetidos
de perfil inválido são limitados para evitar inundar o histórico.

### Backup e reversão

Antes de gravar o perfil, foi criado o backup privado
`.opc-bridge-management/backups/dashboard-snapshot-before-20261106T1719-3286f091/`
(diretório 0700; arquivos 0600). O manifesto registra revisão 19, os Item IDs,
os hashes de `management.sqlite3`, `deployment-state.json`, `agents.json` e do
`source-agents.json` anterior. Os dois arquivos de perfil não existiam antes.
O hash anterior do provisionamento era
`1631c996ab764e8785ed02666ccf2ce5011ec7167a0e4ae88ba953948d25946d`; o atual
é `4bf1d3b9caae30d4dbd92628b9598c80d1fe22b785c27303d4c3e31222336339`.

Para reverter o overlay, remova somente o perfil e seu cache, restaure o
provisionamento anterior e reinicie o configurador Linux para limpar o perfil
em memória:

```bash
rm .opc-bridge-management/lab-dashboard-snapshot.json \
   .opc-bridge-management/lab-dashboard-snapshot-cache.json
install -m 600 \
  .opc-bridge-management/backups/dashboard-snapshot-before-20261106T1719-3286f091/source-agents.json \
  .opc-bridge-management/source-agents.json
systemctl --user restart opc-bridge-management.service
```

Não é necessário restaurar a base SQLite ou `deployment-state.json`: a revisão
19 e o projeto Windows não foram modificados. Esses backups permanecem guardados
caso seja necessário restaurar uma cópia consistente.

### Validação desta etapa

- A configuração persistente permaneceu na revisão **19**, `mode=simulated`,
  aplicação `applied`; os quatro Item IDs foram conferidos antes e depois.
  `source_kind` do módulo permaneceu sem vínculo OPC DA.
- A identidade confirmada do destino foi `RB1PI2`, Windows 7 Professional
  6.1.7601 x86, servidor PID **5780**, executável
  `D:\OPCB\rust-da-agent-lab-20261106-hotupdate-build2\rust-da-com-lab.exe`,
  sessão **2**, listener `127.0.0.1:49321`. O PI OPC Client não estava ativo na
  consulta. Os ciclos do snapshot validaram a identidade do processo; não houve
  reinício do servidor nem desconexão de cliente nesta etapa.
- Foram concluídos **3 ciclos persistentes distintos** pelo serviço e API reais
  (sem Store temporário ou sink de teste):

| Item ID | Valor | Tipo/VARTYPE do schema | Qualidade | Timestamp de origem UTC | PID/sessão |
|---|---:|---|---|---|---|
| `RB1PI1.TESTE.TESTE1.TESTE2` | 7 | Double / 5 (`VT_R8`) | 192 / Good | `2026-11-06T20:24:33.076009Z` a `2026-11-06T20:24:51.640042Z` | 5780 / 2 |
| `RB1PI1.TESTE.TESTE1.DIGITAL` | false | Boolean / 11 (`VT_BOOL`) | 192 / Good | os mesmos timestamps de cada ciclo | 5780 / 2 |

  Cada resposta tinha `simulated=true`, `applied_to_windows=false`,
  `origin=Snapshot de laboratório · Fonte simulada`, e nenhum campo `hresult`.
  O FILETIME foi preservado e convertido para UTC ISO-8601; não foi usado o
  relógio do configurador como substituto.
- Os testes automatizados cobrem vínculo persistente sem mutar revisão/projeto,
  isolamento entre duas máquinas simuladas, itens/tipos não autorizados,
  indisponibilidade, expiração, reinício (cache volta como Bad), recuperação e
  API HTTP. A suíte completa: **136 aprovados, 1 ignorado**; `node --check` e
  `compileall` também passaram.
- Firefox 158 headless capturou a tela do configurador. O DOM/UI mostrou o badge
  “Snapshot de laboratório · Fonte simulada”, status conectado e os detalhes de
  TESTE2: valor 7, Double, Good, timestamp do snapshot e comunicação conectada;
  a verificação do DOM também conferiu DIGITAL=false e Good. O geckodriver não
  criou sessão com o Firefox snap (`binary is not a Firefox executable` /
  `Failed to set preferences`); a inspeção foi feita com captura headless do
  próprio Firefox e proxy temporário somente em loopback que aguardou a API/DOM.
- Perda e recuperação foram testadas por fixture/canal falso; expiração usou
  timestamp antigo. Não interrompemos rede, servidor Rust, porta 49321 ou PI
  OPC Client para provocar falha. Isso valida as regras e o estado reportado,
  não uma desconexão física ao vivo.
- O configurador Linux foi reiniciado quatro vezes para carregar backend e
  frontend, conforme autorização anterior. Nenhum processo/serviço Windows foi
  alterado. Não houve acesso à máquina 1, ABB, RSLinx ou PI Interface.

## Fixture OPC DA de origem isolada — 2026-11-06

### O que foi testado

Foi compilada uma fixture separada de servidor OPC DA em Rust para Windows 7
x86. Ela usa o executável existente do laboratório em uma pasta própria, com
CLSID exclusivo `{C7482207-6725-496D-B846-A0B31B941937}` e modo
`--source-lab-server`. Os dois Item IDs da origem são
`RustLab.Numeric.Double` (VT_R8) e `RustLab.Boolean` (VT_BOOL). O valor numérico
e o Boolean são controláveis pelo arquivo local
`rust-da-source-values.json`; `rust-da-source-offline.flag` simula perda da
fonte e a remoção exige uma amostra nova para recuperar Good. O servidor não
tem caminho de escrita OPC. Os dados são sintéticos.

O artefato Windows foi colocado, sem substituir builds existentes, em
`D:\OPCB\rust-da-source-lab-20261106-build5\rust-da-com-lab.exe`.
SHA-256 `f2bcb996b1c0c768cbb4f36f7bb0a5cbca4a96d52b9bdc7e828e90338a1e7398`;
PE32 Intel i386. O build usou o toolchain isolado `nightly-2026-11-03`
(`rustc 1.101.0-nightly`, Cargo `1.101.0-nightly`), alvo
`i686-win7-windows-gnu`, checkout de bindings fixado no commit
`da44a99265f9706618bbe506c98f714f2aa46f0b` e adaptações locais de imports
Win7 já descritas acima. A tabela de imports contém `ole32.dll`, `oleaut32.dll`
e `rpcrt4.dll`, sem `combase.dll` ou API WinRT.

O script reproduzível é `scripts/test_rust_da_source_fixture.ps1`. Executado no
RB1PI2, ele confirma o hash e a identidade do executável, inicia o servidor,
ativa o CLSID somente pelo class object registrado na memória do processo,
executa browse e SyncRead em processos COM separados, muda os dois valores,
simula indisponibilidade, verifica a qualidade, confirma recuperação e solicita
parada normal. Ele não grava o Registro, não toca no projeto do destino, não
usa o snapshot HTTP e não faz escrita OPC.

Comando usado no Windows 7 x86:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\OPCB\rust-da-source-lab-20261106-build5\test_rust_da_source_fixture.ps1 -LaboratoryDirectory D:\OPCB\rust-da-source-lab-20261106-build5 -ExpectedSha256 f2bcb996b1c0c768cbb4f36f7bb0a5cbca4a96d52b9bdc7e828e90338a1e7398
```

Resultado observado no build5, com fixture PID 5652 e clientes COM auxiliares
em processos separados:

| Fase | Item de origem | Valor e VARIANT | Qualidade DA | Timestamp | HRESULT da leitura |
|---|---|---|---|---|---|
| inicial | `RustLab.Numeric.Double` | 16.5, VT_R8 | 192 / Good | FILETIME `134384741912568915` | `S_OK` |
| inicial | `RustLab.Boolean` | true, VT_BOOL | 192 / Good | FILETIME `134384741912568915` | `S_OK` |
| alterada | `RustLab.Numeric.Double` | 41.25, VT_R8 | 192 / Good | FILETIME `134384741925828938` | `S_OK` |
| alterada | `RustLab.Boolean` | false, VT_BOOL | 192 / Good | FILETIME `134384741925828938` | `S_OK` |
| indisponível | ambos | valores anteriores preservados no cache | 8 / BadNotConnected | FILETIME anterior preservado | `S_OK` por item; o helper retorna falha de validação `0x8000FFFF` por qualidade Bad |
| recuperada | 41.25 / false | VT_R8 / VT_BOOL | 192 / Good | FILETIME novo `134384741957652994` | `S_OK` |

O log do servidor confirma `Browse`, `AddItems`, `IOPCSyncIO::Read`, handles 1
e 2 e `S_OK` para cada item. A amostra mantida durante a indisponibilidade foi
explicitamente `BadNotConnected`; não foi apresentada como atual/Good. Este é
um teste COM real entre processos no Windows, mas a origem é a fixture sintética.
Não é leitura Automation, não percorre SSH/relay, não atualiza o projeto Windows
de destino e não é evidência de PI OPC Client. Também não testa notificações.

### Limite de ativação e próximo passo autorizado

A fixture foi iniciada manualmente no modo de servidor e chamou
`CoRegisterClassObject` apenas enquanto o processo esteve ativo. Assim, o teste
COM entre processos funciona sem registro persistente. O agente existente, por
outro lado, usa `OPC.Automation.1` e valida a relação `ProgID → CLSID` no
Registro antes de fazer `Connect` e `OPCGroup.SyncRead`. O perfil privado atual
`source-agents.json` provisiona para `10.247.140.166` somente o transporte de
snapshot `RustDaBridge.Lab.1`; o `source-identity.json` em
`D:\OPCB\rust-da-source-agent-20261106-discovery-build4` permite somente
`RustDaBridge.Lab.1`. Portanto, a leitura COM da fixture ainda não foi executada
pelo agente PowerShell/Automation nem enviada pelo transporte Linux → agente →
destino.

O nome proposto para provisionar a origem é `RustDaBridge.SourceLab.1`; os
valores de ProgID e CLSID estavam ausentes em HKCU e HKLM no fim desta etapa.
O modo COM espera, para ativação automática, uma entrada de usuário em
`HKCU\Software\Classes`: ProgID → CLSID, CLSID → ProgID, `LocalServer32` e a
categoria OPC DA 2.0 `{63D5F432-CFE4-11D1-B2C8-0060083BA1FB}`. O comando do
LocalServer32 apontaria exclusivamente para o build5 com
`--source-lab-server -Embedding`. Isso registra uma nova classe de laboratório;
não modifica o CLSID/ProgID do destino. Reversão: remover apenas as duas árvores
novas `HKCU\Software\Classes\RustDaBridge.SourceLab.1` e
`HKCU\Software\Classes\CLSID\{C7482207-6725-496D-B846-A0B31B941937}`.
Nenhuma dessas operações foi realizada; requerem autorização explícita antes
de executar.

Depois do registro de usuário autorizado, ainda será necessário adicionar o
ProgID à allowlist `source-identity.json`, criar um segundo perfil
`opc_da_automation` para o mesmo host sem substituir o perfil de snapshot,
descobrir explicitamente o candidato e salvar um módulo de origem com dois
bindings. O snapshot usa o servidor de destino e permanece separado. Para não
duplicar Item IDs publicados, o ensaio no projeto pode manter os quatro IDs
existentes e adicionar dois IDs de destino próprios para os bindings da fixture;
source Item IDs `RustLab.*` e published Item IDs continuam campos distintos.

A inclusão de um módulo/vínculo OPC DA muda o projeto/digest do destino e é
estrutural: o agente não aplica essa mudança enquanto o servidor está ativo.
Aplicá-la exige backup do SQLite/recibo/projeto e do executável, parar
normalmente o servidor do laboratório, aplicar e iniciar o novo projeto; clientes
DA conectados precisarão reconectar. O teste contínuo de alteração só poderá
então verificar PID/handles/PI Client no novo runtime. Esta execução não escreveu
no SQLite, `deployment-state.json`, overlay ou allowlist, não parou o destino e
não reconectou PI OPC Client. Após a verificação inicial na revisão 19, o
configurador registrou cinco eventos novos de “Elemento criado; configuração
salva somente no gerenciamento. Aplicação Windows pendente.” entre
21:28:39Z e 21:30:28Z, sem ação nossa de gravação. A revisão observada ao final
foi 24. O estado agora contém ainda um cadastro `LFS_TD1_PB2` para
10.247.140.166, com módulo simulado `RB1PI2` e variável
`LFS_TD1_PB2.RB1PI2.Current.Velocidade`; o cadastro anterior `RB1PI1` e seus
quatro Item IDs continuam presentes. A aplicação geral está `pending` e
`applied_to_windows=false`. Esses dados foram preservados como encontrados; não
assumimos qual cadastro deverá receber a futura fonte OPC DA.

### Resultado pós-teste e licenças

O destino permaneceu em `RB1PI2`, Windows 7 Professional 6.1.7601 x86, PID
5780, sessão 2, build `hotupdate-build2`, SHA-256
`152a61852bbea39a0ad4d2acea79aa894b55486deed3ed8637eee13b0041d0d0`.
Não havia PI OPC Client ativo na consulta. A fixture build5 encerrou pelo
marcador de parada; os marcadores e o arquivo de override foram removidos pelo
teste. Não restou processo build5. As chaves do ProgID/CLSID candidato
continuaram ausentes em HKCU/HKLM. No fim da consulta, os hashes eram: SQLite
`4d7d864967669fcfbd7d1bdb920277ca069f6c7ba8a298cdfbe9911a734a116b`,
`deployment-state.json`
`5cb226d7ccc81144c001825d83f3ca52a5b874b56e5eac03ab73b05757cfbff6`,
`source-agents.json`
`4bf1d3b9caae30d4dbd92628b9598c80d1fe22b785c27303d4c3e31222336339` e overlay
snapshot `3764348d8859844d0b10dc7ac8f45c7f04edcac50bf90b9148c9b19888e36426`.
O servidor de gerenciamento continuou ativo no PID 3057738; nenhuma edição ou
restauração foi feita nos arquivos persistentes.

A suíte Python completa passou: **136 aprovados, 1 ignorado**. A primeira
execução teve um erro de ambiente no teste nativo C++ (`g++` não conseguiu
escrever temporário por quota de `/tmp`); repetida com `TMPDIR=.offline`, passou.
O modelo Rust passou 11 testes e 0 doctests. A compilação cruzada x86 terminou
com sucesso; Cargo emitiu apenas aviso ao gravar cache global de uso (`/tmp`
cheio) e avisos de métodos Rust depreciados. Não houve instalação de ferramenta
nem alteração global. O script fixture foi executado com sucesso no Windows 7
x86. Não houve teste visual novo do dashboard: a API continua exibindo o
snapshot simulado existente, pois nenhum módulo OPC DA persistente foi aplicado.

Os bindings OPC DA permanecem sob revisão interna e a licença/redistribuição de
IDLs OPC Foundation continua sem esclarecimento conclusivo, conforme registrado
no relatório de viabilidade. O teste interno desta fixture não constitui
autorização de publicar ou redistribuir aqueles arquivos. A origem sintética e o
uso local não alteram essa pendência.

Arquivos de código alterados nesta etapa: `src/opc_da_bridge/management/sources.py`
(perfil, allowlist, cache, qualidade e expiração),
`src/opc_da_bridge/management/server.py` (overlay nas rotas de amostras/status),
`src/opc_da_bridge/management/static/app.js` (consulta automática e rótulos),
`tests/test_management_sources.py` e `tests/test_management.py` (fixtures e API).
Este documento registra o perfil e a reversão. Os demais arquivos locais foram
preservados.

## Continuação da validação de relay — 06/11/2026

Esta seção registra a execução posterior e prevalece sobre os estados de relay,
revisão e PID descritos nas anotações históricas acima. O perfil do dashboard
HTTP continua independente do módulo OPC DA: seu status de snapshot foi
`unavailable (lab_snapshot_identity_mismatch)`, mas as amostras abaixo vieram do
relay COM do módulo persistente e não do endpoint de snapshot.

### Espaço temporário e artefatos preservados

- O erro `Quota exceeded` ocorreu ao criar o destino sintético `/tmp/.git` do
  sandbox. O `/tmp` é tmpfs e, na verificação inicial, estava quase cheio; não
  havia um diretório real `/tmp/.git`. Não foi possível consultar quota por
  usuário porque o executável `quota` não está instalado.
- Foram identificados 32 diretórios `/tmp/opc-da-com-lab-build.*` produzidos
  pelo script de build do laboratório. Não havia processo de compilação nem
  arquivos abertos neles. Seus arquivos foram preservados em
  `.offline/preserved-opc-da-temp-builds-20261106/` antes de remover somente os
  diretórios temporários originais: 79 arquivos, incluindo executáveis e
  manifestos Cargo; o manifesto atual tem SHA-256
  `9fc03be93dc581bb18e0396db88b321271f0b278b7a33265e515a98641d87e1b`.
- A limpeza recuperou aproximadamente 21,5 GB. Na verificação final, `/tmp`
  tinha 22 GB livres (28% usado) e 942.103 inodes livres (11% usados); o volume
  do checkout tinha 475 GB livres e 6% dos inodes usados. Nenhum build,
  configuração, backup ou evidência de teste foi removido.

### Configuração e leitura confirmadas

- O backend local estava respondendo na revisão 28. O cadastro escolhido
  continua chamado `RB1PI1`, aponta para `10.247.140.166` e foi identificado
  pelo Windows como `RB1PI2`. Os Item IDs publicados mantiveram-se
  `RB1PI1.TESTE.TESTE1.TESTE2` e `RB1PI1.TESTE.TESTE1.DIGITAL`; os Item IDs da
  origem são, respectivamente, `RustLab.Numeric.Double` e `RustLab.Boolean`.
- A máquina destino/origem de laboratório respondeu como Windows 7 Professional
  `6.1.7601`, SP1, 32 bits. O servidor OPC DA de destino permaneceu no PID 2112,
  executável
  `D:\OPCB\rust-da-target-opc-source-20261106-build2\rust-da-com-lab.exe`
  com `-Embedding`. Antes e depois da alteração, o PID e o executável eram os
  mesmos. Não houve troca de Registro ou executável.
- Antes da alteração, a leitura OPC DA direta da fixture
  `RustDaBridge.SourceLab.1` retornou TESTE2 `55.5` (`Double`, VARTYPE 5 / VT_R8)
  e DIGITAL `false` (`Boolean`, VARTYPE 11 / VT_BOOL), ambos qualidade 192,
  HRESULT 0 (`S_OK`), timestamp de origem
  `2026-11-06T23:20:16.4793281Z`, FILETIME `134384808164793281`. O relatório
  identifica `IOPCSyncIO::Read (Rust direct COM client)` e a identidade da fonte
  `RB1PI2`; é uma fixture COM local, não o ABB.
- O único arquivo alterado para produzir a mudança controlada foi
  `D:\OPCB\rust-da-source-lab-20261106-build5\rust-da-source-values.json`.
  O valor antigo foi salvo atomicamente no arquivo
  `rust-da-source-values.json.before-relay-test-20261106-2325.bak`, com o mesmo
  conteúdo/hash do backup local
  `.opc-bridge-management/backups/before-auth-token-fix-20261106T200344/source-values-before.json`:
  `{"numeric":55.5,"boolean":false}`, SHA-256
  `42c88037eba9c7073ffc2ba9aefbea46a241f6edab27554e9a6338c766f9d2ea`.
  O arquivo atual contém `{"numeric":61.75,"boolean":true}`, SHA-256
  `7ce85c20afc4d0c8c60f82f955dcc1e1e52e8cb1af5f9e710c9029e0ce30d2db`.
  A fixture lê esse arquivo no ciclo do simulador; não foi necessário reiniciar
  o servidor da origem.
- Depois da troca, uma nova leitura COM direta retornou TESTE2 `61.75`, VT_R8,
  e DIGITAL `true`, VT_BOOL; ambos Good/192, HRESULT 0, timestamp
  `2026-11-06T23:23:39.5760848Z`, FILETIME `134384810195760848`.
- A API de amostras do destino confirmou múltiplas observações; foram vistos ao
  menos dois ciclos de polling com timestamps distintos. Na última consulta,
  ambos os Item IDs tinham valor `61.75`/`true`, tipo Double/Boolean,
  VARTYPE 5/11, qualidade Good/192, HRESULT 0, timestamp de origem
  `2026-11-06T23:26:48.305216Z`, FILETIME `134384812083052163`, `source_kind=opc_da`,
  `applied_to_windows=true` e `server_pid=2112`. O estado da origem era
  `connected`, com método `IOPCSyncIO::Read` e `source_environment=laboratory`.
  Isso valida leitura da origem e encaminhamento para o cache OPC DA de destino;
  não é histórico nem replay.

### Interface e limites observados

- A resposta da API usada pelo dashboard contém os valores acima, mas não foi
  feita validação visual. Firefox não iniciou no host de desenvolvimento:
  `snap-confine has elevated permissions and is not confined but should be. Refusing to continue ... Please make sure that the snapd.apparmor service is enabled and started.`
  Playwright e Puppeteer também não estavam instalados. Para conferir na GUI,
  abra o configurador, selecione o cadastro `RB1PI1`, o módulo de leitura OPC DA
  de laboratório e cada variável; a badge deve indicar “Leitura OPC DA de
  laboratório · Fonte simulada”, com valor, qualidade, timestamp e HRESULT. A
  API não deve ser descrita como inspeção visual.
- O processo `OPCClient.exe` não estava ativo na consulta final. Portanto, este
  turno não observou o PI OPC Client nem confirma visualmente browse/leitura
  após a mudança. O próximo teste manual no RB1PI2 é conectar a
  `RustDaBridge.Lab.1`, browse e ler os dois Item IDs publicados com Sync ou
  Refresh, conferindo `61.75` e `true`, qualidade Good e timestamp.
- Foram encontrados 68 processos com o caminho exato do executável da fixture
  `rust-da-source-lab-20261106-build5` e argumento `--source-lab-server
  -Embedding`; nenhum foi encerrado. A causa e o ciclo de vida dessa quantidade
  de instâncias não foram determinados nesta validação e devem ser investigados
  antes de um ensaio prolongado. Os processos não são o destino (que permaneceu
  no PID 2112). Não houve acesso à máquina 1, ABB, RSLinx ou PI Interface.

Para reverter apenas os valores da origem, confira primeiro se o arquivo atual
continua com o hash novo indicado acima. Leia o `.bak`, escreva seu conteúdo em
um temporário ao lado do arquivo atual, valide o JSON e substitua atomicamente
com `File.Replace`, usando um novo nome para guardar a versão atual. O `.bak`
original deve permanecer intacto. O processo lê o override periodicamente; a
reversão dos valores não precisa trocar executável nem registro COM.

## Continuação: fixture COM acumulada, relay e indisponibilidade (07/11/2026)

> A investigação posterior de `0x80080005` está registrada ao final deste
> documento. O PID 2112 da sessão SSH foi parado normalmente para liberar a
> ativação pelo PI OPC Client na sessão RDP; os números de PID abaixo são
> evidências históricas e não instruções para encerrar processos.

### Causa e correção do acúmulo

A causa dos processos persistentes foi confirmada no código e no inventário
WMI. Cada leitura do relay iniciava um cliente de teste COM separado
(`rust-da-com-lab.exe --source-lab-client-test`). Fechar esse cliente liberava
seus objetos COM, mas não encerrava o LocalServer iniciado pelo COM SCM. A
fixture `--source-lab-server -Embedding` espera explicitamente
`rust-da-source-stop.flag`; portanto cada ativação anterior ficava viva mesmo
depois de seu cliente sair. O caminho do executável, os argumentos, o pai
`svchost.exe` PID 640 e a sessão 0 coincidiram nos processos inventariados.

O agente agora cria o marcador com `FileMode.CreateNew` depois de cada leitura,
aguarda até 45 segundos a saída normal de todos os processos cujo caminho e
argumentos correspondem exatamente à fixture e remove somente o marcador que
ele próprio criou. A espera exige duas observações sem processo; não há
encerramento forçado. Se o marcador já existir ou a fixture não sair, a
consulta falha explicitamente. O timeout SSH desse transporte passou a 60 s
(os outros continuam em 20 s), e a mensagem agora informa o limite usado.

Antes da atualização, o agente remoto tinha SHA-256
`b6a07d191214a120e22807e6f393e1dd02fdb3032861404645764577e052855a`. A cópia
de reversão ficou em
`D:\OPCB\rust-da-source-agent-20261106-discovery-build4\opc_da_source_agent.ps1.before-process-cleanup-45s-20261107-0015.bak`
com esse hash. O script ativo remoto e local tem SHA-256
`81cc39c3624a394ed11c697b5b9c2a2c33d219b1746dffc0610a63f4e67d15c1`.
PowerShell 2.0 no RB1PI2 tokenizou o script sem erros (4.338 tokens). O
executável da origem, o executável/registro do destino, o projeto (revisão 28)
e os valores persistidos não foram trocados.

O inventário detalhado anterior registrou 54 processos e foi salvo em
`.opc-bridge-management/evidence/source-processes-before-cleanup-20261106.csv`;
consultas anteriores haviam contado 68 e, em outra captura transitória, até
155. Após solicitar saída normal dos antigos, a contagem caiu a zero. Em uma
observação de 21 s durante o relay ativo, 20 amostras encontraram zero e uma
capturou o PID 6812 por aproximadamente um segundo; a amostra seguinte voltou
a zero. Um segundo processo exato, PID 10356 (pai 640, sessão 0), foi observado
durante uma leitura e saiu naturalmente em menos de 12 s. A verificação final
encontrou zero processos e nenhum marcador stop/offline. Nenhum processo foi
encerrado à força. O destino permaneceu no PID 2112 e o PI OPC Client não estava
aberto.

O teste de três ciclos sequenciais do relay completou em todos os ciclos:
origem `RustDaBridge.SourceLab.1`, método `IOPCSyncIO::Read` do cliente Rust,
HRESULT 0, qualidade 192, VARTYPE 5/11 e valores 61.75/true. As amostras
recebidas no destino foram confirmadas por ACK, com PID 2112 e sequências 1, 2
e 3. Os timestamps de origem foram, respectivamente, 00:10:18.564201Z,
00:10:29.390620Z e 00:10:41.309040Z. A leitura autenticada do snapshot do destino
após a recuperação mostrou:

| Item ID publicado | Valor | Tipo/VARTYPE | Qualidade | Timestamp de origem | HRESULT | PID destino |
|---|---:|---|---|---|---:|---:|
| `RB1PI1.TESTE.TESTE1.TESTE2` | 61.75 | Double / VT_R8 (5) | Good / 192 | 2026-11-07 00:17:51.760597Z | 0 | 2112 |
| `RB1PI1.TESTE.TESTE1.DIGITAL` | true | Boolean / VT_BOOL (11) | Good / 192 | 2026-11-07 00:17:51.760597Z | 0 | 2112 |

Os Item IDs de origem foram `RustLab.Numeric.Double` e `RustLab.Boolean`.
Esses resultados provam leitura COM na origem e encaminhamento/ACK/cache no
destino; o snapshot é uma consulta ao cache do destino, não outra leitura COM.

### Concorrência de relay

Uma primeira repetição diagnóstica isolada teve um ciclo sem confirmação. O
teste subsequente, dividido em leitura da origem, sessão, ACK de amostras e
snapshot, passou em cada chamada. O log do destino mostra que
`start_source_session` substitui a sessão anterior e põe os itens em Bad até
chegar uma nova sequência. A coleta persistente também estava ativa fora do
processo de diagnóstico: timestamps novos continuaram chegando e o log do
destino contém outras sessões/ACKs. Portanto um poller de diagnóstico
concorrente pode invalidar a sessão do relay ativo; a falha daquele ciclo não
foi falha da leitura COM da fixture. A causa exata do ACK que faltou naquele
ciclo não foi capturada porque o tratamento atual do relay reduz algumas
exceções externas a “Coleta sem confirmação”. Os três ciclos limpos e
sequenciais posteriores passaram. Para testes futuros, observar o relay ativo
sem iniciar outro coletor para o mesmo módulo.

### Falha e recuperação

Foi criado com `CreateNew` um marcador offline temporário da fixture com token
aleatório; ele foi removido apenas depois de conferir que o conteúdo ainda era
o mesmo. O arquivo de valores persistiu com o SHA-256
`7ce85c20afc4d0c8c60f82f955dcc1e1e52e8cb1af5f9e710c9029e0ce30d2db` e contém
61.75/true. Os primeiros três snapshots ainda mostraram a última amostra Good
com o mesmo timestamp enquanto aguardavam o ciclo de polling. No quarto
snapshot, às 00:15:27.366743Z, ambos estavam Bad/8; o número observado foi
12.5 e o Boolean true, mas qualidade Bad impedia apresentá-los como Good. O
HRESULT 0 é sucesso da chamada OPC DA; não sobrepõe a qualidade por item Bad.
Assim, a falha não é instantânea: o teste viu até cerca de 20 s entre o último
timestamp Good e a degradação, limitada pelo próximo poll/falha observada.
Após remover o marcador, o terceiro snapshot de recuperação trouxe novamente
61.75/true Good/192, HRESULT 0, em 00:15:43.122771Z, posterior ao timestamp
anterior. O servidor de destino não reiniciou.

### Teste COM/PI Client e dashboard

O teste COM cliente embutido no build do destino foi tentado por SSH em modo
`--read-only-project-client`, após confirmar executável/hash, configuração 28
e PID 2112. A ativação falhou antes de browse/leitura com `0x80080005`
(`Server execution failed`) no contexto da sessão SSH. Isso não é evidência de
falha de leitura pelo PI Client na sessão RDP. O PI OPC Client estava fechado
(nenhum `OPCClient.exe`); portanto esta execução **não** validou GUI, conexão,
grupos ou handles do PI Client. É necessário executar o teste na sessão RDP:
abrir `RustDaBridge.Lab.1`, adicionar
`RB1PI1.TESTE.TESTE1.TESTE2` e `RB1PI1.TESTE.TESTE1.DIGITAL`, clicar Sync ou
Refresh e conferir 61.75/true, tipos e qualidade Good/timestamp.

A API do dashboard em `10.247.168.43:8765` respondeu HTTP 401 do Nginx sem
credenciais. Nenhuma credencial foi enviada por HTTP sem TLS; Firefox continua
indisponível no ambiente do agente. Portanto não houve validação visual da
página. O resultado do snapshot Windows não deve ser descrito como validação
visual do dashboard.

### Arquivos e verificações desta continuação

- Alterados: `windows/agent/opc_da_source_agent.ps1`,
  `src/opc_da_bridge/management/sources.py`,
  `tests/test_management_sources.py` e este documento.
- Evidências privadas em `.opc-bridge-management/evidence/`: inventário de
  processos antes/depois, monitoramento de 21 s, ciclos, outage/recovery e
  saída do teste COM tentado. Backups privados permanecem em
  `.opc-bridge-management/backups/`.
- Testes focados `tests/test_management_sources.py`: 31 passaram. Suíte
  completa final: **144 passaram, 1 ignorado**. `git diff --check` passou.
- Nenhuma alteração em serviço, firewall, DCOM, Registro COM, servidor ABB,
  RSLinx, PI Interface, configuração estrutural ou executável de destino.

### Testes nesta continuação

- Após a limpeza, a suíte Python passou com **140 aprovados, 1 ignorado**. A
  primeira execução sem escalonamento teve 9 falhas e 14 erros causados por
  `PermissionError: [Errno 1] Operation not permitted` ao abrir sockets de
  loopback; a repetição com permissão de loopback passou. Isso não abriu acesso
  externo.
- Leitura COM direta da fixture, relay e API foram validados nesta execução.
  Não houve teste GUI do PI OPC Client, nem teste de queda/reconexão nesta
  continuação. Os testes do modelo/runtime Windows registrados acima são
  resultados anteriores e não substituem essas validações.

## Ativação PI Client e cadastro TD1/LFS_TD1_PB2 (07/11/2026)

### Evidências anteriores à correção

A consulta SSH confirmou `RB1PI2`, Windows 7 Professional SP1, versão
6.1.7601, x86, endereços 10.247.140.166 e 10.247.238.75 e conta
`RB1PI2\Administrator`. Havia dois PI OPC Clients, PIDs 8548 e 6816, ambos
nessa conta, na sessão RDP 2. O servidor de destino era o PID 2112, mesma
conta, sessão 0, pai `svchost.exe` 640, iniciado com:

```text
D:\OPCB\rust-da-target-opc-source-20261106-build2\rust-da-com-lab.exe -Embedding
```

O PID 2112 era o único listener em 127.0.0.1:49321. O binário tinha SHA-256
`0eea82475cd695aba914fa1d56fee10d04415ec33ad91f6bc4489a42c811b8df`.
Os valores padrão de HKCU e HKLM em
`Software\Classes\CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32`
coincidiam com esse caminho. O ProgID nos dois escopos apontava para o mesmo
CLSID. Nenhum desses valores foi alterado.

O log do destino registra tentativas adicionais de ativação dos PIDs 11968,
5036 e 8228. Cada uma carregou o projeto de quatro itens e falhou antes da
fábrica COM com erro de bind da porta 49321, WinSock 10048, propagado como
`0x8000FFFF` no log. O PI Client informou `CoCreateInstanceEx 0x80080005`.
Isso confirma a colisão de porta de uma nova instância; a falha não foi
deduzida apenas da diferença de sessão. No código, `start_diagnostics` é
executado antes de `LocalClassRegistration::register`: se o bind falha, o
novo processo não chega a registrar a fábrica que o SCM espera. O fato de
um processo continuar atendendo HTTP/relay não prova ativação COM no contexto
do PI Client.

As responsabilidades de `LocalServer32` e registro da fábrica em execução
estão descritas pela Microsoft em
[Registering a Class at Installation](https://learn.microsoft.com/en-us/windows/win32/com/registering-a-class-at-installation)
e [Registering a Running EXE Server](https://learn.microsoft.com/en-us/windows/win32/com/registering-a-running-exe-server).
Nenhuma alteração de identidade DCOM/RunAs foi feita nesta investigação.

### Correção operacional e reversão

Antes da parada, foram guardados e conferidos `project.json`, `identity.json`,
`receipt.json` e snapshot do PID 2112 na pasta:

```text
D:\OPCB\rust-da-target-opc-source-20261106-build2\backup\activation-session-ee472026ad68494db9ac9da124ffec3e
```

O manifest registra, respectivamente, os hashes SHA-256:

```text
project.json=f2572a079bc29a58a8667623eabd6d6d07835de0d98bc2849793c051ffa3e04a
identity.json=b05cd4288aeeb59435378c0d1576ef4f29f91392d8b3cc42ed0fcccb25f407ca
receipt.json=275b1474e54543cbfc97fe07ad679fc84f4e862b53e8933c07bddecf98c15461
```

O banco Linux também foi salvo com a API de backup do SQLite em
`.opc-bridge-management/backups/activation-session-ee472026ad68494db9ac9da124ffec3e/management.sqlite3`.
O comando de reversão `rollback-start-previous.ps1` está nas duas pastas de
backup, SHA-256
`1d5463de8777bdb7596334be8ff8bddb9595eb49722d0752072e728e43dd738b`.
Ele confere hash, recusa outra instância ou marcador de parada e inicia o
mesmo executável com `-Embedding`. Executá-lo por SSH restaura a sessão 0 e
pode restaurar também o conflito com a RDP; é reversão do estado anterior,
não a correção recomendada para acesso pelo PI Client.

O caminho, hash, sessão e argumento do PID 2112 foram revalidados imediatamente
antes de criar `agent-stop.flag` com `CreateNew`. O servidor saiu normalmente
e removeu o marcador. A porta ficou sem listener; conexões TIME_WAIT não são
listeners. Nenhum PI Client foi encerrado e não houve encerramento forçado.
Foi solicitada nova conexão pelo PI Client na RDP, para que a ativação seja
feita por essa sessão. O usuário confirmou “Conectou e aparece RUNNING”. A
consulta seguinte confirmou o novo servidor PID 10324 na sessão 2, conta
`RB1PI2\Administrator`, com o mesmo caminho/build e argumento `-Embedding`.
O listener 127.0.0.1:49321 pertence ao PID 10324. O log registra fábrica COM
pronta, conexão do shutdown callback, criação do `Group1` e browse dos quatro
Item IDs preservados, terminando o enumerador normalmente. O relay abriu a
sessão de origem e recebeu ACK de amostras no novo PID; o snapshot retornou
TESTE2=61.75 e DIGITAL=true, ambos Good/192 e HRESULT 0. Isso confirma conexão
GUI relatada pelo usuário, browse observado no log e retomada do relay/cache;
não comprova leitura dos itens pela GUI nessa interação. A evidência está em
`.opc-bridge-management/evidence/activation-rdp-confirmed-20261107.txt`.
Não iniciar outro cliente COM por SSH para esse CLSID enquanto a RDP estiver
usando o laboratório. O relay/diagnóstico HTTP não precisa de uma nova ativação
COM do destino.

### Por que Velocidade está somente na prévia Linux

O estado persistente tem dois cadastros com o mesmo IP, mas UUIDs diferentes:

| Cadastro | UUID | Provisionamento |
|---|---|---|
| RB4 / RB1PI1 | `733cc9b5-7837-44f6-8ef8-a222b1ee6ddd` | Agente confirmado como RB1PI2, projeto Windows revisão 28 |
| TD1 / LFS_TD1_PB2 | `e4e860d0-e0c3-4c7b-9c59-0f3187a4caf6` | Sem entrada de agente |

`Deployments.targets()` e `target()` consultam `agents.json` pelo UUID do
cadastro; o endereço IP não concede vínculo automático. O agente Windows
também valida esse UUID em `identity.json`, projeto e recibo. Copiar apenas a
entrada SSH para o novo UUID não conclui o provisionamento e pode fazer dois
cadastros disputarem o mesmo catálogo/processo. Os nomes foram preservados;
o endereço dos dois aponta para o mesmo destino físico RB1PI2, não para uma
segunda instalação independente do servidor.

`LFS_TD1_PB2.RB1PI2.Current.Velocidade` está salva no Linux como Double, valor
simulado inicial 1005, módulo `RB1PI2` com `source_kind=simulated`. O snapshot
Windows anterior à parada confirmou somente os quatro IDs RB1PI1 existentes,
sem Velocidade. Portanto “Sem agente provisionado” e “Prévia Linux · fonte
simulada” descrevem corretamente esse cadastro; não houve aplicação Windows.

Antes de aplicar essa variável, escolher explicitamente entre consolidar as
variáveis em um único cadastro/catálogo Windows (preservando os quatro IDs e
o Item ID da Velocidade), migrar o destino para o cadastro TD1 com projeto
consolidado e novo vínculo de identidade, ou preparar outra instância com
ProgID/CLSID/porta próprios. A segunda instância não existe hoje. Qualquer
consolidação ou migração é estrutural e precisa de aplicação controlada e
reconexão; não foi feita automaticamente nesta investigação.

Somente este documento e evidências/backups privados foram escritos no
checkout nesta investigação. Não houve alteração de código, catálogo,
provisionamento, executável, Registro COM, serviços, DCOM ou firewall.

## Publicação da Velocidade no catálogo existente (07/11/2026)

Após o pedido para publicar a outra tag, foi usada a consolidação mínima no
destino já provisionado. O módulo `RB1PI2`, UUID
`6096f40e-c749-4e16-886f-c5f68546bf3f`, passou do cadastro não provisionado
TD1/LFS_TD1_PB2 para RB4/RB1PI1, UUID
`733cc9b5-7837-44f6-8ef8-a222b1ee6ddd`. A variável Velocidade conserva o UUID
`3029e463-fc54-48d2-bcef-5bc33c613532`, nome, tipo Double, valor simulado
1005.0 e Item ID `LFS_TD1_PB2.RB1PI2.Current.Velocidade`. Sua localização no
configurador é agora **RB4 > RB1PI1 > RB1PI2 > Current**. O cadastro TD1 e a
máquina LFS_TD1_PB2 permanecem presentes, sem módulos; não foi criado outro
provisionamento apontando para o mesmo processo Windows.

A migração foi ensaiada numa cópia em `.offline/catalog-consolidation/` e
validada pelo `Store` antes da transação persistente. A transação atualizou
somente o pai do módulo, o `scope_machine` da variável e a revisão 28 → 29;
todos os IDs/valores anteriores e o módulo da origem OPC DA foram preservados.
O exportador e o estado real da API confirmaram cinco itens. O projeto da
revisão 29 tem SHA-256
`71cd8ba5efca0ba0158434ddc3b9b88e2b0965fcd4419a568b28322d7df19b2a`.

Backups verificados de projeto, identidade e recibo Windows ficaram em:

```text
D:\OPCB\rust-da-target-opc-source-20261106-build2\backup\velocity-d06e25e9180f46a2b4975caf5447c066
```

O backup SQLite, provisionamento e recibos Linux estão em
`.opc-bridge-management/backups/velocity-d06e25e9180f46a2b4975caf5447c066/`.
Não foi alterado `agents.json` nem `identity.json`: o destino continua com a
mesma identidade. O hash do executável permaneceu
`0eea82475cd695aba914fa1d56fee10d04415ec33ad91f6bc4489a42c811b8df`.

Como a inclusão de item é estrutural, o PID 10324 da sessão 2 foi revalidado
por caminho, argumentos e hash e encerrado normalmente por marcador. O POST
local `/api/v1/machines/733cc9b5-7837-44f6-8ef8-a222b1ee6ddd/apply`, com
`If-Match: 29`, utilizou o agente existente e confirmou a revisão 29 com cinco
variáveis. A validação temporária iniciou o PID 11968, conferiu catálogo,
valores simulados, qualidade e timestamps e o encerrou normalmente; o recibo
tem `runtime_state=validated_waiting_client_activation`. Nenhum cliente COM
foi iniciado por SSH depois disso. Executável, Registro e serviços permanecem
iguais. Os clientes devem reconectar pela RDP para ativar a nova configuração
na sessão correta; grupos/handles da instância encerrada não são preservados.

Na última consulta, a API já mostrava a variável sob o destino provisionado,
com `deployment.status=applied` e revisão 29. A consulta de amostras retornou
503 `agent_unavailable`, aguardando a reconexão RDP; não foi apresentado valor
Linux como se fosse uma amostra Windows. A confirmação visual pedida ao usuário
é: reconnect em `RustDaBridge.Lab.1`, Add Item > Flat, adicionar o Item ID
completo e Refresh; esperado Double, 1005.0 e Good. Trata-se de fonte simulada,
sem ligação a uma velocidade real de processo.

Para reverter apenas a consolidação, o script privado
`rollback-consolidation.py` nesse backup confere revisão 29, devolve o pai do
módulo e o escopo da variável e grava revisão 30 (não diminui revisões).
Executá-lo a partir da raiz com `PYTHONPATH=src python <caminho-do-script>`.
Depois, com clientes desconectados e somente o servidor Rust de laboratório
parado normalmente, reaplicar pelo configurador o cadastro RB4/RB1PI1 e
reconectar pela RDP. Isso publica novamente os quatro itens. Não restaurar o
banco inteiro sobre alterações posteriores; revisar a migração se a revisão
já tiver mudado.

Validação desta mudança: integridade SQLite/Store e exportação persistente de
cinco itens passaram; o ACK Windows foi obtido pela API do serviço real, sem
reiniciar o configurador. Os 11 testes existentes de deployment passaram.
Evidências privadas: `velocity-deployment-confirmed-20261107.json` e
`velocity-api-20261107.json` em `.opc-bridge-management/evidence/`.

## Browse hierárquico no PI OPC Client (06/11/2026, horário de São Paulo)

O servidor anterior anunciava `OPC_NS_FLAT`. Por isso, mesmo com Flat
desmarcado, o PI Client recebia os cinco IDs completos em uma lista. O novo
adaptador anuncia `OPC_NS_HIERARCHIAL` apenas para projetos configurados. A
fixture de origem legada permanece plana. A árvore é derivada dos segmentos
dos Item IDs e não altera o catálogo, os tipos ou os vínculos de origem:

```text
RustDaBridge.Lab.1
├── LFS_TD1_PB2
│   └── RB1PI2
│       └── Current
│           └── Velocidade
└── RB1PI1
    └── TESTE
        └── TESTE1
            ├── DIGITAL
            ├── PRESSAO
            ├── TESTE1
            └── TESTE2
```

Os nomes de conexão do configurador (por exemplo RB4/TD1) não são segmentos
dos Item IDs existentes. A navegação OPC desta versão usa máquina/módulo/pasta
dos próprios IDs. Flat continua disponível como consulta recursiva e devolve
os IDs completos. GetItemID converte a folha relativa no ID original, incluindo
`LFS_TD1_PB2.RB1PI2.Current.Velocidade`. Cada objeto servidor COM mantém sua
posição em um mutex independente. Subir acima da raiz falha com E_FAIL e não
entra em loop; destinos inválidos retornam E_INVALIDARG sem mudar a posição.
Filtros de tipo/acesso são aplicados às folhas, sem esconder os caminhos para
alcançá-las. Não houve mudança no modelo de valores/qualidade/timestamp.

### Código e testes

- `windows/rust_da_com_lab/src/browse_tree.rs`: lógica independente, std-only,
  MIT, com cinco testes para navegação, filtros, posições isoladas, resolução
  e colisões de prefixo. Não contém definições OPC Foundation.
- `scripts/patch_rust_opc_da_browse.py`: integra essa lógica ao adaptador da
  revisão já fixada, em cópia temporária; não modifica o upstream.
- `windows/rust_da_com_lab/src/configured.rs`: ativa o modo hierárquico e
  estende o cliente de diagnóstico com percurso real de branches/folhas,
  resolução e duas conexões independentes.
- `scripts/test_rust_da_hierarchy.ps1`: ensaio isolado Win7 x86, CLSID transitório
  existente e porta loopback 49326, sem registro persistente ou integração real.

Passaram cinco testes nativos da árvore, 11 do modelo e 13 do runtime de
projeto. O executável PE32 i386 compilou com o toolchain isolado existente;
seus imports continuam sem combase.dll/WinRT. Na RB1PI2, os self-tests COM
legados (incluindo recusa de escrita em processo) passaram. O teste **entre
processos** não chamou escrita: percorreu ambas as árvores, confirmou os cinco
IDs e independência entre clientes, testou a terminação na raiz e leu os cinco
itens por Cache e Device com qualidade 192, FILETIME e S_OK. Foram usados
somente valores simulados locais: TESTE2=7, DIGITAL=false, Velocidade=1005,
TESTE1=11 e PRESSAO=12.4. Servidor isolado PID 5164, cliente 5228, saída normal
do servidor confirmada. Esses valores de fixture não substituíram os valores
recebidos pelo relay no destino. Evidência privada:
`.opc-bridge-management/evidence/hierarchy-windows-test-20261106.txt`.

### Aplicação e reversão

Novo build: `D:\OPCB\rust-da-hierarchy-20261106-build1\rust-da-com-lab.exe`,
SHA-256 `e4705f9188c25320cef8ae609c4a66c7f475b96c8d1643a9846d3241ac50eab7`.
O projeto continua na revisão 29, SHA-256
`71cd8ba5efca0ba0158434ddc3b9b88e2b0965fcd4419a568b28322d7df19b2a`, com os
mesmos cinco itens. Foram copiados no próprio Windows projeto/identidade/recibo,
agente e token local para a pasta nova. Nenhum token foi trazido ao repositório.
Dependências e dúvidas de licença dos bindings existentes permanecem iguais;
nenhuma dependência nova foi adicionada, nem houve publicação.

Antes da troca, foram exportadas somente as subchaves LocalServer32 de HKCU e
HKLM para `backup/HKCU-LocalServer32.reg` e `HKLM-LocalServer32.reg` na pasta
nova; os valores exatos também foram preservados em arquivos de texto. Os
backups Linux de perfil, recibos e SQLite ficaram em
`.opc-bridge-management/backups/hierarchy-b35a4151f352433e98d73e0e4bafbf4d/`.
O PID 10224, sessão 2, caminho/hash/argumentos conferidos, encerrou normalmente.
Foram atualizados apenas os dois valores padrão LocalServer32 do CLSID de
laboratório e `agent_dir` do mesmo UUID de destino; nenhum novo vínculo de
máquina foi criado. Não houve alteração de serviços/DCOM/firewall.

O Apply pelo serviço real confirmou o hash novo e cinco itens, usando a
instância temporária de validação PID 6140, que foi encerrada normalmente.
O recibo é `validated_waiting_client_activation`: reconectar pela RDP para
ativar o servidor na sessão correta. Para testar a interface: fechar Add Item,
Disconnect → Connect, reabrir Add Item, desmarcar Flat e expandir os caminhos
acima. Até confirmação pelo usuário/log, a árvore visual do PI Client não é
declarada validada. Não chamar CoCreateInstance do destino por SSH.

Para reverter, desconectar os clientes e encerrar normalmente apenas o alvo
novo verificado. Executar `backup/rollback-registry.ps1` na pasta Windows nova:
ele verifica o caminho corrente e restaura somente os dois defaults anteriores.
Seu SHA-256 é
`1d1ba614aba01ea9886914ad51d4f4e2a10b9897b12f5effc8a9d32b6abbf21e`.
Restaurar somente `agent_dir` do UUID de destino para
`D:\OPCB\rust-da-target-opc-source-20261106-build2`, usando o perfil preservado
como referência, reaplicar a mesma revisão pela API/interface e reconectar na
RDP. Não restaurar banco inteiro nem reduzir revisões: a troca de build não
alterou o catálogo persistente. O build anterior continua intacto.

## Nova origem Kepware LB3PI1 — 2026-11-09

O operador designou `10.247.140.46` como origem e autorizou provisionar o
agente PowerShell somente leitura. Identidade confirmada por SSH e WMI:
hostname `LB3PI1`, Windows `6.1.7600.0` (Windows 7 RTM, sem SP1), processo x86,
endereços `172.16.4.171` e `10.247.140.46`. Não inferir SP1. KEPServerEX é
4.22.2.376; ProgID `KEPware.KEPServerEx.V4`, CLSID
`{6E6170F0-FF2D-11D2-8087-00105AA8F840}`, `LocalServer32` aponta para
`C:\Program Files\KEPServerEx\servermain.exe -opcrun`. Na consulta, o servidor
estava Running e `ServerMain.exe` PID 4040 já estava ativo; não foi iniciado
nem reiniciado pelo bridge.

A conexão local COM via `OPC.Automation.1` identificou o servidor e seu estado.
`CreateBrowser` retornou um objeto COM, mas `QueryInterface(IID_IDispatch)` no
OPCBrowser retornou `E_NOINTERFACE`; portanto nenhum Item ID foi obtido por
esse caminho. O utilitário instalado para navegação manual é
`C:\Program Files\KEPServerEx\opcquickclient.exe`. Nenhuma tag foi lida e
nenhum valor foi publicado naquela etapa inicial.

Por autorização do operador, o leitor existente foi copiado, sem instalação de
serviço ou registro COM, para
`C:\ProgramData\OPCBridge-LB3PI1-Kepware`. A pasta limita escrita a SYSTEM e
Administrators; usuários autenticados têm somente leitura/execução. Arquivos:
`opc_da_source_agent.ps1` SHA-256
`81cc39c3624a394ed11c697b5b9c2a2c33d219b1746dffc0610a63f4e67d15c1` e
`source-identity.json` SHA-256
`0cde04303dcaa0e169683948e144287839a4f2db44d6ceec426c16b79f71b966`.
O arquivo de identidade autoriza exclusivamente esse hostname/endereço e o
ProgID Kepware, com política `reject_unspecified` para timestamps sem fuso.
As hashes remotas foram conferidas; nenhuma escrita OPC é implementada pelo
agente.

O provisionamento privado de origem no Linux referencia a chave SSH já
existente, a fingerprint conferida do host e o transporte `opc_da_automation`.
O endpoint de descoberta do configurador retornou uma única entrada Kepware
com as categorias DA registradas; disponibilidade foi marcada como não testada
pela descoberta, embora a conexão COM local de estado tenha sido observada em
consulta separada. O relay só coleta quando houver módulo e variáveis fonte
configurados. O banco continua na revisão 29, o destino `RB1PI1` permanece
`10.247.140.166`, e os cinco Item IDs existentes não foram alterados. Backups
anteriores de `source-agents.json` e `source-discovery.json`, mais a fingerprint
verificada, estão sob `.opc-bridge-management/`; nenhum segredo foi incluído no
repositório.

Próximo passo naquela etapa: identificar Item IDs autorizados e os tipos sem
executar escrita. A investigação subsequente está registrada abaixo.

### Browse e tipagem read-only do KepServer — 2026-11-09

Consulta adicional somente leitura pela classe COM nativa do KepServer:
`IOPCBrowseServerAddressSpace::QueryOrganization` retornou `S_OK`, organização
hierárquica, e `BrowseOPCItemIDs(OPC_FLAT, ..., OPC_READABLE)` retornou `S_OK`
com 483 IDs. O PID `servermain.exe` permaneceu 4040 antes e depois. A interface
de browse no wrapper `OPC.Automation.1` continua sem QI; a consulta nativa ao
ProgID `KEPware.KEPServerEx.V4` funcionou. Entre os itens com “Double” no nome,
os 40 resultados eram entradas `_Hints` (descrições de faixas/tipos), não tags
vivas selecionadas; elas não foram usadas como fonte.

Foram examinados por leitura OPC DA os 22 itens sem `_Hints`/`_System` do canal
`Channel_0_User_Defined`. Os tipos VARIANT observados nesse conjunto foram
String (`User1`, `User4`), Boolean (`User2`), Single (`User3`, `Sine1`–`Sine5`,
`Ramp_Float`) e Int32 (`RandomXaxis`, `RandomYaxis`, `Random1`–`Random3`,
`RampXL1`–`RampXL3`, `Ramp1`–`Ramp4`). Não foi encontrado Double entre esses
itens; portanto não se converteu Single/Int32 para Double.

Uma leitura isolada de `Channel_0_User_Defined.User.User2` pelo agente, via
`OPCGroup.SyncRead`, confirmou VARIANT Boolean (`VT_BOOL`), valor `true` e
HRESULT `S_OK`. O timestamp chegou com `DateTimeKind.Unspecified`; como a
política provisionada é `reject_unspecified`, o FILETIME foi deixado ausente e
a qualidade foi rebaixada para 64 (Uncertain). Não foi feita escrita. O teste
não configurou nem iniciou relay contínuo, não alterou o destino e não prova
visualização no PI OPC Client.

O operador autorizou uma conversão explícita Float→Double somente para a
variável numérica de laboratório. A fonte escolhida é
`Channel_0_User_Defined.Ramp.Ramp_Float` (VT_R4/Single); o Item ID publicado
continua `RB1PI1.TESTE.TESTE1.TESTE2` (VT_R8/Double). A tag booleana escolhida
é `Channel_0_User_Defined.User.User2` (VT_BOOL), publicada como
`RB1PI1.TESTE.TESTE1.DIGITAL`; os dois nomes de origem permanecem independentes
dos Item IDs de destino. A conversão aceita apenas um `System.Single` finito e
o promove numericamente para `System.Double`; não é inferida pelo nome e não
altera o tipo de origem registrado.

Em uma leitura COM real isolada pela cópia temporária do agente, a identidade
foi `LB3PI1` / `10.247.140.46`, ProgID `KEPware.KEPServerEx.V4`, registry x86.
`OPCGroup.SyncRead` retornou `S_OK` para os dois itens. Na amostra observada,
`Ramp_Float` retornou valor `36` como VT_R4 e foi convertido para `36.0`
Double/VT_R8; `User.User2` retornou `false` como VT_BOOL. Ambos chegaram com
`DateTimeKind.Unspecified`, sem FILETIME válido. A política
`reject_unspecified` deixou o timestamp ausente e reduziu a qualidade para
64/Uncertain; HRESULT S_OK não transforma esse timestamp em válido. Os valores
podem variar na origem. A Quick Client mostrara outro valor de Float em uma
captura anterior, portanto não se compara essa captura a uma amostra temporal
posterior.

O teste usou uma cópia diagnóstica temporária do script, removida ao final;
não alterou o KEPServer nem executou escrita. Os testes de modelo e relay
validam a preservação de `source_vartype=4`, `published_vartype=5`, valor,
qualidade, timestamp e HRESULT. A revisão persistente ainda precisa ser
aplicada ao servidor de destino para validar a publicação pelo PI OPC Client.
Antes disso deve ser guardado o estado anterior do banco, projeto/recibo,
agente e Registro COM. A mudança de vínculo de origem é estrutural: requer
servidor sem instância ativa e reconexão do PI OPC Client; não há promessa de
preservar grupos/handles nessa alteração. Nenhum teste de PI OPC Client com os
valores Kepware foi feito ainda.

### Relay OPC DA Kepware → servidor de destino — 2026-11-09

Atualização posterior: a origem configurada é o KepServerEx de laboratório
`LB3PI1` (`10.247.140.46`, view x86), e o destino físico é `RB1PI2`
(`10.247.140.166`). Não foi acessada a antiga máquina de produção
`PIMS_TD1_PB2`. O servidor Kepware é consultado localmente pela Automation e
somente por `OPCGroup.SyncRead`; o agente não implementa escrita.

O mapeamento autorizado conserva os Item IDs publicados:

| Origem Kepware | Origem observada | Conversão/publicação | Item ID do destino |
| --- | --- | --- | --- |
| `Channel_0_User_Defined.Ramp.Ramp_Float` | Float/VT_R4 | Float→Double/VT_R8 | `RB1PI1.TESTE.TESTE1.TESTE2` |
| `Channel_0_User_Defined.User.User2` | Boolean/VT_BOOL | sem conversão | `RB1PI1.TESTE.TESTE1.DIGITAL` |

A causa confirmada do HTTP 409 entre relay e servidor Rust era uma diferença
no contrato opcional de tipo: para a tag Boolean, `project.json` omite
`source_data_type` e o runtime interpreta `None` como “mesmo tipo do item
publicado”. O agente de origem, corretamente, informava `Boolean`; o relay
repassava esse campo redundante e o runtime exigia igualdade exata com o
vínculo implícito. Para a conversão Float→Double, `source_data_type=Float` é
explícito no projeto e precisa continuar no lote. O relay agora valida o tipo
de origem e o VARTYPE recebido e normaliza somente esses metadados para a
representação do vínculo publicado. `source_vartype=11` continua evidenciando
o Boolean real, e o dashboard resolve o tipo implícito a partir do tipo da
variável. Não foi necessário recompilar/trocar o servidor Windows nem mudar o
Registro COM.

O teste de regressão Rust confirma que um Boolean com metadado implícito é
aceito com VT_BOOL e que o mesmo pacote com metadado redundante divergente é
recusado sem alterar a amostra anterior. O perfil SSH privado da origem marca
agora `source_environment=laboratory`, para que o dashboard identifique as
duas tags como leitura OPC DA de laboratório com fonte simulada; a alteração
tem backup em
`.opc-bridge-management/backups/before-kepware-relay-20261109/source-agents.json.before-lab-label`.

Após aplicar a revisão persistente 32, o agente do destino confirmou o projeto
SHA-256 `0a20244402267f9f849627631a5022228cffb48fd4ffec2cf45977d7a86f80fc`
e executável SHA-256 `ac66a366c5f74fd9d07214b5eaab3f261cb240a04e7fdc94998c9356ee820f4d`.
Na validação do relay, o processo de destino permaneceu no PID 8488, sessão 2,
no executável `D:\OPCB\rust-da-kepware-float-20261109-build1\rust-da-com-lab.exe`.
Três consultas sucessivas à API mais snapshots do próprio runtime mostraram os
valores coincidentes: `TESTE2=1.0` (VT_R4→VT_R8) e `DIGITAL=true` (VT_BOOL),
ambos com HRESULT da leitura de origem S_OK, qualidade 64/Uncertain e timestamp
ausente. Uma leitura COM independente via agente na LB3PI1 também retornou
S_OK e os mesmos valores/tipos. O Double é variável no Kepware e não deve ser
tratado como constante.

O timestamp permanece ausente porque a Automation devolve
`DateTimeKind.Unspecified` e a política segura `reject_unspecified` não
interpreta esse horário como UTC ou local. Por isso o relay rebaixa a amostra
Good para Uncertain (64); não substitui o timestamp pelo relógio do Linux ou
Windows. A API confirmou o valor no cache publicado, mas a confirmação visual
no PI OPC Client ainda depende do refresh manual na sessão RDP. A UI do
navegador também não foi validada visualmente nesta execução. Os testes desta
correção foram 86 testes Python de gestão/relay e 15 testes do modelo Rust;
nenhum deles representa, por si só, teste visual do PI OPC Client.

### Correção de timestamp e callbacks Kepware — 2026-11-09

A investigação subsequente conferiu diretamente o FILETIME nativo do Kepware
na LB3PI1, por `IOPCSyncIO::Read`, somente nas duas tags autorizadas. Ambas
retornaram qualidade 192/Good e S_OK. Em duas rodadas, a data Unspecified da
Automation interpretada como UTC diferiu 0,093–0,140 s da leitura nativa feita
logo depois; a interpretação local resultaria em aproximadamente três horas de
erro. Portanto **somente nesse agente** `timestamp_policy` foi alterado para
`utc`, com backup atômico em
`C:\ProgramData\OPCBridge-LB3PI1-Kepware\source-identity.before-utc-20261109.json`.
O hash anterior da identidade era
`0cde04303dcaa0e169683948e144287839a4f2db44d6ceec426c16b79f71b966`, o novo é
`fb3d4f2c9d000acd4aa90b09acd76718780e8165c57cd3288adba60824f95bc7`.
Não foi alterado relógio, fuso Windows ou configuração Kepware. A política
padrão para origens não verificadas continua `reject_unspecified`.

O runtime antigo PID 8488 passou a receber Good e timestamp sem reiniciar.
O novo build posterior acrescentou Async Read e Advise, com teste COM nativo
entre processos e dados da origem: TESTE2=1.0 (VT_R4→VT_R8), DIGITAL=true
(VT_BOOL), qualidade 192, S_OK, FILETIMEs 134387125612820000 e
134387125613290000. O processo de teste foi PID 9332 e terminou normalmente.
Ele não era o PI OPC Client. Refresh/Sync/Async e callback inicial de Advise
passaram nesse teste configurado; mudança de valores, indisponibilidade e
recuperação foram validadas separadamente na fixture simulada.

O build final registrado é
`D:\OPCB\rust-da-async-20261109-build3\rust-da-com-lab.exe`, hash
`7f9c7b730ec9359bbaaf65a41116e06212c769abd8ecd378c7024ef4f6d4bc12`.
Revisão 32, digest e os cinco Item IDs foram preservados. Os backups Linux estão
em `.opc-bridge-management/backups/before-async-20261109`; as reversões dos dois
LocalServer32 estão na pasta do build. Ver RUST_DA_COM_LAB.md para processos,
interfaces, limites e procedimento de reversão. Após os testes, a fixture de
sessão 0 encerrou e o servidor ficou aguardando ativação pelo PI Client da RDP;
não se afirma confirmação visual dos novos botões enquanto ela não ocorrer.

### Coleta mais rápida sem reiniciar o servidor — 2026-11-09

A revisão 33 reduziu o intervalo de origem de 10 para 5 segundos. O catálogo,
Item IDs, valores iniciais, tipos e vínculos de origem foram comparados antes e
depois: a única diferença no projeto exportado foi a revisão de implantação
(`stale_after_s` continuou 30). O agente confirmou
`hot_applied_server_preserved`, PID 2324, projeto SHA-256
`de1c656db682bccbe00c7041450e232fe2477a048bb3c7ae3566e4982dd9e8b1`.
O PI OPC Client PID 6404 permaneceu aberto; nenhum processo Windows, executável
COM ou valor do Registro foi trocado nesta otimização. O log não mostra
remoção/recriação do Group1 na aplicação. Uma nova leitura GUI após esta
alteração ainda deve ser distinguida da confirmação do cache pela API.

Mudanças localizadas:

- O intervalo passa a ser contado entre os **inícios** dos ciclos, sem uma
  nova pausa integral depois de cada operação SSH. Há um único worker; um ciclo
  lento não provoca sobreposição ou fila de leituras.
- A telemetria pode usar `runtime_transport=ssh_stdin_v1` no perfil privado do
  destino. Cada pacote usa uma execução SSH verificada, sem SCP/arquivo de
  inbox intermediário. Aplicações de projeto continuam usando transferência de
  arquivo, validação e confirmação transacional. Perfis sem a opção conservam
  o transporte anterior; nomes de transporte desconhecidos são recusados.
- No PowerShell 2, stdin já é consumido pela pipeline do host. O comando
  encaminha esse conteúdo ao script em memória, com JSON ASCII/escapes Unicode
  e limite de 2 MiB. O conteúdo nunca é interpolado no código ou colocado nos
  argumentos do processo SSH/PowerShell. A identidade do destino, revisão,
  digest, sessão, sequência, token local e pinning SSH continuam verificados.
  Stdin só é aceito para telemetria; não permite aplicar projeto ou executar
  comandos genéricos.
- Quando o agente é alcançável e somente o runtime está fechado, o status é
  `waiting`, com espera adicional de 1 segundo entre tentativas, acrescida do
  tempo SSH. Não há leitura da origem nesse estado. Falhas reais continuam
  usando backoff limitado a 60 s e o runtime continua expirando amostras.
- O perfil de snapshot simulado fica `superseded` quando todos os seus UUIDs
  já pertencem à coleta OPC DA. Seus IDs de monitoramento persistem, mas não
  é feita a consulta SSH redundante, nem se apresentam amostras simuladas sobre
  as amostras reais. Não foi removida a configuração antiga.

Medição no processo real da RB1PI2, com o mesmo PID 2324:

| Medida | Antes | Depois |
| --- | --- | --- |
| Intervalo médio entre ACKs | 14,729 s (11 intervalos) | 5,282 s (5 intervalos / 6 ciclos) |
| Intervalos observados depois | — | 4,321–6,341 s |
| Tempo de processamento depois | — | 3,744–4,839 s, média 4,124 s |

É uma redução de aproximadamente 64% no intervalo médio entre lotes. A variação
entre ACKs inclui a duração variável das operações; 5 s é a meta entre inícios,
não uma garantia de tempo real. O endpoint `source-status` agora expõe
`timings_ms` para sessão inicial, leitura da origem, entrega e total, sem
credenciais. A medição de seis ciclos está em
`.opc-bridge-management/backups/before-fast-relay-20261109/timing-validation.json`.

A API confirmou TESTE2=1.0/Double (VT_R4→VT_R8) e DIGITAL=true/Boolean (VT_BOOL),
ambos Good/192, S_OK e timestamps UTC da origem. O relay ficou `connected`.
O tempo até a primeira amostra numa **nova ativação COM** não foi cronometrado
novamente, pois o servidor e o cliente foram preservados. Portanto o resultado
5,282 s não deve ser descrito como tempo de startup comprovado. A política de
retry inicial foi coberta por testes, e será medida na próxima abertura natural.

Validação: 90 testes Python passaram (gestão, canal SSH, stdin limitado,
Unicode, confirmação/isolamento, temporização, indisponibilidade e overlay).
No Windows 7/PowerShell 2, o candidato foi analisado e um envelope stdin com
Unicode/destino incorreto foi recusado antes de mutação; os ciclos reais depois
confirmaram o canal. A primeira tentativa usando Console.OpenStandardInput
retornou entrada vazia porque o host já a consumira; a versão instalada usa a
pipeline. O servidor não foi reiniciado para esse diagnóstico.

O agente instalado tem SHA-256
`dee6c8e89961dbf45aa916b31fb4d6a36adf1cc304da145ecd4b0c9b8993418b`.
O anterior está preservado no Windows em
`D:\OPCB\rust-da-async-20261109-build3\lab_agent.before-fast-d95d44e01e43.ps1`
(hash `d95d44e01e431f16b6eb6e72fd1b2b614303005d8d89b6bc68355cfaa2435e2e`).
Os backups Linux de perfil, recibo e banco estão em
`.opc-bridge-management/backups/before-fast-relay-20261109`.
Para reversão operacional, desabilitar/remover somente `runtime_transport`
desse destino restabelece SCP; restaurar o script com substituição atômica caso
necessário. Para retornar ao intervalo 10, editar o módulo e aplicar uma **nova**
revisão, sem copiar o banco/recibo antigo sobre alterações posteriores. Somente
o configurador Linux precisa ser recarregado para trocar o código/perfil;
nenhuma alteração no Registro, serviço Windows, DCOM ou firewall é necessária.
