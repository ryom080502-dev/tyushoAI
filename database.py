"""
データベース接続・初期化
Firestore と Cloud Storage の初期化
"""
from google.cloud import firestore, storage
from passlib.context import CryptContext
import config

# === Firestore / Cloud Storage 初期化 ===
db = firestore.Client()
storage_client = storage.Client()

# === パスワードハッシュ設定 ===
# pbkdf2_sha256を優先、bcryptも下位互換性のためサポート
pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")

def init_admin():
    """管理者アカウントの初期化（マルチユーザー構造）"""
    admin_ref = db.collection(config.COL_USERS).document("admin")
    if not admin_ref.get().exists:
        admin_ref.set({
            "email": "admin@smartbuilder.ai",
            "password": pwd_context.hash("password"),
            "role": "admin",
            "created_at": firestore.SERVER_TIMESTAMP,
            "line_user_id": None,
            "subscription": {
                "plan": "unlimited",
                "status": "active",
                "limit": 99999,
                "used": 0,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "current_period_start": None,
                "current_period_end": None,
                "cancel_at_period_end": False
            }
        })
        print("[OK] 管理者アカウントを初期化しました")
