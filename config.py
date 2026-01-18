"""
設定ファイル
環境変数や定数を一元管理
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === 環境変数 ===
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-123")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")

# === JWT設定 ===
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24時間

# === Cloud Storage設定 ===
BUCKET_NAME = "my-receipt-app-storage-01"

# === Firestore コレクション名 ===
COL_USERS = "users"
COL_LINE_TOKENS = "line_tokens"

# === ディレクトリ設定 ===
UPLOAD_DIR = "uploads"
FONT_DIR = "fonts"

# === Gemini AI プロンプト ===
GEMINI_PROMPT = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON形式で返せ。
※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈。和暦禁止。"""

# === サブスクプラン定義 ===
PLANS = {
    "free": {
        "name": "無料プラン",
        "limit": 10,
        "price": 0,
        "currency": "jpy",
        "stripe_price_id": None,
        "features": [
            "月10件まで",
            "基本的な解析機能",
            "CSV/Excelエクスポート"
        ]
    },
    "premium": {
        "name": "プレミアムプラン",
        "limit": 100,
        "price": 980,
        "currency": "jpy",
        "stripe_price_id": None,
        "features": [
            "月100件まで",
            "高精度AI解析",
            "PDF対応",
            "LINE連携",
            "優先サポート"
        ]
    },
    "enterprise": {
        "name": "エンタープライズプラン",
        "limit": 1000,
        "price": 4980,
        "currency": "jpy",
        "stripe_price_id": None,
        "features": [
            "月1000件まで",
            "全機能利用可能",
            "API連携",
            "専任サポート",
            "カスタマイズ対応"
        ]
    },
    "unlimited": {
        "name": "無制限プラン（管理者用）",
        "limit": 99999,
        "price": 0,
        "currency": "jpy",
        "stripe_price_id": None,
        "features": ["全機能無制限"]
    }
}

# === Stripe設定 ===
STRIPE_ENABLED = False

# === CORS設定 ===
ALLOWED_ORIGINS = [
    "https://my-ai-app-643484544688.asia-northeast1.run.app",  # 本番URL
    "http://localhost:8000",  # ローカル開発用
    "http://127.0.0.1:8000",  # ローカル開発用
]
