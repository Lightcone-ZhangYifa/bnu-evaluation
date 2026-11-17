FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd -g 10001 ledger && useradd -r -u 10001 -g ledger ledger \
    && mkdir -p /data && chown ledger:ledger /data
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=ledger:ledger app ./app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn","app.main:create_app","--factory","--host","0.0.0.0","--port","8000","--workers","2","--proxy-headers","--forwarded-allow-ips","*","--limit-concurrency","100","--timeout-keep-alive","10"]
