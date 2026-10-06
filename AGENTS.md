# MUR — Model Usage Reports

Projeto independente em `~/Code/mur`. Não pertence a Games ou a um projeto de relatório pessoal.

- App instalado: `~/Applications/MUR.app`; perfil: `~/Library/Application Support/MUR`.
- Windows: instalador por usuário; perfil `%LOCALAPPDATA%\MUR`, preservado na desinstalação. Testes e executável Windows são validados em Windows com fontes sintéticas. Nunca usar `os.kill(pid, 0)` para observar processos no Windows.
- Registros originais são somente leitura. Nunca apagar, mover, truncar ou reduzir históricos Codex/Claude/Grok.
- Instaladores usam a lista pública explícita de `build_release.py`. Não incluir perfis, contas, tokens, relatórios pessoais ou `installation/`.
- Saldos vêm dos serviços dos provedores, somente para o login atual deste Mac. Ausência de resposta não significa saldo zero. Créditos não equivalem a dólares.
- Consulta de saldo é opcional. Credenciais permanecem em memória e no armazenamento original; nunca enviar para a interface, exportações, logs ou hospedagem.
- Arquivos de versões já publicadas são imutáveis. Alterar versão, documentação e notas antes de gerar uma nova distribuição.

Validação principal:

```sh
python3 -m unittest -q test_limits test_release test_billing test_accounts test_local test_machines test_replay test_portable test_windows
node --test distribution/test-worker.mjs
node --check web/app.js
node --check web/flow.js
```

Mudanças nativas também exigem `python3 build_release.py --validation` e `python3 verify_release.py installation/build-<id>/MUR.app`.

Windows: `python windows/build.py` e `python windows/verify.py dist/windows/app/MUR/MUR.exe`, além da instalação/desinstalação verificada em `.github/workflows/windows.yml`.
