# Imagem do sistema de credenciamento: só a biblioteca padrão do Python, sem pip install.

# Versão mostrada no painel, tirada do .git que o compose passa como contexto "git" (tag vX.Y.Z,
# commits depois dela e commit curto). O .git não entra na imagem final, só o arquivo VERSION.
# Sem esse contexto (docker build direto), a versão fica "desconhecida".
FROM scratch AS git
FROM python:3.13-slim AS versao
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=git . /src/.git
RUN git --git-dir=/src/.git describe --tags --always --match 'v[0-9]*' > /VERSION 2>/dev/null \
    || echo desconhecida > /VERSION

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHECKIN_DATA_DIR=/data \
    CHECKIN_RANGES_FILE=/data/guiches.json

WORKDIR /app

# O código fica com o root (somente leitura para o serviço); o que muda vai para /data.
RUN useradd --system --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin credenciamento \
    && install -d -o credenciamento -g credenciamento -m 700 /data /backups

COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY . .
COPY --from=versao /VERSION /app/VERSION

USER credenciamento
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=4).close()"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
