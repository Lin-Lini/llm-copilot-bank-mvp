FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
ENV PYTHONPATH="/app:/app/packages/contracts/src"
EXPOSE 8080
CMD ["uvicorn", "services.backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
