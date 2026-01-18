"""
LINE連携ルーター
LINE Bot Webhook・トークン管理
"""
import os
import time
import re
from fastapi import APIRouter, Request, HTTPException, Depends
from google.cloud import firestore
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextMessage, TextSendMessage
from database import db
from services.auth_service import get_current_user
from services.gemini_service import analyze_with_gemini_retry
from services.image_service import compress_image
from services.storage_service import upload_to_gcs
from utils.helpers import generate_token, get_user_by_line_id, check_usage_limit
import config

router = APIRouter()

# LINE 設定
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

@router.get("/api/line-token")
async def generate_line_token(u_id: str = Depends(get_current_user)):
    """LINE連携用トークンを生成"""
    # 既存のトークンを削除（1ユーザー1トークン）
    old_tokens = db.collection(config.COL_LINE_TOKENS).where("user_id", "==", u_id).stream()
    for old_token in old_tokens:
        old_token.reference.delete()

    # 新しいトークンを生成
    token = generate_token(8)

    # Firestoreに保存
    db.collection(config.COL_LINE_TOKENS).document(token).set({
        "user_id": u_id,
        "created_at": firestore.SERVER_TIMESTAMP,
        "used": False,
        "expires_at": firestore.SERVER_TIMESTAMP  # 24時間後に期限切れにする場合は別途処理
    })

    return {"token": token, "message": "LINEでこのトークンを送信してください"}

@router.get("/api/line-status")
async def get_line_status(u_id: str = Depends(get_current_user)):
    """LINE連携ステータスを取得"""
    user_doc = db.collection(config.COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    user_data = user_doc.to_dict()
    line_user_id = user_data.get("line_user_id")

    return {
        "connected": line_user_id is not None,
        "line_user_id": line_user_id
    }

@router.post("/api/line-disconnect")
async def disconnect_line(u_id: str = Depends(get_current_user)):
    """LINE連携を解除"""
    db.collection(config.COL_USERS).document(u_id).update({
        "line_user_id": None
    })

    return {"message": "LINE連携を解除しました"}

@router.post("/webhook")
async def webhook(request: Request):
    """LINE Webhook エンドポイント"""
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """テキストメッセージハンドラー（トークン連携対応）"""
    text = event.message.text
    line_user_id = event.source.user_id

    # トークン形式かチェック（8文字の英数字）
    if re.match(r'^[A-Z0-9]{8}$', text):
        # トークンを検証
        token_doc = db.collection(config.COL_LINE_TOKENS).document(text).get()

        if token_doc.exists:
            token_data = token_doc.to_dict()

            if not token_data.get("used", False):
                user_id = token_data["user_id"]

                # ユーザーにline_user_idを紐付け
                db.collection(config.COL_USERS).document(user_id).update({
                    "line_user_id": line_user_id
                })

                # トークンを使用済みにする
                db.collection(config.COL_LINE_TOKENS).document(text).update({"used": True})

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="✅ LINE連携が完了しました！\n\n今後は画像を送信すると自動的に解析されます。")
                )
                return
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ このトークンは既に使用されています。\n\nWebアプリから新しいトークンを生成してください。")
                )
                return
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 無効なトークンです。\n\nWebアプリで正しいトークンを確認してください。")
            )
            return

    # トークン以外のテキストメッセージ
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="画像を送信してください📷\n\nまたは、Webアプリで生成したトークンを送信してLINE連携を完了してください。")
    )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    """画像メッセージハンドラー（マルチユーザー対応）"""
    print(f"=== LINE Image Message Received ===")
    line_user_id = event.source.user_id
    print(f"LINE User ID: {line_user_id}")

    # LINE User IDからユーザーを検索
    user_id = get_user_by_line_id(line_user_id)
    print(f"Found User ID: {user_id}")

    if not user_id:
        print("❌ User not found")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ LINE連携が完了していません。\n\nWebアプリにログインして、トークンを生成・送信してください。")
        )
        return

    # 使用上限チェック
    if not check_usage_limit(user_id):
        print("❌ Usage limit exceeded")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ 月間上限に達しました。\n\nWebアプリからプランをアップグレードしてください。")
        )
        return

    try:
        print("📥 Downloading image...")
        # 画像をダウンロード
        message_content = line_bot_api.get_message_content(event.message.id)

        # 一時保存
        temp_path = os.path.join(config.UPLOAD_DIR, f"line_{int(time.time())}.jpg")
        print(f"Saving to: {temp_path}")

        # image_contentは既にバイナリデータ
        with open(temp_path, "wb") as f:
            f.write(message_content.content)

        # 画像を圧縮
        print("Compressing image...")
        temp_path = compress_image(temp_path, max_size=(1920, 1080), quality=85)

        print("☁️ Uploading to GCS...")
        # GCSにアップロード
        gcs_file_name = f"line_receipts/{int(time.time())}.jpg"
        public_url = upload_to_gcs(temp_path, gcs_file_name)
        print(f"GCS URL: {public_url}")

        print("🤖 Analyzing with Gemini...")
        # Gemini解析（リトライ機能付き）
        data_list = analyze_with_gemini_retry(temp_path, max_retries=3)

        print("💾 Saving to Firestore...")
        # サブコレクションに保存
        for item in (data_list if isinstance(data_list, list) else [data_list]):
            doc_id = str(int(time.time()*1000))
            time.sleep(0.001)
            item.update({
                "image_url": public_url,
                "id": doc_id,
                "created_at": firestore.SERVER_TIMESTAMP,
                "is_pdf": False,
                "pdf_images": [],
                "category": "その他",
                "source": "line"
            })
            db.collection(config.COL_USERS).document(user_id).collection("records").document(doc_id).set(item)

        # 使用回数をインクリメント
        db.collection(config.COL_USERS).document(user_id).update({
            "subscription.used": firestore.Increment(1)
        })

        # 一時ファイル削除
        os.remove(temp_path)
        print("✅ Processing complete")

        # 結果を通知
        result_text = "✅ 解析完了しました！\n\n"
        for item in (data_list if isinstance(data_list, list) else [data_list]):
            result_text += f"📅 日付: {item.get('date', '不明')}\n"
            result_text += f"🏪 店舗: {item.get('vendor_name', '不明')}\n"
            result_text += f"💰 金額: ¥{item.get('total_amount', 0):,}\n\n"

        result_text += "Webアプリで詳細を確認できます。"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=result_text)
        )

    except Exception as e:
        print(f"❌ LINE image processing error: {str(e)}")
        import traceback
        traceback.print_exc()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"❌ 画像の解析に失敗しました。\n\nエラー: {str(e)}\n\n別の画像で再度お試しください。")
        )
