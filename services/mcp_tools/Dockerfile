FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
ENV PYTHONPATH="/app:/app/packages/contracts/src"
EXPOSE 8090
CMD ["uvicorn", "services.mcp_tools.app.main:app", "--host", "0.0.0.0", "--port", "8090"]
