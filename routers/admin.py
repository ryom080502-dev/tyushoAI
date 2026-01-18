"""
管理者ルーター
ユーザー管理機能
"""
from fastapi import APIRouter, HTTPException, Depends
from google.cloud import firestore
from database import db
from services.auth_service import get_current_user, hash_password
from utils.helpers import generate_user_id
import config

router = APIRouter()

def require_admin(u_id: str = Depends(get_current_user)):
    """管理者権限チェック"""
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    user_data = user_doc.to_dict()
    if user_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です")

    return u_id

@router.get("/admin/users")
async def get_all_users(admin_id: str = Depends(require_admin)):
    """全ユーザーの一覧を取得（管理者のみ）"""
    users_ref = db.collection(config.COL_USERS).stream()
    users = []

    for user_doc in users_ref:
        user_data = user_doc.to_dict()
        users.append({
            "id": user_doc.id,
            "email": user_data.get("email", ""),
            "role": user_data.get("role", "user"),
            "created_at": user_data.get("created_at"),
            "subscription": user_data.get("subscription", {}),
            "line_user_id": user_data.get("line_user_id")
        })

    return {"users": users}

@router.post("/admin/users")
async def create_user(data: dict, admin_id: str = Depends(require_admin)):
    """ユーザーを作成（管理者のみ）"""
    print(f"=== Create User Request ===")
    print(f"Admin ID: {admin_id}")
    print(f"Data: {data}")

    email = data.get("email")
    password = data.get("password")
    plan = data.get("plan", "free")

    print(f"Email: {email}")
    print(f"Password: {'***' if password else None}")
    print(f"Plan: {plan}")

    if not email or not password:
        print("❌ Missing email or password")
        raise HTTPException(status_code=400, detail="メールアドレスとパスワードは必須です")

    # メールアドレスの重複チェック
    existing = db.collection(config.COL_USERS).where("email", "==", email).limit(1).stream()
    existing_list = list(existing)
    print(f"Existing users: {len(existing_list)}")

    if existing_list:
        print("❌ Email already exists")
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています")

    # ユーザーID生成
    user_id = generate_user_id()
    print(f"Generated user ID: {user_id}")

    # プラン情報取得
    plan_info = config.PLANS.get(plan, config.PLANS["free"])

    # ユーザー作成
    try:
        db.collection(config.COL_USERS).document(user_id).set({
            "email": email,
            "password": hash_password(password),
            "role": "user",
            "created_at": firestore.SERVER_TIMESTAMP,
            "line_user_id": None,
            "subscription": {
                "plan": plan,
                "status": "active",
                "limit": plan_info["limit"],
                "used": 0,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "current_period_start": firestore.SERVER_TIMESTAMP,
                "current_period_end": None,
                "cancel_at_period_end": False
            }
        })
        print(f"✅ User created successfully: {user_id}")
        return {"message": "ユーザーを作成しました", "user_id": user_id}
    except Exception as e:
        print(f"❌ Error creating user: {str(e)}")
        raise HTTPException(status_code=500, detail=f"ユーザー作成に失敗しました: {str(e)}")

@router.delete("/admin/users/{user_id}")
async def delete_user(user_id: str, admin_id: str = Depends(require_admin)):
    """ユーザーを削除（管理者のみ）"""
    if user_id == "admin":
        raise HTTPException(status_code=403, detail="管理者アカウントは削除できません")

    user_ref = db.collection(config.COL_USERS).document(user_id)
    if not user_ref.get().exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    # サブコレクションのレコードを全削除
    records_ref = user_ref.collection("records").stream()
    for record in records_ref:
        record.reference.delete()

    # ユーザードキュメントを削除
    user_ref.delete()

    return {"message": "ユーザーを削除しました"}

@router.put("/admin/users/{user_id}/subscription")
async def update_user_subscription(user_id: str, data: dict, admin_id: str = Depends(require_admin)):
    """ユーザーのプランを変更（管理者のみ）"""
    plan_id = data.get("plan")

    if plan_id not in config.PLANS:
        raise HTTPException(status_code=400, detail="無効なプランです")

    plan = config.PLANS[plan_id]

    # サブスク情報を更新
    db.collection(config.COL_USERS).document(user_id).update({
        "subscription.plan": plan_id,
        "subscription.limit": plan["limit"],
        "subscription.status": "active"
    })

    return {"message": "プランを更新しました"}
