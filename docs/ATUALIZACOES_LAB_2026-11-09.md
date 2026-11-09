# OPC DA Bridge — atualizações do laboratório em 09/11/2026

## Escopo desta entrega

Esta entrega reúne o configurador web persistente, agentes Windows somente leitura,
relay de amostras e protótipo Rust do servidor OPC DA. O fluxo de laboratório é:

```text
KepServerEx OPC DA (origem de laboratório)
  → agente Windows / OPCGroup.SyncRead
  → relay Linux / SSH autenticado com identidade do destino validada
  → cache do servidor Rust OPC DA no Windows 7 SP1 x86
  → PI OPC Client e dashboard
```

O snapshot HTTP consulta o cache; não representa uma nova leitura OPC DA.
O caminho OPC UA possui um módulo de laboratório separado e não participa deste
fluxo validado. Não houve conexão ao ABB, RSLinx ou PI Interface nesta etapa.

## Funcionalidades reunidas

- Configurador com conexão, máquina, módulo, pasta, variáveis e persistência SQLite.
- Aplicação por máquina, validação de identidade, revisão e confirmação do runtime.
- Alterações apenas de valores simulados sem reiniciar o servidor, preservando
  grupos e handles. Mudanças estruturais seguem um fluxo distinto e podem exigir
  reinício/reconexão; não são anunciadas como atualização a quente.
- Origem OPC DA com Item IDs independentes dos Item IDs publicados no destino.
- Valores, tipos, qualidade, timestamps e erros por item; ausência de timestamp
  não é substituída silenciosamente pela hora atual.
- Browse hierárquico e Flat, leitura síncrona, Refresh2, leitura assíncrona DA 2
  e callbacks de alteração no protótipo. Escritas são recusadas.
- Estado de comunicação e degradação de qualidade quando a fonte fica indisponível.
  O cache de último valor não oferece histórico nem recuperação integral de eventos.
- Templates para execução persistente do configurador Linux e acesso autenticado.
  Credenciais e arquivos específicos da instalação não acompanham o código.

## Redução do intervalo do relay

O intervalo configurado passa a contar entre inícios de ciclos, sem sobreposição,
em vez de adicionar uma pausa completa após toda a comunicação. O transporte
opcional `ssh_stdin_v1` envia telemetria em uma única sessão SSH, com JSON limitado
em tamanho e validado pelo agente. O fluxo anterior por SCP permanece disponível.
Um destino acessível cujo runtime ainda não iniciou usa um estado de espera
específico, sem declarar aplicação ou conexão confirmada.

| Medição registrada no laboratório | Resultado |
| --- | --- |
| Intervalo médio anterior entre confirmações | 14,729 s |
| Intervalo médio posterior, cinco intervalos | 5,282 s |
| Faixa dos intervalos posteriores | 4,321–6,341 s |
| Redução observada do intervalo médio | aproximadamente 64% |
| Processamento por ciclo após a mudança | 3,744–4,839 s |

Esses números medem ciclos com o servidor em execução. Não são garantia de latência
nem uma nova medição do tempo de primeira ativação COM. O PID de destino observado
permaneceu 2324 durante a aplicação da revisão 33; o PI Client permaneceu aberto.
Não foi necessário trocar o executável Rust ou o Registro para essa otimização.

## Evidências e limites

### Verificação executada para esta publicação

```bash
PYTHONPATH=src pytest -q
```

Resultado: **155 aprovados, 1 ignorado**, em 10,78 segundos. Os testes que abrem
sockets de loopback precisaram ser executados com essa permissão do ambiente.
Nenhuma máquina Windows foi acessada novamente para preparar esta publicação.

### Validações Windows registradas nas etapas anteriores

- Compilação cruzada Linux para PE32 x86 e execução no Windows 7 SP1 x86.
- Modelo de projeto: 15 testes; harness nativo Windows: 5 testes.
- Cliente COM entre processos: Sync Cache/Device, Refresh Cache/Device,
  AsyncRead, erros parciais, cancelamento e notificações de alteração.
- Fixture separada: perda/retorno da fonte e qualidade Good → Bad → Good.
- Origem Kepware: leitura OPC DA real por COM, com conversão Float → Double
  explicitamente autorizada; a variável Boolean conserva seu tipo.
- Amostras observadas no relay/destino: Double 1,0 e Boolean true,
  qualidade 192/Good, HRESULT S_OK e timestamp de origem válido.
- Na GUI do PI OPC Client, o usuário mostrou a tag numérica com valor 1,0,
  qualidade Good, tipo VT_R8 e timestamp após Refresh.

Os testes COM automatizados não comprovam, sozinhos, cada botão da GUI.
Sync/Async/Advise e a variável Boolean ainda precisam de confirmação visual
específica no cenário final. A API não equivale a validação visual do dashboard.
A política UTC para datas Automation sem fuso foi validada especificamente nessa
origem; a configuração geral continua exigindo uma política explícita.

## Componentes e licenciamento

| Componente | Versão/revisão documentada | Situação |
| --- | --- | --- |
| Rust | nightly-2026-11-03 | MIT OR Apache-2.0; ambiente isolado |
| MinGW-w64 | 13.0.0 | Avisos/licenças por componente; compilação x86 GNU |
| windows / windows-core | 0.62.2 | MIT OR Apache-2.0 |
| Ronbb/rust_opc | da44a99265f9706618bbe506c98f714f2aa46f0b | Wrapper e materiais terceiros exigem análises distintas |
| Modelo Rust e projeto independentes | 0.1.0 | MIT, conforme LICENSE de cada crate |
| asyncua, módulo DA → UA separado | 1.1.8 | LGPL-3.0; verificar obrigações antes de redistribuir |
| pywin32, módulo Windows Python | 312 | PSF; dependência Windows separada |
| Softing Classic SDK, protótipo C# preservado | 08f4b3e968acfce4b775299741cef1819e24fda1 | MIT no repositório; componentes externos possuem termos próprios |

A licença do projeto principal permanece a declarada no `pyproject.toml`;
esta entrega não relicencia o conjunto. Windows, KepServerEx e PI OPC Client
são componentes proprietários preexistentes do laboratório.

**Não estão incluídos IDLs OPC Foundation, bindings gerados de terceiros,
SDKs vendorizados ou binários.** O adaptador referencia uma revisão externa e os
scripts de laboratório trabalham sobre uma cópia temporária. Essas referências
não resolvem a dúvida sobre os direitos de uso/redistribuição de IDLs e bindings.
Não distribuir executáveis que os incorporem até esclarecer os termos aplicáveis.
Consultar [viabilidade e procedência](RUST_DA_POC_FEASIBILITY.md).

## Instalação, preservação e reversão

- [Configurador web](WEB_CONFIGURATOR.md).
- [Aplicação de configuração](LAB_CONFIGURATION_DEPLOYMENT.md).
- [Descoberta e relay](OPC_DA_DISCOVERY_AND_RELAY.md).
- [Servidor Rust e interfaces](RUST_DA_COM_LAB.md).
- [Procedimento PI OPC Client](PI_OPC_CLIENT_TEST.md).
- [Laboratório DA → UA separado](LAB_DA_TO_UA.md).

A publicação contém código, templates, exemplos e relatórios; não contém bancos
SQLite, perfis de agentes, segredos, certificados privados, logs ou builds.
Os backups privados e executáveis anteriores permanecem no ambiente de laboratório.
Publicar no GitHub não instala nem atualiza o servidor Windows.

Para desfazer apenas a otimização de transporte, remover `runtime_transport` do
perfil privado restaura o caminho SCP; a versão anterior do agente foi preservada.
A frequência pode voltar ao valor anterior por uma nova revisão aplicada e confirmada.
Não restaurar um banco antigo sobre edições posteriores. Mudanças de executável e
LocalServer32 possuem procedimentos próprios de backup/reversão nos guias.

## Pendências

Confirmar visualmente os demais modos de leitura no PI Client, medir estabilidade
prolongada e resolver os termos dos materiais OPC Foundation antes de distribuir
binários. Integração OPC UA → DA, PI Interface, Windows XP, execução COM x64 e uso
em produção continuam sem validação específica.
