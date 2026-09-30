# Sistema de Credenciamento

Sistema local para pré-check-in, busca de kits, atendimento em guichês e acompanhamento da coordenação. Usa Python 3.10+ e a biblioteca padrão, sem dependências de aplicação.

## Fluxo

1. Participante abre `/` pelo QR Code, informa o CPF (ou nome completo e e-mail) como constam na inscrição e confirma presença. Ao confirmar, a página mostra o número do guichê de retirada e a faixa de letras atendida por ele. A página abre em inglês e oferece um botão para português brasileiro.
2. Voluntário abre `/busca`, assume uma pessoa, busca o kit e marca que o deixou no guichê mostrado no cartão.
3. Atendente abre `/fila` e confirma a retirada após entregar o kit. A conta do atendente determina o guichê inicial; o menu permite consultar outros guichês, mas cada atendente só pode confirmar entregas do próprio guichê. Na tela de um guichê, os cartões mostram os 3 primeiros dígitos do CPF para conferência; o número do guichê aparece nos cartões apenas em "Todos os guichês".
4. Qualquer pessoa pode abrir `/painel/resumo`, sem login, para acompanhar inscritos, pessoas que chegaram e participantes credenciados. A coordenação usa `/painel` para ver também as etapas intermediárias, a lista individual, o estado da sincronização, a importação/exportação de participantes e as faixas dos guichês. Pendências (pagamento não confirmado ou afiliação não informada) são tratadas no próprio painel detalhado: o atalho "Pendências de orientação" filtra quem chegou com pendência, o pagamento e a prioridade são alterados na linha, e a afiliação pode ser preenchida ali mesmo. Resolvidas as pendências, a pessoa entra na fila de separação ou pode ser credenciada direto pela linha de etapas. A página pública não informa o motivo: só pede que a pessoa procure um voluntário para orientações.

As telas operacionais da equipe exigem conta e senha; o resumo público mostra apenas três totais. A busca pública retorna somente nome e afiliação com os caracteres centrais de cada palavra mascarados, além de um token aleatório de uso único, válido por dez minutos, para confirmar a chegada. Ela não informa CPF, e-mail, pagamento ou situação da inscrição; quem não conseguir confirmar deve procurar atendimento. O menu da equipe mostra todos os guichês de `config/guiches.json`, além dos guichês atribuídos diretamente a participantes ou atendentes. A fila atualiza automaticamente a cada cinco segundos; os painéis, a cada dez segundos. Assumir ou liberar uma busca age imediatamente; marcar o material como pronto no guichê e confirmar a retirada pedem um segundo clique.

## Instalação e implantação

### O que precisa

| Item | Detalhe |
|---|---|
| Python 3.10 ou mais novo | Só a biblioteca padrão: não há `pip install`, e o SQLite já vem com o Python. |
| Um servidor | Linux (ou macOS) acessível pela internet ou pela rede do evento. Uma máquina pequena basta. |
| Um domínio com HTTPS | Para os celulares dos participantes acessarem pelo QR Code. O HTTPS fica num proxy reverso (Caddy ou nginx) na frente da aplicação. |
| Acesso ao terminal | Para criar as contas da equipe e importar a lista. |
| Google Sheets (opcional) | Cópia de segurança em planilha; veja [Google Sheets](#google-sheets-configurar-depois). |

### Experimentar na própria máquina

```sh
python3 app.py import exemplos/participantes-teste.csv
python3 app.py user coordenacao admin        # pede a senha (mínimo 10 caracteres)
export CHECKIN_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python3 app.py serve                         # abre em http://127.0.0.1:8000
```

A página pública fica em `/`; a equipe entra em `/login`. Os dados vão para a pasta `data/`.

### Colocar num servidor

**1. Copie o código** para o servidor (por exemplo, em `/opt/credenciamento`) e crie um usuário do sistema só para o serviço:

```sh
sudo git clone <url-do-repositório> /opt/credenciamento
sudo useradd --system --home /opt/credenciamento credenciamento
sudo chown -R credenciamento: /opt/credenciamento
```

**2. Configure o evento:** crie `config/evento.yaml` (veja [Identidade visual](#identidade-visual-do-evento)) e ajuste as faixas de `config/guiches.json` (veja [Guichês](#guichês)).

**3. Defina as variáveis de ambiente** num arquivo, por exemplo `/etc/credenciamento.env` (permissão `600`):

| Variável | Obrigatória | Para que serve |
|---|---|---|
| `CHECKIN_SESSION_SECRET` | sim | Assina as sessões da equipe. Mínimo de 32 caracteres; gere com o comando acima e guarde-o. Se mudar, todos precisam entrar de novo. |
| `CHECKIN_PUBLIC_URL` | recomendada | Endereço público, ex.: `https://credenciamento.seu-dominio.org`. Com `https://`, os cookies passam a exigir conexão segura. |
| `CHECKIN_TRUSTED_PROXY` | com proxy | `1` quando o proxy HTTPS roda na mesma máquina. Faz o limite de tentativas usar o IP real de cada pessoa. |
| `CHECKIN_DATA_DIR` | não | Pasta do banco e do CSV espelho. Padrão: `data/` dentro da instalação. |
| `CHECKIN_EVENT_CONFIG` | não | Outro caminho para o YAML do evento. |
| `CHECKIN_RANGES_FILE` | não | Outro caminho para o arquivo de guichês. |
| `CHECKIN_SHEETS_URL` e `CHECKIN_SHEETS_SECRET` | não | Ativam a cópia no Google Sheets. |

```sh
CHECKIN_SESSION_SECRET=cole-aqui-o-segredo-gerado
CHECKIN_PUBLIC_URL=https://credenciamento.seu-dominio.org
CHECKIN_TRUSTED_PROXY=1
```

**4. Crie as contas e importe a lista.** Rode os comandos como o usuário do serviço e com as mesmas variáveis, para usar o mesmo banco:

```sh
cd /opt/credenciamento
run() { sudo -u credenciamento sh -c 'set -a; . /etc/credenciamento.env; exec "$@"' sh "$@"; }
run python3 app.py user coordenacao admin
run python3 app.py user voluntario1 volunteer
run python3 app.py user atendimento1 attendant --guiche 1
run python3 app.py import /caminho/participantes.csv
```

Papéis: `admin` (coordenação, painel detalhado), `volunteer` (separação dos kits) e `attendant` (um guichê, informado em `--guiche`). As senhas são pedidas no terminal; rodar o comando de novo para o mesmo nome troca a senha.

**5. Deixe rodando como serviço** (`/etc/systemd/system/credenciamento.service`):

```ini
[Unit]
Description=Credenciamento do evento
After=network.target

[Service]
User=credenciamento
WorkingDirectory=/opt/credenciamento
EnvironmentFile=/etc/credenciamento.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 app.py serve --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now credenciamento
sudo journalctl -u credenciamento -f      # acompanhar o log
```

**6. Publique com HTTPS.** A aplicação escuta só em `127.0.0.1`; o proxy recebe os acessos externos. Com [Caddy](https://caddyserver.com), que obtém o certificado sozinho (`/etc/caddy/Caddyfile`):

```caddyfile
credenciamento.seu-dominio.org {
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-For {remote_host}
    }
}
```

Com nginx, use `proxy_pass http://127.0.0.1:8000;` e `proxy_set_header X-Forwarded-For $remote_addr;`. O proxy precisa **substituir** esse cabeçalho pelo IP real: a variante comum `$proxy_add_x_forwarded_for` acrescenta ao valor enviado pelo cliente e permitiria burlar o limite de tentativas.

**7. Gere o QR Code** apontando para a raiz do domínio (`https://credenciamento.seu-dominio.org/`) quando o endereço estiver definitivo.

### Atualizar para uma nova versão

Com o atalho `run` do passo 4:

```sh
cd /opt/credenciamento
run python3 app.py backup /caminho/seguro/antes-da-atualizacao.sqlite3
sudo -u credenciamento git pull
sudo systemctl restart credenciamento
```

O banco é atualizado automaticamente ao iniciar; não há passo manual de migração.

### Antes do evento

- [ ] Faixas de guichês revisadas e lista real importada (confira os totais por guichê no painel).
- [ ] Contas criadas para coordenação, voluntários e cada guichê.
- [ ] Domínio com HTTPS funcionando e QR Code impresso.
- [ ] Ensaio completo em vários celulares e guichês com uma cópia da lista, incluindo queda de rede e volta da sincronização.
- [ ] Backup testado (`app.py backup`) e guardado fora do servidor.

## Identidade visual do evento

Nome, logo, fontes e cores do evento ficam num único arquivo, sem mexer no código.

**Como configurar**

1. Copie o exemplo: `cp config/evento.example.yaml config/evento.yaml`.
2. Coloque logo, decoração e fontes em `static/assets/`.
3. Edite `config/evento.yaml`, usando apenas o nome dos arquivos (ex.: `logo: meu-evento.svg`).
4. Recarregue a página. Se você acabou de criar o `evento.yaml` com o servidor rodando, reinicie-o uma vez; sem esse arquivo, a aplicação usa o exemplo.

O servidor valida a configuração e aponta o campo com problema.

### Campos

| Campo | Obrigatório | O que define | Formato |
|---|---|---|---|
| `name` | sim | Nome completo (títulos e abas do navegador) | texto, até 100 caracteres |
| `short_name` | sim | Nome curto | texto, até 100 caracteres |
| `logo` | sim | Logo do cabeçalho | SVG, PNG, JPEG ou WebP |
| `decoration` | não | Imagem decorativa de fundo | SVG |
| `fonts.body` | sim | Fonte dos textos | WOFF2 |
| `fonts.display` | sim | Fonte dos títulos | WOFF2 |
| `registration_hint_en` e `registration_hint_pt` | não, mas os dois juntos | Instrução na busca da página pública (ex.: onde a pessoa se inscreveu) | texto, até 300 caracteres |

Sem os `registration_hint_*`, a página pública mostra um texto genérico. Assim, nomes de sistemas de inscrição não ficam fixos no código.

### Cores

Todas são obrigatórias, no formato `"#RRGGBB"` **com aspas**. Escolha pares de fundo e texto com contraste legível.

| Chave | Onde aparece |
|---|---|
| `primary` | Títulos e painel de destaque |
| `navigation` | Barra de navegação |
| `text` | Texto principal |
| `text_muted` | Textos secundários |
| `action` | Botões e estados positivos |
| `action_hover` | Botões ao passar o mouse |
| `focus` | Detalhes e contorno de foco |
| `page_background` | Fundo das páginas |
| `surface` | Fundo de cartões e painéis |

### Formato do arquivo

O YAML é intencionalmente simples, para a aplicação continuar sem dependências extras:

- pares `chave: valor`;
- seções (`fonts`, `colors`) com recuo de dois espaços;
- comentários com `#`;
- sem listas nem outros recursos avançados.

```yaml
name: Nome do Evento
short_name: EVENTO
logo: event-logo.svg
fonts:
  body: noto-sans.woff2
  display: bebas-neue.woff2
colors:
  primary: "#18515a"
  # … demais cores da tabela acima
```

### Idiomas

A página pública tem versões em inglês e português; as telas da equipe ficam em português.

## Lista de participantes e guichês

### Arquivo de participantes

CSV (separado por vírgula, ponto e vírgula ou tabulação) ou JSON (lista de objetos com as mesmas chaves, ou `{ "participants": [...] }`). Há modelos em [exemplos/participantes.csv](exemplos/participantes.csv) e [exemplos/participantes-teste.csv](exemplos/participantes-teste.csv). Importe pelo painel detalhado ou com `python3 app.py import arquivo.csv`.

| Coluna | Obrigatória | Regra |
|---|---|---|
| `nome` | sim | Nome completo. A primeira letra define o guichê. |
| `email` | sim | Junto com o nome, identifica a pessoa numa reimportação. |
| `nome_cracha` | não | Vazio: usa o primeiro e o último nome. |
| `afiliacao` | não | Vazia: a pessoa registra a chegada, mas fica em "Orientação pendente" até alguém preencher. |
| `cpf` | não | 11 dígitos ou `xxx.xxx.xxx-xx`. |
| `pago` | não | `0` ou `1`. Com `0`, fica em "Orientação pendente" até confirmar o pagamento. |
| `prioridade` | não | `0` ou `1`. Com `1`, vai sempre para o guichê de prioridade. |
| `guiche` | não | Substitui o guichê calculado pelas faixas (exceto para quem tem prioridade). |
| `id` | não | Vem da exportação; permite corrigir nome ou e-mail sem criar outra pessoa. |

- É preciso pelo menos uma pessoa após o cabeçalho; linhas vazias são ignoradas.
- Reimportar atualiza os dados cadastrais sem apagar as etapas já registradas. A troca de guichê é recusada para quem já está em busca ou já retirou o kit.
- Os campos operacionais de um CSV exportado (`situacao`, `responsavel`, horários, `revisao`) são aceitos e ignorados.
- Pendências (pagamento ou afiliação) são resolvidas no painel detalhado: o atalho "Pendências de orientação" filtra quem chegou nessa situação.

### Guichês

- As faixas de letras ficam em [config/guiches.json](config/guiches.json) (as incluídas são **só um exemplo**) e podem ser editadas no painel, na aba **Configuração dos guichês**.
- As faixas precisam cobrir de A a Z uma única vez. Vale a primeira letra do nome completo, sem acento.
- O guichê de prioridade tem o padrão `P` e pode ser renomeado no painel; deixe o campo vazio para desativá-lo. Os prioritários aparecem no topo da separação.
- Ao salvar a configuração, quem ainda não começou a busca muda de guichê na hora. Renomear um guichê, mantendo a mesma faixa, leva junto todos os participantes e as contas de atendente daquele guichê.
- Um guichê informado na coluna `guiche`, diferente do calculado, é tratado como escolha manual e mantido (exceto para quem tem prioridade).
- O painel mostra o total de inscritos por guichê.
- **Dividir pelo número de inscritos**: na mesma aba, informe quantos guichês usar (sem contar o de prioridade) e clique em **Calcular divisão**. O sistema propõe faixas contíguas de letras com números de pessoas o mais parecidos possível (primeiro reduz o guichê mais cheio, depois deixa os demais o mais iguais possível). Uma mesma letra nunca é dividida entre guichês, então letras muito frequentes limitam o equilíbrio. Entram na conta só as pessoas que seguem as faixas: prioritários e guichês manuais ficam de fora. Use antes do início do credenciamento, com a lista de inscritos já importada. A proposta reaproveita os nomes atuais dos guichês, mostra o total de cada faixa e só vale depois de **Salvar guichês**; ao salvar, valem as mesmas regras de mudança de guichê acima.
- Cada faixa mostra, enquanto se edita, quantas pessoas ela atende.

### Exportações

No painel detalhado:

- **Exportar dados**: CSV reimportável com os dados cadastrais e os campos operacionais.
- **Exportar logs de movimentações**: histórico de cada ação, com responsável e data/hora.

Os arquivos contêm dados pessoais; guarde-os com cuidado.

## Armazenamento e cópia

O arquivo `data/credenciamento.sqlite3` é a fonte principal. Cada alteração gera um evento com data e responsável. `data/participantes.csv` é uma cópia local para consulta, reconstituída na partida e atualizada a cada alteração. Campos que poderiam ser interpretados como fórmulas recebem um apóstrofo de segurança nessa cópia; use a lista original para reimportações. Ambos contêm dados pessoais; proteja a pasta, backups e contas do servidor.

Para criar uma cópia consistente do banco enquanto o serviço está em uso, execute `python3 app.py backup /caminho/seguro/backup-AAAA-MM-DD.sqlite3`. O comando não substitui um backup existente. Guarde cópias fora do servidor do evento.

O sistema guarda os eventos pendentes em `sheet_outbox` no mesmo banco até o Google Sheets confirmar o lote. Se o serviço ficar fora do ar, a fila de sincronização continua guardada e será tentada novamente a cada 15 segundos. Rode `python3 app.py sync` para forçar uma tentativa. O painel mostra quantas alterações aguardam envio.

## Google Sheets (configurar depois)

1. Crie uma planilha vazia e abra **Extensões → Apps Script**.
2. Cole [google-sheets/Code.gs](google-sheets/Code.gs) no projeto vinculado à planilha.
3. Em **Configurações do projeto → Propriedades do script**, crie `CHECKIN_SHEETS_SECRET` com um valor longo e aleatório.
4. Implante como **Aplicativo da Web**, executado como proprietário, com acesso para qualquer pessoa que tenha a URL. A chave no corpo da requisição protege as gravações; não compartilhe URL ou chave.
5. No servidor, configure `CHECKIN_SHEETS_URL` com a URL do aplicativo, `CHECKIN_SHEETS_SECRET` com a mesma chave e reinicie o serviço.
6. Verifique no painel que a fila pendente zera e confira as abas `Participantes` e `Histórico` antes do evento.

O envio preserva revisões mais recentes e não duplica IDs de eventos em tentativas repetidas. O teste com uma planilha real ainda depende dessas credenciais.

## Verificação local

```sh
python3 -m unittest discover -s tests -v
```

Os testes ficam em `tests/`, um arquivo por área (`test_public_checkin.py`, `test_queue_and_status.py`, `test_import_export.py`, `test_desks.py`, `test_web.py`, `test_storage.py`). A base comum em `tests/support.py` cria banco e configuração numa pasta temporária, faz requisições diretas ao servidor e já tem os usuários `vol1`, `vol2`, `att1` e `admin`.

## Organização do código

`app.py` é só o ponto de entrada; o código fica no pacote `credenciamento/`:

| Módulo | Responsabilidade |
|---|---|
| `settings.py` | Caminhos e variáveis de ambiente (lidos como `settings.NOME` no momento do uso) |
| `common.py` | Data atual, normalização de nomes e máscara de dados públicos |
| `event_theme.py` | YAML do evento e geração do `theme.css` |
| `db.py` | Conexão SQLite, esquema/migrações e `update_participant()`, o único caminho para alterar um participante |
| `desks.py` | Faixas de letras, guichê de prioridade e propagação de mudanças de guichê |
| `participants.py` | Etapas da situação e ações da fila de busca e guichê |
| `csv_io.py` | Importação e exportações CSV/JSON |
| `auth.py` | Senhas, sessão, CSRF e limite de tentativas |
| `sheets.py` | Sincronização com o Google Sheets |
| `web/server.py` | Servidor HTTP, páginas estáticas e despacho da API (login, CSRF e papel checados num lugar só) |
| `web/routes.py` | Tabela de rotas: cada rota declara método, caminho e papéis (`@route`) |
| `web/public.py` / `staff.py` / `admin.py` | Rotas públicas, da equipe e da coordenação |
| `bootstrap.py` / `cli.py` | Preparação do banco e comandos `serve`, `import`, `user`, `sync`, `backup` |

No navegador, cada página carrega um módulo ES de `static/js/` (sem etapa de build): `common.js` (API, sessão, menu da equipe e utilitários), `public.js` (pré-check-in), `login.js`, `queue.js` (Separação e guichês), `dashboard.js` com `dashboard/table.js` e `dashboard/tools.js` (painel detalhado) e `summary.js` (painel resumido). O servidor só entrega arquivos `.js` que existem dentro de `static/js/`.
