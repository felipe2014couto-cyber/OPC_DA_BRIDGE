# Configurador web — laboratório

A descoberta/coleta OPC DA com agente autorizado foi acrescentada em 06/11/2026.
Consulte [OPC_DA_DISCOVERY_AND_RELAY.md](OPC_DA_DISCOVERY_AND_RELAY.md) para o fluxo
atual, requisitos e limites. As seções de simulação/OPC UA abaixo também mantêm
registros históricos de entregas anteriores.

A hierarquia é **Conexão → Máquina de destino → Módulo de origem → Pasta → Variáveis**.
A aplicação isolada por SSH ao servidor DA Windows está documentada em
[LAB_CONFIGURATION_DEPLOYMENT.md](LAB_CONFIGURATION_DEPLOYMENT.md). O painel
distingue simulação Linux, aplicação confirmada e leitura do agente Windows.
As fontes continuam simuladas; a origem OPC UA ainda não está integrada.

## Iniciar no Linux

Dependências de execução: Python 3.8 ou superior com `sqlite3`, biblioteca
padrão e um navegador moderno. Esta entrega foi executada em Python 3.14.4,
SQLite 3.46.1, Linux x86_64, no host de desenvolvimento **AUTOU3**. A sintaxe
dos novos módulos também foi conferida para Python 3.8; outros runtimes não
foram executados nesta validação.

Não é necessário instalar pacotes Python, Node, servidores de banco ou
serviços do sistema para executar a aplicação a partir do checkout:

```bash
cd /home/felipe/dev/OPC-DA-Bridge
PYTHONPATH=src python3 -m opc_da_bridge.management \
  --data-dir /tmp/opc-bridge-management-lab \
  --host 127.0.0.1 --port 8765
```

Abra **http://127.0.0.1:8765** no navegador desse Linux. `Ctrl+C` encerra o
backend. Reinicie com o **mesmo `--data-dir`** para recuperar os cadastros.
O diretório padrão, quando omitido, é `.opc-bridge-management` no diretório
atual e está ignorado pelo Git. Para conservar a configuração além da limpeza
de `/tmp`, escolha explicitamente um diretório persistente sob controle do
operador, com permissão de leitura/escrita.

O configurador está instalado como serviço de usuário no AUTOU3 conforme a
seção abaixo. Para outro Linux de execução, copie o checkout para o host
autorizado, confira Python/SQLite e adapte os caminhos e a porta antes da
instalação. O bind do backend é restrito a `127.0.0.1`; o acesso pelo IP usa
a instância autenticada do proxy.
Para usar o navegador de outra estação, um túnel SSH para o Linux escolhido
permite manter esse alcance local:

```bash
ssh -N -L 8765:127.0.0.1:8765 usuario@linux-autorizado
```

Depois abra `http://127.0.0.1:8765` na estação. Esse comando é um procedimento
manual; não instala serviço nem muda firewall. O acesso direto pelo IP agora
usa a autenticação do proxy descrito abaixo; o backend permanece local.

## Serviço no AUTOU3

Por autorização posterior, foi instalado e habilitado o serviço de usuário
**`opc-bridge-management.service`**, executado como `felipe`:

- Unidade no checkout: `deploy/systemd/opc-bridge-management.service`.
- Unidade instalada: `~/.config/systemd/user/opc-bridge-management.service`.
- Python: `/usr/bin/python3` (3.14.4 nesta máquina).
- Checkout: `~/dev/OPC-DA-Bridge`.
- Dados preservados: `~/dev/OPC-DA-Bridge/.opc-bridge-management`.
- Endereço: **http://127.0.0.1:8765**.

O systemd inicia o serviço no boot pelo `default.target` do usuário. O usuário
`felipe` já tinha `Linger=yes`, permitindo que o serviço permaneça em execução
sem uma sessão aberta. Nenhuma instalação de ferramentas foi necessária.
O serviço tem reinício automático em caso de falha, com espera de três segundos.
Cinco falhas rápidas em sessenta segundos interrompem novas tentativas, para
evitar repetição sem fim caso a configuração esteja inválida.

Comandos de operação:

```bash
systemctl --user status opc-bridge-management.service
journalctl --user -u opc-bridge-management.service -f

# Depois de atualizar o código Python do backend:
systemctl --user restart opc-bridge-management.service

# Parar ou voltar a iniciar manualmente o serviço:
systemctl --user stop opc-bridge-management.service
systemctl --user start opc-bridge-management.service
```

Os cadastros feitos pela interface são persistidos automaticamente e não
exigem reiniciar o backend. O fechamento do navegador também não encerra o
serviço. O log da aplicação continua em `management.log`; stdout/stderr e
falhas do processo ficam no journal do systemd.

A execução manual anterior (PID 2627300) foi identificada pelo comando,
usuário e diretório e encerrada normalmente com SIGINT. Antes de iniciar a
unidade, o SQLite foi copiado com a API de backup para
`.opc-bridge-management/backups/before-service-20261105T170521494296Z.sqlite3`.
O serviço foi iniciado e seu processo principal foi encerrado uma vez com
SIGKILL para testar recuperação: o systemd criou outro PID após três segundos,
o endpoint de saúde voltou a HTTP 200 e a configuração e a integridade do
banco foram preservadas. O boot automático foi configurado e conferido por
`is-enabled` e `Linger`; não foi feito reboot para esse teste.

Para retirar o início automático e voltar à execução manual, sem apagar dados:

```bash
systemctl --user disable --now opc-bridge-management-access.service opc-bridge-management.service
# Depois execute o comando manual da seção anterior usando o mesmo data-dir.
```

Em uma instalação nova no mesmo layout de diretórios, confira primeiro se já
existe uma unidade com esse nome e preserve-a antes de substituir. A unidade
do checkout usa `%h` para o diretório do usuário; adapte-a se o checkout estiver
em outro local. Em seguida, instale a unidade e use `systemctl --user
daemon-reload` e `systemctl --user enable --now opc-bridge-management.service`.
O serviço independente `opc-bridge-server.service` já existente no sistema não
foi alterado.

## Acesso HTTP pelo IP e usuário administrativo

Por solicitação explícita, o acesso da estação foi configurado em
**http://10.247.168.43:8765**, endereço da interface de rede do AUTOU3.
O navegador apresenta uma solicitação HTTP Basic: entre com **`admin`** e a
senha definida pelo operador. A senha não está neste guia, no código, no Git,
em argumentos de comandos ou nos logs. Seu hash **bcrypt, custo 10**, está em
`.opc-bridge-management/access/users.htpasswd`, com permissão `0600`.

HTTP Basic sobre HTTP **não criptografa as credenciais nem os dados**. Esse
modo foi escolhido pelo operador e deve ser usado apenas na rede confiável
do laboratório. Para tráfego protegido, use o túnel SSH ou migre o acesso
para HTTPS com certificados confiáveis. Nenhuma validação TLS foi desativada.

O fluxo deste acesso é:

```text
Navegador da estação
  → HTTP 10.247.168.43:8765 / autenticação no Nginx
  → backend 127.0.0.1:8765 / configuração e agente simulado
```

Foi utilizada uma **instância independente e sem privilégios de root** do
Nginx já instalado, executada pelo serviço de usuário
`opc-bridge-management-access.service`. Ela protege a página, arquivos
estáticos e toda a API na interface LAN. O Nginx global e seus demais sites
não foram alterados. O acesso local direto ao backend continua restrito ao
host Linux; não oferece uma segunda porta de rede sem autenticação.

Arquivos da instalação, ignorados pelo Git:

- `access/nginx.conf`: configuração da instância de acesso.
- `access/settings.json`: endereço e portas usados nesta instalação.
- `access/users.htpasswd`: usuário e hash; nunca copiar seu conteúdo para logs.
- `management.env`: origem pública exata em `OPC_BRIDGE_PUBLIC_ORIGIN`.
- `access/backups/`: cópia da unidade anterior do backend.

Esses caminhos são relativos a `.opc-bridge-management`. Os modelos sem
credenciais estão em `deploy/nginx/opc-bridge-management-access.conf.template`
e `deploy/systemd/opc-bridge-management-access.service`. O IP é uma
configuração desta instalação, não um endereço fixado no código Python.

O proxy remove `Authorization` antes de encaminhar ao backend e preserva o
`Origin` real do navegador. O backend aceita apenas sua origem pública
explicitamente configurada, além das origens locais já previstas; não confia
em cabeçalhos `Forwarded` fornecidos pelo cliente. Assim, o acesso pelo IP
não desativa a proteção das mutações contra origens de outros sites.

Comandos de operação e troca da senha, sem colocá-la na linha de comando:

```bash
systemctl --user status opc-bridge-management-access.service
journalctl --user -u opc-bridge-management-access.service -f
htpasswd -B -C 10 .opc-bridge-management/access/users.htpasswd admin
```

Não use `htpasswd -b` com senha em argumentos nem `-c` para atualizar um
arquivo de usuários existente. O Nginx lê o arquivo atualizado nas próximas
requisições. Para encerrar o acesso autenticado no navegador, feche a sessão
privativa ou encerre a sessão do navegador que guardou as credenciais HTTP.
Esse usuário pertence somente ao acesso web; não cria conta Linux/Windows ou
permissões de administração no sistema operacional.

Os dois serviços estão habilitados no boot. O proxy repete a inicialização
após cinco segundos em caso de falha, inclusive enquanto o endereço LAN
configurado ainda não estiver disponível no boot. Se o IP do AUTOU3 mudar,
atualize o endereço do Nginx e `OPC_BRIDGE_PUBLIC_ORIGIN` de forma consistente,
valide com `nginx -t` e reinicie somente os dois serviços do configurador.
Nenhuma regra de firewall foi modificada; o alcance a partir da estação
depende da conectividade da rede até esse endereço e porta.

Para voltar ao alcance somente local:

```bash
systemctl --user disable --now opc-bridge-management-access.service
```

O backend, os cadastros e o serviço original permanecem disponíveis no Linux.

Validação do acesso: requisições anônimas e senha incorreta foram recusadas
com `401`; a credencial correta abriu a interface e API pelo IP. Um navegador
real autenticado enviou uma operação JSON que chegou à validação do backend,
enquanto outra origem foi recusada com `403`. Esses testes não alteraram os
cadastros. Após esta mudança, passaram 34 testes HTTP do gerenciamento e
99 testes da suíte completa, com 1 ignorado por ausência de `clang++`.
A conexão originada da estação do operador não foi executada pelo
agente; abra o endereço acima para conferir o caminho de rede da sua estação.

## Cadastrar e organizar

1. Clique **Nova conexão** e informe o nome do agrupamento.
2. Selecione a conexão e clique **Nova máquina**. Nome e IP/hostname identificam
   o destino. Salvar não conecta; use Verificar agente e Aplicar na máquina após provisionar.
3. Clique **Novo módulo**. O endpoint completo `opc.tcp://host:porta/caminho`
   identifica a origem futura; pode ficar vazio nesta simulação.
4. Crie uma **Nova pasta** no módulo e selecione-a na árvore.
5. Crie uma variável **Double**, por exemplo `Pressao`, valor inicial `6,25`,
   origem `Simulado.Pressao`; crie também uma **Boolean**, por exemplo
   `StatusBomba`, valor inicial `True`.
6. Deixe o Item ID em branco para geração automática ou informe o identificador
   planejado. Cada gravação válida é persistida imediatamente no Linux.

Todos os níveis podem ser editados ou excluídos pelo menu de contexto da
árvore. Uma exclusão de nível superior remove também seus descendentes após
confirmação. Os formulários podem ser movidos pela barra de título, cancelados
por Escape e usados pelo teclado.

O Item ID padrão combina máquina, módulo, pasta e variável; acentos são
normalizados e separadores viram `_`. Por exemplo,
`Máquina 2 → Módulo 1 → Pasta 1 → Pressão` gera
`Maquina_2.Modulo_1.Pasta_1.Pressao`. O Item ID é armazenado no cadastro e
permanece estável ao renomear seus ancestrais; pode ser editado explicitamente
na variável. Ele deve ser único dentro de uma máquina de destino. Nomes são
únicos, sem distinção de maiúsculas/minúsculas, entre irmãos de cada nível.

Boolean exige valor booleano; Double exige número finito. Endereços não podem
conter protocolo/porta e endpoints não podem conter credenciais. Campos
desconhecidos, tipos inválidos, duplicatas e revisões desatualizadas são
recusados sem gravar parcialmente o cadastro.

## Ler e monitorar

O botão **Conectar** consulta a fonte selecionada: simulador Linux para destino sem agente, ou snapshot Windows confirmado para destino provisionado.
Na variável, use **Ler valor** para uma amostra ou **Monitorar** para consultas
periódicas ao mesmo adaptador (a chamada SSH pode levar mais de um segundo). Valor, tipo, qualidade e
timestamp de origem são retornados pelo backend; a interface não inventa uma
leitura a partir do campo de configuração inicial.

Os valores simulados permanecem constantes no valor inicial cadastrado;
estabilidade de valor não indica falha. As amostras aceitas recebem timestamp
UTC gerado pelo próprio simulador e qualidade Good (`192`). Timestamp ausente
ou inválido resulta em qualidade Uncertain (`64`) e timestamp nulo. O menu
**Comunicação** permite simular perda e retorno somente da fonte Linux: na perda, o último
valor e timestamp permanecem com Bad (`8`); o retorno exige uma nova amostra
antes de Good. Uma falha de acesso ao backend limpa as leituras atuais da
interface, evitando manter uma indicação Good como se ainda estivesse ligada.

O painel **Eventos** e `management.log` registram inicialização, cadastros,
alterações, erros, leituras manuais e mudanças da fonte simulada. Eventos
recentes são persistidos no SQLite; o log é texto UTF-8. O cache de amostras é
volátil e limitado; não é histórico, buffer de eventos nem replay.

## Separação dos componentes

| Componente | Local | Função |
|---|---|---|
| Interface | `management/static/` | Árvore, tabela, formulários, eventos e consultas ao backend |
| HTTP | `management/server.py` | API local e arquivos estáticos da interface |
| Persistência | `management/store.py` | SQLite, validação, transações, revisão e eventos |
| Adaptador local | `management/agents.py` | Fonte simulada Linux |
| Aplicação Windows | `management/deployment.py`, `windows/agent/lab_agent.ps1` | Canal SSH, identidade, validação, ACK e snapshots |
| Inicialização | `management/__main__.py` | Host/porta/diretório configuráveis, execução manual ou systemd |
| Serviço | `deploy/systemd/opc-bridge-management.service` | Início automático e recuperação do processo no AUTOU3 |
| Acesso de rede | `deploy/nginx/` e unidade `opc-bridge-management-access.service` | Proxy HTTP Basic na interface LAN; backend continua em loopback |

As mutações usam `If-Match` com a revisão recebida do backend. Isso evita
sobrescrever uma alteração feita em outra aba. A configuração fica em
`management.sqlite3`; o backend verifica esquema, integridade e dados
armazenados antes de iniciar. Um banco inválido é recusado e preservado,
sem substituição silenciosa. Para backup simples, encerre o backend e copie
seu diretório de dados.

O servidor oferece configuração, consulta de fontes simuladas e aplicação ao
agente Windows provisionado. Não oferece terminal, comandos remotos arbitrários,
driver de PLC, importação CSV ou cliente OPC UA. As tags publicadas são somente leitura.

## Aplicação Windows e próxima integração

O transporte atual é `SshLabChannel`, com verificação estrita da chave do host.
O estado depende da confirmação do agente. A última configuração válida fica no
Windows; o recibo identifica revisão, digest e catálogo. Consulte
[LAB_CONFIGURATION_DEPLOYMENT.md](LAB_CONFIGURATION_DEPLOYMENT.md) para provisionar,
aplicar, interpretar estados, testar no PI Client e reverter os dois LocalServer32.

Falta integrar o cliente UA à origem autorizada e validar segurança entre máquinas,
perda/retorno da origem e PI Interface. Windows XP/x64 e navegador Windows 7
continuam sem validação nesta entrega.

## Testes

Testes Python, incluindo HTTP real em portas efêmeras de loopback:

```bash
python3 -m pytest -q tests/test_management.py
python3 -m pytest -q
```

O teste visual cria seu próprio backend e diretório temporário, abre Chromium,
cadastra a hierarquia pela UI, lê e monitora, verifica os formulários
arrastáveis, reinicia o backend para conferir persistência e exercita edição
e exclusão. Não aponta para uma configuração existente:

```bash
# Ferramentas opcionais de teste, fora do checkout; sem baixar navegadores:
npm install --prefix /tmp/opc-management-browser-tools --ignore-scripts \
  --omit=optional --no-audit --no-fund --save-exact playwright@1.58.2

NODE_PATH=/tmp/opc-management-browser-tools/node_modules \
OPC_BROWSER_EXECUTABLE=/caminho/para/chromium \
  node scripts/test_management_browser.cjs
```

O runner informa a versão do navegador, resultados, diretório temporário e
caminho da captura de tela. Use um Chromium existente e compatível; não há
instalação de navegador automática pela aplicação.

## Componentes e licenças

| Componente | Versão usada | Origem/licença | Distribuição e obrigações |
|---|---|---|---|
| CPython | 3.14.4 | [python.org](https://docs.python.org/3/license.html), PSF-2.0 e avisos incorporados | Runtime já disponível; conservar avisos se redistribuído |
| SQLite | 3.46.1 | [sqlite.org](https://sqlite.org/copyright.html), domínio público | Biblioteca já fornecida pelo Python; não instalamos servidor de banco |
| HTML/CSS/JavaScript | Fontes do projeto | Sem bibliotecas ou assets externos | Nenhum pacote de execução frontend |
| pytest | 9.0.2 | [pytest-dev](https://github.com/pytest-dev/pytest), MIT | Ferramenta de teste já disponível; avisos MIT em redistribuição |
| Playwright + playwright-core | 1.58.2 | [Microsoft/playwright](https://github.com/microsoft/playwright/tree/v1.58.2), Apache-2.0 | Apenas teste em `/tmp`; preservar licença/avisos e identificar modificações em redistribuição |
| Node.js | 22.22.1 | [nodejs/node](https://github.com/nodejs/node), MIT e avisos de terceiros | Somente testes; runtime já disponível |
| Nginx | 1.28.3 (Ubuntu) | [nginx.org](https://nginx.org/), BSD-2-Clause e avisos dos componentes da distribuição | Já instalado; instância de usuário separada, sem modificar sites globais |
| htpasswd / apache2-utils | 2.4.66 | [Apache HTTP Server](https://httpd.apache.org/), Apache-2.0 e avisos da distribuição | Já instalado; geração interativa do hash bcrypt, sem senha em argumentos |
| Chrome Headless Shell | 156.0.8078.4 | Licença BSD do Chromium e avisos de terceiros em `LICENSE.headless_shell` da distribuição existente | Navegador já disponível, não incorporado nem redistribuído pelo projeto; o nome da versão exibido pelo binário é Google Chrome for Testing |

Os pacotes Playwright foram obtidos do registro oficial npm, com scripts de
instalação desabilitados e SHA-512 verificado pelo npm. O pacote opcional
`fsevents` (macOS) não foi instalado. Os hashes oficiais consultados foram:

```text
playwright@1.58.2
sha512-vA30H8Nvkq/cPBnNw4Q8TWz1EJyqgpuinBcHET0YVJVFldr8JDNiU9LaWAE1KqSkRYazuaBhTpB5ZzShOezQ6A==
playwright-core@1.58.2
sha512-yZkEtftgwS8CsfYo7nm0KE8jsvm6i/PTgVtB8DL726wNf6H2IMsDuxCpJj59KDaxCtSnrWan2AeDqM7JBaultg==
```

As dúvidas de licença dos IDLs/bindings OPC Foundation do protótipo Rust
continuam documentadas em `RUST_DA_COM_LAB.md`. O gerenciamento web não os
importa, incorpora ou distribui. O campo de licença do projeto existente em
`pyproject.toml` foi preservado; a lista acima descreve as dependências e não
altera a licença do código do projeto.

## Resultados históricos da primeira etapa web (antes da aplicação Windows)

- **34 testes do gerenciamento** aprovados com HTTP real em loopback: CRUD,
  validação, concorrência, persistência/reinício, configuração inválida,
  simulador, qualidade/timestamp, eventos, limites HTTP e origem do proxy.
- **Suíte completa após o acesso HTTP: 99 aprovados, 1 ignorado**. O teste C++ parametrizado para
  `clang++` foi ignorado porque esse compilador não está instalado; a variante
  `g++` passou. Nenhum teste COM/PI desta etapa web foi executado ou afirmado.
- **Navegador real em Linux:** criação da hierarquia e de Boolean/Double pelos
  formulários, leitura, monitoramento, Good → Bad → Good, movimentação do
  formulário, edição, rejeição de duplicata e exclusão de todos os níveis
  passaram. O backend foi encerrado e reiniciado usando o mesmo SQLite; os
  cadastros e Item IDs foram preservados. Nenhum erro JavaScript foi observado.
- A captura da execução é gerada pelo runner no diretório temporário informado
  em `SCREENSHOT=...`; ela mostra as leituras explicitamente simuladas e o
  estado de aplicação Windows pendente.
- Na validação inicial, o gerenciamento foi iniciado manualmente no AUTOU3 em
  `http://127.0.0.1:8765`, com dados em `.opc-bridge-management` do checkout.
  Posteriormente foram habilitados os dois serviços documentados acima.
  O backend continua em loopback e o proxy oferece acesso HTTP autenticado
  na interface LAN configurada.
- As consultas de fechamento da etapa anterior confirmaram que os dois
  `LocalServer32` da RB1PI2 continuam em `shutdown-build1`. Nenhum Registro,
  serviço, DCOM ou firewall do Windows foi modificado nesta etapa web.

Os resultados e as alterações posteriores de aplicação Windows estão no
[relatório de aplicação](LAB_CONFIGURATION_DEPLOYMENT.md); substituem os limites
da primeira etapa descritos na seção histórica acima.
