# Credenciamento BRACIS

Sistema local para pré-check-in, busca de kits, atendimento em guichês e acompanhamento da coordenação. Usa Python 3.10+ e a biblioteca padrão, sem dependências de aplicação.

## Fluxo

1. Participante abre `/` pelo QR Code, informa nome completo e e-mail da inscrição e confirma presença.
2. Voluntário abre `/busca`, assume uma pessoa, busca o kit e marca que o deixou no guichê mostrado no cartão.
3. Atendente abre `/fila` e confirma a retirada após entregar o kit. A conta do atendente determina o guichê; a coordenação pode usar `/fila?guiche=1`.
4. Coordenação abre `/painel` para totais, situação individual e estado da sincronização.

As telas da equipe exigem conta e senha. O participante nunca vê a lista completa nem o CPF. A fila atualiza automaticamente a cada cinco segundos. As confirmações de entrega pedem um segundo clique.

## Identidade visual

As telas usam a marca **BRACIS 2026** e os arquivos de logo, Bebas Neue e Noto Sans copiados para `static/assets/` do tema local `bracis-edition`. Cores e hierarquia visual seguem o `theme.json` e o `DESIGN.md` desse tema. A interface de credenciamento permanece em português e usa os termos crachá, kit, busca e retirada de forma consistente.

## Preparação

Revise [config/guiches.json](config/guiches.json) antes de importar a lista real. As faixas incluídas são **somente um exemplo**. Cada faixa inclui as letras inicial e final; nomes com acentos são normalizados. A importação falha se a inicial não estiver coberta por uma faixa única. Uma coluna `guiche` no arquivo pode substituir a regra para casos específicos.

CSV: cabeçalhos `name,email,cpf,affiliation,guiche`; apenas `name` e `email` são obrigatórios quando as faixas estão configuradas. JSON: lista de objetos com essas mesmas chaves, ou objeto `{ "participants": [...] }`. Há um modelo em [exemplos/participantes.csv](exemplos/participantes.csv). Se importar novamente após ajustar as faixas, os guichês são atualizados e os estados e horários de credenciamento permanecem. Uma mudança de guichê é recusada se a busca do kit ou a retirada já começou.

```sh
python3 app.py import caminho/participantes.csv
python3 app.py user coordenacao admin
python3 app.py user voluntario1 volunteer
python3 app.py user atendimento1 attendant --guiche 1
export BRACIS_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python3 app.py serve --host 127.0.0.1 --port 8000
```

Senhas são pedidas no terminal. O segredo de sessão deve ser salvo num gerenciador de segredos ou variável persistente do servidor; se mudar, sessões existentes expiram. Para acesso por celular ou tablet, use um domínio com HTTPS apontando para o servidor e configure `BRACIS_PUBLIC_URL=https://seu-dominio`. Publique apenas o aplicativo por um proxy HTTPS, mantenha a pasta `data/` fora do acesso web e faça backup periódico dessa pasta. O servidor de desenvolvimento escuta apenas em `127.0.0.1` por padrão.

Se o proxy HTTPS estiver no mesmo servidor, configure `BRACIS_TRUSTED_PROXY=1` e faça o proxy substituir o cabeçalho `X-Forwarded-For` pelo IP real do cliente. Isso mantém o limite de tentativas individual mesmo com muitos participantes atrás do proxy. Sem essa configuração, todas as requisições repassadas de `127.0.0.1` compartilham o mesmo limite.

O QR Code deverá apontar para a URL pública da raiz (`https://seu-dominio/`). Gere e imprima o código quando o domínio estiver definido; antes da implantação não há URL definitiva para codificar.

## Armazenamento e cópia

O arquivo `data/credenciamento.sqlite3` é a fonte principal. Cada alteração gera um evento com data e responsável. `data/participantes.csv` é uma cópia local para consulta, reconstituída na partida e atualizada a cada alteração. Campos que poderiam ser interpretados como fórmulas recebem um apóstrofo de segurança nessa cópia; use a lista original para reimportações. Ambos contêm dados pessoais; proteja a pasta, backups e contas do servidor.

Para criar uma cópia consistente do banco enquanto o serviço está em uso, execute `python3 app.py backup /caminho/seguro/backup-AAAA-MM-DD.sqlite3`. O comando não substitui um backup existente. Guarde cópias fora do servidor do evento.

O sistema guarda os eventos pendentes em `sheet_outbox` no mesmo banco até o Google Sheets confirmar o lote. Se o serviço ficar fora do ar, a fila de sincronização continua guardada e será tentada novamente a cada 15 segundos. Rode `python3 app.py sync` para forçar uma tentativa. O painel mostra quantas alterações aguardam envio.

## Google Sheets (configurar depois)

1. Crie uma planilha vazia e abra **Extensões → Apps Script**.
2. Cole [google-sheets/Code.gs](google-sheets/Code.gs) no projeto vinculado à planilha.
3. Em **Configurações do projeto → Propriedades do script**, crie `BRACIS_SHEETS_SECRET` com um valor longo e aleatório.
4. Implante como **Aplicativo da Web**, executado como proprietário, com acesso para qualquer pessoa que tenha a URL. A chave no corpo da requisição protege as gravações; não compartilhe URL ou chave.
5. No servidor, configure `BRACIS_SHEETS_URL` com a URL do aplicativo, `BRACIS_SHEETS_SECRET` com a mesma chave e reinicie o serviço.
6. Verifique no painel que a fila pendente zera e confira as abas `Participantes` e `Histórico` antes do evento.

O envio preserva revisões mais recentes e não duplica IDs de eventos em tentativas repetidas. O teste com uma planilha real ainda depende dessas credenciais.

## Verificação local

```sh
python3 -m unittest discover -s tests -v
```

Antes do uso real, teste o fluxo completo em vários celulares e guichês com uma cópia da lista, confirme o domínio HTTPS e faça um ensaio de perda de rede e retorno da sincronização.
