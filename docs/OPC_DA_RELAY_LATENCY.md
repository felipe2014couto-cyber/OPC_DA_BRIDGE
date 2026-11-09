# Relay OPC DA: medição de latência em laboratório

## Escopo

Em 9 de novembro de 2026 foram comparados intervalos de 1 s e 500 ms entre
leituras de duas variáveis de teste (uma numérica Float convertida para
Double e uma Boolean) e a confirmação do lote pelo servidor DA de destino.
As chamadas da origem foram `OPCGroup.SyncRead`, com HRESULT `S_OK` e
qualidade Good. A conversão Float→Double foi explícita; a qualidade e o
timestamp de origem foram preservados.

Não foram escritos valores, alterados catálogos, trocados registros COM nem
modificados serviços, DCOM ou firewall. Cada ensaio pausou só o poller de
teste, usou um único ciclo serial e restaurou o controle original. A coleta
normal continuou com seus intervalos conservadores; nenhum novo ciclo
contínuo de 500 ms ficou ativo.

## Resultados

`source_ms` é o tempo da leitura/conversão da origem com sessão aberta;
`delivery_ms` cobre transporte e confirmação do lote pelo destino. `total_ms`
é a soma sequencial. A abertura de sessões frias é relatada à parte.

| Intervalo-alvo | Duração | Ciclos | Origem média / p95 / máx. | Entrega média / p95 / máx. | Total média / p95 / máx. | Intervalo de confirmação média / p95 / máx. |
|---|---:|---:|---:|---:|---:|---:|
| 1 s | 60 s | 60 | 93 / 108 / 116 ms | 44 / 69 / 377 ms | 137 / 173 / 476 ms | 1.000 / 1.061 / 1.307 s |
| 500 ms | 60 s | 120 | 97 / 111 / 113 ms | 51 / 71 / 352 ms | 148 / 180 / 437 ms | 500 / 562 / 815 ms |
| 500 ms, cliente de teste aberto | 120 s | 240 | 95 / 112 / 127 ms | 51 / 99 / 351 ms | 146 / 197 / 454 ms | 500 / 577 / 827 ms |

Nos 420 ciclos rápidos não houve slots omitidos, ciclos sobrepostos, amostras
degradadas ou erros. O teste com cliente aberto manteve a mesma identidade do
processo de destino. Refresh e Sync mostraram os valores e qualidade Good,
conforme confirmação manual. A taxa solicitada pelo grupo não foi registrada;
logo, não se mediu a latência visual nem se comprovou uma taxa de grupo de
500 ms.

Algumas confirmações do destino demoraram mais que o intervalo-alvo (máximo
827 ms a 500 ms). Isso comprova a operação rápida observada, mas não garante
um limite superior de 500 ms nem cadência sem jitter. Sessões frias demoraram
cerca de 0,96–0,99 s na origem e 1,16–1,18 s no destino; a primeira amostra
após reinício pode ultrapassar um segundo.

O cache atual não é histórico e não recupera eventos ocorridos durante uma
interrupção. A medição não calculou CPU média nem crescimento de memória e
não caracteriza operação prolongada.

## Ferramentas e comportamento

- `scripts/benchmark_relay_stream.py` executa uma medição opt-in de 1–2
  minutos em 1 s ou 500 ms, serializa ciclos, usa deadlines monotônicos,
  recusa rajadas de recuperação e restaura o controle do poller ao terminar.
  Exige artefatos já verificados, módulo de teste e destino confirmado.
- `scripts/measure_relay_timing.py` observa o relay existente pela API sem
  iniciar poller adicional. Pode perder estados intermediários; não é uma
  medida do instante exato da amostragem.
- O transporte opt-in `ssh_stream_v1` mantém um canal SSH autenticado por
  destino, usa mensagens JSON delimitadas, serialização de um pedido por vez,
  limite de tamanho e ausência de retry automático após confirmação ambígua.
  O transporte padrão anterior permanece disponível.
- O agente Rust novo não contém IDL nem bindings OPC. O worker PowerShell que
  mantém COM na origem é um artefato privado do laboratório e não faz parte
  desta publicação; o canal só funciona onde o artefato autorizado já foi
  provisionado.

O build x86 para o Windows legado de teste foi feito em Linux com Rust
nightly `2026-11-03` e MinGW já existente. O alvo Rust é Tier 3 e requer
compilar a biblioteca padrão. As APIs usadas exigem um Windows moderno para
o qual a função de caminho de processo esteja disponível. O binário é
PE32 x86 e importa apenas `ADVAPI32`, `KERNEL32`, `msvcrt`, `ntdll`, `USERENV`
e `WS2_32`.

O crate depende de `serde_json 1.0.151`; dependências ativas travadas:
`serde_core 1.0.229`, `itoa 1.0.18`, `memchr 2.8.3` e `zmij 1.0.23`.
`serde_json`, `serde_core`, `itoa` e `zmij` usam MIT; `memchr` oferece
Unlicense ou MIT. Os avisos das dependências e a licença MIT do crate estão
incluídos. OPC Automation e o runtime do Windows são componentes
preexistentes, não ferramentas open source fornecidas pelo agente. O build
não incorpora bindings da OPC Foundation; as dúvidas de licença existentes
do servidor COM permanecem separadas.

## Reproduzir

Com o toolchain isolado já provisionado no ambiente de desenvolvimento:

```sh
PYTHONPATH=src python -m pytest \
  tests/test_management.py tests/test_management_sources.py \
  tests/test_management_deployment.py tests/test_ssh_stream.py \
  tests/test_relay_timing_observer.py tests/test_source_stream_transport.py -q

OPC_RUST_TOOLCHAIN_ROOT="$HOME/.cache/opc-rust-toolchain" bash scripts/build_rust_telemetry_agent.sh
PYTHONPATH=src python scripts/benchmark_relay_stream.py --help
PYTHONPATH=src python scripts/measure_relay_timing.py --help
```

Os benchmarks exigem opções explícitas para máquina, módulo, artefatos,
duração e caminho de relatório novo. As evidências brutas, perfis, chaves e
configurações privadas ficam fora do Git. Não reutilize uma saída anterior nem
execute benchmarks contra uma origem ou destino sem autorização explícita.

## Reversão e limites

Não houve alteração permanente no intervalo normal. Para desativar o
transporte de sessão, remova o opt-in `runtime_transport` ou
`read_session_transport` da configuração privada e deixe o canal padrão em
uso; preserve as configurações e chaves SSH. O agente não foi instalado como
serviço e não foi registrado como COM.

O teste cobriu variáveis simuladas e confirmação do cliente por Refresh/Sync.
Não incluiu notificação `Advise`, dados de processo, PI Interface, perda de
rede real, operação prolongada ou medição visual automatizada. Antes de
reduzir a coleta normal para 500 ms, falta uma observação mais longa que
registre a taxa do grupo, jitter e tendências de recursos.
