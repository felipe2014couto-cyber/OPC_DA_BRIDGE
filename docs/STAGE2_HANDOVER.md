# Entrega da etapa 2 — OPC-DA-Bridge

> Decisão atualizada na etapa 3: Softing OPC Classic SDK (MIT), no SHA fixado em `THIRD_PARTY.md`, substitui a recomendação histórica Advosol deste relatório. O relatório abaixo permanece como registro histórico da etapa 2.

Data da pesquisa: 08/10/2026. Backend Windows: trabalho supervisionado em ambiente isolado. Coordenação, revisão e integração: Codex.

## Estado inicial e preservação

- Projeto de destino: `OPC-DA-Bridge`.
- `git status`, branch e log falharam: o destino não é um repositório Git válido. Não há commit inicial nem branch a informar. Os contextos reservados do ambiente foram preservados.
- Baseline: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider` → **34 passed in 1.49s**.
- Projeto original: repositório `OPC-Bridge`, inicialmente limpo na referência registrada durante a etapa. Nenhum caminho local ou nome de branch é publicado neste relatório.
- A implementação ocorreu em `/tmp/opc-da-bridge-stage2-ouj9r36r/work`, com baseline separado e hashes dos 26 arquivos iniciais. O Git criado nessa cópia serviu apenas para revisar diferenças; nenhum commit, push ou merge foi feito.

## Comparação técnica e decisão

A escolha técnica do backend é **Advosol DANSrv**, inicialmente Standard para o namespace estático. **Softing OPC Classic SDK publicado sob MIT** é a alternativa. A avaliação de risco abaixo é uma inferência de arquitetura e manutenção, não homologação com PI.

| Opção | Servidor e operações | x86/x64 e compilação | Licença, custo e redistribuição | Avaliação para uso industrial/PI |
| --- | --- | --- | --- | --- |
| [Advosol DANSrv](https://www.advosol.com/product/5/opc-da-net-server-toolkit) | SDK de servidor DA 2.05/3.0; groups/items, sync/async, browse e VQT. O EXE genérico fornece COM; plugin .NET implementa a lógica da aplicação. | EXEs x86/x64, plugin AnyCPU. Produto lista .NET 4.8.1 ou 6/8/10; compiler/VS exatos dependem do pacote fornecido. Standard: namespace estático; Pro: dinâmico. | Standard single developer **US$ 1.250**; Pro **US$ 2.090**. [EULA pública](https://www.advosol.com/topic/licenseagreement): runtime binário integrado sem royalties adicionais; não redistribuir fonte/documentação do SDK. Trial só avaliação, 30 min. Conferir termos da versão adquirida. | Principal: menor esforço de implementar COM e suporte comercial. Compatibilidade do Mirror com PI ainda precisa de teste. |
| [Softing OPC Classic SDK](https://github.com/SoftingIndustrial/OPC-Classic-SDK) | Servidor e cliente DA 1.0a/2.05/3.0; C++ e .NET, incluindo groups, leituras e callbacks. | Configurações x86/x64 históricas; VS/MSVC/.NET e dependências precisam ser verificados na revisão escolhida. A [matriz de 2018](https://industrial.softing.com/uploads/softing_downloads/OpcClassicDevelopmentToolkits_D_EN_180302_300_sk.pdf) não prova suporte ao toolchain atual. | MIT, sem cobrança de licença do código publicado; preservar copyright e permission notice. Dependências têm seus próprios termos. [EOL](https://industrial.softing.com/us/services/discontinued-products.html): produto encerrou em 01/04/2020, suporte em 31/03/2021. | Alternativa: manutenção, correções e validação passam à equipe. Não foi baixado ou compilado. |
| [OPC Foundation Classic samples](https://opcfoundation.org/developer-tools/samples-and-tools-classic/net-api-sample-client-source-code/) | Página específica inclui cliente e reference server. Cobertura das interfaces DA deve ser conferida no pacote efetivamente fornecido; não substituir pelo SDK UA. | Wrapper COM nativo e componentes .NET legados. Build, DA2/3 e x86/x64 dependem do pacote; não foram comprovados localmente. | Página declara MIT, acesso Corporate Member e remoção anunciada para 01/09/2026. Disponibilidade atual não confirmada. Custo de acesso não cotado. Licença por componente/header; não aplicar RCL de UA genericamente a Classic. | Referência útil, mas disponibilidade e adaptação não permitem adotá-la como solução comprovada nesta etapa. |
| [C++/ATL próprio](https://learn.microsoft.com/en-us/cpp/atl/atl-com-desktop-components) | Tecnicamente viável. A equipe teria de implementar IOPCServer, IOPCItemMgt, IOPCGroupStateMgt, IOPCSyncIO, IOPCAsyncIO2, browse, connection points, shutdown e, se DA3, SyncIO2/AsyncIO3/ItemIO. | Compilar EXE e recursos COM para x86/x64 com MSVC, ATL, MIDL e Windows SDK. Validar proxy/stub e lifetime/threading. | Código próprio; custo de engenharia e [licença do toolchain](https://www.microsoft.com/licensing/guidance/Visual-Studio). Community depende de elegibilidade; não presumir custo zero. Conferir termos dos headers e redistributables OPC usados. | Não escolhido: maior custo e responsabilidade de interoperabilidade e manutenção para este projeto. |
| Python: [OpenOPC](https://openopc.sourceforge.net/api.html), [pywin32](https://mhammond.github.io/pywin32/html/com/win32com/HTML/QuickStartServerCom.html), [OpcDa do autor](https://github.com/PockusaevAndrey/pyOpcDa/blob/master/Interfaces/IOPCServer.py) | OpenOPC/OpcDa conectam a servidores existentes; gateway não vira servidor DA Classic. pywin32 oferece infraestrutura COM genérica, sem prova de servidor DA completo. Generic IDispatch não demonstra as interfaces custom exigidas por DA. | Python/COM e wrappers precisam de bitness compatível; não há servidor DA x86/x64 comprovado dessas bibliotecas. Extensões nativas exigiriam toolchain e trabalho adicional. | Custos/termos por pacote: OpenOPC/forks exigem revisão da licença; [pywin32 tem licenças mistas](https://github.com/mhammond/pywin32#licenses); [OpcDa declara MIT](https://pypi.org/project/OpcDa/). Não foram instalados. | Úteis como clientes/testes ou suporte ao núcleo; não escolhidos como SDK de servidor. |

## Contrato e o que funciona

- `mirror/`: namespace navegável, aliases `OPCBridge.Demo.Temperature` e `OPCBridge.Demo.Pressure`, groups/items, leitura síncrona e callbacks Python acionados por polling.
- `core/cache.py`: snapshot do lote sob o lock, com avaliação stale e cópia defensiva do payload. Getters existentes foram preservados.
- VQT: preservação de valor, ValueType, quality word e timestamp da origem. Conversão FILETIME por inteiros: `timestamp_us * 10 + 116444736000000000`, com validação de limites.
- Stale/desconexão não viram Good; o Mirror não substitui o timestamp antigo por um recente.
- Escritas recusadas com `OPC_E_BADRIGHTS = 0xC0040006` por item, `S_FALSE` no lote. O código incorreto anterior foi corrigido conforme a [especificação DA 2.05, apêndice A](https://www.advosol.com/OpcSpecs/opcda205_cust.pdf). O adapter real deverá aplicar essa política em Write e todos os WriteVQT sync/async/ItemIO.
- Taxa negociada limita o polling de callbacks; unsubscribe/remove group e notificação de shutdown são contratos locais. O mock não implementa COM/DCOM, ABI, marshaling ou um scheduler industrial.
- `windows_mirror/`: mock explicitamente identificado, esboço conceitual do plugin e entrada protegida que falha sem implementação/runtime real. O núcleo, transporte e demo Linux não importam esse módulo ou bibliotecas COM.

## Evidência Windows e limite da etapa

A inspeção foi limitada ao PATH e aos locais usuais verificados. Python/pytest estavam disponíveis; SDKs não foram encontrados nesses locais e `dotnet`, `msbuild`, `cl` não estavam no PATH. A existência em outros diretórios não foi descartada. O caminho PI verificado não continha `opcint.exe`; isso não prova ausência em toda a máquina.

**Servidor OPC DA COM real não foi implementado, registrado nem validado no Windows ou com PI.** Foram executados somente testes Python de contrato/mock. `AdvosolPluginContract` não é uma assembly .NET, nem ABI/assinatura do SDK verificada.

## Testes e arquivos finais

No destino `OPC-DA-Bridge`:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
50 passed in 1.51s
```

- Mantidos os 34 testes existentes; adicionados 16 testes de Mirror.
- Linux: suite final completa e isolamento de imports em subprocesso passaram.
- Windows: checkpoint inicial de **15 testes Python passou**, antes das últimas correções. **A suíte final revisada de 16 testes não foi reexecutada no Windows**; não extrapolar o checkpoint para os arquivos finais. Nenhuma ativação COM ou teste PI ocorreu.
- `git diff --check` no destino retornou 129 por ausência de repositório válido. A mesma checagem na cópia isolada, com todos os arquivos novos incluídos por `git add -N`, passou com exit 0, inclusive após integração do relatório.
- Ruff: executável e módulo ausentes no Linux; não foi instalado nem executado.
- As opções `PYTHONDONTWRITEBYTECODE=1` e `-p no:cacheprovider` evitam artefatos no projeto; a execução foi elevada para permitir sockets TLS locais dos testes autorizados. Certificados efêmeros são apenas fixtures temporárias de teste, sem alteração dos certificados do ambiente.

**Criados (9):**

- `docs/STAGE2_HANDOVER.md`
- `docs/STAGE2_REPORT.md`
- `src/opc_da_bridge/mirror/__init__.py`
- `src/opc_da_bridge/mirror/contracts.py`
- `src/opc_da_bridge/mirror/facade.py`
- `src/opc_da_bridge/mirror/hresult.py`
- `src/opc_da_bridge/windows_mirror/__init__.py`
- `src/opc_da_bridge/windows_mirror/adapter.py`
- `tests/test_mirror_facade.py`

**Modificados (4):**

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/DECISIONS_REAL_OPC_DA_MIRROR.md`
- `src/opc_da_bridge/core/cache.py`

Todos os 26 arquivos iniciais foram preservados. `docs/REUSE.md`, modelo, protocolo, transporte, coletor, receiver, demo e os testes anteriores permanecem byte a byte iguais ao baseline. Nenhum arquivo/diretório foi removido. Os novos módulos não adicionam dependências de execução.

## Requisitos exatos para o próximo laboratório

1. Fornecer pacote/versão DANSrv licenciado, autorização de uso e termos aplicáveis. Não houve compra, download, instalação de SDK ou aceite de licença.
2. Disponibilizar Windows de laboratório, toolchain/runtime aprovados e OPC Core Components/proxy-stubs compatíveis e licenciados. Confirmar versões/arquiteturas reais, sem copiar configuração ou credencial de produção.
3. Implementar plugin .NET e definir ligação local somente de leitura ao cache Python (IPC ou hosting). O protótipo atual não fornece essa ligação. Fazer publicar VQT e transições de qualidade sem leituras/escritas na origem.
4. Fornecer cliente OPC DA x86 autorizado; opcionalmente cliente x64 e PI Interface licenciada de laboratório. O [catálogo observado da PI Interface v2.7.2.20](https://techsupport.osisoft.com/Products/PI-Interfaces-and-PI-Connectors/PI-Interface-for-OPC-DA/Interface-Details/) lista 32 bits. Verificar a versão instalada e configurar consumo de timestamp do servidor só no laboratório autorizado.
5. Validar cliente x86 → Mirror x86 primeiro; depois x86 → Mirror x64 e cliente x64, com proxies corretos. Testar browse, AddGroup/AddItems, sync, Advise/Unadvise, revised rate, sete tipos, qualidade-only, stale, disconnect/reconnect, shutdown e todas as recusas de escrita. Testes Python e escolha do SDK não homologam o produto final.
6. Obter autorização explícita para as mudanças de Registro necessárias à ativação do cliente real. Não foi comprovado um modo sem registro do DANSrv com PI. O [mecanismo ClickOnce de COM sem registro](https://learn.microsoft.com/en-us/visualstudio/deployment/deploying-com-components-with-clickonce?view=visualstudio) não cobre servidores EXE fora de processo.

## Registro e reversão futuros — templates, não executados

O [manual DANSrv](https://advosol.com/Manuals/DanSrv/topic27.html) documenta `/RegServer` para processo e `/UnregServer` para reversão. O EXE abaixo ainda não existe: nome, caminho, CLSID e configuração precisam ser conferidos no pacote/build efetivo. Não usar `/Service` nesta proposta.

```cmd
"C:\OPC-DA-Bridge-Lab\server\OPCBridge.Mirror.Server.exe" /RegServer
"C:\OPC-DA-Bridge-Lab\server\OPCBridge.Mirror.Server.exe" /UnregServer
```

Antes de registrar, inventariar as views 32/64 e as entradas do ProgID `OPCBridge.Mirror.1`, ProgID sem versão, CLSID, LocalServer32 e, se o pacote usar, AppID/TypeLib/categories. Identificadores são propostos; existência prévia não foi inspecionada. Confirmar ausência de conflito e salvar apenas o estado relevante do laboratório.

Impacto esperado: registro da classe e associação ProgID → CLSID → EXE, com categorias OPC. A lista exata dependerá do SDK/configuração. Após `/UnregServer`, conferir ambas as views e comparar com o estado anterior; restaurar entradas preexistentes em vez de apagar ramos compartilhados. Não instalar serviço nem alterar DCOM/firewall/PI por consequência automática. Essas ações, se necessárias, exigem escopo e autorização futuros.

## Limites preservados

Esta tarefa não alterou o OPC-Bridge original, produção, integração PI Web API, PI Data Archive, PI Interface, OPC de origem, DCOM, Registro, firewall, serviços, certificados ou credenciais do ambiente. Não fez deploy nem escrita em OPC/PI. Não copiou segredos, bancos, certificados ou configurações de produção. Nenhum arquivo/diretório existente foi removido.

A leitura final do projeto original encontrou branch `feat/pi-integration-concluir`, status limpo e HEAD `6f7a25da4570903e906d1040d7a6b9eaffe50fcb`. O HEAD avançou durante esta execução; esta tarefa somente leu o projeto original e não criou esses commits. Não atribuir esse avanço à implementação do OPC-DA-Bridge.

O destino continua sem repositório Git válido e sem commit/branch próprios. Não se criou `.git` no destino. Os terminais externos Orca foram preservados; a tarefa do backend foi concluída e não há worker aguardando decisão de liberação.
