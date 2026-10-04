# MUR — Model Usage Reports

Aplicativo macOS independente para consultar consumo, conversas, projetos e atividade de agentes a partir dos registros locais de Codex, Claude Code/Companion e Grok CLI/Companion. O guia para quem instala está em [DISTRIBUTION.md](DISTRIBUTION.md).

O projeto fica em `~/Code/mur`, separado de Games. O aplicativo instalado e o perfil de dados têm pastas próprias; mover o código não muda o histórico nem exige migrá-lo novamente.

Código público: [github.com/oalanicolas/mur](https://github.com/oalanicolas/mur). Download da beta: [mur.lendario.ai](https://mur.lendario.ai/).

## Versão 3.0.0 beta 5

- Bundle universal para Apple Silicon e Intel, macOS 13 ou superior.
- Janela nativa AppKit/WebKit, ícone no Dock e nenhuma barra de endereço.
- Python incluído; não exige Homebrew, Python do sistema ou ferramentas de desenvolvimento no computador do usuário.
- Perfil por conta do macOS em `~/Library/Application Support/MUR`. Nenhum dado pessoal faz parte do instalador.
- Primeira abertura vazia, fontes locais detectadas e revisão opcional de contas e mensalidades antes de começar a análise.
- Contas e planos reconhecidos nos metadados locais compatíveis; valores pagos são confirmados pelo usuário. Contas já observadas permanecem disponíveis após trocas de login.
- Assinaturas por conta ou quantidade, com períodos de preço sem sobreposição para a mesma conta. Datas e valores desconhecidos ficam a confirmar.
- Pagamentos datados separados do custo proporcional e da previsão de renovação; cancelamento não apaga o que já foi pago.
- Uso disponível nas contas atuais de Codex, Claude Code e Grok, com percentual restante, renovação e indicação de resposta ausente ou desatualizada.
- Item nativo na barra de menus do macOS, com acesso ao painel, atualização de saldos e opção para ocultá-lo.
- Sparkle 2.10.0 integrado ao menu e às configurações. O canal de distribuição é `https://mur.lendario.ai/updates/appcast.xml`; compilar com `--updates-config macos/update-channel.json` para ativá-lo.
- Nome da máquina, fontes e fuso configuráveis; períodos e gráficos seguem o fuso do perfil.
- Resumo financeiro acima dos filtros. CSV e coletas de sete dias usam exportação nativa.
- Indicadores de carregamento em filtros, navegação, paginação, salvamentos, importações e exportações. Consultas substituídas são canceladas; falhas permitem tentar novamente.
- Coletas de outros Macs importadas pela interface, deduplicadas por evento, sem mensagens e sem sincronização automática.
- Reprodução de sessões locais do Claude Code sem seleção de conversa ou projeto fixos.
- O serviço inicia com o aplicativo e termina com ⌘Q. Um monitor de processo também encerra o serviço se o aplicativo morrer.

## Dados e limites

O índice lê os arquivos compatíveis nas fontes configuradas. Logs originais nunca são alterados. Contadores Codex são reconciliados, Claude deduplica usage pelo ID da mensagem e Grok deduplica turnos e interpreta o custo registrado. Modelos sem tarifa ficam sem preço apurado. A tabela de referência é datada e não é atualizada automaticamente.

Mensagens de usuário e assistente são exibidas como texto, com ocultação de padrões comuns de credenciais. Resultados de ferramentas, raciocínio interno e anexos não entram na transcrição. A ocultação não garante ausência de conteúdo sensível. Exportações de outro Mac incluem metadados de sessões e consumo, não mensagens.

Atribuição de projetos por pasta é uma hipótese, com confiança baixa; o usuário pode corrigi-la. Sinais dos logs e presença de processos não equivalem a controle remoto de agentes. Históricos de serviços apenas na web e formatos locais não suportados podem não ter cobertura.

Identidades de conta são transformadas em identificadores estáveis por hash e rótulos com e-mail abreviado. O registro local `account-observations.json` não guarda tokens, chaves nem e-mails completos. O plano pode estar em cache e não comprova cobrança. Eventos antigos sem identidade da conta não são atribuídos ao login atual.

O saldo disponível é uma consulta opcional aos serviços dos próprios provedores, a cada cinco minutos. Usa o login existente neste Mac: o App Server oficial do Codex e os serviços de uso de Claude/Grok. O Chaves do Claude só é acessado depois de clicar em **Conectar Claude**. Essa conexão é opcional e vale enquanto o MUR estiver aberto; histórico, tokens e custos funcionam sem ela. O botão pode pedir autorização para ler o item `Claude Code-credentials`; as leituras posteriores em segundo plano não abrem diálogos nem tentam desbloquear o Chaves. A beta 5 corrige esse comportamento nos Chaves tradicionais do macOS. Se o acesso falhar ou for negado, novas tentativas ficam suspensas até clicar em **Conectar Claude**. Tokens são usados apenas em memória, não passam para o WebKit nem entram no índice ou nas exportações. Perfis Claude personalizados precisam ter suas próprias credenciais compatíveis. Login expirado, serviço incompatível ou limite de consultas ficam indicados, sem inventar percentuais. Depois da renovação ou de uma falha, o último valor conhecido fica desatualizado até uma resposta nova. Créditos Codex são exibidos como créditos, sem conversão para dólares. Coletas importadas não fornecem saldo atual de outro Mac nem de uma conta antiga.

Cada assinatura tem períodos de preço: início inclusivo e fim exclusivo, no fuso do perfil. O custo proporcional aplica preço × quantidade / 30 apenas à interseção com o período consultado, por dias de calendário. Datas desconhecidas são sinalizadas; valores antigos migrados não retroagem automaticamente. Cancelar encerra a previsão de renovação; se a cobertura paga continuar, o fim da vigência deve ser informado. Sem essa informação, o trecho após o cancelamento permanece pendente. Pagamentos são lançamentos independentes, pelo valor total e pela data; não são multiplicados pela quantidade nem repetidos automaticamente. Em consultas com dias parciais, os pagamentos abrangem as datas de calendário tocadas pelo recorte, pois não há hora de cobrança registrada.

## Desenvolvimento e validação

Requer Python 3.13 ou superior para empacotar (extração segura de tar), Swift/Command Line Tools e ferramentas do macOS. O app distribuído traz seu próprio runtime.

```sh
python3 -m unittest -v test_limits test_release test_billing test_accounts test_local test_machines test_replay test_portable
node --test distribution/test-worker.mjs
node --check web/app.js
node --check web/flow.js
npm ci
npx playwright install chromium
npm run test:ui
python3 build_release.py
python3 build_release.py --validation
python3 verify_release.py installation/build-<id>/MUR.app
python3 verify_updates.py
python3 build_release.py --updates-config macos/update-channel.json --package
```

`installation/latest-build.json` identifica o bundle mais recente. `--validation` compila testes dentro de um app com identificador próprio; esse app não pode ser instalado como versão de uso. A validação nativa executa com perfis sintéticos nas duas arquiteturas, inclusive Intel via Rosetta neste Mac. Não substitui testes em um Mac Intel físico ou em cada versão mínima do macOS.

`build_release.py` usa uma lista explícita de arquivos públicos, runtimes com SHA-256 fixado em `macos/runtime-manifest.json`, compilação universal e verificação de assinatura. Rejeita históricos, índices, configurações particulares e referências pessoais no conteúdo próprio do bundle. Os artefatos ficam em `dist/`, acompanhados de SHA256SUMS.txt. Fontes e runtimes levam suas licenças.

O Sparkle também tem versão e hash fixados em `macos/sparkle-manifest.json`. `verify_updates.py` usa um aplicativo de teste isolado, chave descartável e servidor local para verificar instalação e reabertura, ausência de versão nova e rejeição de feed e pacote adulterados. Nenhum aplicativo pessoal participa desses testes.

## Publicação

A beta usa assinatura ad-hoc. Ainda não equivale a uma versão notarizada para download irrestrito. Com Developer ID disponível, compilar com `--sign '<identidade do Keychain>' --package`, enviar o pacote pelo fluxo oficial de notarização da Apple e anexar o ticket antes de publicar. Não guardar certificados ou senhas no repositório. Hospedar somente os arquivos da distribuição, nunca perfis, relatórios pessoais ou a pasta de desenvolvimento completa.

A chave Ed25519 de produção está no Chaves do macOS, na conta `app.mur.reports` do Sparkle. Somente a chave pública aparece na configuração de build. O catálogo e os arquivos de atualização precisam ser assinados; alterações posteriores no XML invalidam sua assinatura. As versões devem aumentar `CFBundleVersion` e nunca substituir o conteúdo de um arquivo já publicado. A checagem automática avisa sobre novas versões, mas não instala silenciosamente nem envia perfil de sistema. A primeira beta distribuída sem Sparkle exige uma instalação manual inicial da edição com o atualizador.

Para preparar a distribuição, use `python3 release_updates.py installation/build-<id>/MUR.app --notes macos/release-notes.html`. O comando confere o bundle, compara arquivos do ZIP com ele, assina a atualização e o catálogo e valida as assinaturas antes de promover o XML. Uma autorização cancelada no Chaves deixa a publicação pendente; nunca publique `appcast.pending.xml` ou um catálogo sem assinatura. A chave privada nunca vai para a hospedagem.

`dist/site/` contém exclusivamente a página pública, fontes, guia, hashes e catálogo assinado. `distribution/wrangler.jsonc` define o Worker de `mur.lendario.ai`, com instaladores no bucket R2 `mur-releases`. Publique os instaladores verificados no prefixo `downloads/` do bucket antes de publicar o site com Wrangler. O Worker admite apenas nomes de versão de MUR em GET/HEAD, sem listagem ou upload público. Não use a pasta raiz do projeto como origem de assets. Verifique após publicar o HTTP, os hashes dos downloads, a assinatura do catálogo e a checagem nativa de atualização. Novas versões alteram versão de build, notas e links de download; o canal permanece fixo.

## Instalação de desenvolvimento e migração

`python3 install.py` prepara e instala o bundle no usuário atual; `--prepare-only` apenas prepara. A instalação preserva o bundle anterior em `installation/backups`. Encerre a versão em execução antes da atualização.

A migração da antiga versão pessoal é explícita: encerrar o serviço antigo, executar `python3 migrate_profile.py data` e instalar a nova versão. A migração usa backup transacional do SQLite, conserva o índice original e guarda qualquer perfil de destino anterior. Mensalidades, classificação revisada, coleta importada e relatório salvo ficam exclusivamente no perfil local. Nada disso é copiado para os instaladores.

`server.py` aceita uma pasta de dados para desenvolvimento. No app, o servidor recebe uma porta local livre; a porta é preservada nas preferências para manter o armazenamento WebKit. O servidor rejeita origens externas, exige token para alterações e não escuta na rede. Dados e log de serviço ficam no perfil, nunca dentro do bundle.
