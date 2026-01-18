"""
SmartBuilder AI - メインアプリケーション
シンプル化されたエントリーポイント
"""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware

# 設定とデータベース初期化
import config
from database import init_admin

# ルーター
from routers import auth, records, line, export, admin

# ディレクトリ作成
os.makedirs(config.UPLOAD_DIR, exist_ok=True)
os.makedirs(config.FONT_DIR, exist_ok=True)

# FastAPI アプリケーション初期化
app = FastAPI(title="SmartBuilder AI", version="2.0.0")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(auth.router, tags=["認証"])
app.include_router(records.router, tags=["レコード管理"])
app.include_router(line.router, tags=["LINE連携"])
app.include_router(export.router, tags=["エクスポート"])
app.include_router(admin.router, tags=["管理者"])

# 基本エンドポイント
@app.get("/")
async def root():
    """ルートエンドポイント - index.htmlを返す"""
    return FileResponse("index.html")

@app.get("/favicon.ico")
async def favicon():
    """Favicon（404エラー防止）"""
    return Response(status_code=204)

# アプリケーション起動時の初期化
@app.on_event("startup")
async def startup_event():
    """起動時処理"""
    print("=" * 50)
    print("SmartBuilder AI - Starting...")
    print("=" * 50)
    init_admin()
    print("[OK] Application ready!")
    print("=" * 50)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
