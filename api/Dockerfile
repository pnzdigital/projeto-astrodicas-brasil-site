FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1
# Binding to 0.0.0.0 is appropriate for Coolify/Kubernetes deployment where:
# - Container runs in isolated network namespace
# - Multi-container setup requires inter-container communication (nginx → api)
# - Infrastructure layer handles network access control
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
