# Plano do pacote offline de compilação para a máquina B

**Estado: bloqueado para preparação física do pacote.** Este documento registra o planejamento; instaladores não foram baixados nem transferidos, e nada foi instalado. Nenhuma pasta foi criada na B. O projeto em `C:\OPC-DA-Bridge` não foi alterado.

**Decisão do SDK:** Windows SDK `10.0.14393.0` aprovado e configurado no projeto Mirror. O servidor será compilado para Windows 7 SP1 x86 usando `WINVER=0x0601` e `_WIN32_WINNT=0x0601`; a decisão se baseia no projeto Softing `OPCToolboxC++_VS2017`, que fixa o SDK `10.0`, e no SDK 10.0.14393 recomendado para Win32 no VS 2017. A máquina B não recebeu alterações.

## Inventário remoto somente leitura

Consulta executada por SSH com chave pública. A B informou Windows 7 Professional SP1 (`6.1.7601`), 32-bit. No instante da consulta, `D:` tinha 18.039.128.064 bytes livres (**16,80 GiB**). `C:` também foi reportado pelo comando, mas não será usado para o pacote, instalação, SDK ou build.

| Pré-requisito | Resultado observado na B | Decisão de planejamento |
|---|---|---|
| OPC Core Components x86 | DLLs em `C:\Windows\System32`: `opcproxy.dll` 3.00.100.0, `opccomn_ps.dll` 1.10.100.0, `opc_aeps.dll` 1.10.100.0, `opcenum.exe` 1.10.100.0 e `opcdaauto.dll` 2.02.5.30. Não apareceu produto “OPC Core Components” na chave padrão de desinstalação. | Componentes OPC x86 estão presentes, mas a versão/identidade completa da instalação não foi confirmada. Não incluir instalador OPC no pacote base; confirmar compatibilidade e integridade em etapa posterior. |
| Visual C++ 2017 Redistributable x86 | `Microsoft Visual C++ 2017 Redistributable (x86) - 14.11.25325`, runtime x86 `v14.11.25325.00`. | Presente, mas o plano fixa MSVC v141 14.16. Para um binário produzido por esse toolset, preparar o runtime 14.16.27052 x86, que satisfaz o requisito de versão igual ou posterior. |
| Universal CRT | `KB2999226` apareceu em `Get-HotFix`; o campo de data estava vazio. | Considerado presente na enumeração; confirmar versão/estado por inventário de pacotes antes de qualquer instalação futura. |
| SHA-2 / servicing stack do Windows 7 | `KB4474419` apareceu em `Get-HotFix`, sem data. `KB4490628` não apareceu. | Estado inconclusivo. A Microsoft lista KB4490628 e KB4474419 como pré-requisitos de atualizações posteriores a agosto de 2019 no Windows 7 SP1. Confirmar ambos e o estado de reinicialização antes de instalar atualização assinada. Não instalar nesta etapa. |

As consultas não encontraram a versão em “Programas e Recursos” para OPC Core Components, então a presença de DLLs não prova que o instalador oficial atual esteja instalado ou que seus componentes sejam consistentes. A B continua sem nenhuma alteração.

## Artefatos candidatos

Os caminhos abaixo são propostas futuras em `D:\OPC-DA-Bridge\offline\...`; não existem como resultado desta etapa.

| Artefato | Versão/arquitetura | Fonte oficial e tamanho esperado | Licença/acesso | Obtenção no AUTOU3 | Caminho proposto na B | SHA-256 |
|---|---|---|---|---|---|---|
| Visual Studio 2017 Build Tools | 15.9.61; MSVC v141 14.16; alvo x86; componentes `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` e `Microsoft.VisualStudio.Component.Windows10SDK.14393` (mais os componentes base requeridos pelo instalador) | [Bootstrapper oficial](https://aka.ms/vs/15/release/vs_buildtools.exe). O bootstrapper é pequeno; o layout offline enxuto ainda não tem tamanho medido. O requisito publicado para Build Tools 2017 varia de 2,3 a 60 GB conforme componentes. | Termos Microsoft para Build Tools; a página de downloads antigos avisa que downloads de VS 2017 podem exigir uma conta/assinatura. Confirmar acesso e aceitar os termos antes de obter. | O bootstrapper pode ser baixado como arquivo, mas a criação oficial do layout requer executar o bootstrapper Windows; AUTOU3 é Linux. Não é possível gerar aqui o layout reproduzível via fluxo oficial. | `offline\\vs2017\\layout\\` | Não disponível antes do download; calcular e guardar após obter o layout e validar assinatura Microsoft. |
| Windows 10 SDK | Target `10.0.14393.0`; instalador arquivado `10.0.14393.795`; x86 headers/libs | [Arquivo oficial do Windows SDK](https://learn.microsoft.com/en-us/windows/apps/windows-sdk/downloads-archive), instalador `https://go.microsoft.com/fwlink/p/?LinkId=838916`. O link distribui bootstrapper; payload selecionado não tem tamanho publicado confirmado. | Licença Microsoft SDK; arquivo oficial sem indicação de login. | O executável pode ser baixado no AUTOU3, mas o cache offline completo e a escolha de componentes precisam de validação do instalador Windows. | `offline\windows-sdk\10.0.14393.795\` (separado apenas se não estiver contido no layout VS) | Não disponível antes do download; calcular após obtenção. |
| Visual C++ Redistributable x86 | 14.16.27052.0, linha VS 2017 | [Download Microsoft por versão](https://aka.ms/vs/15/release/14.16.27052/VC_redist.x86.exe). Esperado cerca de 13 MiB; verificar o tamanho real via `Content-Length`/propriedades antes da transferência. | Termos Microsoft de redistribuição; sujeito às condições de redistribuição do Visual Studio. Download oficial direto; sem login esperado pelo link. | Sim, o arquivo individual pode ser baixado no AUTOU3. Não baixado nesta etapa. | `offline\vc-redist\14.16.27052\VC_redist.x86.exe` | Ainda não calculado localmente. Validar Authenticode e SHA-256 após download. |
| Atualização SHA-2/SSU, condicional | Windows 7 SP1 x86: KB4490628 (SSU) e KB4474419 (SHA-2), se confirmação posterior mostrar ausência | [Catálogo Microsoft KB4490628](https://www.catalog.update.microsoft.com/Search.aspx?q=KB4490628) e [KB4474419](https://www.catalog.update.microsoft.com/Search.aspx?q=KB4474419). Baixar pacote x86 aplicável; tamanho depende da revisão encontrada no Catálogo. | Licença/termos de atualização Microsoft; sem login esperado no Catálogo. | Provavelmente, depois de identificar os arquivos x86 exatos no Catálogo. Nenhum pacote foi baixado. | `offline\windows7-updates\` | Ainda não calculado; calcular após selecionar e obter cada `.msu`. |
| OPC Core Components, condicional | x86; os componentes existentes foram vistos nas versões acima | [OPC Foundation Core Components](https://opcfoundation.org/developer-tools/samples-and-tools-classic/core-components). A página lista 3.1.4.132 em ZIP/MSI/MSM, mas não expõe o tamanho sem acesso autenticado. | A página exige usuário registrado para download; indica licença MIT para o pacote. Confirmar termos do redistribuível antes de distribuir. | Não automaticamente sem credencial/acesso de usuário registrado. Não necessário no pacote base enquanto os componentes x86 existentes forem confirmados como utilizáveis. | `offline\opc-core\3.1.4.132\` somente se inventário posterior provar ausência/incompatibilidade | Não disponível; depende de acesso ao pacote. |

O identificador de componente do SDK é `Microsoft.VisualStudio.Component.Windows10SDK.14393`. As notas de versão do VS 2017 identificam esse SDK e o toolset v141 como o conjunto recomendado para projetos Win32. O instalador do SDK listado pelo arquivo é a revisão `10.0.14393.795`, que fornece o SDK da família `10.0.14393`; não confundir a revisão do pacote com a versão de target `10.0.14393.0`.

## Revisão estática dos projetos e decisão do SDK

Checkout Softing inspecionado no commit fixado `08f4b3e968acfce4b775299741cef1819e24fda1`.

| Projeto | SDK/toolset/arquitetura | Includes e bibliotecas relevantes |
|---|---|---|
| Mirror: `windows/softing_mirror_server/OPCBridge.Mirror.vcxproj` | Linha 12 fixa `WindowsTargetPlatformVersion` em `8.1`; não declara `WindowsTargetPlatformMinVersion`. Debug e Release Win32 usam v141 e Unicode (linhas 4–5, 15–19). Link x86 via `MachineX86` (35, 46). | Includes C++ SDK e `server`, mais `src` do projeto (33, 44). Release vincula `TBCVS2017u.lib` e `Ws2_32.lib`; Debug usa `TBCVS2017uD.lib` e `Ws2_32.lib` (36, 47). |
| Exemplo Softing `AddressSpace_VS2017.vcxproj` | Não fixa `WindowsTargetPlatformVersion` nem declara `WindowsTargetPlatformMinVersion`. ReleaseU|Win32 usa v141, Unicode (44–48). O linker seleciona x86 (`TargetMachine=MachineX86`, linha 397). | Usa includes `C++/src` e `C++/src/server` (ReleaseU, linha 376); vincula `TBCVS2017u.lib`, a pasta `C++/lib` e produz `AddressSpace.exe` (386–388). O passo pós-build desse exemplo também chama `/RegServer` (linhas 364–371); não foi executado. |
| SDK Softing `OPCToolboxC++_VS2017.vcxproj` | Fixa `WindowsTargetPlatformVersion` como `10.0` (41), sem `WindowsTargetPlatformMinVersion`. ReleaseU|Win32 usa v141 e Unicode (44–48). | Usa includes próprios e `core/include`/`core/common` (563); vincula `OTBu.lib`, gera `TBCVS2017u.dll` e `TBCVS2017u.lib`, target x86 (594–609). |
| SDK Softing `OTB_VS2017.vcxproj` | ReleaseU|Win32 usa v141 e Unicode (43–48); não fixa `WindowsTargetPlatformVersion` nem `WindowsTargetPlatformMinVersion`. | Inclui headers do core e módulos (496); produz `OTBu.dll`/`OTBu.lib`, target x86 (519, 528–535). |

Em `development/OPCToolbox_VS2017.sln`, o projeto `OPCToolboxC++_VS2017` declara dependência de `OTB_VS2017` (linhas 6–9, 16), portanto o build correto é `OTBu` primeiro e depois `TBCVS2017u`. O Mirror usa as mesmas configurações de ABI do exemplo Unicode x86 e consome a import library `TBCVS2017u.lib`; o exemplo não demonstra que 8.1 seja requisito.

### Conclusão técnica

- **SDK 8.1 não é obrigatório.** O projeto Mirror é o único desses quatro que fixa 8.1. O projeto C++ Softing fixa `10.0`, e o exemplo AddressSpace deixa a escolha ao SDK instalado/configurado.
- **Windows SDK 10.0.14393 é suportado para compilar esse código sem alteração de fontes.** O SDK é uma escolha de headers/libs do projeto; o Softing já compila seu API C++ VS2017 com SDK da família 10.0 e o exemplo usa o mesmo v141/Unicode/x86. A revisão 14393 é recomendada no VS 2017 para projetos Win32. Isso não prova, por si só, que todos os imports do SDK Softing sejam compatíveis em execução com Windows 7; essa verificação precisa ocorrer sobre os binários produzidos.
- Para manter Windows 7 SP1 como alvo de API, a documentação Microsoft orienta usar `WINVER`/`_WIN32_WINNT` apropriados; para Windows 7, `_WIN32_WINNT=0x0601`. O Mirror hoje não define nenhum dos dois, e nem define `WindowsTargetPlatformMinVersion`. Essa propriedade não é aplicada pelo sistema de projetos C++ como validação de APIs. A compatibilidade de runtime ainda exige auditar imports e testar em Windows 7.
- **Retarget mínimo aplicado com aprovação:** `<WindowsTargetPlatformVersion>10.0.14393.0</WindowsTargetPlatformVersion>` e `WINVER=0x0601;_WIN32_WINNT=0x0601` nas configurações Debug|Win32 e Release|Win32. Não há evidência que exija `WindowsTargetPlatformMinVersion` para este projeto desktop Win32. Nenhuma alteração de código-fonte foi necessária.
- Para compilar em conjunto o servidor e as bibliotecas Softing a partir do checkout, SDK 10.0.14393 é a opção de melhor compatibilidade com a configuração explícita do projeto Softing e a recomendação Win32 do VS 2017; as macros Win7 limitam as declarações de API que o código pode usar. O SDK 8.1 isolado não satisfaz o `WindowsTargetPlatformVersion=10.0` do projeto `OPCToolboxC++_VS2017`.

## Método oficial para o layout mínimo Build Tools 2017

O fluxo oficial requer um **computador Windows com Internet**, acesso administrativo para executar o instalador/layout e espaço local para os pacotes. O AUTOU3 deste trabalho é Linux: ele pode guardar um bootstrapper baixado, mas não executar `vs_buildtools.exe` para resolver manifestos e montar um layout suportado. A documentação Microsoft não descreve um gerador de layout do Visual Studio para Linux.

Bootstrapper oficial Build Tools: `https://aka.ms/vs/15/release/vs_buildtools.exe`. Em um host Windows com Internet, o comando proposto para gerar o layout seletivo é:

```bat
vs_buildtools.exe --layout <diretorio-de-layout> ^
  --add Microsoft.VisualStudio.Workload.VCTools ^
  --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ^
  --add Microsoft.VisualStudio.Component.Windows10SDK.14393 ^
  --lang en-US
```

O comando acima é documentação, não foi executado. Não usar `--includeRecommended` nem `--includeOptional` para evitar baixar componentes além do toolchain e SDK selecionados; o instalador inclui dependências necessárias. A identidade do layout deve ser VS Build Tools 2017, versão final 15.9.61. Antes de usar o layout na B, medir seu tamanho real, confirmar os certificados do diretório `certificates` e validar que a instalação pode direcionar instalação, cache e SDK/itens compartilhados a D:. Na B a instalação posterior deverá usar o layout com `--noWeb`; essa instalação continua fora desta etapa.

**Alternativa oficial no Linux:** nenhuma localizada. É possível baixar o bootstrapper como arquivo, mas isso não cria o cache offline autocontido. A alternativa oficial é usar um host Windows com Internet para a geração e depois transferir o layout completo ao destino sem internet.

### Pré-requisitos de Windows 7

- Windows 7 SP1 é a versão mínima indicada para VS 2017; Build Tools 2017 suporta Windows 7 SP1. O instalador requer .NET Framework 4.5.2 ou posterior. A presença/versão de .NET e o estado de reinicialização ainda precisam ser confirmados antes da instalação.
- A atualização KB2999226 (Universal CRT) foi encontrada. A Microsoft descreve essa atualização como necessária para aplicações dependentes do Universal CRT em sistemas anteriores, incluindo Windows 7 SP1.
- Antes de instalar atualizações Microsoft pós-agosto de 2019 em Windows 7 SP1, a Microsoft exige SSU KB4490628 e SHA-2 KB4474419, seguidos de reinicialização. A B reportou KB4474419, mas não KB4490628; é necessário confirmar o inventário completo e o estado de reinicialização antes de qualquer instalação futura.
- A versão mais recente atual do redistribuível v14 não é adequada ao Windows 7. Este plano usa especificamente 14.16.27052, a versão final da linha VS 2017, compatível com a linha de runtime necessária ao toolset v141 14.16. Não usar o link “latest” para essa máquina.

## Estimativa de espaço em D:

O checkout Softing local mede **79 MiB incluindo `.git`**; os arquivos de fonte necessários sem o histórico Git medem aproximadamente **66 MiB** (`.vendor/softing-opc-classic-sdk`, commit fixado `08f4b3e968acfce4b775299741cef1819e24fda1`). O projeto Git tem menos de 1 MiB antes de artefatos de build. A previsão de build usa margem para objetos e executável.

| Uso simultâneo em D: | Estimativa de planejamento |
|---|---:|
| Layout offline VS 2017 + SDK (componentes mínimos; ainda não medido) | 5,5 GiB |
| Instalação MSVC v141/MSBuild | 4,0 GiB |
| Windows 10 SDK instalado | 1,5 GiB |
| Fontes Softing sem `.git` | 0,07 GiB |
| Projeto e saída de build | 0,5 GiB |
| Área temporária para extração/instalação/build | 1,5 GiB |
| Reserva livre mínima solicitada | 3,0 GiB |
| **Total previsto** | **16,07 GiB** |

Com 16,80 GiB livres medidos em D:, a estimativa nominal deixa 3,72 GiB livres após o uso listado (0,72 GiB acima da reserva mínima). Essa margem é estreita e não é uma prova de capacidade: a Microsoft publica apenas o intervalo de 2,3–60 GB para Build Tools 2017, e o tamanho do layout para esses componentes não está publicado. Uma diferença de 0,72 GiB já reduziria a reserva abaixo de 3 GiB. O tamanho deve ser obtido pelo modo de prévia do layout em uma máquina Windows com Internet antes de autorizar qualquer transferência.

## Bloqueios e próximos passos

1. AUTOU3 é Linux, portanto o bootstrapper do VS não pode gerar nele o layout offline oficial. Não há neste escopo outro host Windows com Internet autorizado para a geração/medição do layout.
2. Antes de qualquer instalação, validar que o instalador permite direcionar **instalação, cache e componentes compartilhados/SDK para D:**. O VS Installer expõe caminhos separados para instalação, cache e shared; o SDK standalone também precisa ser configurado para D:. Qualquer caminho efetivo em C: viola o requisito do laboratório. A disponibilidade dos caminhos compartilhados pode depender de instalações anteriores.
3. Medir o layout mínimo real usando os componentes v141 x86 e SDK 14393 e recalcular espaço; não prosseguir se o total de layout + instalação + SDK + projeto/build + temporários consumir a reserva de 3 GiB.
4. Confirmar KB4490628, KB4474419, KB2999226, .NET Framework e estado de reinicialização por metadados de sistema antes de qualquer pacote de runtime/SDK.
5. Fazer nova verificação de OPC Core Components x86 (produto/versão/registro e arquivos). Baixar o OPC Foundation pacote apenas se a instalação existente estiver ausente ou incompatível e houver acesso/licença confirmados.
6. Depois de uma aprovação separada, baixar primeiro no AUTOU3, registrar tamanho, assinatura e SHA-256, e então propor transferência para `D:\OPC-DA-Bridge\offline\...`. Não instalar, registrar COM ou iniciar o servidor no mesmo passo.

O retarget aprovado foi aplicado ao `.vcxproj`; a máquina B não recebeu alterações. A geração do layout também depende de um host Windows com internet, que não está disponível no AUTOU3 Linux.

**Estado do pacote: ainda bloqueado para preparação física.** A capacidade nominal parece caber por cerca de 0,73 GiB além da reserva de 3 GiB, mas o layout real não foi medido e não pode ser gerado no AUTOU3 Linux pelo instalador oficial. Também falta confirmar a instalação final do SDK exclusivamente em D: e o SSU KB4490628. Nenhum artefato foi transferido ou instalado.

## Fontes oficiais

- [Visual Studio 2017 System Requirements](https://learn.microsoft.com/en-us/visualstudio/releases/2017/vs2017-system-requirements-vs): Windows 7 SP1 e intervalo de armazenamento Build Tools.
- [Visual Studio 2017 15.9 Release Notes](https://learn.microsoft.com/en-us/visualstudio/releasenotes/vs2017-relnotes): release final 15.9.61.
- [Finding the Visual C++ compiler tools in Visual Studio 2017](https://devblogs.microsoft.com/cppblog/finding-the-visual-c-compiler-tools-in-visual-studio-2017/): workload Build Tools, identificador `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` para o toolset v141 e identificador do SDK 14393.
- [Criar instalação offline do Visual Studio](https://learn.microsoft.com/en-us/visualstudio/install/create-an-offline-installation-of-visual-studio): geração de layout com bootstrapper Windows e uso de `--noWeb`.
- [Visual Studio 2017 C++ workload/release notes](https://learn.microsoft.com/en-us/visualstudio/releases/2017/vs2017-relnotes-v15.0): componentes v141 e Windows SDK 10.0.14393 para Win32.
- [Propriedades gerais de projeto C++](https://learn.microsoft.com/en-us/cpp/build/reference/general-property-page-project?view=msvc-170): distinção do SDK de build e mínimo de runtime; `_WIN32_WINNT=0x0601` para Windows 7 e observação de que o C++ não valida esse mínimo.
- [Método de layout mínimo no Visual Studio Installer](https://learn.microsoft.com/en-us/answers/questions/92830/build-c-code-using-cmd-or-powershell): exemplo Microsoft de `vs_buildtools.exe --layout` para o workload VCTools.
- [Arquivo do Windows SDK](https://learn.microsoft.com/en-us/windows/apps/windows-sdk/downloads-archive): SDK 10.0.14393.795 e instalador oficial.
- [Visual C++ downloads e compatibilidade](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist): requisito runtime da mesma versão ou posterior; [download de versões antigas](https://learn.microsoft.com/en-us/cpp/windows/troubleshoot-vc-redistributable-installation-issues?view=msvc-170).
- [KB2999226: Universal C Runtime](https://support.microsoft.com/en-us/servicing/os/windows/2020/04/update-for-universal-c-runtime-in-windows).
- [Requisito SHA-2 para Windows 7](https://support.microsoft.com/pt-br/servicing/os/windows/2020/09/2019-sha-2-code-signing-support-requirement-for-windows-and-wsus).
- [OPC Foundation Core Components](https://opcfoundation.org/developer-tools/samples-and-tools-classic/core-components).
