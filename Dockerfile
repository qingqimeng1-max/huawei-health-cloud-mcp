FROM python:3.12-slim

ENV PYTHONPATH=/app \
    TZ=Asia/Shanghai \
    DATA_DIR=/data \
    SYNC_INTERVAL_HOURS=2

RUN pip install --no-cache-dir cryptography fastmcp

WORKDIR /app
COPY hwclient/ ./hwclient/
COPY app/ ./app/

EXPOSE 8000

CMD ["python", "app/mcp_server.py"]
