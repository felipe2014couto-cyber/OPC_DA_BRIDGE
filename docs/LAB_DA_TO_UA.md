# Laboratório: OPC DA local para OPC UA

## Escopo e decisão técnica

Este módulo isolado executa **servidor DA local → cliente DA → servidor UA**. O cliente DA é `OPC.Automation.1` via pywin32/COM Automation e só chama `Connect`, `AddItem`, `OPCItem.Read(2)` e `Disconnect`; não existe método de escrita. A leitura é solicitada diretamente ao dispositivo. Chamadas COM ficam em uma thread única, com `CoInitialize`/`CoUninitialize`, para não bloquear o event loop UA nem migrar o objeto COM entre apartamentos. O núcleo atual, transporte TLS e fluxo de demonstração não são alterados.

Foi escolhido Python para aproveitar o modelo `ItemSample`, a classificação DA e o cache `BoundedLatestValueCache` existentes, sem introduzir novo host COM/serviço. `asyncua` (fixada em 1.1.8) é LGPLv3+, Python >=3.8 conforme metadata do pacote; `pywin32` (fixado em 312) usa PSF License e contém componentes com avisos/licenças próprios. Pywin32 só instala no Windows e seus wheels atuais exigem CPython moderno. [asyncua 1.1.8](https://pypi.org/project/asyncua/1.1.8/), [pywin32 312](https://pypi.org/project/pywin32/312/).

Cliente .NET OPC UA da OPC Foundation seria a alternativa para instalação Windows mais tradicional, sob licença MIT, mas não foi escolhido porque exigiria outro executável/solução .NET e não melhora o problema XP: a documentação atual lista .NET Framework 4.8 e alvos modernos. A alternativa não foi compilada nem validada aqui. [UA-.NETStandard](https://github.com/OPCFoundation/UA-.NETStandard).

## Compatibilidade e evidências

- Projeto: Python >=3.8 para o core; extra de laboratório requer versões modernas de CPython. A arquitetura do Python deve corresponder ao COM server ABB (x86 com COM x86 ou x64 com COM x64); registrar ou alterar COM/DCOM não é feito pelo programa.
- **Validação no laboratório Windows 10 x64 em 2026-11-03**: Windows 10 Enterprise x64 `10.0.19045.6456`; CPython 3.13.17 x86; `asyncua==1.1.8`; `pywin32==312`. A ProgID ABB e `OPC.Automation.1` aparecem no Registro. `OPCDAAuto.dll` foi encontrada na view COM x86, não na x64. Python x86 conectou ao ABB e leu as duas tags com `OPCItem.Read(2)`. O gateway publicou os dados em `127.0.0.1:4840`; um cliente `asyncua` local leu valores, tipos UA, qualidade Good e `SourceTimestamp`. A leitura em lote `SyncRead` retornou `E_INVALIDARG` nesse host e foi substituída pela leitura individual `OPCItem.Read(2)`.
- A Automation informou `AccessRights=3` (leitura e escrita) para os itens testados. O conversor não chama métodos de escrita DA; sua leitura usa somente `OPCItem.Read(2)`. A permissão de escrita anunciada pelo ABB não foi alterada.
- O instalador x86 oficial do Python 3.13.17 teve SHA-256 conferido com python.org, verificação Sigstore aprovada para a identidade publicada do release manager e Authenticode `Valid` no Windows. As wheels offline tiveram hashes conferidos com o JSON oficial do PyPI e foram rechecadas no Windows. Python 3.13.17 foi a última versão 3.13 com instalador Windows; 3.13.18 passou a ser source-only. [Python 3.13.17](https://www.python.org/downloads/release/python-31317/), [guia Sigstore do Python](https://www.python.org/downloads/metadata/sigstore/).
- A validação não constitui homologação do ABB: foram observados dois valores e timestamps em uma sessão. Reconexão após indisponibilidade física do ABB, interpretação temporal sob outras configurações regionais, estabilidade prolongada e compatibilidade com outros releases ABB seguem pendentes.
- **Windows XP não é compatível nem validado.** asyncua 1.1.8 declara Python >=3.8, mas isso não significa suporte a Windows XP; o conjunto validado usa CPython 3.13 x86 e pywin32 312, e não foi testado nem suportado em XP. O sistema está fora de suporte Microsoft desde 8/abr/2014. Não se deve rebaixar dependências ou declarar compatibilidade XP com base neste protótipo. [ciclo de vida Windows XP](https://learn.microsoft.com/en-in/lifecycle/products/windows-xp).
- As versões fixadas são uma referência reproduzível, não uma afirmação de homologação ou suporte de fornecedor. A disponibilidade de wheels x86/x64 deve ser confirmada para a versão de CPython escolhida. pywin32 312 tem wheel x86/x64 para versões recentes do CPython; isso não garante que servidor ABB x86/x64 específico seja acessível.

## Qualidade, timestamp e tipos

O tipo é inferido do valor efetivamente retornado pelo COM em cada leitura (`bool` antes de `int`; `int` usa I16/I32 pelo intervalo; `float` vira F64; bytes e texto preservam categoria). Valores de outros tipos geram erro explícito; não há inferência pelo nome da tag. A classe Python retornada também vai para o log.

Como o tipo ainda não é conhecido antes da primeira leitura DA, cada variável UA inicia com `DataType=BaseDataType` e valor nulo com status Bad_NoCommunication. Antes de publicar a primeira amostra, o gateway atualiza `DataType` para o tipo UA equivalente ao valor observado; se o tipo real mudar, atualiza novamente antes de escrever o valor. O cliente UA continua sem permissão de escrita.

Mapeamento do major quality DA para `StatusCode` UA: `Good (DA & 0xC0 == 0xC0)` → `Good (0x00000000)`; `Uncertain (0x40)` → `Uncertain (0x40000000)`; `Bad (0x00)` → `Bad (0x80000000)`. Substatus e limit bits DA não cabem nesse mapeamento genérico e são descartados. Indisponibilidade ou stale → `Bad_NoCommunication (0x80310000)`. Erros HRESULT por item preservam o último valor/timestamp, se houver, e o rebaixam para Bad; HRESULTs COM conhecidos de objeto desconectado/servidor indisponível encerram a sessão e acionam reconexão. Antes da primeira amostra o node inicia Bad_NoCommunication. Não enviar Good durante desconexão. O valor armazenado mais recente pode continuar visível no cache, mas sua qualidade é Bad.

Timestamp DA válido é convertido para UTC e publicado como UA `SourceTimestamp`. Timestamp ausente, zero ou não representável é registrado como warning e publicado sem `SourceTimestamp`; **nunca** é preenchido com o relógio do gateway. Na Automation, a representação de data/hora pode depender da conversão OLE/local do servidor COM, por isso deve ser validada com a ABB no host alvo.

O cache só contém estado atual limitado. Ele não persiste automaticamente nem reconstrói alterações ocorridas enquanto o DA esteve desconectado. Não é histórico nem buffer de eventos.

## Segurança

`lab-config.example.json` continua usando `local_no_security` em `127.0.0.1`. O código rejeita qualquer bind NoSecurity fora de loopback. Esse modo não criptografa nem autentica e serve somente a um cliente local isolado; nunca publique esse endpoint na rede.

Para máquina 2, `lab-config.secure.example.json` prepara uma opção independente: `Basic256Sha256` + `SignAndEncrypt`, autenticação de usuário por certificado X.509 (`X509IdentityToken`) e validação mútua dos certificados das aplicações. O servidor valida somente certificados públicos `.der` colocados manualmente em `trusted_client_certificates_dir`; certificados não conhecidos não são aceitos automaticamente. A credencial de usuário associada a cada certificado tem papel comum, e as variáveis do gateway permanecem não graváveis. Esta configuração ainda precisa de teste integrado com a máquina 2 e com um cliente UA real.

Preparação dos certificados, sem colocar chaves privadas no projeto:

1. Escolha um nome DNS estável para a máquina 1 que a máquina 2 resolva. Configure esse DNS em `opc_ua.host`; o certificado do servidor precisa incluir o nome DNS e o `application_uri` configurado. No Python do laboratório, `asyncua.crypto.cert_gen.setup_self_signed_certificate` gera DER público e chave PKCS#8 PEM localmente, com validade de um ano. Em M1, numa pasta privada `pki`, execute:

   ```powershell
   $py = "$env:USERPROFILE\opc-da-ua-lab\.venv\Scripts\python.exe"
   & $py -c "import asyncio,socket; from pathlib import Path; from asyncua.crypto.cert_gen import setup_self_signed_certificate; from cryptography.x509.oid import ExtendedKeyUsageOID; p=Path('pki'); p.mkdir(exist_ok=True); asyncio.run(setup_self_signed_certificate(p/'server-private-key.pem',p/'server-certificate.der','urn:opc-da-bridge:lab:server',socket.gethostname(),[ExtendedKeyUsageOID.SERVER_AUTH],{'countryName':'BR','organizationName':'OPC DA Bridge Lab'}))"
   ```

   Em M2, execute o mesmo helper localmente, trocando os arquivos para `client-private-key.pem`/`client-certificate.der`, o URI para um URI único do cliente e o uso para `ExtendedKeyUsageOID.CLIENT_AUTH`. O certificado do cliente é identidade de aplicação e também identidade do usuário UA. Não copie a chave privada para a outra máquina. O helper inclui o hostname; para acessar por IP em vez de DNS, use certificado emitido pela PKI com esse IP em `subjectAltName` e valide o perfil do cliente.
2. Copie somente o certificado público DER do cliente para M1 em `pki/trusted-clients/`. Copie somente o certificado público DER do servidor para a trust list do cliente UA em M2. Confira fingerprints SHA-256 por canal independente antes de confiar. Uma CA corporativa também pode emitir os certificados; instale a CA e CRL de acordo com a política local, sem aceitar automaticamente certificados pendentes.
3. Restrinja as ACLs das chaves privadas às contas que executam o conversor/cliente. Proteja os diretórios de trust list contra gravação por usuários não autorizados. Configure o cliente UA para Basic256Sha256/SignAndEncrypt e certificado de usuário X.509, validando o certificado do servidor. Não marque “trust all” ou equivalente.
4. Gere uma cópia local de `lab-config.secure.example.json`, preencha DNS, application URI e caminhos absolutos locais. Só então execute o endpoint seguro. O servidor não faz alterações em firewall, DCOM, serviços ou ABB. A rede deve permitir explicitamente o tráfego OPC UA conforme o processo operacional de TI.

   Um cliente `asyncua` pode conectar definindo primeiro `client.application_uri` para o URI do certificado, depois chamando `Client.set_security(SecurityPolicyBasic256Sha256, client-cert.der, client-key.pem, server_certificate=server-cert.der, mode=SignAndEncrypt)` e `load_client_certificate(client-cert.der)`, e só então `connect()`. O cliente deve validar o certificado do servidor por pin/trust store e não desabilitar a validação. No UA Expert, selecione o mesmo modo/política e identidade X.509, aprove o certificado público do cliente em M1 após verificar seu fingerprint e confie manualmente no certificado público do servidor em M2.

`asyncua==1.1.8` contém `Server.load_certificate`, `load_private_key`, `set_security_policy`, `set_identity_tokens`, `set_certificate_validator`, `CertificateValidator`/`TrustStore` e `CertificateUserManager`; o exemplo oficial da mesma versão demonstra `Basic256Sha256_SignAndEncrypt` e validação com trust store. Foi implementada somente essa política para a opção remota. Isso não garante interoperabilidade com UA Expert/cliente específico ainda não testado. Clientes/sistemas legados que não suportem esse perfil e certificados modernos exigem avaliação antes de qualquer exceção; a política não é enfraquecida automaticamente. Referências: [exemplo de criptografia v1.1.8](https://github.com/FreeOpcUa/opcua-asyncio/blob/v1.1.8/examples/server-with-encryption.py), [validador v1.1.8](https://github.com/FreeOpcUa/opcua-asyncio/blob/v1.1.8/asyncua/crypto/validator.py), [geração de certificados v1.1.8](https://github.com/FreeOpcUa/opcua-asyncio/blob/v1.1.8/asyncua/crypto/cert_gen.py).

Nenhuma credencial nem chave privada é mantida no repositório. Não use NoSecurity na interface da rede como alternativa temporária.

Logs texto são gravados no caminho configurado e incluem conexão, falha/desconexão e tipo de retorno. ProgID e Item IDs não são tratados como segredos; credenciais não são logadas. Exceções COM podem trazer dados do fornecedor e devem ser revisadas antes de compartilhar logs.

## Executar no Windows 10 x64

1. Instale CPython >=3.10 em arquitetura compatível com a classe COM Automation registrada. **No laboratório validado, use CPython x86**, pois `OPCDAAuto.dll` só consta na view x86. Não registrar componentes, instalar serviços ou mexer em DCOM/firewall como parte do gateway.
2. Para uma instalação offline equivalente à validada, transfira o instalador x86 e as wheels para uma pasta local. Verifique o SHA-256 do instalador contra a release oficial do Python e os hashes das wheels contra o PyPI antes de executar. Instale em pasta dedicada, crie o ambiente e instale sem índice de rede:

   ```powershell
   .\python-3.13.17.exe /quiet InstallAllUsers=0 TargetDir="$env:USERPROFILE\opc-da-ua-lab\python" PrependPath=0 Include_launcher=0 Include_test=0 Shortcuts=0
   & "$env:USERPROFILE\opc-da-ua-lab\python\python.exe" -m venv "$env:USERPROFILE\opc-da-ua-lab\.venv"
   & "$env:USERPROFILE\opc-da-ua-lab\.venv\Scripts\python.exe" -m pip install --no-index --find-links .\wheels "asyncua==1.1.8" "pywin32==312"
   $env:PYTHONPATH = "$env:USERPROFILE\opc-da-ua-lab\src"
   ```

   Se usar um checkout e rede de pacotes confiável, também é possível criar `.venv` e instalar `.[lab]` conforme o `pyproject.toml`.
3. Copie `lab-config.example.json` para `lab-config.json` e ajuste host/porta e Item IDs conforme o host local. As duas tags ABB solicitadas já estão no exemplo. Não versionar configurações locais.
4. Inicie com `python -m opc_da_bridge.lab.server --config lab-config.json`. O endereço padrão é `opc.tcp://127.0.0.1:4840/opc-da-bridge/lab/`. O intervalo de polling DA/UA é definido por `update_interval_s`. O log fica em `opc-da-ua-lab.log`.
5. No UA Expert ou outro cliente de teste, adicione esse endpoint, navegue Objects e leia as duas variáveis. Verifique valor, `StatusCode`, `SourceTimestamp` e desconexão. O nome NodeId é exposto no namespace registrado `urn:opc-da-bridge:lab`. Durante a validação de 2026-11-03, o cliente Python `asyncua` local confirmou os dois nós; nenhum UaExpert estava instalado.

## Testes

`tests/test_lab_gateway.py` cobre inferência de tipos, validação de timestamp, preservação de timestamp ausente, mapeamento de qualidade, erro por item, reconexão com fonte simulada e regra que impede NoSecurity fora de loopback. Isso não substitui teste COM/ABB nem validação integrada de certificados. A opção `sign_and_encrypt` ainda precisa de teste com certificados emitidos e cliente UA acessível.

## Ensaio de duração prolongada

O executor `python -m opc_da_bridge.lab.stability` sobe o conversor como processo filho, verifica exclusivamente o endpoint de loopback e requer as duas tags configuradas. O padrão é 3600 segundos, uma leitura UA a cada 15 segundos e métricas de processo a cada 30 segundos; todos os tempos podem ser ajustados pela linha de comando. A cada amostra registra valor, tipo Python, VariantType UA, StatusCode e timestamp de origem. Verifica consistência do tipo, status Good, timestamps não futuros e não regressivos. Timestamp de origem ausente é registrado como dado ausente, sem inventar um valor nem encerrar o ensaio; valor estável não é contado como falha. CPU acumulada, working set, handles e estado do **processo Python efetivo do gateway** são coletados via PowerShell; em Windows o executor segue o processo filho criado pelo launcher do `venv`. Eventos e falhas ficam em texto; leituras e métricas ficam em CSV.

No Windows 10 x64 validado, com o Python x86 do ambiente do laboratório:

```powershell
Set-Location $env:USERPROFILE\opc-da-ua-lab
$env:PYTHONPATH = "$PWD\src"
& .\.venv\Scripts\python.exe -m opc_da_bridge.lab.stability `
  --config .\lab-config.json --duration-s 3600 --sample-interval-s 15
```

O comando deve ficar ativo por uma hora. Não feche a sessão e não inicie outra cópia em 4840. `stability-results` recebe o log do ensaio, CSV das leituras UA, CSV dos recursos do gateway e log próprio do gateway. No encerramento normal, o executor encerra apenas o processo do conversor; não reinicia nem interrompe o serviço ABB. Uma interrupção do SSH/terminal pode encerrar o ensaio antes de 3600 segundos; o tempo efetivo é gravado no log.
