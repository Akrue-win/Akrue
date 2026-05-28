FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY akrue ./akrue
COPY webhook/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
COPY webhook ./webhook
EXPOSE 8080
CMD ["gunicorn", "webhook.app:app", "--bind", "0.0.0.0:8080", "--workers", "2"]
