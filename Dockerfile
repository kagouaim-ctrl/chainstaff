# Public Telegram demo (test mode only). See docs/telegram-demo.md.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY agent agent
COPY bot bot
COPY backend backend
COPY web web
COPY config config
COPY scripts scripts
RUN pip install --no-cache-dir -e . && useradd -m demo && mkdir -p data && chown demo data
USER demo
CMD ["python3", "scripts/public_demo.py"]
