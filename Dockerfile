# Imagem do sistema de credenciamento: só a biblioteca padrão do Python, sem pip install.
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

USER credenciamento
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=4).close()"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
