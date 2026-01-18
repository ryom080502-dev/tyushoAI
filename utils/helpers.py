"""
共通ヘルパー関数
"""
import random
import string
from database import db
import config

def generate_user_id() -> str:
    """ユニークなユーザーIDを生成"""
    return 'user_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))

def generate_token(length=8) -> str:
    """LINE連携用トークンを生成"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def check_usage_limit(u_id: str) -> bool:
    """使用上限をチェック"""
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        return False

    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})

    used = subscription.get("used", 0)
    limit = subscription.get("limit", 10)

    return used < limit

def get_user_subscription(u_id: str):
    """ユーザーのサブスク情報を取得"""
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        return None

    user_data = user_doc.to_dict()
    return user_data.get("subscription", {})

def get_user_by_line_id(line_user_id: str):
    """LINE User IDからユーザーIDを取得"""
    users = db.collection(config.COL_USERS).where("line_user_id", "==", line_user_id).limit(1).stream()
    user_list = list(users)

    if user_list:
        return user_list[0].id
    return None
