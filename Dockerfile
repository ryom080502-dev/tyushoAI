FROM python:3.11-slim

WORKDIR /workspace

# システムパッケージのインストール
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Pythonパッケージのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションファイルをコピー
COPY . .

# サービスアカウントキーをコピー
COPY gen-lang-client-0553940805-e017df0cff23.json /workspace/

# ポートを公開
EXPOSE 8080

# Uvicornでアプリを起動
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]