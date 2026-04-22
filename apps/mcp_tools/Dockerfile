FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/packages/contracts/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    PIP_ROOT_USER_ACTION=ignore

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install \
      --no-cache-dir \
      --index-url https://pypi.org/simple \
      --default-timeout=180 \
      --retries 10 \
      -r /app/requirements.txt

COPY . /app

EXPOSE 8090

CMD ["uvicorn", "apps.mcp_tools.app.main:app", "--host", "0.0.0.0", "--port", "8090"]