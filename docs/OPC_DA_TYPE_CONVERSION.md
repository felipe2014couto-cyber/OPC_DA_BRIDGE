# Conversão automática e quatro tipos publicados

## Uso

Ao criar uma variável OPC DA, escolha o tipo de saída e mantenha
**Conversão da origem: Automática**. O agente verifica o escalar devolvido
pela Automation; o tipo não é inferido pelo nome do item.

| Saída | Configuração | Tipo canônico DA |
| --- | --- | --- |
| Real | `Double` | `VT_R8` / 5, ponto flutuante de 64 bits |
| Inteiro | `Integer` | `VT_I4` / 3, inteiro com sinal de 32 bits |
| String | `String` | `VT_BSTR` / 8, texto Unicode |
| Booleano | `Boolean` | `VT_BOOL` / 11 |

As constantes estão documentadas pela [Microsoft em VARIANT Type Constants](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-oaut/3fe7db9f-5803-4dc4-9d14-5425d3f5461f).
Uma origem Float/VT_R4 pode ser convertida para Real/Double; Float não é uma
quinta saída. Os modos legados sem conversão e `float_to_double` continuam
aceitos. Configurações existentes não são convertidas automaticamente.

## Regras

O vínculo automático usa `source_data_type=Auto` e
`source_conversion=automatic`. `source_vartype` e `published_vartype` são
validados separadamente. Aliases VT_INT/VT_I4 podem ter a mesma representação
CLR retornada pela Automation.

Entradas aceitas: Boolean, String/BSTR, Float, Double e inteiros com ou sem
sinal de 8/16/32/64 bits. Arrays, objetos, datas, Currency e Decimal ficam fora
deste escopo.

| Saída | Regra de conversão |
| --- | --- |
| Real | Número finito; Boolean vira 0/1; texto numérico usa cultura invariável, ponto decimal e expoente. Inteiro de 64 bits fora de ±2^53 é recusado para preservar precisão. |
| Inteiro | Número integral entre -2147483648 e 2147483647; Boolean vira 0/1; texto aceita sinal opcional e dígitos decimais, sem fração, separador ou expoente. |
| String | Texto preservado, ou escalar convertido com cultura invariável; Boolean vira `true`/`false`. Máximo de 4096 unidades UTF-16. |
| Booleano | Boolean, número 0/1 ou texto `true`/`false`/`0`/`1`, ignorando maiúsculas e espaços externos. |

Não há truncamento de fração, saturação nem interpretação de vírgula na origem.
Conversão de tipo não aplica escala de engenharia nem altera unidades. Real
segue a precisão usual de ponto flutuante binário de 64 bits. Unicode e NUL
interno em BSTR foram testados; substitutos Unicode isolados são recusados.

Conversões inválidas retornam por item `OPC_E_BADTYPE` / `0xC0040004`, sem
qualidade Good. Conversões válidas preservam qualidade e timestamp de origem.
Timestamp inválido não é substituído pela hora atual. A resposta da origem
inclui `source_quality` para diagnóstico; isso não é uma segunda qualidade
publicada no DA.

Python valida metadados e valores antes do transporte. Rust valida novamente o
lote e atualiza o cache atomicamente. A normalização contextual de JSON impede
que um Double integral serializado como `7` passe a ser item Integer. Clientes
DA permanecem somente leitura.

## Validação registrada

- Suíte Python: 194 aprovados e 1 ignorado.
- Teste de navegador com backend e agentes simulados: cadastro das quatro
  saídas, conversão automática como padrão e salvamento sem erro JavaScript.
- PowerShell 2 x86: 48 casos de conversão, incluindo cultura pt-BR, Unicode,
  overflow e valores não finitos.
- Modelo Rust: 18 testes; adaptador COM: 7 testes.
- Teste COM entre processos em Windows 7 SP1 x86 com fixture simulada: browse,
  leitura Cache/Device, quatro tipos, qualidade e timestamps. Isso é distinto
  de teste de lógica em processo.
- Leitura somente leitura de uma origem OPC DA de laboratório confirmou
  conversões numéricas e Boolean. Identificadores, endereços e valores da
  origem foram omitidos desta documentação pública.
- PI OPC Client confirmou browse hierárquico e leitura Good de uma variável
  Real e uma Boolean após a atualização do Item ID publicado. Os tipos Integer
  e String ainda não foram validados visualmente nesse cliente.

Windows XP, Windows x64 para COM ABB, PI Interface, produção e operação
prolongada não foram validados. Notificações automáticas para todos os quatro
tipos também permanecem sem validação.

## Árvore de browse

A hierarquia do OPC DA é formada pelos segmentos do Item ID **publicado**;
a localização no configurador e o Item ID de origem são independentes. Para
que duas variáveis apareçam na mesma pasta, seus Item IDs publicados devem
compartilhar o prefixo daquela pasta. No cliente, desmarque **Flat**, use
**List** e navegue pelos ramos. Na entrada manual, informe o Item ID publicado,
nunca o ID da origem.

## Dependências e licenças

Nenhuma dependência nova foi instalada para esse trabalho. O modelo e os
helpers criados pelo projeto usam MIT. O toolchain isolado usa Rust nightly e
MinGW para a compilação cruzada x86; versões e comandos de build ficam no
script e no guia do servidor de laboratório.

O checkout upstream `Ronbb/rust_opc` continua fixado na revisão já documentada.
As dúvidas de licença sobre arquivos derivados OPC Foundation e bindings
continuam pendentes; esta implementação permanece para laboratório interno e
nenhum desses arquivos é republicado por este guia. Não há afirmação de
compatibilidade com sistemas além dos ambientes explicitamente testados.
