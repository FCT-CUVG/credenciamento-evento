# Sistema de Credenciamento

Sistema local para pré-check-in, busca de kits, atendimento em guichês e acompanhamento da coordenação. Usa Python 3.10+ e a biblioteca padrão, sem dependências de aplicação.

## Fluxo

1. Participante abre `/` pelo QR Code, informa o CPF (ou nome completo e e-mail) como constam na inscrição e confirma presença. Ao confirmar, a página mostra o número do guichê de retirada e a faixa de letras atendida por ele. A página abre em inglês e oferece um botão para português brasileiro.
2. Voluntário abre `/busca`, assume uma pessoa, busca o kit e marca que o deixou no guichê mostrado no cartão.
3. Atendente abre `/fila` e confirma a retirada após entregar o kit. A conta do atendente determina o guichê inicial; o menu permite consultar outros guichês, mas cada atendente só pode confirmar entregas do próprio guichê. Na tela de um guichê, os cartões mostram os 3 primeiros dígitos do CPF para conferência; o número do guichê aparece nos cartões apenas em "Todos os guichês".
4. Qualquer pessoa pode abrir `/painel/resumo`, sem login, para acompanhar inscritos, pessoas que chegaram e participantes credenciados. A coordenação usa `/painel` para ver também as etapas intermediárias, a lista individual, o estado da sincronização, a importação/exportação de participantes e as faixas dos guichês. Pendências (pagamento não confirmado ou afiliação não informada) são tratadas no próprio painel detalhado: o atalho "Pendências de orientação" filtra quem chegou com pendência, o pagamento e a prioridade são alterados na linha, e a afiliação pode ser preenchida ali mesmo. Resolvidas as pendências, a pessoa entra na fila de separação ou pode ser credenciada direto pela linha de etapas. A página pública não informa o motivo: só pede que a pessoa procure um voluntário para orientações.

As telas operacionais da equipe exigem conta e senha; o resumo público mostra apenas três totais. A busca pública retorna somente nome e afiliação com os caracteres centrais de cada palavra mascarados, além de um token aleatório de uso único, válido por dez minutos, para confirmar a chegada. Ela não informa CPF, e-mail, pagamento ou situação da inscrição; quem não conseguir confirmar deve procurar atendimento. O menu da equipe mostra todos os guichês de `config/guiches.json`, além dos guichês atribuídos diretamente a participantes ou atendentes. A fila atualiza automaticamente a cada cinco segundos; os painéis, a cada dez segundos. Assumir ou liberar uma busca age imediatamente; marcar o material como pronto no guichê e confirmar a retirada pedem um segundo clique.

## Identidade visual do evento

Copie [config/evento.example.yaml](config/evento.example.yaml) para `config/evento.yaml` e edite a cópia para definir o nome completo e curto do evento, a logo, a decoração opcional, as fontes de corpo e títulos e as cores. Os arquivos de imagem e fonte ficam em `static/assets/`; escreva no YAML apenas o nome do arquivo, por exemplo `logo: meu-evento.svg`. A logo aceita SVG, PNG, JPEG ou WebP; a decoração, quando definida, aceita SVG; as fontes aceitam WOFF2. Ao iniciar, a aplicação usa `config/evento.yaml` quando ele existir; caso contrário, usa o exemplo incluído.

As cores devem ser escritas como `"#RRGGBB"`, com aspas. `navy` controla o painel de destaque e os títulos; `navy_header`, a navegação; `leaf` e `leaf_dark`, ações e estados positivos; `gold`, detalhes e foco; `ink` e `slate`, textos; `fog` e `white`, fundos. Preserve contraste legível entre fundo e texto ao escolher a paleta. Depois de editar o YAML ou substituir arquivos, recarregue a página; o servidor valida os nomes, formatos e campos da configuração. O YAML aceito aqui é intencionalmente simples: pares `chave: valor`, seções com recuo de dois espaços e comentários com `#`, sem listas ou recursos avançados. Isso mantém a aplicação sem dependências extras.

A página pública tem versões em inglês e português; as telas da equipe permanecem em português. As instruções específicas da origem da inscrição ficam em `registration_hint_en` e `registration_hint_pt` no mesmo YAML. Defina ambas ou remova ambas para usar textos genéricos. Assim, nomes de sistemas de inscrição não ficam fixos no código da aplicação.

## Preparação

Revise [config/guiches.json](config/guiches.json) antes de importar a lista real. As faixas incluídas são **somente um exemplo**. Cada faixa inclui as letras inicial e final; nomes com acentos são normalizados. A importação falha se a inicial não estiver coberta por uma faixa única. Uma coluna `guiche` no arquivo pode substituir a regra para casos específicos.

CSV: cabeçalhos obrigatórios `nome,email`, em qualquer ordem; as colunas podem ser separadas por vírgula, ponto e vírgula ou tabulação. `nome_cracha` é opcional; quando vazio ou ausente, usa o primeiro e o último nome de `nome` (ou o único nome, se houver apenas um). `afiliacao` pode ficar vazia ou não existir no arquivo; nesse caso, a chegada é registrada, a pessoa é orientada a procurar um voluntário e aparece como "Orientação pendente" no painel detalhado. A busca do kit aguarda a afiliação ser preenchida no painel ou por uma nova importação. `cpf` é opcional, mas, quando preenchido, deve ter 11 dígitos ou seguir o formato `xxx.xxx.xxx-xx`. As colunas opcionais `pago` e `prioridade` aceitam `0` ou `1`; `guiche` também é opcional e, quando ausente, é calculado pelas faixas configuradas. Quem tem `prioridade=1` vai sempre para o guichê de prioridade, mesmo com `guiche` preenchido, (padrão `P`, configurável na aba **Configuração dos guichês** do painel; deixe o campo vazio para desativar). A prioridade também pode ser alterada participante a participante no painel detalhado; ao marcar, a pessoa passa para o guichê de prioridade, e ao desmarcar volta para o guichê da sua letra, desde que a busca do kit ainda não tenha começado. Na separação, os prioritários aparecem no topo de Aguardando busca, com um selo. É preciso haver pelo menos um participante após o cabeçalho; linhas vazias são ignoradas. JSON: lista de objetos com essas mesmas chaves, ou objeto `{ "participants": [...] }`. Há um modelo em [exemplos/participantes.csv](exemplos/participantes.csv). Participantes não pagos também registram a chegada, são orientados a procurar um voluntário e aparecem como "Orientação pendente" até o pagamento ser confirmado. Ao salvar a configuração de guichês no painel, quem ainda não começou a busca do kit passa automaticamente para o guichê calculado pelas novas faixas (ou pelo guichê de prioridade). Renomear um guichê, mantendo a mesma faixa de letras, atualiza todos os participantes daquele guichê, inclusive quem já está em busca ou pronto, e também as contas de atendentes. Guichês informados na coluna `guiche` com valor diferente do calculado são tratados como escolha manual e mantidos, exceto para quem tem prioridade. Numa reimportação, uma mudança de guichê é recusada se a busca do kit ou a retirada já começou. O painel detalhado mostra, por guichê, o total de inscritos e quantos ainda têm pagamento ou afiliação pendente.

No painel detalhado, a coordenação pode usar **Exportar dados** para baixar um CSV reimportável com `id,nome,nome_cracha,afiliacao,email,cpf,pago,prioridade,guiche` e os campos operacionais `situacao,responsavel,pre_checkin_em,busca_iniciada_em,pronto_em,retirado_em,atualizado_em,revisao`. O `id` permite corrigir nome ou e-mail na planilha exportada sem criar outra pessoa; arquivos sem `id` continuam sendo associados por nome e e-mail. Na importação, os campos operacionais podem existir, mas são ignorados para preservar o estado já registrado. **Exportar logs de movimentações** baixa o histórico com participante, ação, responsável e data/hora. Os arquivos contêm dados pessoais e devem ser guardados com cuidado.

As faixas de letras podem ser editadas no mesmo painel. É preciso cobrir A a Z uma única vez; cada linha aponta para um guichê. A alteração afeta a atribuição em importações futuras. Reimporte a lista para recalcular o guichê de participantes que ainda não iniciaram a busca; guichês de buscas ou retiradas já iniciadas ficam protegidos.

```sh
python3 app.py import caminho/participantes.csv
python3 app.py user coordenacao admin
python3 app.py user voluntario1 volunteer
python3 app.py user atendimento1 attendant --guiche 1
export CHECKIN_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python3 app.py serve --host 127.0.0.1 --port 8000
```

Senhas são pedidas no terminal. O segredo de sessão deve ser salvo num gerenciador de segredos ou variável persistente do servidor; se mudar, sessões existentes expiram. Para acesso por celular ou tablet, use um domínio com HTTPS apontando para o servidor e configure `CHECKIN_PUBLIC_URL=https://seu-dominio`. Publique apenas o aplicativo por um proxy HTTPS, mantenha a pasta `data/` fora do acesso web e faça backup periódico dessa pasta. O servidor de desenvolvimento escuta apenas em `127.0.0.1` por padrão.

Se o proxy HTTPS estiver no mesmo servidor, configure `CHECKIN_TRUSTED_PROXY=1` e faça o proxy substituir o cabeçalho `X-Forwarded-For` pelo IP real do cliente. Isso mantém o limite de tentativas individual mesmo com muitos participantes atrás do proxy. Sem essa configuração, todas as requisições repassadas de `127.0.0.1` compartilham o mesmo limite.

O QR Code deverá apontar para a URL pública da raiz (`https://seu-dominio/`). Gere e imprima o código quando o domínio estiver definido; antes da implantação não há URL definitiva para codificar.

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

Antes do uso real, teste o fluxo completo em vários celulares e guichês com uma cópia da lista, confirme o domínio HTTPS e faça um ensaio de perda de rede e retorno da sincronização.

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
