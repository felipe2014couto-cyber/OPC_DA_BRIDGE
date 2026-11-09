# Investigação de viabilidade: servidor OPC DA em Rust no Windows 7 x86

Data da verificação: 2026-11-04. Esta nota registra uma avaliação e um teste mínimo de toolchain. Ela **não** implementa nem valida um servidor OPC DA.

## Checkout preservado e ambientes

O checkout de trabalho estava no commit `382fae92c17d25a9b672729197b9b80f5cbe6a6e` e já continha alterações locais em `README.md`, `pyproject.toml`, `.offline/`, `docs/LAB_DA_TO_UA.md`, arquivos de configuração, `src/opc_da_bridge/lab/`, `tests/test_lab_gateway.py` e `windows/softing_mirror_server_csharp/`. Elas foram preservadas. Esta avaliação só adiciona este documento.

O ambiente remoto consultado foi `RB1PI2`, Windows 7 Professional SP1 (`6.1.7601`), 32 bits. Na consulta inicial, havia 2,20 GB livres em C: e 14,41 GB em D:. Não foram encontrados `rustc`, `cargo`, `gcc`, `clang`, `cmake` ou `msbuild` no PATH. A máquina 1 não foi acessada nem modificada.

## Biblioteca examinada

- Projeto: [Ronbb/rust_opc](https://github.com/Ronbb/rust_opc)
- Commit fixado e analisado: `da44a99265f9706618bbe506c98f714f2aa46f0b`
- `rust-toolchain.toml`: Rust `1.97.1`; workspace: `resolver = "3"`, Edition 2024, `rust-version = "1.97"`.
- Sem release marcada identificada na revisão; fonte consultada no commit acima.

O código contém adaptadores COM de servidor, mas não constitui um servidor completo pronto para uso:

| Área | Situação na revisão |
|---|---|
| COM apartment, class factory e registro de servidor local | Helpers disponíveis; a aplicação ainda precisa compor o objeto, definir CLSID e executar o ciclo de vida. `make_server()` no exemplo é `unimplemented!()`. |
| `IOPCServer` | `AddGroup`, `GetErrorString`, `GetGroupByName`, `GetStatus` e `RemoveGroup` encaminham para serviços. `CreateGroupEnumerator` retorna `E_NOTIMPL`. |
| Grupo e gestão de itens | `IOPCItemMgt` tem Add/Validate/Remove/Active/ClientHandles/DataTypes; `CreateEnumerator` retorna `E_NOTIMPL`. `IOPCGroupStateMgt` oferece Get/Set/SetName; `CloneGroup` retorna `E_NOTIMPL`. |
| Leitura/escrita síncrona | `IOPCSyncIO::Read` e `Write` encaminham para o serviço do aplicativo. A política somente leitura teria de ser imposta pelo serviço, retornando erro por item em escrita. |
| Browse | `IOPCBrowseServerAddressSpace`, `IOPCBrowse` e propriedades aparecem nas bindings, mas não no adaptador de servidor implementado. |
| Assinatura/notificações | Não há adaptador de servidor `IOPCAsyncIO*`, connection point/`IConnectionPointContainer` nem emissão por `IOPCDataCallback`. Inscrição/notificação não está implementada. |
| Teste real do servidor | O teste de integração ignorado exige um ProgID de servidor externo já registrado (`OPCLabs.KitServer.2`); não demonstra que a implementação do servidor da biblioteca funciona. Há testes unitários de algumas rotinas do adaptador, mas não teste COM de um servidor Rust próprio. |

Portanto, browse e subscriptions requerem novas interfaces COM e uma camada de estado/callbacks além das implementações já existentes. `IOPCSyncIO` poderia servir a uma primeira prova limitada de leitura se o restante da ativação COM fosse implementado e validado.

## Toolchain e tentativa no Windows 7

O Rust atual declara Windows 10 como baseline para os targets GNU/MSVC `*-pc-windows-*`. O target explícito `i686-win7-windows-gnu` existe como Tier 3, mas não tem `std` pré-compilada: é necessário construir o target ou usar `build-std`. Isso não equivale a suporte testado pelo projeto Rust.

O pacote oficial arquivado Rust 1.77.2 `i686-pc-windows-gnu` foi usado somente para um smoke test porque é anterior à mudança de baseline e executa no Windows 7. Origem: [arquivo oficial de instaladores Rust](https://forge.rust-lang.org/infra/archive-stable-version-installers.html). O MSI de 247,3 MiB foi baixado por HTTPS com validação TLS, sua assinatura GPG detached foi aceita, e seu SHA-256 foi conferido antes da transferência:

```text
8fccfb8171a81dec12841e8cadb480fe26436d3e4be2d769adf64eb448e645a5
```

A assinatura reportou `Good signature` para `Rust Language (Tag and Release Signing Key)`, fingerprint primário `108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE`; o GPG local também avisou que a chave não estava certificada no seu trust database. O hash foi confirmado de novo na RB1PI2 com `certutil`.

Foi feita extração administrativa do MSI para `D:\OPCB\rust177\image` (não houve instalação de produto, alteração de PATH nem registro COM). O compilador reportou Rust `1.77.2`, host `i686-pc-windows-gnu`, LLVM `17.0.6`. O comando mínimo equivalente foi:

```cmd
set PATH=D:\OPCB\rust177\image\Rust\bin;%PATH%
rustc hello.rs -o hello.exe
hello.exe
```

Resultado no RB1PI2: compilação concluída e execução imprimiu `win7 x86`. Isso valida apenas que aquele programa mínimo x86 roda no host; não valida o Rust 1.97, a crate OPC, COM, nem o servidor.

A tentativa seguinte foi `cargo check -p opc_da_bindings --offline` com a cópia temporária da revisão `da44a992...`. Cargo 1.77 encerrou ao ler o manifesto:

```text
`resolver` setting `3` is not valid, valid options are "1" or "2"
```

Logo, o compilador legado que roda no Windows 7 não consegue construir sequer o workspace fixado. Rust 1.97.1 e a dependência `windows` 0.62.2 não foram instalados nem executados na máquina. O target `i686-win7-windows-gnu` também não foi compilado. Um compilador atual pode ser executado em um host de build Linux x64/Windows atual, mas esse ambiente não tinha Rust disponível; não foi instalado toolchain fora da máquina 2. Para uma tentativa cruzada seriam necessários um host separado autorizado, `rust-src`/nightly e `build-std` ou uma toolchain Rust reconstruída para Win7, além de validar as APIs importadas pelas dependências no Windows 7.

## Interfaces COM e dependências

O commit declara dependências diretas `windows 0.62.2`, `windows-core 0.62.2` e `windows-bindgen 0.66.0`, além das crates internas do workspace. As três crates Windows declaram `MIT OR Apache-2.0`. O `Cargo.lock` também inclui `proc-macro2 1.0.107`, `quote 1.0.47`, `serde 1.0.229`, `serde_json 1.0.151`, `syn 2.0.119/3.0.3` e outras transitivas. Os metadados oficiais do crates.io para todas as versões travadas consultadas indicam MIT/Apache-2.0, Unlicense OR MIT ou MIT conforme a crate; os avisos/licenças correspondentes devem ser preservados em qualquer redistribuição. Nenhuma dessas dependências foi compilada nesta verificação.

O projeto Rust está sob MIT; redistribuição requer manter o aviso de copyright e o texto de licença. Porém, `opc_da_bindings/.metadata/opcda.idl` e `opc_comn_bindings/.metadata/opccomn.idl` têm copyright OPC Foundation, “ALL RIGHTS RESERVED” e condicionam o uso à seção License Grant da especificação. O `NOTICE` upstream confirma a licença separada. Não foi demonstrado que a licença MIT do wrapper autoriza redistribuir essas definições ou bindings derivados. A licença OPC Foundation e o escopo de derivação devem ser confirmados antes de incorporar ou distribuir artefatos. A OPC Foundation descreve OPC Classic como COM/DCOM; os Core Components são componentes de sistema/redistribuíveis separados, não uma dependência open source que substitua a implementação de interfaces do servidor.

## Testes do checkout

Foi executada a suíte existente com `python -m pytest -q` no Linux x86_64:

```text
58 passed, 7 failed, 1 skipped
```

As sete falhas ocorreram nos testes de transporte TLS/pipeline quando o sandbox negou `socket.bind` em `127.0.0.1` (`OSError: could not bind on any address`). Elas não foram tratadas como falhas funcionais do código. Nenhum teste de COM ou do servidor Rust foi executado. O smoke test x86 foi executado somente na RB1PI2.

## Decisão e próximos passos

Não foi criado servidor no projeto nesta etapa. O smoke test confirma apenas que é possível executar um Rust x86 legado; o build da biblioteca falha por incompatibilidade de toolchain, e o próprio adaptador não entrega browse nem notificações necessárias para a interoperabilidade planejada. Implementar o servidor por cima agora exigiria substituir/retroportar o workspace ou escrever novas interfaces COM e resolver primeiro o licenciamento do material OPCF.

Próximo experimento recomendado: obter autorização para usar um host de build separado e avaliar, sem redistribuir, se uma toolchain Rust atual consegue compilar um binário x86 com o target Tier 3 Win7 e quais imports o binário efetivamente requer. Em paralelo, confirmar os termos OPC Foundation para IDL e bindings. Depois disso, implementar por fases começando por class factory e `IOPCServer`/`IOPCItemMgt`/`IOPCSyncIO`, registrar COM somente mediante autorização específica, e então adicionar browse e connection points antes do teste no PI OPC Client. Windows XP, PI Interface e produção permanecem sem validação.

## Continuação: protótipo COM interno (2026-11-04)

Foi criado um adaptador isolado em `windows/rust_da_com_lab`, com build script
`scripts/build_rust_da_com_lab.sh` e relatório
`docs/RUST_DA_COM_LAB.md`. A falha inicial do loader foi rastreada a
`windows-result` (WinRT error API) e aos links recentes de `windows`/
`windows-core` para `combase.dll`. O build agora define o modo documentado
`windows_slim_errors` e redireciona somente as três APIs COM usadas para
`ole32.dll` em cópias temporárias das crates MIT/Apache. O executável iniciou
na RB1PI2. O self-test COM passou ativação em memória, grupos, itens, leituras,
qualidade/timestamps, escrita negada, perda/retorno simulado da fonte e
revogação da fábrica. `-Embedding` também foi executado sem registro
persistentemente e permaneceu em execução como esperado. ProgID/CLSID seguem
sem registro; PI OPC Client ainda não foi testado. Browse/notificações e a
licença de IDL/bindings continuam pendentes.

## Continuação autorizada: cross-build em host Linux isolado (2026-11-04)

O usuário autorizou o toolchain open source isolado. O host identificado foi
Linux x86_64 (`AUTOU3`, kernel 7.0.0-15-generic), GCC 15.2.0, Python 3 e 501 GB
livres no filesystem principal. Toda a instalação e os caches desta tentativa
ficaram em `/tmp/opc-rust-toolchain`; não foram alterados PATH, rustup/cargo
globais, pacotes do sistema ou o checkout.

### Componentes

| Componente | Versão/origem | Licença e observações |
|---|---|---|
| rustup-init | 1.29.3, pacote oficial `static.rust-lang.org`, Linux x86_64 | MIT OR Apache-2.0. SHA-256 publicado e conferido: `b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55`. Instalado apenas em `/tmp`. |
| Rust nightly | `nightly-2026-11-03`, rustc/cargo `1.101.0-nightly`, commit `1c3a6506966e21845b31de0b075d2527660db132`; LLVM 23.1.5 | Rust MIT OR Apache-2.0; LLVM Apache-2.0 com LLVM exception, além dos avisos incluídos na distribuição. `rust-src` e `rustfmt 1.11.0-nightly` foram instalados no mesmo prefixo. O manifesto diário teve assinatura GPG válida, fingerprint `108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE`, SHA-256 `6014518e425a9ee9653de9d021ea583b33ea3235df8b84efad2d115b79026257`. |
| GCC MinGW i686 POSIX | GCC `13.2.0-6ubuntu1+26.1`, pacotes do repositório Ubuntu Resolute `archive.ubuntu.com` | GCC GPL-3-or-later; runtime `libgcc` com GCC Runtime Library Exception 3.1. |
| Binutils MinGW i686 | `2.45.90.20260125-1ubuntu1+13.3`; binutils-common `2.46-3ubuntu2` | GPL-3-or-later. |
| Headers/runtime MinGW-w64 | `13.0.0-2ubuntu1` | ZPL-2.1 para os arquivos gerais, com exceções e licenças próprias por arquivo descritas nos notices do pacote. |

Os oito `.deb` foram baixados por `apt-get download` para `/tmp`, sem instalar
pacotes. Cada SHA-256 foi conferido contra o índice APT autenticado do Ubuntu.
Os hashes conferidos foram:

```text
binutils-common 2.46-3ubuntu2
  3c846f001d87b16e5038a3919ca975276808a2db17459308a9d0f965c1cddac5
binutils-mingw-w64-base 2.45.90.20260125-1ubuntu1+13.3
  f3dbbe4aec206d7078987c9ba3286df20a5fab4ed8c63b0f0d31969c26bdfcbf
binutils-mingw-w64-i686 2.45.90.20260125-1ubuntu1+13.3
  f80c4b88b182bcbe94ea1e0d5f2e172f85f9fc280d47d04077349c67bf901a58
gcc-mingw-w64-base 13.2.0-6ubuntu1+26.1
  a30c07e807e9874ea93909448c5d6d37f9569ebe60ad32de8e490aad198538a3
gcc-mingw-w64-i686-posix-runtime 13.2.0-6ubuntu1+26.1
  650bcd76e0c56c713428950aec8823fd517f911eaad80406907379a69d36ad0a
gcc-mingw-w64-i686-posix 13.2.0-6ubuntu1+26.1
  8c63daeb59c96044c2a5c26b5a3a2b793237f3e10d3428bb659401122b8e501c
mingw-w64-common 13.0.0-2ubuntu1
  da9b2cb14134ca0ef3a31302df50295a2dd6fee7aa4fd888b11c6f72f8ab939e
mingw-w64-i686-dev 13.0.0-2ubuntu1
  abd55207c0b9f35dcca53666bc932327e7eb73e6839218801dd5c70296d0661d
```

O toolchain Rust também usa transitivas de `rust-src` para `build-std`; elas
foram obtidas do canal assinado. O Cargo validou os checksums das crates
baixadas conforme o `Cargo.lock`. Para futura distribuição de qualquer binário,
será necessário reavaliar os avisos do Rust/LLVM e runtimes efetivamente
linkados; esta nota não concede licença OPC Foundation.

### Resultados

O smoke test foi compilado em `/tmp/opc-rust-toolchain/smoke` por:

```bash
cargo +nightly-2026-11-03 build \
  --manifest-path /tmp/opc-rust-toolchain/smoke/Cargo.toml \
  -Z build-std=std,panic_abort --target i686-win7-windows-gnu
```

Usou `CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER` apontando ao GCC MinGW POSIX
extraído em `/tmp`. O resultado é PE32 Intel i386; imports: `ADVAPI32.dll`,
`KERNEL32.dll`, `msvcrt.dll`, `ntdll.dll`, `USERENV.dll`, `WS2_32.dll`. O
executável não requer DLL de MinGW copiada ao lado. Foi enviado somente esse
smoke test para `D:\OPCB\rust-cross-20261104\win7_x86_smoke.exe`; o SHA-256
confirmado no host e na RB1PI2 é
`0947f4a8e5a4e88e56ff55b5f7dcd04c8f98b81c286c3ff95051fdf58f95a8e0`. Na
RB1PI2, ele encerrou com código 0 e imprimiu `Rust cross build:
i686-win7-windows-gnu`.

Depois, na cópia temporária do repositório no commit fixado, o primeiro build
falhou porque o profile mínimo não trazia `rustfmt`, requerido pelos
`build.rs`. Após instalar o componente oficial `rustfmt` isoladamente, o
seguinte compilou com sucesso (`BUILD_EXIT=0`):

```bash
cargo +nightly-2026-11-03 build -p opc_da_bindings \
  --target i686-win7-windows-gnu --locked \
  -Z build-std=std,panic_abort
```

Também compilou o harness dos testes (`TEST_BUILD_EXIT=0`) com:

```bash
cargo +nightly-2026-11-03 test -p opc_da_bindings \
  --target i686-win7-windows-gnu --locked \
  -Z build-std=std,panic_abort --no-run
```

O segundo comando **não executou** os testes; só gerou os executáveis de teste
x86 no diretório `target/` da cópia temporária. Eles não foram transferidos à
RB1PI2 enquanto o uso/redistribuição dos bindings derivados das definições OPC
Foundation aguarda esclarecimento. O crate é compilável em cross-build; isso
não prova que o binário de servidor completo rode no Win7, porque ainda não
foi criado um programa servidor e `windows-rs 0.62.2`/imports efetivamente
usados não passaram por teste de execução neste sistema.

Nenhum arquivo foi copiado para o checkout a partir da biblioteca auditada,
nenhum artefato derivado dela foi enviado à máquina 2, nenhum endpoint COM foi
registrado e nenhuma máquina de origem foi acessada. A cópia temporária em
`/tmp/opc-rust-audit-da44a992` foi usada para o build de avaliação.

## Estado atualizado

O teste mínimo cross-target e a compilação do crate DA são viáveis em host
Linux x86_64 com nightly e MinGW open source isolados. A compilação específica
usa `rustc 1.101.0-nightly`, não a toolchain exata `1.97.1` declarada pelo
upstream; usa nightly porque o `build-std` do target Tier 3 é experimental.
Não se deve confundir esse resultado com validação oficial do target ou de
execução do servidor.

Para um servidor DA funcional seguem pendentes: confirmar o escopo da licença
OPC Foundation; implementar o objeto de aplicação e class factory; definir
ProgID/CLSID sem conflito; implementar política de dados somente leitura,
qualidade/timestamp/log e ciclo de vida; adicionar browse/enumeradores e
connection points/notificações; executar os testes COM no Windows 7; obter
autorização antes de qualquer registro; e validar browse/leitura/assinaturas/
escrita negada no PI OPC Client. Nenhum binding OPCF nem binário derivado foi
incorporado ou redistribuído pelo cross-build autorizado.

## Continuação: harness e modelo independente (2026-11-04)

### Integridade do ambiente e do harness

O prefixo isolado `/tmp/opc-rust-toolchain` continuava disponível e as versões
e hashes acima correspondiam aos arquivos presentes. O checkout upstream
temporário continuava no commit `da44a99265f9706618bbe506c98f714f2aa46f0b`.
O harness `--no-run` ainda estava somente nessa cópia temporária. Foram
inspecionados os dois executáveis PE32 x86: neles há símbolos/strings como
`IOPCServer`, `IOPCDataCallback`, `tagOPCITEMSTATE` e constantes OPC DA; as
importações incluem `ole32.dll`, `oleaut32.dll`, `combase.dll` e APIs WinRT.
Assim, não foi possível excluir que contenham material derivado das definições
OPC Foundation. Por cautela com a licença pendente, nenhum harness foi
transferido ou executado na RB1PI2. Os testes do crate permanecem compilados,
mas não executados.

### Limite da análise de licença OPC Foundation

Os cabeçalhos do IDL na revisão fixada reservam os direitos à OPC Foundation e
remetem à concessão de licença da especificação. A [OPC Foundation Source
License](https://opcfoundation.org/license/source/1.11/) orienta a verificar
os termos e avisos de cada arquivo. A [Specifications License
Agreement](https://opcfoundation.org/license/specifications/1.15/) descreve
uso interno para implementar uma especificação por um licenciado autorizado e
restringe a reprodução/distribuição da especificação; ela não esclarece, por si
só, a redistribuição de código-fonte de bindings gerados ou de executáveis que
os incorporem. A [Redistributables License
Agreement](https://opcfoundation.org/license/redistributables/1.2.hidden/) é
uma licença distinta para componentes identificados como redistribuíveis e
não foi demonstrado que cubra esses IDLs ou bindings Rust derivados. A licença
MIT do wrapper Rust não resolve os direitos sobre esses materiais. Isto é uma
avaliação técnica de avisos públicos, não parecer jurídico nem autorização do
titular.

Antes de qualquer incorporação, é necessário obter da OPC Foundation uma
resposta escrita para a revisão e arquivos específicos: (1) a licença aceita
ao obter diretamente a especificação permite usar esses IDLs para gerar
bindings e compilar/testar uma implementação interna em RB1PI2? (2) permite
manter os bindings gerados no checkout privado? (3) permite publicar ou
redistribuir fonte e binários derivados, inclusive em um projeto open source,
e quais avisos/condições devem acompanhá-los? Até essa confirmação, os bindings
e harnesses não entram no checkout nem são transferidos.

### Modelo independente, sem OPC/COM

Foi adicionado `windows/rust_da_model`, um crate separado sob MIT, sem
dependências externas e sem código, definições ou bindings OPC Foundation.
Ele modela `RustLab.Numeric.Double` (`f64`) e `RustLab.Boolean` (`bool`),
inicializa em `BadNoSample`, rejeita escrita, rejeita tipo incompatível,
preserva o FILETIME válido, representa timestamp ausente/inválido com status
incerto e timestamp vazio, e degrada snapshots armazenados para
`BadNotConnected` na perda da fonte. Reconexão não torna dados antigos Good;
uma nova amostra é exigida. Não há relógio substituto nem buffer histórico.
O FILETIME é mantido em ticks de 100 ns desde 1601 UTC; validação/conversão no
futuro adaptador Windows ainda precisa ser implementada.

Arquivos adicionados: `windows/rust_da_model/Cargo.toml`, `Cargo.lock`,
`LICENSE`, `.gitignore`, `README.md`, `src/lib.rs`, `src/main.rs` e
`scripts/test_rust_da_model.sh`. O crate tem apenas a biblioteca padrão.
O MIT exige preservar o aviso de copyright e o texto da licença. O script usa
o prefixo isolado documentado, roda testes e tenta o cross-build sem acesso à
rede (`--offline`); não muda configurações globais.

Resultados no host Linux x86_64:

```text
scripts/test_rust_da_model.sh
7 passed; 0 failed; 0 ignored; doc-tests: 0
cargo fmt --manifest-path windows/rust_da_model/Cargo.toml -- --check: passou
cargo build --offline --manifest-path windows/rust_da_model/Cargo.toml \
  --target i686-win7-windows-gnu -Z build-std=std,panic_abort: passou
```

O executável resultante é PE32 Intel i386, SHA-256
`ab12f2554287b89ab42e544092f23f1a2bcde292789942e0ed0113c89049ecf9`.
Sua inspeção de imports não encontrou DLL de OPC/COM; importa apenas APIs do
Windows e runtime C. Foi transferido sem sobrescrever o smoke test anterior
para `D:\OPCB\rust-da-model-final-20261104\opc-da-lab-model.exe`. Na RB1PI2,
`certutil` confirmou o mesmo SHA-256 e o processo terminou com código 0,
imprimindo os dois snapshots simulados como Good e, em seguida, que os valores
armazenados não permanecem Good após desconexão. Isso valida somente o modelo
independente e a execução desse binário x86 no Windows 7 SP1 x86; não é teste
COM nem teste de servidor OPC DA.

### Mapa técnico e próximo experimento

O binding auditado fornece partes de class factory/apartment/registro e
adaptadores de `IOPCServer`, `IOPCItemMgt`, `IOPCGroupStateMgt` e
`IOPCSyncIO`. No commit fixado ainda faltam composição do servidor real e seu
ciclo de vida (`make_server()` está `unimplemented!()`), enumeradores de
servidores/grupos/itens, adaptador de browse, async I/O, connection points e
`IOPCDataCallback`, além de mapeamento de qualidade/timestamp para os tipos OPC
Classic e gerenciamento de memória/ownership ABI. A escrita teria de retornar
erro explícito por item. O modelo isolado ainda não implementa nenhuma dessas
interfaces.

Não houve registro COM, conexão com ABB, uso de PI Interface, nem teste com PI
OPC Client. O próximo passo é resolver por escrito o escopo da licença. Com
isso liberado, preparar uma aplicação mínima em uma branch isolada: class
factory e `IOPCServer`, depois grupo/itens/leitura e browse, com ProgID/CLSID
novos previamente verificados. Antes de registrar, apresentar o comando exato,
as entradas de Registro afetadas e sua reversão, e aguardar autorização
específica. Então validar no PI OPC Client browse, AddItems, tipos, qualidade,
timestamp, escrita negada e degradação ao interromper apenas a fonte simulada.
O executável de modelo aqui testado não pode ser usado como servidor OPC DA.

## Continuação: rota de implementação e alternativas (2026-11-04)

### Rechecagem do estado

O HEAD do checkout segue `382fae92c17d25a9b672729197b9b80f5cbe6a6e`; alterações
locais anteriores foram preservadas. O prefixo Rust isolado e a cópia de
auditoria da revisão upstream continuam presentes em `/tmp`. Confirmados:
`rustc 1.101.0-nightly` (`1c3a6506966e21845b31de0b075d2527660db132`), LLVM
23.1.5 e MinGW GCC 13 POSIX. O `rustup` requer `RUSTUP_HOME` e `CARGO_HOME`
apontados ao prefixo isolado para não consultar/criar `$HOME/.rustup`.

O aviso literal no IDL `opcda.idl` diz “(c) Copyright 1997-2002 The OPC
Foundation / ALL RIGHTS RESERVED”, que o código é fornecido para auxiliar a
compreensão/uso da especificação, e que pode ser usado conforme a seção License
Grant da especificação. `opccomn.idl` tem reserva equivalente. O arquivo
upstream `NOTICE` reconhece que tais arquivos estão sujeitos aos termos OPCF;
os crates que os incluem declaram MIT, mas essa declaração não altera os
cabeçalhos dos arquivos incorporados. A [especificação OPCF 1.15](https://opcfoundation.org/license/specifications/1.15/)
define Authorized Licensee como quem obtém a especificação pelo processo do
site e aceita o acordo; permite reprodução para fins internos na medida
razoavelmente necessária à implementação e proíbe redistribuir a própria
especificação. O acordo [Source Deliverables](https://opcfoundation.org/license/source/1.11/)
confirma que a licença varia por arquivo. O acordo de [Redistributables 1.3](https://opcfoundation.org/license/redistributables/1.3/)
permite distribuir certos deliverables compilados sujeitos, entre outras
condições, a funcionalidade primária significativa, termos protetivos, aviso
de copyright e indenização; não identificamos o IDL ou bindings Rust derivados
como deliverables abrangidos. Isso não responde se bindings gerados podem ser
mantidos no checkout ou distribuídos. Não é parecer jurídico nem autorização.

### Alternativas auditadas, sem incorporação

| Alternativa consultada | O que fornece | Licença/proveniência relevante | Adequação nesta etapa |
|---|---|---|---|
| `Ronbb/rust_opc`, commit `da44a99265f9706618bbe506c98f714f2aa46f0b` | Adaptadores Rust parciais e bindings OPC DA/COM | Wrapper MIT; `.metadata/*.idl` contém direitos reservados OPCF e remissão à licença da especificação; `NOTICE` mantém o aviso | Não usar até esclarecimento; além disso, faltam browse/enumeradores/notifications e servidor completo. |
| `oiweiwei/go-opcda` (repositório consultado; pkg.go.dev lista v0.7.0) | Cliente Go OPC DA sobre DCOM/MS-RPC, interfaces geradas | Repositório declara MIT, mas README informa que IDLs foram montados de cópias das definições OPCF, inclusive OPCF-Members/UA-.NET-Legacy e mtconnect/adapter | Não é servidor; sua geração também não elimina a questão de proveniência/licença. Não foi incorporado. |
| `marcschier/opc-classic` (branch main consultada) | Implementação .NET 10 alpha com servidores DA e wrappers DCOM/COM callable | Código de produção declara MIT, mas `interop/` preserva IDL, assets e avisos da OPC Foundation; `THIRD-PARTY-NOTICES` aplica licenças separadas | Não é Rust, requer .NET 10 e inclui assets OPCF que exigiriam auditoria; Microsoft lista Windows 7 SP1 como não suportado pelo .NET moderno atual. Não atende RB1PI2 e não foi incorporado. |
| OPC Core Components / OPC DA redistributables | Proxy/stub COM e componentes de suporte para interoperabilidade | Deliverables da OPC Foundation com acordo próprio; não são open source por essa razão | Não implementam a lógica do servidor e a permissão aplicável teria de ser checada para o pacote exato. Não instalados nem copiados. |

Fontes consultadas: [repositório Rust](https://github.com/Ronbb/rust_opc/tree/da44a99265f9706618bbe506c98f714f2aa46f0b),
[go-opcda](https://github.com/oiweiwei/go-opcda),
[Opc.Classic](https://github.com/marcschier/opc-classic),
[requisitos .NET no Windows](https://learn.microsoft.com/en-us/dotnet/core/install/windows).
Os dois projetos alternativos não foram baixados ao checkout nem usados como
dependências. A pesquisa não encontrou, nesta verificação, um servidor OPC DA
Rust pronto que ao mesmo tempo tivesse provenance/licença de todos os contratos
COM necessária claramente compatível com redistribuição aberta e estivesse
validado no Windows 7 x86.

### Decisão técnica desta etapa

Não foi adicionada implementação COM: fazer isso agora exigiria introduzir as
definições de interface (ou bindings derivados) cujos direitos para o uso e a
distribuição pretendidos permanecem sem resposta. Interfaces não se limitam a
`IOPCServer`: a prova de conceito solicitada precisa ainda class factory e
ativação, `IOPCServer`, enumeração, `IOPCItemMgt`, grupo, `IOPCSyncIO`, browse,
connection points, `IOPCAsyncIO2`/`IOPCDataCallback`, marshaling e gerenciamento
COM de memória. O modelo MIT permanece intacto e é a única implementação nova;
seus sete testes e execução Windows estão registrados acima. Não há ProgID ou
CLSID proposto, portanto não há comando de registro nem verificação de conflito
que possa ser apresentada sem inventar um servidor inexistente. Nada foi
registrado e o PI OPC Client não foi iniciado.

Para desbloquear, solicitar à OPC Foundation por escrito confirmação para os
arquivos `opcda.idl` e `opccomn.idl` da revisão fixada: direito de gerar/usar
internamente bindings para o protótipo; direito de armazenar bindings gerados
no repositório; e direito/condições para publicar código e binários que os
incorporem sob uma licença aberta. Alternativamente, o projeto precisa de uma
implementação independente cuja proveniência dos GUIDs, contratos e tipos COM
e cuja licença permitam explicitamente essa distribuição; ela deve ser
auditada por arquivo e testada para Windows 7 x86 antes de ser escolhida.

## Continuação: serviço independente para a futura fronteira COM (2026-11-04)

Atendendo ao pedido de avançar as partes independentes, `windows/rust_da_model`
agora também contém `ServerCore`, sem alterar as regras anteriores do
`LabModel`. O núcleo implementa uma lista plana para browse local, handles
internos de grupos/itens, criação/remoção de grupos e itens, leituras por item,
erro para Item ID desconhecido, rejeição de escrita e batches de notificações
em canais Rust. Fonte indisponível emite snapshots `BadNotConnected`; a simples
reconexão não promove dados, e uma nova amostra válida gera notificação.

Este núcleo é deliberadamente independente de OPCF e COM: não contém GUIDs,
interfaces/vtables, HRESULTs, tipos `VARIANT`/`FILETIME`, ABI, marshaling ou
registro. A taxa de atualização é armazenada e limitada a 1 ms, mas não existe
temporizador, polling, deadband nem ciclo de callbacks COM. As notificações em
canal não são um `IConnectionPoint`; a adaptação ao contrato DA permanece
pendente. O grupo guarda os canais dos assinantes e descarta canais fechados na
próxima atualização; ainda não existe método OPC de cancelamento de assinatura.
Licença MIT, sem dependências transitivas.

Validação após a mudança:

```text
scripts/test_rust_da_model.sh                  Linux x86_64: 10 passed, 0 failed
cargo fmt --manifest-path windows/rust_da_model/Cargo.toml -- --check: passou
cross-build do harness i686-win7-windows-gnu: passou (`--no-run`)
git diff --check: passou
```

O harness de teste do núcleo, não o harness OPC Foundation, foi inspecionado
antes da transferência: PE32 Intel i386; imports `ADVAPI32`, `KERNEL32`,
`msvcrt`, `ntdll`, `USERENV` e `WS2_32`; buscas por `IOPCServer`,
`IOPCDataCallback`, `opcda.idl` e `OPC Foundation` não retornaram strings.
SHA-256: `7d13ce20d82e513142e2b4442daeedae4b8a758eaa73158ae480139c6be34277`.
Esse foi o primeiro build transferido durante a etapa; após separar o núcleo em
`src/server_core.rs`, o harness final foi novamente compilado e inspecionado.
O hash final é `d2486aacb00a9671afc91f72ac5c27de5495085bed2d09b899b0f72f2de07c53`.
Foi transferido para a pasta nova
`D:\OPCB\rust-da-core-tests-final-20261104\model-tests.exe` na RB1PI2;
`certutil` confirmou o hash. No Windows 7 SP1 x86, o harness final executou os
10 testes: 10 passaram, 0 falharam, código de saída 0. Isso valida em Win7
apenas o modelo e o núcleo local de grupos/itens/notificações; não exercita COM
nem um cliente OPC DA.

Estado de entrega permanece: nenhum servidor OPC DA executável, ProgID ou CLSID
foi criado. Não há conflito de Registro a verificar até existir uma identidade
COM proposta. Não houve registro, alteração de serviços/DCOM/firewall, conexão
à ABB, nem teste no PI OPC Client. O bloqueio preciso para a próxima camada é a
proveniência/licença dos contratos OPC DA (em especial GUIDs, assinaturas e
layout de estruturas/vtables): obter esclarecimento escrito sobre o uso
interno dos IDLs e bindings da revisão ou escolher outra fonte com termos e
proveniência que permitam claramente o uso pretendido. Com isso resolvido, o
núcleo pode ser ligado a uma camada COM pequena sem substituir a lógica já
testada.

## Continuação: simulador determinístico independente (2026-11-04)

O usuário autorizou uso interno de laboratório e especificou que os arquivos
com dúvidas não sejam publicados nem redistribuídos. Reconfirmei os termos
publicados: o portal da OPC Foundation informa que as especificações OPC
Classic DA (incluindo IDLs) estão disponíveis para download somente a membros
pagos. O acordo genérico de especificações define *Authorized Licensee* como
quem obtém a especificação pelo processo do site e aceita os termos. Ainda não
há evidência de que o detentor deste projeto tenha essa associação/licença ou
tenha obtido a DA 3.0 specification oficialmente. A instrução de uso interno
do usuário não substitui essa condição externa. Não baixei material de membro,
não aceitei acordo em nome do usuário e não usei os IDLs/bindings no build da
camada de aplicação. Fontes: [página OPC DA da OPC Foundation](https://opcfoundation.org/developer-tools/specifications-classic/data-access/),
[acordo de especificações](https://opcfoundation.org/license/specifications/1.15/)
e [acordo de redistribuíveis](https://opcfoundation.org/license/redistributables/1.3/).
Isso não é parecer jurídico. Para usar os contratos upstream em build interno,
é necessário confirmar que a organização tem o acesso/licença de membro
aplicável e aceitou os termos para obter a revisão DA/IDL por via oficial; a
futura distribuição de bindings ou binários segue sem autorização confirmada.

Avancei apenas a fonte simulada independente: `src/simulator.rs` adiciona
`LabSimulator`, gerando sequências determinísticas para `RustLab.Numeric.Double`
e `RustLab.Boolean`, recebendo o timestamp como argumento, propagando
indisponibilidade ao `ServerCore` e exigindo nova amostra depois da reconexão.
Não muda o comportamento anterior de `LabModel`. A camada COM continua
isolada/ausente; sem GUIDs, HRESULTs ou declarações de interface OPCF neste
crate.

Validação desta mudança:

```text
scripts/test_rust_da_model.sh: 11 testes Linux passaram; cross-build do binário
  demo e do harness Win7 x86 --no-run passaram.
Rustfmt --check e git diff --check: passaram.
```

O harness final PE32 x86 foi inspecionado: imports somente
`ADVAPI32`, `KERNEL32`, `msvcrt`, `ntdll`, `USERENV`, `WS2_32`; nenhum símbolo
`IOPCServer`, `IOPCDataCallback`, `opcda.idl` ou `OPC Foundation` foi
encontrado. SHA-256
`140080d85de449790560606aef6099e2141d04b0b3e665906fa7e47cb5a1e67f`.
Transferido para a pasta nova
`D:\OPCB\rust-da-core-tests-sim-20261104\model-tests.exe` e executado em
RB1PI2/Windows 7 SP1 x86: **11 passaram, 0 falharam**, saída 0. Isso continua
sendo teste do modelo/núcleo/fonte simulada independente, sem COM.

O PI OPC Client foi aberto manualmente pelo usuário e listou os servidores
RSLinx. Nenhum deles foi acessado. Não existe servidor DA Rust pronto, ProgID,
CLSID ou comando de registro, e ainda não é possível testar tags na GUI. A
implementação do servidor COM permanece bloqueada até confirmar o acesso à
especificação/IDL Classic DA sob os termos aplicáveis; o núcleo independente
está pronto para ser adaptado quando esse pré-requisito for esclarecido.
