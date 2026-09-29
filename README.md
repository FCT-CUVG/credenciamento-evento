# Sistema de Credenciamento

Sistema local para pré-check-in, busca de kits, atendimento em guichês e acompanhamento da coordenação. Usa Python 3.10+ e a biblioteca padrão, sem dependências de aplicação.

## Fluxo

1. Participante abre `/` pelo QR Code, informa nome completo e e-mail como constam na inscrição e confirma presença. A página abre em inglês e oferece um botão para português brasileiro.
2. Voluntário abre `/busca`, assume uma pessoa, busca o kit e marca que o deixou no guichê mostrado no cartão.
3. Atendente abre `/fila` e confirma a retirada após entregar o kit. A conta do atendente determina o guichê inicial; o menu permite consultar outros guichês, mas cada atendente só pode confirmar entregas do próprio guichê.
4. Qualquer pessoa pode abrir `/painel/resumo`, sem login, para acompanhar inscritos, pessoas que chegaram e participantes credenciados. A coordenação usa `/painel` para ver também as etapas intermediárias, a lista individual, o estado da sincronização, a importação/exportação de participantes e as faixas dos guichês.

As telas operacionais da equipe exigem conta e senha; o resumo público mostra apenas três totais. O menu da equipe mostra todos os guichês de `config/guiches.json`, além dos guichês atribuídos diretamente a participantes ou atendentes. O participante nunca vê a lista completa nem o CPF. A fila atualiza automaticamente a cada cinco segundos; os painéis, a cada dez segundos. Assumir ou liberar uma busca age imediatamente; marcar o material como pronto no guichê e confirmar a retirada pedem um segundo clique.

## Identidade visual do evento

Edite [config/evento.yaml](config/evento.yaml) para definir o nome completo e curto do evento, a logo, a decoração opcional, as fontes de corpo e títulos e as cores. Os arquivos de imagem e fonte ficam em `static/assets/`; escreva no YAML apenas o nome do arquivo, por exemplo `logo: meu-evento.svg`. A logo aceita SVG, PNG, JPEG ou WebP; a decoração, quando definida, aceita SVG; as fontes aceitam WOFF2. O exemplo incluído usa os arquivos já presentes no projeto.

As cores devem ser escritas como `"#RRGGBB"`, com aspas. `navy` controla o painel de destaque e os títulos; `navy_header`, a navegação; `leaf` e `leaf_dark`, ações e estados positivos; `gold`, detalhes e foco; `ink` e `slate`, textos; `fog` e `white`, fundos. Preserve contraste legível entre fundo e texto ao escolher a paleta. Depois de editar o YAML ou substituir arquivos, recarregue a página; o servidor valida os nomes, formatos e campos da configuração. O YAML aceito aqui é intencionalmente simples: pares `chave: valor`, seções com recuo de dois espaços e comentários com `#`, sem listas ou recursos avançados. Isso mantém a aplicação sem dependências extras.

A página pública tem versões em inglês e português; as telas da equipe permanecem em português. As instruções específicas da origem da inscrição ficam em `registration_hint_en` e `registration_hint_pt` no mesmo YAML. Defina ambas ou remova ambas para usar textos genéricos. Assim, nomes de sistemas de inscrição não ficam fixos no código da aplicação.

## Preparação

Revise [config/guiches.json](config/guiches.json) antes de importar a lista real. As faixas incluídas são **somente um exemplo**. Cada faixa inclui as letras inicial e final; nomes com acentos são normalizados. A importação falha se a inicial não estiver coberta por uma faixa única. Uma coluna `guiche` no arquivo pode substituir a regra para casos específicos.

CSV: cabeçalhos obrigatórios `nome,nome_cracha,afiliacao,email,cpf`; `cpf` deve ter 11 dígitos ou seguir o formato `xxx.xxx.xxx-xx`. As colunas opcionais `pago` e `prioridade` aceitam `0` ou `1`; `guiche` também é opcional e, quando ausente, é calculado pelas faixas configuradas. JSON: lista de objetos com essas mesmas chaves, ou objeto `{ "participants": [...] }`. Há um modelo em [exemplos/participantes.csv](exemplos/participantes.csv). Participantes não pagos recebem a orientação para procurar atendimento antes do pré-check-in. Se importar novamente após ajustar as faixas, os guichês são atualizados e os estados e horários de credenciamento permanecem. Uma mudança de guichê é recusada se a busca do kit ou a retirada já começou.

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
