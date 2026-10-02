#!/bin/sh
# Na primeira partida, copia as faixas de guichês da imagem para o volume de dados:
# o painel grava alterações nesse arquivo, que precisa sobreviver a atualizações da imagem.
set -e
if [ -n "$CHECKIN_RANGES_FILE" ] && [ ! -e "$CHECKIN_RANGES_FILE" ]; then
    cp /app/config/guiches.json "$CHECKIN_RANGES_FILE"
fi
exec "$@"
