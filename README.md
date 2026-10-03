# Sistema de Credenciamento

Sistema local para pré-check-in, busca de kits, atendimento em guichês e acompanhamento da coordenação. Usa Python 3.10+ e a biblioteca padrão, sem dependências de aplicação.

## Fluxo

1. Participante abre `/` pelo QR Code e informa o CPF da inscrição (ou, se não tiver CPF, o e-mail). Num passo só, a página registra a chegada e mostra o número do guichê de retirada e a faixa de letras atendida por ele. Se a inscrição tiver pendência, o pré-check-in também é concluído e a página pede, em destaque, que a pessoa procure um voluntário em vez de ir ao guichê. Se não encontrar a inscrição (ou houver mais de uma com o mesmo e-mail), a página avisa em destaque e oferece buscar de novo ou procurar um voluntário. A página abre em inglês e oferece um botão para português brasileiro.
2. Voluntário abre `/busca`, assume uma pessoa, busca o kit e marca que o deixou no guichê mostrado no cartão.
3. Atendente abre `/fila` e confirma a retirada após entregar o kit. A conta do atendente determina o guichê inicial; o menu permite consultar outros guichês, mas cada atendente só pode confirmar entregas do próprio guichê. Na tela de um guichê, os cartões mostram os 3 primeiros dígitos do CPF para conferência; o número do guichê aparece nos cartões apenas em "Todos os guichês".
4. Qualquer pessoa pode abrir `/painel/resumo`, sem login, para acompanhar inscritos, pessoas que chegaram e participantes credenciados. A coordenação usa `/painel` para ver também as etapas intermediárias, a lista individual, o estado da sincronização, a importação/exportação de participantes e as faixas dos guichês. Pendências (pagamento não confirmado ou afiliação não informada) são tratadas no próprio painel detalhado: o atalho "Pendências de orientação" filtra quem chegou com pendência, o pagamento e a prioridade são alterados na linha, e a afiliação pode ser preenchida ali mesmo. Resolvidas as pendências, a pessoa entra na fila de separação ou pode ser credenciada direto pela linha de etapas. A página pública não informa o motivo: só pede que a pessoa procure um voluntário para orientações.

As telas operacionais da equipe exigem conta e senha; o resumo público mostra apenas três totais. A busca pública retorna somente o nome com os caracteres centrais de cada palavra mascarados e o guichê (ou o aviso para procurar um voluntário). Ela não informa CPF, e-mail, afiliação, pagamento nem o motivo de uma pendência, só funciona enquanto a coordenação deixa o pré-check-in aberto e limita quem erra muitas buscas (veja [Segurança e dados pessoais](#segurança-e-dados-pessoais)). Buscar de novo uma inscrição já registrada só mostra o mesmo resultado, sem registrar outra chegada. O menu da equipe mostra todos os guichês de `config/guiches.json`, além dos guichês atribuídos diretamente a participantes ou atendentes. A fila atualiza automaticamente a cada cinco segundos; os painéis, a cada dez segundos. Assumir ou liberar uma busca age imediatamente; marcar o material como pronto no guichê e confirmar a retirada pedem um segundo clique.

## Instalação e implantação

### O que precisa

| Item | Detalhe |
|---|---|
| Python 3.10 ou mais novo | Só a biblioteca padrão: não há `pip install`, e o SQLite já vem com o Python. |
| ou Docker | Alternativa ao Python instalado no servidor; veja [Com Docker](#com-docker). |
| Um servidor | Linux (ou macOS) acessível pela internet ou pela rede do evento. Uma máquina pequena basta. |
| Um domínio com HTTPS | Para os celulares dos participantes acessarem pelo QR Code. O HTTPS fica num proxy reverso (Caddy ou nginx) na frente da aplicação. |
| Acesso ao terminal | Para criar as contas da equipe e importar a lista. |
| Google Sheets (opcional) | Cópia de segurança em planilha; veja [Google Sheets](#google-sheets-configurar-depois). |

### Experimentar na própria máquina

```sh
export CHECKIN_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export CHECKIN_LOOKUP_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python3 app.py import exemplos/participantes-teste.csv
python3 app.py user coordenacao admin        # pede a senha (mínimo 10 caracteres)
python3 app.py public-checkin open           # o pré-check-in começa fechado
python3 app.py serve                         # abre em http://127.0.0.1:8000
```

A página pública fica em `/`; a equipe entra em `/login`. Os dados vão para a pasta `data/`. Use sempre o mesmo `CHECKIN_LOOKUP_SECRET` para o mesmo banco: com outro, a busca pública não reconhece as inscrições.

### Com Docker

A imagem usa `python:3.13-slim`, roda com um usuário sem privilégios e guarda tudo o que muda (banco, CSV espelho e faixas dos guichês) no volume `dados`. Um serviço à parte faz uma cópia do banco a cada hora no volume `backups`. Precisa de Docker com o plugin `docker compose`.

| Arquivo | Para que serve |
|---|---|
| `Dockerfile` | Monta a imagem com o código, a identidade visual e as faixas iniciais dos guichês. |
| `compose.yaml` | Sobe a aplicação (por padrão, porta `8000` só em `127.0.0.1`), o backup a cada hora e, conforme o perfil, um [Caddy](https://caddyserver.com) para o HTTPS. |
| `.env.example` | Modelo das variáveis; copie para `.env` (que não vai para o git nem para a imagem). |
| `docker/entrypoint.sh` | Na primeira partida, copia `config/guiches.json` para o volume. |
| `docker/Caddyfile` | HTTPS com certificado do Let's Encrypt (perfil `https`). |
| `docker/Caddyfile.interno` | HTTPS atrás do proxy da instituição (perfil `https-interno`). |

**1. Personalize o evento antes de montar a imagem:** `config/evento.yaml`, arquivos em `static/assets/` (veja [Identidade visual](#identidade-visual-do-evento)) e as faixas iniciais de `config/guiches.json`. Esses arquivos entram na imagem; depois de mudá-los, monte de novo com `--build`.

**2. Crie o `.env`** com os dois segredos:

```sh
cp .env.example .env
openssl rand -hex 32      # cole em CHECKIN_SESSION_SECRET
openssl rand -hex 32      # rode de novo e cole em CHECKIN_LOOKUP_SECRET
```

`CHECKIN_LOOKUP_SECRET` é a chave das buscas por CPF e e-mail, que ficam guardados só como chave (veja [Segurança e dados pessoais](#segurança-e-dados-pessoais)). Guarde uma cópia dele em lugar seguro e separado dos backups: se ele se perder ou mudar, a busca pública deixa de reconhecer as inscrições e é preciso reimportar a lista original.

No servidor, defina também `CHECKIN_PUBLIC_URL` e `CHECKIN_DOMAIN` com o seu domínio. As demais variáveis (Google Sheets, limites de tentativas) estão comentadas no `.env.example`. `CHECKIN_DATA_DIR`, `CHECKIN_RANGES_FILE` e `CHECKIN_TRUSTED_PROXY` já vêm definidas pela imagem e pelo compose.

**3. Escolha como o HTTPS chega** e suba o serviço:

| Situação | Comando |
|---|---|
| Só experimentar, em `http://127.0.0.1:8000` | `docker compose up -d --build` |
| O servidor recebe direto da internet nas portas 80 e 443, e o DNS do domínio aponta para ele | `docker compose --profile https up -d --build` |
| Um proxy da instituição fica com o certificado e repassa **em HTTPS** para a porta 443 deste servidor | `docker compose --profile https-interno up -d --build` |
| Um proxy (da instituição ou nginx/Caddy nesta máquina) repassa **em HTTP** | `docker compose up -d --build`, com as variáveis abaixo |

Use um perfil de Caddy ou o outro, nunca os dois juntos.

**Atrás de um proxy**, a aplicação precisa saber o IP de quem o proxy atende; sem isso, todo mundo conta como uma pessoa só no limite de tentativas. No `.env`:

```sh
CHECKIN_PUBLIC_URL=https://checkin.seu-dominio.br
CHECKIN_DOMAIN=checkin.seu-dominio.br
CHECKIN_PROXY_IPS=10.0.0.1        # IP(s) do proxy, separados por espaço
# perfil https-interno: IP deste servidor para onde o proxy repassa
CHECKIN_SERVER_IP=10.0.0.10
# proxy que repassa em HTTP: onde a aplicação fica acessível
# CHECKIN_BIND=10.0.0.10
# CHECKIN_PORT=8000
```

- Para descobrir o IP do proxy, veja de onde chegam as conexões quando alguém abre o site, por exemplo com `sudo tcpdump -ni any 'tcp[tcpflags] & tcp-syn != 0 and port 443'`.
- Peça que o proxy envie o cabeçalho `X-Forwarded-For` (substituindo ou acrescentando, os dois funcionam). No perfil `https-interno`, o proxy também não pode exigir certificado válido deste lado (o padrão do nginx): o Caddy usa um certificado da própria CA interna.
- Com um nginx ou Caddy nesta mesma máquina repassando para `127.0.0.1:8000`, use `CHECKIN_PROXY_IPS=172.31.250.1` (o gateway da rede do Docker).
- **Confira depois de subir:** abra o site pelo celular, no 4G, e rode `docker compose logs app`. A linha do seu acesso deve começar pelo IP do celular (`IP-DO-CELULAR via 172.31.250.10`, ou `via IP-DO-PROXY`). Se começar pelo IP do proxy ou por `172.31.250.1`, o IP real não está chegando: revise `CHECKIN_PROXY_IPS` ou peça o `X-Forwarded-For` ao proxy.
- O `X-Forwarded-For` só é aceito do Caddy deste compose (IP fixo `172.31.250.10`) e dos IPs em `CHECKIN_PROXY_IPS`; vindo de qualquer outro lugar, é ignorado.
- Com `CHECKIN_BIND` fora de `127.0.0.1`, a aplicação fica acessível em HTTP na rede. Peça que a porta só aceite conexões do proxy. Regras do `ufw` não bastam, porque o Docker passa por cima delas nas portas publicadas.

**4. Crie as contas, importe a lista e abra o pré-check-in:**

```sh
docker compose exec app python app.py user coordenacao admin --gerar-senha
docker compose exec app python app.py user voluntario1 volunteer --gerar-senha
docker compose exec app python app.py user atendimento1 attendant --guiche 1 --gerar-senha

docker compose exec -T app sh -c 'cat > /tmp/participantes.csv' < participantes.csv
docker compose exec app python app.py import /tmp/participantes.csv
docker compose exec app rm /tmp/participantes.csv
# para experimentar: docker compose exec app python app.py import exemplos/participantes-teste.csv
```

`--gerar-senha` cria e mostra uma senha forte; sem ela, o comando pede a senha. Crie uma conta para cada pessoa da equipe. O envio do arquivo pela entrada padrão o cria já com o usuário do container (um `docker compose cp` o criaria como root e, se o original tiver permissão restrita, a importação não conseguiria lê-lo). A lista também pode ser importada pelo painel detalhado.

O pré-check-in pelo celular **começa fechado**. Abra pelo painel (botão no topo) ou com `docker compose exec app python app.py public-checkin open`, quando o credenciamento começar.

**Operação do dia a dia:**

```sh
docker compose logs -f app                                    # acompanhar o log
docker compose exec app python app.py public-checkin close    # fechar o pré-check-in (open para abrir)
docker compose exec app python app.py revoke voluntario1      # encerrar as sessões de uma conta (celular perdido)
docker compose exec app python app.py sync                    # forçar o envio ao Google Sheets
docker compose exec backup ls -lt /backups                    # backups automáticos (um por hora, os últimos 48)
docker compose cp backup:/backups/credenciamento-AAAAMMDD-HHMMSS.sqlite3 .   # levar um para fora do servidor
```

**Atualizar para uma nova versão:** confira que há um backup recente e rode `git pull && docker compose build --pull && docker compose up -d` (acrescente o perfil de Caddy que você usa, por exemplo `docker compose --profile https-interno up -d`). O `build --pull` traz as correções de segurança da imagem do Python; para atualizar o Caddy, rode antes `docker compose --profile https-interno pull`. Os dados ficam no volume e o banco é migrado ao iniciar.

**Observações:**

- As faixas dos guichês editadas no painel ficam em `/data/guiches.json`, no volume. Depois da primeira partida, mudar `config/guiches.json` e remontar a imagem não altera as faixas em uso; ajuste-as pelo painel.
- `docker compose down` mantém os dados; `docker compose down -v` **apaga** os volumes com o banco e os backups.
- O compose fixa a rede interna em `172.31.250.0/24`, com o Caddy em `172.31.250.10`. Se essa faixa conflitar com outra rede, mude `CHECKIN_DOCKER_SUBNET` e `CHECKIN_CADDY_IP` no `.env`.
- Para usar uma pasta do servidor em vez do volume nomeado, troque `dados:/data` por `./data:/data` (no `app` e no `backup`) e dê a pasta ao usuário do container: `sudo chown 10001:10001 data`.

### Colocar num servidor (sem Docker)

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
| `CHECKIN_SESSION_SECRET` | sim | Assina o token CSRF da equipe. Mínimo de 32 caracteres; gere com o comando acima. |
| `CHECKIN_LOOKUP_SECRET` | sim | Chave das buscas por CPF e e-mail, que só ficam guardados como chave. Mínimo de 32 caracteres e diferente do anterior. Guarde uma cópia em lugar seguro: se mudar, é preciso reimportar a lista original. |
| `CHECKIN_PUBLIC_URL` | recomendada | Endereço público, ex.: `https://credenciamento.seu-dominio.org`. Com `https://`, os cookies passam a exigir conexão segura e o navegador passa a usar só HTTPS no endereço (HSTS). |
| `CHECKIN_TRUSTED_PROXY` | com proxy | `1` quando o proxy HTTPS roda na mesma máquina; ou uma lista de IPs/redes, separados por vírgula ou espaço, de onde o proxy se conecta. Faz o limite de tentativas usar o IP real de cada pessoa (lido do `X-Forwarded-For` só dessas origens, da direita para a esquerda, parando no primeiro endereço que não é de proxy confiável). |
| `CHECKIN_DATA_DIR` | não | Pasta do banco e do CSV espelho. Padrão: `data/` dentro da instalação. |
| `CHECKIN_EVENT_CONFIG` | não | Outro caminho para o YAML do evento. |
| `CHECKIN_RANGES_FILE` | não | Outro caminho para o arquivo de guichês. |
| `CHECKIN_SHEETS_URL` e `CHECKIN_SHEETS_SECRET` | não | Ativam a cópia no Google Sheets. |
| `CHECKIN_LIMIT_SEARCH`, `CHECKIN_LIMIT_SEARCH_MISS`, `CHECKIN_LIMIT_LOGIN_IP`, `CHECKIN_LIMIT_LOGIN_USER`, `CHECKIN_ALERT_SEARCH_MISS` | não | Limites de tentativas e alerta do painel, no formato `quantidade/minutos`; veja [Segurança e dados pessoais](#segurança-e-dados-pessoais). |

```sh
CHECKIN_SESSION_SECRET=cole-aqui-o-segredo-gerado
CHECKIN_LOOKUP_SECRET=cole-aqui-outro-segredo-gerado
CHECKIN_PUBLIC_URL=https://credenciamento.seu-dominio.org
CHECKIN_TRUSTED_PROXY=1
```

**4. Crie as contas e importe a lista.** Rode os comandos como o usuário do serviço e com as mesmas variáveis, para usar o mesmo banco:

```sh
cd /opt/credenciamento
run() { sudo -u credenciamento sh -c 'set -a; . /etc/credenciamento.env; exec "$@"' sh "$@"; }
run python3 app.py user coordenacao admin --gerar-senha
run python3 app.py user voluntario1 volunteer --gerar-senha
run python3 app.py user atendimento1 attendant --guiche 1 --gerar-senha
run python3 app.py import /caminho/participantes.csv
run python3 app.py public-checkin open      # quando o credenciamento começar
```

Papéis: `admin` (coordenação, painel detalhado), `volunteer` (separação dos kits) e `attendant` (um guichê, informado em `--guiche`). Com `--gerar-senha`, o comando cria e mostra uma senha forte; sem ela, pede a senha no terminal. Rodar o comando de novo para o mesmo nome troca a senha e encerra as sessões abertas com a anterior. `run python3 app.py revoke NOME` encerra as sessões sem trocar a senha (por exemplo, celular perdido). O pré-check-in pelo celular começa fechado: abra e feche pelo painel ou com `public-checkin open` / `close`.

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

Com nginx, use `proxy_pass http://127.0.0.1:8000;` e `proxy_set_header X-Forwarded-For $remote_addr;`. A variante comum `$proxy_add_x_forwarded_for`, que acrescenta ao valor enviado pelo cliente, também funciona: a aplicação ignora o que vem antes do último proxy confiável.

**7. Gere o QR Code** apontando para a raiz do domínio (`https://credenciamento.seu-dominio.org/`) quando o endereço estiver definitivo.

### Atualizar para uma nova versão

Com o atalho `run` do passo 4:

```sh
cd /opt/credenciamento
run python3 app.py backup /caminho/seguro/antes-da-atualizacao.sqlite3
sudo -u credenciamento git pull
sudo systemctl restart credenciamento
```

O banco é atualizado automaticamente ao iniciar; não há passo manual de migração. Vindo de uma versão que guardava CPF e e-mail em texto, defina `CHECKIN_LOOKUP_SECRET` antes de reiniciar (veja [Armazenamento e cópia](#armazenamento-e-cópia)).

### Antes do evento

- [ ] Faixas de guichês revisadas e lista real importada (confira os totais por guichê no painel).
- [ ] Uma conta por pessoa (coordenação, voluntários e cada guichê), com senhas geradas e entregues individualmente.
- [ ] `CHECKIN_LOOKUP_SECRET` guardado em lugar seguro, fora do servidor e longe dos backups.
- [ ] Log conferido: um acesso pelo 4G aparece com o IP do celular (veja "Confira depois de subir" em [Com Docker](#com-docker)).
- [ ] Pré-check-in aberto pelo painel na hora de começar (ele vem fechado).
- [ ] Domínio com HTTPS funcionando e QR Code impresso.
- [ ] Ensaio completo em vários celulares e guichês com uma cópia da lista, incluindo queda de rede e volta da sincronização.
- [ ] Backup testado (`app.py backup`) e guardado fora do servidor.
- [ ] Lista impressa por guichê, como plano B se o sistema ou a rede caírem.

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
| `email` | sim | Junto com o nome, identifica a pessoa numa reimportação. Na página pública, serve para buscar a inscrição de quem não tem CPF. |
| `nome_cracha` | não | Vazio: usa o primeiro e o último nome. |
| `afiliacao` | não | Vazia: a pessoa registra a chegada, mas fica em "Orientação pendente" até alguém preencher. |
| `cpf` | não | 11 dígitos ou `xxx.xxx.xxx-xx`. |
| `email_hash`, `cpf_hash`, `cpf_inicio` | não | Vêm da exportação e substituem `email` e `cpf` quando eles não estão no arquivo. |
| `pago` | não | `0` ou `1`. Com `0`, fica em "Orientação pendente" até confirmar o pagamento. |
| `prioridade` | não | `0` ou `1`. Com `1`, vai sempre para o guichê de prioridade. |
| `guiche` | não | Substitui o guichê calculado pelas faixas (exceto para quem tem prioridade). |
| `id` | não | Vem da exportação; permite corrigir nome ou e-mail sem criar outra pessoa. |

- **CPF e e-mail não ficam guardados no sistema.** Na importação, cada um vira uma chave (HMAC com `CHECKIN_LOOKUP_SECRET`) que só serve para reconhecer o que a pessoa digita na busca; do CPF ficam também os 3 primeiros dígitos, para a conferência no guichê. Guarde a lista original fora do servidor: ela é a única cópia desses dados.
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

- **Exportar dados**: CSV reimportável com os dados cadastrais e os campos operacionais. Não traz CPF nem e-mail, só as chaves (`email_hash`, `cpf_hash`) e o início do CPF; reimportado, reconhece as mesmas pessoas.
- **Exportar logs de movimentações**: histórico de cada ação, com responsável e data/hora.

Os arquivos contêm nomes e outros dados pessoais; guarde-os com cuidado.

No painel, a busca encontra pelo nome, afiliação ou guichê. Para achar alguém pelo CPF ou e-mail, digite o CPF completo (11 dígitos) ou o e-mail completo: o servidor confere a chave e mostra a pessoa.

## Armazenamento e cópia

O arquivo `data/credenciamento.sqlite3` é a fonte principal. Cada alteração gera um evento com data e responsável. `data/participantes.csv` é uma cópia local para consulta, reconstituída na partida e atualizada a cada alteração. Campos que poderiam ser interpretados como fórmulas recebem um apóstrofo de segurança nessa cópia; use a lista original para reimportações. Nenhum dos dois tem CPF ou e-mail, mas ambos têm nomes e dados do credenciamento; proteja a pasta, backups e contas do servidor.

Um banco criado por uma versão anterior, que guardava CPF e e-mail em texto, é convertido automaticamente na primeira partida com `CHECKIN_LOOKUP_SECRET` definido: as chaves são calculadas, as colunas em texto são apagadas e o arquivo é reescrito. Backups feitos antes disso ainda têm os dados em texto; apague-os.

Para criar uma cópia consistente do banco enquanto o serviço está em uso, execute `python3 app.py backup /caminho/seguro/backup-AAAA-MM-DD.sqlite3`. O comando não substitui um backup existente. Guarde cópias fora do servidor do evento.

O sistema guarda os eventos pendentes em `sheet_outbox` no mesmo banco até o Google Sheets confirmar o lote. Se o serviço ficar fora do ar, a fila de sincronização continua guardada e será tentada novamente a cada 15 segundos. Rode `python3 app.py sync` para forçar uma tentativa. O painel mostra quantas alterações aguardam envio.

## Segurança e dados pessoais

O que o sistema faz sozinho:

| Proteção | Como funciona |
|---|---|
| CPF e e-mail não ficam guardados | Só uma chave (HMAC) e os 3 primeiros dígitos do CPF. Sem `CHECKIN_LOOKUP_SECRET`, um banco, backup ou exportação vazados não revelam CPFs nem e-mails. A chave precisa ser secreta porque existem só um bilhão de CPFs: um hash comum seria revertido em minutos. |
| Pré-check-in só no horário | Começa fechado; a coordenação abre e fecha pelo painel (ou `app.py public-checkin`). Fechado, a página pública só mostra um aviso e não consulta nada. |
| Limite de quem erra a busca | Quem testa CPFs ao acaso quase sempre erra; o participante real quase sempre acerta. Por isso o limite principal conta só as buscas **sem resultado** por IP, e quem acerta não é barrado mesmo com muita gente no mesmo Wi-Fi. |
| Alerta no painel | Avisa quando há muitas buscas sem resultado, de todas as origens, em pouco tempo. Se não houver fila no credenciamento, feche o pré-check-in. |
| Limite de senhas erradas | Por IP e **por conta**; o login certo não conta. Usuário inexistente gasta o mesmo tempo e conta igual, sem revelar que não existe. |
| Sessões encerráveis | Ficam no banco: sair encerra a sessão, trocar a senha encerra todas as da conta, e `app.py revoke NOME` encerra sem trocar a senha. Duram 12 horas. |
| Navegador | Cookie `HttpOnly`, `SameSite=Strict` e `Secure` (com HTTPS), CSRF ligado à sessão, CSP restritiva e HSTS com `CHECKIN_PUBLIC_URL` em `https://`. |

Limites padrão, ajustáveis no ambiente (`quantidade/minutos`):

| Variável | Padrão | O que conta |
|---|---|---|
| `CHECKIN_LIMIT_SEARCH` | `60/1` | Todas as buscas da página pública, por IP. |
| `CHECKIN_LIMIT_SEARCH_MISS` | `20/10` | Buscas sem resultado, por IP. Atingido, o IP espera até a janela passar. |
| `CHECKIN_LIMIT_LOGIN_IP` | `20/5` | Senhas erradas, por IP. |
| `CHECKIN_LIMIT_LOGIN_USER` | `10/15` | Senhas erradas, por conta. Quem souber o nome de uma conta consegue bloqueá-la por esse tempo; use nomes que não sejam óbvios. |
| `CHECKIN_ALERT_SEARCH_MISS` | `30/10` | Alerta no painel: buscas sem resultado, somando todas as origens. |

Os limites por IP dependem de a aplicação saber o IP de cada pessoa; atrás de um proxy, configure-o como em [Com Docker](#com-docker) e confira pelo log. Os limites ficam na memória e recomeçam quando o serviço reinicia.

O que depende da equipe:

- **Contas:** uma por pessoa, com senha gerada (`--gerar-senha`) e entregue individualmente. Nada de conta compartilhada nem senha colada no guichê. Ao fim de cada turno ou se um celular sumir, `app.py revoke NOME`.
- **Exportações e backups:** só a coordenação exporta. Arquivos não circulam por WhatsApp ou e-mail. Cópias fora do servidor ficam criptografadas (um `.zip` com senha ou disco com BitLocker/FileVault).
- **`CHECKIN_LOOKUP_SECRET`:** guarde uma cópia fora do servidor, separada dos backups (um gerenciador de senhas, por exemplo).
- **Servidor:** acesso SSH só com chave (`sudo sshd -T | grep -i passwordauthentication` deve mostrar `no`), atualizações automáticas ativas (`systemctl status unattended-upgrades`), poucas pessoas no grupo `docker` (equivale a root) e, atrás de proxy, a porta da aplicação aceitando só o proxy.
- **Depois do evento (LGPD):** os dados só podem ser guardados enquanto forem necessários. Defina um prazo (por exemplo, 30 dias), exporte o que precisa ficar para relatórios e apague o resto: `docker compose down -v` (banco e backups), os backups copiados para fora e o `.env`.

## Google Sheets (configurar depois)

1. Crie uma planilha vazia e abra **Extensões → Apps Script**.
2. Cole [google-sheets/Code.gs](google-sheets/Code.gs) no projeto vinculado à planilha.
3. Em **Configurações do projeto → Propriedades do script**, crie `CHECKIN_SHEETS_SECRET` com um valor longo e aleatório.
4. Implante como **Aplicativo da Web**, executado como proprietário, com acesso para qualquer pessoa que tenha a URL. A chave no corpo da requisição protege as gravações; não compartilhe URL ou chave.
5. No servidor, configure `CHECKIN_SHEETS_URL` com a URL do aplicativo, `CHECKIN_SHEETS_SECRET` com a mesma chave e reinicie o serviço.
6. Verifique no painel que a fila pendente zera e confira as abas `Participantes` e `Histórico` antes do evento.

O envio preserva revisões mais recentes e não duplica IDs de eventos em tentativas repetidas. A planilha não recebe CPF nem e-mail, só o início do CPF; se você criou a planilha com uma versão anterior (colunas `Email` e `CPF`), use uma planilha nova. O teste com uma planilha real ainda depende dessas credenciais.

## Verificação local

```sh
python3 -m unittest discover -s tests -v
```

Os testes ficam em `tests/`, um arquivo por área (`test_public_checkin.py`, `test_queue_and_status.py`, `test_import_export.py`, `test_desks.py`, `test_web.py`, `test_storage.py`). A base comum em `tests/support.py` cria banco e configuração numa pasta temporária, faz requisições diretas ao servidor e já tem os usuários `vol1`, `vol2`, `att1` e `admin`, com o pré-check-in aberto.

## Organização do código

`app.py` é só o ponto de entrada; o código fica no pacote `credenciamento/`:

| Módulo | Responsabilidade |
|---|---|
| `settings.py` | Caminhos e variáveis de ambiente (lidos como `settings.NOME` no momento do uso) |
| `common.py` | Data atual, normalização de nomes e máscara de dados públicos |
| `lookup.py` | Chaves de busca (HMAC) de CPF e e-mail, que não são guardados em texto |
| `event_theme.py` | YAML do evento e geração do `theme.css` |
| `db.py` | Conexão SQLite, esquema/migrações e `update_participant()`, o único caminho para alterar um participante |
| `desks.py` | Faixas de letras, guichê de prioridade e propagação de mudanças de guichê |
| `participants.py` | Etapas da situação, ações da fila de busca e guichê e abertura do pré-check-in |
| `csv_io.py` | Importação e exportações CSV/JSON |
| `auth.py` | Senhas, sessões no banco, CSRF e limites de tentativas |
| `sheets.py` | Sincronização com o Google Sheets |
| `web/server.py` | Servidor HTTP, páginas estáticas e despacho da API (login, CSRF e papel checados num lugar só) |
| `web/routes.py` | Tabela de rotas: cada rota declara método, caminho e papéis (`@route`) |
| `web/public.py` / `staff.py` / `admin.py` | Rotas públicas, da equipe e da coordenação |
| `bootstrap.py` / `cli.py` | Preparação do banco e comandos `serve`, `import`, `user`, `revoke`, `public-checkin`, `sync`, `backup` |

No navegador, cada página carrega um módulo ES de `static/js/` (sem etapa de build): `common.js` (API, sessão, menu da equipe e utilitários), `public.js` (pré-check-in), `login.js`, `queue.js` (Separação e guichês), `dashboard.js` com `dashboard/table.js` e `dashboard/tools.js` (painel detalhado) e `summary.js` (painel resumido). O servidor só entrega arquivos `.js` que existem dentro de `static/js/`.
