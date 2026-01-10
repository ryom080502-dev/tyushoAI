FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
COPY gen-lang-client-0553940805-e017df0cff23.json /workspace/