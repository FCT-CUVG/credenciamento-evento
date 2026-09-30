#!/bin/sh
# Atualiza esta instalação com o código do GitHub e reinicia o serviço.
#
# Uso, no servidor, dentro da pasta da instalação:
#   sudo ./atualizar.sh            # versão mais recente da main
#   sudo ./atualizar.sh v1.2.0     # uma tag, branch ou commit (serve também para voltar atrás)
#
# Antes de trocar o código, faz um backup do banco. Se já estiver na versão pedida, não faz nada.
# Os valores abaixo seguem o README; podem ser trocados por variáveis de ambiente.
set -eu

SERVICO=${CHECKIN_SERVICE:-credenciamento}
USUARIO=${CHECKIN_USER:-credenciamento}
ARQUIVO_ENV=${CHECKIN_ENV_FILE:-/etc/credenciamento.env}
PASTA_BACKUP=${CHECKIN_BACKUP_DIR:-/var/backups/credenciamento}

falha() { echo "$*" >&2; exit 1; }
curto() { printf '%.7s' "$1"; }
como_servico() { sudo -u "$USUARIO" "$@"; }
# Roda com as mesmas variáveis do serviço, para usar o mesmo banco.
com_ambiente() { (set -a; . "$ARQUIVO_ENV"; set +a; exec sudo -E -u "$USUARIO" "$@"); }
eh_branch() { como_servico git show-ref --verify --quiet "refs/remotes/origin/$1"; }

# O corpo fica numa função para o shell ler o script inteiro antes de rodar:
# o git pode trocar este mesmo arquivo durante a atualização.
main() {
    alvo=${1:-main}
    cd "$(dirname "$0")"
    [ "$(id -u)" -eq 0 ] || falha "Rode com sudo: sudo $0 $*"
    [ -r "$ARQUIVO_ENV" ] || falha "Não encontrei $ARQUIVO_ENV (veja o passo 3 do README)."

    como_servico git fetch --quiet --tags --prune origin
    if eh_branch "$alvo"; then
        novo=$(como_servico git rev-parse "origin/$alvo")
    else
        novo=$(como_servico git rev-parse --verify --quiet "$alvo^{commit}") \
            || falha "A versão \"$alvo\" não existe no GitHub."
    fi
    atual=$(como_servico git rev-parse HEAD)
    if [ "$novo" = "$atual" ]; then
        echo "Já está na versão $alvo ($(curto "$atual")). Nada a fazer."
        return
    fi

    echo "Atualizando de $(curto "$atual") para $alvo ($(curto "$novo"))..."
    install -d -o "$USUARIO" -m 700 "$PASTA_BACKUP"
    backup="$PASTA_BACKUP/antes-de-$(curto "$novo")-$(date +%Y%m%d-%H%M%S)-$$.sqlite3"
    com_ambiente python3 app.py backup "$backup"

    if eh_branch "$alvo"; then
        destino="-B $alvo origin/$alvo"
    else
        destino="--detach $novo"
    fi
    # shellcheck disable=SC2086
    como_servico git checkout --quiet $destino \
        || falha "O git recusou a troca (arquivo alterado no servidor?). Veja: sudo -u $USUARIO git status. O serviço continua na versão anterior."

    systemctl restart "$SERVICO"
    sleep 2
    if systemctl is-active --quiet "$SERVICO"; then
        echo "Pronto: $SERVICO rodando $alvo ($(curto "$novo"))."
    else
        echo "O serviço não subiu. Veja: journalctl -u $SERVICO -n 50" >&2
        falha "Para voltar à versão anterior: sudo $0 $atual"
    fi
}

main "$@"
