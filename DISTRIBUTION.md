# MUR — Model Usage Reports

3.0.0 beta 5 · macOS 13 ou superior · Apple Silicon e Intel.

## Instalação

Abra o DMG e arraste MUR para Aplicativos. Abra o MUR no Finder, Dock ou Spotlight. Python, Homebrew e ferramentas de desenvolvimento já não são necessários.

Esta beta tem assinatura local, sem certificação de distribuição da Apple. Um download pode ser bloqueado pelo macOS. Em um teste de uma cópia cuja origem você conferiu, a liberação individual fica em Ajustes do Sistema → Privacidade e Segurança → Abrir Mesmo Assim. A versão pública definitiva depende de Developer ID e notarização; não desative o Gatekeeper.

## Primeiro uso

O MUR identifica as pastas de registros e mostra contas e planos encontrados nos arquivos locais. Clique em **Começar análise** ou em **Revisar contas e mensalidades**. A revisão é opcional e continua disponível em **Configurações → Contas e assinaturas**. O valor pago precisa ser confirmado: metadados de plano não são faturas e podem estar em cache.

Cadastre cada período de preço uma vez, mesmo usando vários Macs. Você pode vincular uma conta detectada ou cadastrar manualmente; para várias contas de mesmo valor e vigência, use Quantidade. Informe a data de início e o primeiro dia sem aquele preço. Quando o valor mudar, encerre a faixa anterior e adicione uma nova. Datas ou valores desconhecidos permanecem a confirmar; zero no valor significa gratuito. As contas já observadas continuam na lista após uma troca de login. Registros antigos sem identificação não são associados automaticamente à conta atual.

Para uma assinatura encerrada, desmarque as próximas renovações e preencha as datas conhecidas. A data de cancelamento pode ser diferente do fim do acesso pago; o trecho sem cobertura confirmada fica pendente. Em **Pagamentos efetuados**, registre cada cobrança uma vez, com data e total pago, já incluindo a quantidade de contas. Prever cancelamento no MUR não cancela seu contrato com o provedor.

Não é necessário entrar em uma conta nem fornecer uma chave de API. Se você ainda não usa esses agentes, o painel começa vazio. Conversas apenas na web não entram automaticamente. Os formatos locais compatíveis são os registros JSONL de Codex e Claude Code, sessões do Grok CLI e registros de seus companions. Formatos diferentes podem precisar de novos adaptadores.

O fluxo de trabalho reproduz sessões locais do Claude Code. A seleção mostra até 100 sessões recentes e o comparativo, até 12. Histórico, projetos e consumo incluem os três provedores quando há registros compatíveis.

Filtros, navegação e ações demoradas mostram um indicador de carregamento. Os dados anteriores ficam atenuados enquanto a consulta termina; você pode ajustar os filtros novamente. Uma consulta substituída não sobrescreve a seleção mais recente. Se falhar, use **Tentar novamente**. Salvamentos, importações e exportações mostram o andamento e impedem repetir a mesma ação enquanto ela está em execução.

## Uso disponível e barra de menus

Abra **Uso disponível** e ative **Consultar saldos das contas conectadas**. O MUR consulta o percentual restante e a próxima renovação usando os logins que já existem neste Mac. Não é necessário informar uma chave de API. As consultas ocorrem a cada cinco minutos, inclusive com a janela fechada, e podem ser desativadas nessa tela.

Codex precisa estar instalado e autenticado. O saldo do Claude é opcional: histórico, tokens e custos funcionam sem conectar. O MUR só acessa o Chaves depois de clicar em **Conectar Claude**; essa conexão vale enquanto o aplicativo estiver aberto. O macOS pode pedir autorização nesse momento, e você pode negar e continuar usando o MUR. As consultas automáticas não abrem pedidos de senha nem tentam desbloquear o Chaves. Se você negar ou a leitura falhar, o MUR aguarda uma nova conexão explícita; os outros provedores continuam funcionando. Se o login de um provedor expirou, entre novamente no aplicativo dele. Uma conta detectada nos registros não garante que o serviço de saldo esteja disponível. Saldo desconhecido não é zero; o último valor conhecido fica marcado como desatualizado quando a consulta falha ou a data de renovação passa. Créditos adicionais do Codex são créditos informados pelo serviço, não dólares.

O ícone MUR na barra de menus mostra o saldo do Codex quando disponível; no menu, aparecem os demais provedores e as renovações. O valor destacado corresponde ao período mais restrito. A opção **Mostrar MUR na barra de menus** permite ocultar o item. Fechar a janela mantém o monitor funcionando; **⌘Q** encerra o aplicativo e as consultas. Saldos são das contas atuais deste Mac: históricos importados e contas antigas não fornecem saldo ao vivo.

## Outros computadores

Em cada Mac, conclua a leitura e use **Configurações → Exportar este Mac · 7 dias**. Transfira o arquivo `.mur` e use **Importar outro Mac** no computador onde deseja consolidar. O arquivo contém identificadores, títulos, projetos, caminhos de origem e consumo; mensagens não são incluídas. Compartilhe-o apenas com quem deve receber esses metadados.

Eventos repetidos são deduplicados, com preferência pela cópia local. A janela “7 dias · coleta importada” usa o período do último pacote. Importações anteriores mantêm seus períodos originais, indicados na interface. A coleta é pontual, sem conexão automática entre Macs.

## Atualizações

Use **MUR → Verificar atualizações…** ou **Configurações → Atualizações do MUR**. Nas edições conectadas ao canal `mur.lendario.ai`, o aplicativo avisa quando há uma nova versão, mostra as novidades e permite instalar e reabrir. Você pode desativar os avisos automáticos. Histórico, contas e configurações ficam no perfil e são preservados ao substituir o aplicativo.

Uma edição sem canal configurado informa essa condição; ela não afirma estar atualizada. Quem instalou a primeira beta sem atualizador precisa instalar manualmente a primeira edição com esse recurso. A partir dela, as próximas versões podem ser recebidas dentro do app. O macOS ainda pode aplicar suas verificações de segurança a betas não notarizadas.

## Privacidade e armazenamento

Cada conta do macOS tem seu próprio perfil em `~/Library/Application Support/MUR`. O instalador não contém históricos, contas, mensalidades, relatórios ou configurações de nenhum usuário. Os logs originais são somente leitura. Não há telemetria, upload automático ou chamadas às APIs de modelos.

As verificações de atualização acessam o catálogo público e baixam instaladores pelo HTTPS. Não enviam conversas, consumo, credenciais ou perfil de sistema; o servidor recebe os dados normais de uma conexão, como endereço IP e identificação do aplicativo. Se você ativar a consulta de saldos, as credenciais locais são usadas apenas para autenticar pedidos aos serviços dos respectivos provedores. Elas não são enviadas à interface, exportadas, copiadas para o perfil nem para os servidores do MUR. Os metadados de contas ficam no perfil local, com identificadores por hash e e-mails abreviados, sem copiar chaves de acesso.

O serviço funciona apenas na interface de rede local do próprio computador e acompanha o aplicativo. Fechar a janela permite reabri-la pelo Dock. **⌘Q** encerra o MUR e a coleta; ao abrir novamente, ele lê as novidades. A janela não exibe endereço de servidor. Abrir referências de preços ou conversas Codex aciona o aplicativo externo correspondente.

## Interpretação dos valores

Custo técnico equivalente é uma estimativa, não uma fatura. A tabela de tarifas tem data de referência visível, não é atualizada automaticamente e modelos sem preço ficam como não apurados. Custos registrados no Grok têm precedência sobre estimativas.

O custo proporcional das assinaturas considera o valor vigente em cada dia, usando uma base de 30 dias. A previsão mensal mostra somente as renovações ainda previstas. Os pagamentos cadastrados mostram cobranças pelas suas datas e nunca são inventados a partir da mensalidade. Consultas com dias parciais incluem pagamentos nas datas tocadas pelo período, sem precisão de horário. Não some custo técnico, proporcional e pagamentos como se fossem três cobranças diferentes.

## Componentes

Janela AppKit/WebKit e serviço Python incluído no aplicativo. Runtime CPython fornecido pelo projeto python-build-standalone; versões e hashes estão em `Contents/Resources/runtime-manifest.json`. Licenças do Python e componentes acompanham os runtimes. Fontes Geist, Geist Mono e Source Serif são distribuídas sob SIL Open Font License; texto completo em `Contents/Resources/backend/web/fonts/LICENSES.txt`.

Atualizações fornecidas pelo Sparkle 2.10.0, com licença em `Contents/Resources/Sparkle-LICENSE.txt` e manifesto de integridade em `Contents/Resources/sparkle-manifest.json`.
