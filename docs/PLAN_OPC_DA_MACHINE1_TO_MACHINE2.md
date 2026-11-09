# Plano: dados OPC DA da máquina 1 para o servidor DA da máquina 2

Documento de contexto para planejamento. **Nesta etapa, não acessar, conectar,
instalar, compilar, copiar arquivos nem alterar qualquer configuração na
máquina 1.** A integração real ainda não foi implementada. Não tratar este
documento como autorização para mudanças em ABB, DCOM, firewall, serviços ou
produção.

## Objetivo

Planejar o caminho de dados:

```text
Servidor OPC DA ABB (máquina 1)
  → cliente OPC DA local (máquina 1)
  → servidor OPC UA seguro (máquina 1)
  → cliente OPC UA (máquina 2)
  → servidor OPC DA espelho Rust (máquina 2)
  → cliente OPC DA / PI OPC Client
```

O PI OPC Client é o cliente de laboratório usado para conferir o servidor DA
espelho. A integração futura com PI Interface e o uso de dados de produção
exigem validação e autorização separadas.

## Ambiente conhecido

| Papel | Identidade conhecida | Estado e limites |
|---|---|---|
| Máquina 1 — origem | `PIMS_TD1_PB2`, `10.247.144.28`, Windows 10 Enterprise x64 | ABB `ABB.AfwOpcDaSurrogate.1`; a interface `OPC.Automation.1` foi encontrada na view COM x86. O laboratório anterior usou CPython 3.13.17 x86 e `OPCItem.Read(2)` individual. Máquina em coleta: somente leitura, sem instalação, build, cópia ou alteração. |
| Máquina 2 — destino | `RB1PI2`, `10.247.140.166`, Windows 7 SP1 x86 | Agente de configuração e servidor OPC DA Rust de laboratório; PI OPC Client 1.4.0.0 instalado. O cadastro do configurador continua chamado `RB1PI1`; o IP foi confirmado como hostname `RB1PI2`. Preservar os nomes e Item IDs cadastrados. |
| Desenvolvimento | Checkout Linux/AUTOU3 `/home/felipe/dev/OPC-DA-Bridge` | Builds Rust x86 são cruzados com toolchain isolado. Não usar a máquina 1 como build host. |

### Tags de origem DA conhecidas

- `Pims_A10L:ToPims.M120_NIVEL_DETERGENTE`
- `Pims_A40:gUsw.ToPims.RODANDO_ENROLADOR_SUC`

O tipo deve ser lido do `VARIANT` realmente retornado e registrado; não inferir
`Float`/`Boolean` a partir do nome. Registrar por item o HRESULT, tipo COM,
valor, qualidade e timestamp retornados. Não gravar na origem.

## O que já foi validado

- Na máquina 1, em uma validação anterior e separada, a leitura DA individual
  das duas tags e sua publicação em um endpoint UA local foram observadas. O
  endpoint `NoSecurity` ficou restrito a `127.0.0.1` e foi encerrado ao final.
  Isso não valida conexão UA segura entre máquinas, reconexão após falha real,
  longa duração nem a leitura atual da origem.
- Na máquina 2, o servidor OPC DA Rust foi encontrado pelo PI OPC Client e o
  browse, a adição de itens e a leitura Sync/Refresh das variáveis simuladas
  foram testados.
- Em 06/11/2026, a revisão simulada 18 foi aplicada sem reiniciar o processo do
  servidor. O PI OPC Client permaneceu conectado; o servidor manteve PID 5780,
  Group1 e os handles de item existentes. Refresh e Sync retornaram o novo
  valor `7.0` para TESTE2 com Good e HRESULT `S_OK`; o usuário confirmou Value
  `7` e Quality `Good` na GUI. Sync retornou `FILETIME`, embora não aparecesse
  coluna Timestamp na captura da tabela do cliente.
- O estado acima prova atualização de configuração/valor **simulado** no
  destino. Não prova cliente OPC UA na máquina 2 nem fluxo de dados ABB→PI.

## Componentes existentes e limite da integração

- O gateway DA→UA de laboratório está isolado em `src/opc_da_bridge/lab`.
  Usa COM Automation somente para leitura e `asyncua==1.1.8` para UA. O perfil
  local NoSecurity só pode bindar em loopback.
- `lab-config.secure.example.json` documenta uma configuração remota preparada
  com `Basic256Sha256` + `SignAndEncrypt`, certificados X.509 e confiança
  explícita. Essa configuração ainda **não foi validada entre as máquinas**.
- O agente Linux→Windows aplica configurações de tags simuladas via SSH
  autenticado, com verificação estrita da chave de host. Esse canal transporta
  configuração, não valores periódicos da origem ABB.
- O servidor Rust DA da máquina 2 expõe o catálogo aplicado, leitura,
  qualidade/timestamp e atualização de valores simulados. Ainda falta integrar
  ao processo o cliente OPC UA que receberá os dados reais da máquina 1.
- A máquina 2 é Windows 7 SP1 x86, sistema legado. A biblioteca cliente UA,
  runtime criptográfico e algoritmos/certificados compatíveis devem ser
  avaliados especificamente para esse SO e arquitetura. Nenhum suporte XP,
  compatibilidade futura ou interoperação entre máquinas pode ser presumido.
- Preferir componentes open source; antes de incorporar novas dependências,
  registrar versão, origem oficial, licença, dependências transitivas,
  compatibilidade e evidências de build/teste. As dúvidas de licença dos IDLs
  OPC Foundation/bindings do servidor Rust permanecem documentadas; não copiar
  nem redistribuir esses arquivos sem esclarecer os termos aplicáveis.

## Plano de trabalho proposto

### 1. Congelar requisitos e inventário

1. Confirmar que as duas tags acima continuam autorizadas para leitura e que
   nenhum outro Item ID será consultado.
2. Definir intervalo de polling DA e publicação UA; registrar timeout,
   orçamento de reconexão e nível máximo aceitável de atraso.
3. Confirmar o comportamento esperado quando a origem perde conexão: nenhum
   dado antigo pode continuar Good; timestamp ausente/inválido não deve virar
   horário atual; o cache não é histórico nem replay.
4. Antes de qualquer teste futuro, verificar identidade, versão/arquitetura,
   processos e endpoint já ativos em cada host. Fazer leituras somente; nenhuma
   alteração no ABB ou no Windows da máquina 1.

### 2. Compatibilidade e desenho de segurança

1. Escolher o cliente OPC UA para Windows 7 SP1 x86 após testar versão de
   Python/runtime, arquitetura, criptografia, dependências e licenças. Não
   escolher apenas com base em requisitos genéricos do pacote.
2. Definir endereço/porta de rede e nome estável para o endpoint UA. O
   certificado do servidor deve corresponder a `ApplicationUri` e DNS/IP
   realmente usados pelo cliente.
3. Usar política e modo autenticados/criptografados comuns aos dois stacks,
   com certificados públicos trocados e conferidos por fingerprint em canal
   independente. Configurar trust lists manualmente; rejeitar certificados
   desconhecidos. Nunca desativar validação, usar “trust all” ou expor
   NoSecurity na rede.
4. Não armazenar chave privada, senha ou segredo no Git, na configuração de
   exemplo ou nos logs. Restringir ACLs dos arquivos locais de chave.
5. Levantar, sem alterar, o caminho de rede e os requisitos de firewall/ACL.
   Se houver bloqueio, preparar proposta com portas, origem/destino, impacto
   e reversão para autorização da equipe responsável; não modificar firewall,
   DCOM, serviços ou ABB automaticamente.

### 3. Ensaio seguro DA→UA na máquina 1

Após autorização específica para executar o ensaio, usar primeiro uma
configuração isolada e local. Fazer leitura COM individual somente leitura das
duas tags e verificar:

- Tipo COM observado e regra explícita de conversão para tipo UA.
- Valor, qualidade DA, HRESULT, timestamp DA e timestamp UA `SourceTimestamp`.
- Qualidade ruim na desconexão; nunca apresentar último valor como Good.
- Timestamp inválido/ausente publicado como ausente, sem preenchimento pelo
  relógio do gateway.
- Logs em texto sem credenciais e sem cópia de dados reais desnecessários.
- Encerramento limpo do processo do gateway sem parar/reiniciar ABB.

Somente depois de validar o perfil seguro local, iniciar endpoint UA de rede
com segurança habilitada e testar a conexão a partir da máquina 2. Não migrar
para NoSecurity como contorno de incompatibilidade.

### 4. Integrar o cliente UA ao servidor DA da máquina 2

O cliente UA deve validar certificado do servidor, identidade da aplicação e
NodeIds configurados; subscrever ou ler no intervalo definido; preservar valor,
tipo, StatusCode e `SourceTimestamp`; mapear qualidade sem transformar Bad em
Good; e publicar cada revisão de runtime sem reiniciar o servidor DA. A
comunicação com o configurador deve permanecer opcional para o runtime, de
modo que queda do Linux não pare o espelho.

Definir antes do build os limites para reconexão, expiração de amostra,
reinscrição e atualização estrutural. Mudanças de valor podem usar o caminho
hot-update já validado. Inclusão, remoção, renomeação ou mudança de tipo devem
ser recusadas ou sinalizadas como exigindo reconexão/reinício, até que a
preservação de grupos e handles seja especificamente implementada e testada.

### 5. Matriz de validação antes de qualquer uso operacional

| Camada | Teste mínimo | Evidência exigida |
|---|---|---|
| Origem DA (máquina 1) | Leitura individual das duas tags; zero chamadas de escrita | Item ID, PID/ProgID, HRESULT, tipo `VARIANT`, valor, qualidade e timestamp; log sem segredo |
| Publicação UA (máquina 1) | Cliente local seguro lê os dois nós | Endpoint, NodeId, DataType, valor, StatusCode, `SourceTimestamp`; cadeia de certificado verificada |
| Rede segura M1→M2 | Cliente UA na máquina 2 confia no servidor e conecta com SignAndEncrypt | Resultado de validação de certificados e leitura por tag; conexão rejeitada para certificado não confiável |
| Espelho DA (máquina 2) | PI OPC Client faz browse, AddItems, Sync e Refresh | Item IDs, tipos, valores, qualidade, timestamps/HRESULT por item, PID/grupo/handles antes e depois |
| Falhas | Queda de rede/UA sem interromper ABB, retorno e nova amostra | Qualidade Bad durante indisponibilidade; recuperação Good somente após amostra válida; nenhum dado histórico presumido |
| Estabilidade | Execução prolongada com carga mínima | Duração efetiva, reconnects, memória/CPU, erros e logs; não reiniciar ABB para provocar falha |

Manter os testes simulados separados dos testes ABB, dos testes UA entre
máquinas e da GUI PI. Um sucesso numa camada não comprova as demais. Não testar
PI Interface nesta fase sem escopo e autorização próprios.

## Decisões que o próximo prompt deve pedir

1. A janela autorizada para conectar somente em leitura ao ABB e a pessoa
   responsável por observar/autorizar a sessão na máquina 1.
2. Endpoint UA alvo (DNS/IP, porta e caminho), responsável pelo certificado e
   política criptográfica admitida pela TI.
3. Intervalo de leitura/publicação, tolerância para amostra stale e estratégia
   de reconexão.
4. Qual cliente UA de código aberto pode ser instalado/testado no Windows 7
   x86 e quais versões/runtimes já existem na máquina 2.
5. Critérios de aceite para qualidade, timestamps, erro de certificado,
   estabilidade e reconexão.

Até essas decisões e permissões, limitar-se a planejamento, revisão de código
e testes com valores simulados. Não acessar a máquina 1.
