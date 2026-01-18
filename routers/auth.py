"""
認証ルーター
ログイン・登録機能
"""
from fastapi import APIRouter, Form, HTTPException, Depends
from google.cloud import firestore
from database import db
from services.auth_service import create_access_token, verify_password, hash_password, get_current_user
from utils.helpers import generate_user_id
import config

router = APIRouter()

@router.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    """ログイン（メールアドレス対応）"""
    print(f"=== Login attempt ===")
    print(f"Email: {email}")

    # メールアドレスでユーザーを検索
    users = db.collection(config.COL_USERS).where("email", "==", email).limit(1).stream()
    user_list = list(users)

    print(f"Users found: {len(user_list)}")

    if not user_list:
        print("❌ User not found")
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

    user_doc = user_list[0]
    user_id = user_doc.id
    user_data = user_doc.to_dict()

    print(f"User ID: {user_id}")
    print(f"User email in DB: {user_data.get('email')}")
    print(f"Password hash (first 20 chars): {user_data.get('password', '')[:20]}...")

    # パスワード検証
    password_valid = verify_password(password, user_data["password"])
    print(f"Password valid: {password_valid}")

    if not password_valid:
        print("❌ Invalid password")
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

    print("✅ Login successful")
    token = create_access_token({"sub": user_id})
    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "role": user_data.get("role", "user")}

@router.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):
    """新規ユーザー登録"""
    # メールアドレスの重複チェック
    existing_users = db.collection(config.COL_USERS).where("email", "==", email).limit(1).stream()
    if list(existing_users):
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています")

    # 新規ユーザーID生成
    user_id = generate_user_id()

    # 初期サブスク設定
    initial_subscription = {
        "plan": "free",
        "status": "active",
        "limit": config.PLANS["free"]["limit"],
        "used": 0,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "current_period_start": firestore.SERVER_TIMESTAMP,
        "current_period_end": None,
        "cancel_at_period_end": False
    }

    # Firestoreに保存
    db.collection(config.COL_USERS).document(user_id).set({
        "email": email,
        "password": hash_password(password),
        "role": "user",
        "created_at": firestore.SERVER_TIMESTAMP,
        "line_user_id": None,
        "subscription": initial_subscription
    })

    # トークン生成
    token = create_access_token({"sub": user_id})

    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "message": "登録完了"}

@router.get("/api/status")
async def get_status(u_id: str = Depends(get_current_user)):
    """ユーザーのステータスとレコード一覧を取得（サブコレクション対応）"""
    # ユーザー情報を取得
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})

    # サブコレクションからレコードを取得
    records = []
    records_ref = db.collection(config.COL_USERS).document(u_id).collection("records").stream()

    for record in records_ref:
        data = record.to_dict()
        data["id"] = record.id
        records.append(data)

    return {
        "user_id": u_id,
        "email": user_data.get("email", ""),
        "role": user_data.get("role", "user"),
        "subscription": subscription,
        "records": records
    }

@router.get("/api/subscription")
async def get_subscription(u_id: str = Depends(get_current_user)):
    """現在のサブスク状態を取得"""
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})

    plan_id = subscription.get("plan", "free")
    plan_info = config.PLANS.get(plan_id, config.PLANS["free"])

    return {
        "plan": plan_id,
        "plan_name": plan_info["name"],
        "status": subscription.get("status", "active"),
        "limit": subscription.get("limit", 10),
        "used": subscription.get("used", 0),
        "remaining": subscription.get("limit", 10) - subscription.get("used", 0),
        "current_period_end": subscription.get("current_period_end"),
        "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
        "features": plan_info["features"]
    }

@router.get("/api/plans")
async def get_plans():
    """利用可能なプランの一覧を取得"""
    return {
        "plans": [
            {
                "id": plan_id,
                "name": plan_data["name"],
                "price": plan_data["price"],
                "currency": plan_data.get("currency", "jpy"),
                "limit": plan_data["limit"],
                "features": plan_data["features"]
            }
            for plan_id, plan_data in config.PLANS.items()
            if plan_id != "unlimited"
        ]
    }
