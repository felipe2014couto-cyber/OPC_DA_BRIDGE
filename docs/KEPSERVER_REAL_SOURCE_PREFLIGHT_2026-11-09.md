# Pré-verificação para leitura real no KepServerEx 4

## Resultado desta execução

Consultas SSH somente leitura confirmaram:

| Papel | Identidade | Sistema | Processo observado |
| --- | --- | --- | --- |
| Origem | LB3PI1, 10.247.140.46 | Windows 7 Professional 6.1.7600, x86, sem SP1 | ServerMain.exe, PID 4040, sessão 2 |
| Destino | RB1PI2, 10.247.140.166 | Windows 7 Professional 6.1.7601, x86, SP1 | rust-da-com-lab.exe, PID 2324, sessão 2 |
| Cliente | RB1PI2 | PI OPC Client | OPCClient.exe, PID 6404, sessão 2 |

PIDs são observações, não parâmetros fixos. A presença do cliente não comprova
que ele está conectado nem que exibiu valores nesta execução.

O KepServerEx tem versão 4.22.2.376 e ProgID `KEPware.KEPServerEx.V4`,
CLSID `{6E6170F0-FF2D-11D2-8087-00105AA8F840}`. O registro aponta para
`C:\Program Files\KEPServerEx\servermain.exe -opcrun`.

O destino está registrado em
`D:\OPCB\rust-da-async-20261109-build3\rust-da-com-lab.exe` e o PID observado
possui o argumento `-Embedding` e escuta em `127.0.0.1:49321`.

## Agente existente

O agente já provisionado está em
`C:\ProgramData\OPCBridge-LB3PI1-Kepware\opc_da_source_agent.ps1`.
Seu SHA-256 é
`232fcd704e44c5613af109dca290c9419f25b70304c4555a136feb248a7b4f64`,
igual ao arquivo do checkout. Executa sob PowerShell 2.0 x86, usando Automation
local; a allowlist autoriza somente a identidade da origem e o ProgID Kepware.
A política de timestamp existente é `utc`, validada no laboratório anterior.
Não foi instalado, atualizado ou iniciado um novo agente nesta verificação.

## Configuração preservada e bloqueio de seleção

O banco continua na revisão 33. O módulo existente aponta para as fontes
**simuladas**:

- `Channel_0_User_Defined.Ramp.Ramp_Float` → `RB1PI1.TESTE.TESTE1.TESTE2`,
  Float → Double explicitamente autorizado no laboratório anterior.
- `Channel_0_User_Defined.User.User2` → `RB1PI1.TESTE.TESTE1.DIGITAL`, Boolean.

O relay existente tem intervalo configurado de 5 segundos. A consulta da API
mostrou estado conectado, método `OPCGroup.SyncRead` e confirmação do destino
no PID 2324. Isso descreve a operação já existente com as fontes simuladas;
não comprova leitura de tags reais solicitadas agora.

**Pendente:** o operador deve fornecer um ou dois Item IDs reais autorizados
e escolher se substituem as fontes desses itens publicados ou se serão novos
itens. Nenhuma tag de processo foi presumida nem lida por esta investigação.
Nenhuma configuração foi aplicada, processo encerrado ou registro alterado.
Não há alteração operacional para reverter nesta execução.

## Próxima execução após a seleção

1. Fazer uma leitura pontual apenas dos IDs aprovados pelo agente existente.
2. Validar tipos retornados, qualidade, FILETIME e HRESULT por item; confirmar
   a interpretação de datas sem fuso para essas fontes antes de publicá-las.
3. Preparar backup e comparar a estrutura com o catálogo ativo. Mudança de
   vínculo/estrutura não é presumida como atualização de valor a quente.
4. Fazer observação curta com intervalo conservador, sem criar poller
   concorrente ao módulo existente. Definir antes o mecanismo de parada.
5. Comparar origem/destino por COM e solicitar comprovação visual pelo PI Client,
   separadamente da API. Não deixar uma nova coleta contínua sem confirmação.

Não foi acessado ABB, RSLinx ou PI Interface. Não houve escrita OPC nem mudança
em KepServer, serviços, firewall, DCOM, Registro ou executáveis.

## Continuação — tag real indicada pelo operador

O operador autorizou `Channel_1.Device_1.Bool_1` (informado duas vezes; tratado
como um único ID). Foram feitas três leituras pontuais por `OPCGroup.SyncRead`
no ProgID Kepware confirmado, sem snapshot, escrita ou mudança de configuração.

| Timestamp de origem, interpretado em UTC | Valor | Tipo observado | Qualidade | HRESULT |
| --- | --- | --- | --- | --- |
| 2026-11-09T16:48:31.069Z | false | VT_BOOL (11) | 192 / Good | 0 / S_OK |
| 2026-11-09T16:49:05.108Z | false | VT_BOOL (11) | 192 / Good | 0 / S_OK |
| 2026-11-09T16:49:11.442Z | false | VT_BOOL (11) | 192 / Good | 0 / S_OK |

As duas últimas consultas foram sequenciais, com pausa de cinco segundos;
cada chamada levou aproximadamente 1,3 segundo. Não houve mudança natural do
valor durante a observação. As datas Automation vieram com Kind=Unspecified;
a interpretação usa a política UTC já existente. Nenhuma conversão de valor
foi necessária. A leitura do Kepware foi confirmada; a configuração física do
canal/PLC não foi inspecionada ou alterada.

A observação terminou; não foi criado poller persistente para essa tag. O relay
anterior das tags simuladas não foi alterado. As evidências por item ficaram
no diretório privado de evidências do configurador, fora do Git.

A troca do vínculo `source_item_id` é estrutural: o agente recusa aplicá-la com
o servidor em execução (`Alteração estrutural: reinício exigido.`). Antes de
publicar, aguarda-se a escolha de reutilizar DIGITAL ou criar outro Item ID,
e autorização da interrupção do servidor Rust de laboratório caso necessária.
Não houve validação dessa nova tag pelo destino, PI Client ou dashboard ainda.

## Plano aprovado de identificação, ainda não aplicado

Item ID de destino aceito pelo operador: `RB1PI1.KEPSERVER.REAL.Bool_1`.
Foi preparado um candidato isolado com módulo KEPSERVER / pasta REAL, somente
a tag autorizada, Boolean e intervalo proposto de 10 segundos. A comparação
confirmou que os cinco itens existentes e seus metadados permanecem idênticos;
o candidato contém seis itens. A revisão ativa continua 33. A revisão 34 é
apenas uma proposta, que deve ser recalculada se houver edições concorrentes.

Backup consistente SQLite, perfis, recibo, estado e catálogo exportado antes da
mudança: `.opc-bridge-management/backups/before-real-bool1-20261109-135850`.
Contém também o candidato identificado como `not-applied`; não é configuração ativa.

Execução proposta, pendente de autorização da interrupção:

1. Revalidar revisão, PID/caminho e cliente de laboratório; fechar o PI Client
   normalmente ou solicitar fechamento na RDP.
2. Parar normalmente somente o Rust do laboratório e aplicar o catálogo validado
   de seis itens. Não mudar executável, LocalServer32 ou componentes Windows.
3. Reabrir/conectar o PI Client na sessão RDP, adicionar o novo Item ID e validar
   leituras durante uma observação limitada de cerca de um minuto a cada 10 s.
4. Interromper a nova coleta ao final, salvo autorização explícita para mantê-la.
   Não marcar a amostra antiga como Good após a parada/expiração.

A inclusão é estrutural e desconecta os clientes do servidor Rust durante a
aplicação. Não afeta o KepServer de origem. Para reverter, remover somente o
novo módulo/pasta/item numa nova revisão e reaplicar o catálogo anterior de
cinco itens pelo mesmo fluxo de parada/reconexão. Não sobrescrever o banco
ativo com o backup se houver edições posteriores. Nenhuma mudança de Registro
ou troca de build é necessária para essa reversão.

## Aplicação autorizada — revisão 36

Após autorização, foi incluído `RB1PI1.KEPSERVER.REAL.Bool_1`, Boolean,
origem `Channel_1.Device_1.Bool_1`, módulo KEPSERVER / pasta REAL.
A criação dos três níveis produziu a revisão 36, com seis itens. O agente
confirmou o catálogo com SHA-256
`89f3ededa2b4799128e546e68c21cd40bbe125b08c10531ec8014bd5b29cd43f`.
A comparação anterior preservou os cinco itens existentes.

O executável foi reconferido (SHA-256
`7f9c7b730ec9359bbaaf65a41116e06212c769abd8ecd378c7024ef4f6d4bc12`),
e não foi trocado. LocalServer32 em HKCU/HKLM foi consultado e não alterado.
O PI OPC Client PID 6404, cujo caminho foi revalidado, precisou de encerramento
forçado após CloseMainWindow não encerrar a aplicação. O servidor Rust PID 2324
encerrou normalmente pelo marcador próprio. Nenhum outro processo foi encerrado.

Backup Windows:
`D:\OPCB\rust-da-async-20261109-build3\before-real-bool1-3833424920d44aa3bb38e43e0f588518`.
O agente iniciou somente a validação temporária do catálogo no PID 7000 e a
encerrou normalmente. O recibo `validated_waiting_client_activation` não significa
que o PI Client esteja conectado. É necessário reabrir o PI Client na RDP para
ativar o servidor na sessão correta; não deixar uma instância SSH ocupando a porta.

### Limite de coleta do novo módulo

Foi acrescentado um controle privado de janela no relay Linux:
`.opc-bridge-management/relay-read-windows.json`. A entrada do módulo
`33b7ba68-2e2e-42a3-bba5-0f4d17ce5fda` contém o machine_id e os limites
`not_before_unix_ms` / `until_unix_ms`. A duração máxima aceita é cinco minutos;
para este teste será usada uma janela de aproximadamente um minuto.
A janela permanece fechada até o usuário confirmar o cliente conectado.

O controle não altera o catálogo OPC nem reinicia o runtime. Só os módulos
listados são limitados; a coleta simulada preexistente permanece independente.
JSON inválido impede leituras enquanto o controle estiver inválido. O arquivo
é controle operacional privado do operador: para parar, substituir atomicamente
o limite por uma janela encerrada, **não excluir o arquivo**. A remoção elimina
esse controle opcional. Uma amostra cuja janela termina durante a chamada não
é encaminhada. A qualidade no destino expira pelo prazo de 30 s deste vínculo;
parar o relay não constitui uma nova leitura falha da origem.

O configurador Linux foi reiniciado para carregar o controle, conforme autorização
anterior restrita a esse serviço. Nenhum serviço Windows foi alterado. A suíte
completa passou com **160 aprovados e 1 ignorado**, incluindo janela fechada,
isolamento, recuperação e término durante uma leitura.

O estado API confirmou `paused` para o novo módulo e `waiting` para o módulo
anterior, pois o servidor aguarda ativação do PI Client. A nova leitura real no
servidor de destino ainda depende da ativação RDP e da abertura da janela.

### Validação visual do cadastro, sem leitura de destino

O Chromium já instalado em `/tmp/aurora-playwright` abriu o configurador real
em loopback, selecionou a pasta REAL e a variável nova. A tela mostrou o Item ID
correto, origem `Channel_1.Device_1.Bool_1`, Boolean, valor ausente e qualidade
“Sem leitura”. Após correção pontual de um rodapé antigo, a identificação é
“Origem OPC DA real · coleta pausada”, sem afirmar simulação ou leitura concluída.
O teste de sintaxe JavaScript passou. Evidência privada:
`.opc-bridge-management/evidence/real-bool1-applied-paused.png`.
Esta validação visual é do configurador Linux, não do PI OPC Client.

### Diagnóstico do erro 0x800706BA no PI OPC Client

Após o agente confirmar a revisão 36, o cliente RDP abriu o servidor no PID
7504 (sessão 2), e o browse resolveu o ID novo corretamente. A imagem do primeiro
Sync mostrou `0x800706BA`; naquele momento o runtime ainda não tinha amostra válida
do novo source module. As leituras COM repetidas desse período registram
`READ_RESULT ... hresult=0x800706ba`.

Em uma janela posterior, a API recebeu quatro amostras COM da origem:
`false`, VT_BOOL/11, quality 192, S_OK, timestamps de origem entre
`2026-11-09T17:15:17.762Z` e `2026-11-09T17:18:06.492Z`, todas no mesmo PID
7504. O valor não mudou naturalmente. O PI Client permaneceu PID 9716 e o
servidor PID 7504; não houve restart durante a coleta.

A inspeção do log após a amostra Good não encontrou nova chamada SyncRead ou
Refresh do PI Client. O único `REFRESH_CALLBACK ... result=S_OK` consultado era
anterior à amostra válida e não demonstra exibição da leitura real. O screenshot
posterior mostrava o diálogo Add Item ainda aberto com `Bool_1` selecionado,
portanto era necessário confirmar **OK** antes de ler. A tela do PI Client ainda
não comprovou Value/Quality/Timestamp desta tag.

O limite dessa janela foi encerrado; `relay-read-windows.json` fecha este módulo
e o cache do destino degrada ao expirar o prazo de 30 s. Para uma nova leitura
GUI é necessária nova janela explicitamente autorizada. A evidência de API foi
salva em `.opc-bridge-management/evidence/real-bool1-destination-read.json`.

## Correção da coleta pausada — operação contínua autorizada

Em 2026-11-09, o operador autorizou explicitamente a coleta contínua somente de
`Channel_1.Device_1.Bool_1`, com intervalo configurado de 10 s, pelo agente
existente. O controle temporário anterior expirava e impedia novas leituras.
O runtime degradava a amostra em 30 s e retornava o HRESULT por item
`0x800706BA`; a conexão COM e o estado RUNNING permaneciam ativos. O erro visto
no PI Client era reproduzido pelas leituras registradas do PID 9280.

O controle privado agora também aceita um modo explícito:

```json
{
  "machine_id": "733cc9b5-7837-44f6-8ef8-a222b1ee6ddd",
  "mode": "continuous",
  "enabled": true,
  "interval_s": 10
}
```

Essa entrada pertence exclusivamente ao módulo
`33b7ba68-2e2e-42a3-bba5-0f4d17ce5fda` em `relay-read-windows.json`.
Identidade divergente, modo desconhecido e `enabled` ausente ou de tipo inválido
impedem a coleta. As janelas limitadas existentes mantêm seu comportamento.
A permissão contínua persiste após reinício do configurador. Não há novo poller:
o worker existente continua serializando os módulos. Os 10 s são o intervalo
mínimo imposto pelo controle, sujeito ao tempo de IO e às outras consultas do worker.
O banco foi encontrado na revisão 37 com intervalo de módulo de 5 s. Isso
divergia do fingerprint confirmado na revisão Windows 36 e deixou a aplicação
pendente após reinício do configurador. A comparação reproduziu exatamente o
fingerprint confirmado ao restaurar apenas o intervalo para 10 s. Foi salvo
backup SQLite consistente em
`.opc-bridge-management/backups/before-restore-authorized-interval-20261109.sqlite3`
e o intervalo foi restaurado pela API com If-Match 37, produzindo a revisão
Linux 38. O catálogo Windows continua na revisão 36; nenhuma reaplicação
ou reinicialização Windows foi feita. Item IDs, fontes, tipos e valores
configurados foram preservados. O controle aplica o maior dos dois intervalos,
portanto nunca acelera uma coleta configurada para ser mais lenta.

Foi reiniciado somente `opc-bridge-management.service` no Linux para carregar
o código. Nenhum processo Windows foi encerrado, executável substituído ou
Registro COM alterado. Rust PID **9280** e PI Client PID **6884**, ambos na
sessão **2** do RB1PI2, permaneceram ativos. O catálogo publicado não foi editado.

Três verificações completas do relay confirmaram Bool_1=false, Boolean/VT_BOOL
11, qualidade 192/Good, HRESULT 0/S_OK, pelo método `OPCGroup.SyncRead`. Os
timestamps de origem foram `17:30:38.835Z`, `17:30:52.797Z` e `17:31:06.759Z`.
O destino confirmou todas no PID 9280. Evidência privada:
`.opc-bridge-management/evidence/real-bool1-continuous-20261109.json`.

Durante a operação do PI Client, o log do mesmo processo registrou
`READ_RESULT server_handle=1 client_handle=100 value=Bool(false) quality=0xc0
filetime=134387190667590000 result=S_OK` e callbacks de Refresh com S_OK
(transactions 4 a 8). São chamadas COM reais atendidas pelo servidor;
a confirmação visual de Refresh e Sync permanece dependente do operador.

Depois da restauração do intervalo, três ciclos adicionais estabilizados
confirmaram `false`, Good/192, S_OK e timestamps `17:35:58.666Z`,
`17:36:12.129Z` e `17:36:25.452Z`. O log passou a registrar leituras repetidas
com `server_handle=1`, `client_handle=100`, Bool(false), Good e S_OK, intercaladas
com ACKs do módulo real (sequências 3 a 6). Os PIDs 9280 e 6884 continuaram
ativos. Evidências privadas adicionais:
`real-bool1-continuous-min10s-20261109.json` e
`real-bool1-continuous-server-log-20261109.txt`, dentro do diretório de evidências.

Validação automatizada: **53 testes da camada de origens aprovados** e
**168 aprovados, 1 ignorado** na suíte completa. A primeira execução completa
no sandbox falhou por bloqueio de sockets; a execução com loopback permitido
passou. Os novos testes cobrem coleta contínua, parada sem alterar o catálogo,
isolamento por módulo/máquina e rejeição de permissões malformadas. Não foi
provocada falha na origem real nem realizada escrita OPC.

### Parada e reversão

Para pausar somente esta coleta, no checkout Linux:

```bash
python scripts/control_relay_collection.py \
  --data-dir .opc-bridge-management \
  --machine-id 733cc9b5-7837-44f6-8ef8-a222b1ee6ddd \
  --module-id 33b7ba68-2e2e-42a3-bba5-0f4d17ce5fda \
  --pause
```

O comando salva backup e substitui atomicamente o controle, sem alterar o
catálogo DA. Não exige reinício do servidor ou do configurador. Uma leitura
em andamento pode terminar, mas a permissão é conferida novamente antes do
envio. O cache expira em 30 s; clientes poderão voltar a receber qualidade
ruim/erro por item. Para retomar a operação já autorizada, usar `--continuous`
no lugar de `--pause`. `--duration-s 60` permite uma janela limitada.

O controle original está preservado em
`.opc-bridge-management/backups/relay-collection-20261109T172946Z-7425816354aa439f83a969724be12ee7.json`.
Para regressar ao código anterior, pausar primeiro a coleta, restaurar a entrada
limitada original e retirar somente o suporte ao modo contínuo antes de reiniciar
o configurador. Não apagar o arquivo de controle: sua ausência elimina esse
limite opcional. Nenhuma reversão Windows é necessária nesta correção.
