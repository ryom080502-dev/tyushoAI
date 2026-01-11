import os, time, json, shutil, io, re
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext

# --- Google Cloud / LINE 用インポート ---
from google.cloud import firestore, storage
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextMessage, TextSendMessage

# --- PDF処理用インポート ---
try:
    from pdf2image import convert_from_path
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("警告: pdf2imageがインストールされていません。PDF画像化機能は無効です。")

load_dotenv()

# --- 認証設定 ---
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-123")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 
# pbkdf2_sha256を優先、bcryptも下位互換性のためサポート
pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")

# Gemini 設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-pro')

# LINE 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# --- Google Cloud 初期化 ---
db = firestore.Client()
storage_client = storage.Client()

# 【重要】ここをご自身のバケット名に書き換えてください
BUCKET_NAME = "my-receipt-app-storage-01" 

COL_USERS = "users"
COL_LINE_TOKENS = "line_tokens"

# ===== サブスクプラン定義 =====
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

# Stripe設定（将来実装）
STRIPE_ENABLED = False
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")

app = FastAPI()

# CORS設定
from fastapi.middleware.cors import CORSMiddleware

# 本番環境のドメインを設定
ALLOWED_ORIGINS = [
    "https://my-ai-app-643484544688.asia-northeast1.run.app",  # 本番URL
    "http://localhost:8000",  # ローカル開発用
    "http://127.0.0.1:8000",  # ローカル開発用
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

FONT_DIR = "fonts"
os.makedirs(FONT_DIR, exist_ok=True)

# --- 日本語フォント管理 ---
def download_japanese_font():
    """Noto Sans JPフォントをダウンロード"""
    font_path = os.path.join(FONT_DIR, "NotoSansJP-Regular.ttf")
    
    if os.path.exists(font_path):
        print("日本語フォントは既にダウンロード済みです")
        return font_path
    
    print("日本語フォントをダウンロード中...")
    try:
        import requests
        url = "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf"
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            with open(font_path, "wb") as f:
                f.write(response.content)
            print("日本語フォントのダウンロードが完了しました")
            return font_path
        else:
            print(f"フォントのダウンロードに失敗しました: {response.status_code}")
            return None
    except Exception as e:
        print(f"フォントダウンロードエラー: {e}")
        return None

# フォントを事前にダウンロード
JAPANESE_FONT_PATH = download_japanese_font()

# --- ユーティリティ関数 ---

def upload_to_gcs(file_path, destination_blob_name):
    """ファイルをCloud Storageにアップロードし、公開URLを返す"""
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(file_path)
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{destination_blob_name}"

def convert_pdf_to_images(pdf_path: str) -> list:
    """PDFを画像に変換してGCSにアップロード"""
    if not PDF_SUPPORT:
        print("PDF画像化機能が無効です")
        return []
    
    try:
        images = convert_from_path(pdf_path, dpi=150)
        image_urls = []
        
        base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
        timestamp = int(time.time())
        
        for i, image in enumerate(images):
            temp_image_path = os.path.join(UPLOAD_DIR, f"{base_filename}_page{i+1}.jpg")
            image.save(temp_image_path, "JPEG", quality=85)
            
            gcs_file_name = f"pdf_images/{timestamp}_{base_filename}_page{i+1}.jpg"
            public_url = upload_to_gcs(temp_image_path, gcs_file_name)
            image_urls.append(public_url)
            
            os.remove(temp_image_path)
        
        return image_urls
    except Exception as e:
        print(f"PDF画像化エラー: {e}")
        return []

# --- 認証ロジック ---

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request):
    token = request.headers.get("Authorization")
    if not token or not token.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user_optional(request: Request):
    """オプショナルな認証（トークンがない場合はNoneを返す）"""
    token = request.headers.get("Authorization")
    if not token or not token.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def require_admin(u_id: str = Depends(get_current_user)):
    """管理者権限チェック"""
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    
    user_data = user_doc.to_dict()
    if user_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    
    return u_id

# --- ヘルパー関数 ---

def generate_user_id() -> str:
    """ユニークなユーザーIDを生成"""
    import random
    import string
    return 'user_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))

def generate_token(length=8) -> str:
    """LINE連携用トークンを生成"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def analyze_with_gemini_retry(file_path: str, max_retries: int = 3) -> dict:
    """Gemini APIを使用して画像を解析（リトライ機能付き）"""
    for attempt in range(max_retries):
        try:
            print(f"Gemini API attempt {attempt + 1}/{max_retries}...")
            
            # ファイルをアップロード
            genai_file = genai.upload_file(path=file_path)
            
            # 処理待ち
            while genai_file.state.name == "PROCESSING":
                time.sleep(1)
                genai_file = genai.get_file(genai_file.name)
            
            # 解析実行
            response = model.generate_content([genai_file, PROMPT])
            
            if not response.text:
                raise ValueError("Gemini APIからの応答が空です")
            
            # JSONをパース
            data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
            
            print(f"✅ Gemini analysis successful")
            return data_list
            
        except Exception as e:
            print(f"❌ Gemini API error (attempt {attempt + 1}): {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指数バックオフ: 1秒, 2秒, 4秒
                print(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                raise Exception(f"Gemini API解析に失敗しました（{max_retries}回試行）: {str(e)}")

def compress_image(input_path: str, output_path: str = None, max_size: tuple = (1920, 1080), quality: int = 85) -> str:
    """画像を圧縮してファイルサイズを削減"""
    from PIL import Image
    
    if output_path is None:
        output_path = input_path
    
    try:
        with Image.open(input_path) as img:
            # EXIF情報に基づいて画像を回転
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except:
                pass
            
            # RGBに変換（PNGのアルファチャンネル対応）
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # サイズ調整
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # 保存
            img.save(output_path, 'JPEG', optimize=True, quality=quality)
            
            print(f"✅ Image compressed: {input_path} -> {output_path}")
            return output_path
    except Exception as e:
        print(f"⚠️ Image compression failed: {str(e)}, using original")
        return input_path



def check_usage_limit(u_id: str) -> bool:
    """使用上限をチェック"""
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        return False
    
    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})
    
    used = subscription.get("used", 0)
    limit = subscription.get("limit", 10)
    
    return used < limit

def get_user_subscription(u_id: str) -> Optional[dict]:
    """ユーザーのサブスク情報を取得"""
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        return None
    
    user_data = user_doc.to_dict()
    return user_data.get("subscription", {})

def get_user_by_line_id(line_user_id: str) -> Optional[str]:
    """LINE User IDからユーザーIDを取得"""
    users = db.collection(COL_USERS).where("line_user_id", "==", line_user_id).limit(1).stream()
    user_list = list(users)
    
    if user_list:
        return user_list[0].id
    return None

# --- 初期化 ---

def init_admin():
    """管理者アカウントの初期化（マルチユーザー構造）"""
    admin_ref = db.collection(COL_USERS).document("admin")
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
        print("✅ 管理者アカウントを初期化しました")

init_admin()

PROMPT = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON形式で返せ。
※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈。和暦禁止。"""

# --- エンドポイント ---

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/favicon.ico")
async def favicon():
    """Favicon（404エラー防止）"""
    from fastapi.responses import Response
    return Response(status_code=204)

# ===== 認証エンドポイント =====

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    """ログイン（メールアドレス対応）"""
    print(f"=== Login attempt ===")
    print(f"Email: {email}")
    
    # メールアドレスでユーザーを検索
    users = db.collection(COL_USERS).where("email", "==", email).limit(1).stream()
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
    password_valid = pwd_context.verify(password, user_data["password"])
    print(f"Password valid: {password_valid}")
    
    if not password_valid:
        print("❌ Invalid password")
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")
    
    print("✅ Login successful")
    token = create_access_token({"sub": user_id})
    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "role": user_data.get("role", "user")}

@app.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):
    """新規ユーザー登録"""
    # メールアドレスの重複チェック
    existing_users = db.collection(COL_USERS).where("email", "==", email).limit(1).stream()
    if list(existing_users):
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています")
    
    # 新規ユーザーID生成
    user_id = generate_user_id()
    
    # 初期サブスク設定
    initial_subscription = {
        "plan": "free",
        "status": "active",
        "limit": PLANS["free"]["limit"],
        "used": 0,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "current_period_start": firestore.SERVER_TIMESTAMP,
        "current_period_end": None,
        "cancel_at_period_end": False
    }
    
    # Firestoreに保存
    db.collection(COL_USERS).document(user_id).set({
        "email": email,
        "password": pwd_context.hash(password),
        "role": "user",
        "created_at": firestore.SERVER_TIMESTAMP,
        "line_user_id": None,
        "subscription": initial_subscription
    })
    
    # トークン生成
    token = create_access_token({"sub": user_id})
    
    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "message": "登録完了"}

# ===== ユーザー情報エンドポイント =====

@app.get("/api/status")
async def get_status(u_id: str = Depends(get_current_user)):
    """ユーザーのステータスとレコード一覧を取得（サブコレクション対応）"""
    # ユーザー情報を取得
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    
    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})
    
    # サブコレクションからレコードを取得
    records = []
    records_ref = db.collection(COL_USERS).document(u_id).collection("records").stream()
    
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

@app.get("/api/subscription")
async def get_subscription(u_id: str = Depends(get_current_user)):
    """現在のサブスク状態を取得"""
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    
    user_data = user_doc.to_dict()
    subscription = user_data.get("subscription", {})
    
    plan_id = subscription.get("plan", "free")
    plan_info = PLANS.get(plan_id, PLANS["free"])
    
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

@app.get("/api/plans")
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
            for plan_id, plan_data in PLANS.items()
            if plan_id != "unlimited"
        ]
    }

# ===== アップロードエンドポイント =====

@app.post("/upload")
async def upload_receipt(files: List[UploadFile] = File(...), u_id: str = Depends(get_current_user)):
    """複数ファイルのアップロード（サブコレクション対応）"""
    print(f"=== Upload request received ===")
    print(f"User: {u_id}")
    print(f"Files count: {len(files) if files else 0}")
    
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="ファイルが選択されていません")
    
    # 使用上限チェック
    if not check_usage_limit(u_id):
        raise HTTPException(
            status_code=403, 
            detail="月間上限に達しました。プランをアップグレードしてください。"
        )
    
    all_results = []
    
    for idx, file in enumerate(files):
        print(f"\n--- Processing file {idx + 1}/{len(files)}: {file.filename} ---")
        try:
            # ファイル名をサニタイズ
            original_filename = file.filename
            file_ext = os.path.splitext(original_filename)[1]
            safe_filename = f"{int(time.time() * 1000)}{file_ext}"
            
            print(f"Original filename: {original_filename}")
            print(f"Safe filename: {safe_filename}")
            
            # 1. 一時保存
            temp_path = os.path.join(UPLOAD_DIR, safe_filename)
            print(f"Saving to: {temp_path}")
            
            with open(temp_path, "wb") as b: 
                shutil.copyfileobj(file.file, b)
            
            # PDFファイルかどうかをチェック
            is_pdf = original_filename.lower().endswith('.pdf')
            print(f"Is PDF: {is_pdf}")
            
            # 画像の場合は圧縮
            if not is_pdf and file_ext.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                print("Compressing image...")
                temp_path = compress_image(temp_path, max_size=(1920, 1080), quality=85)
            
            # 2. Cloud Storageへアップロード
            gcs_file_name = f"receipts/{safe_filename}"
            print(f"Uploading to GCS: {gcs_file_name}")
            public_url = upload_to_gcs(temp_path, gcs_file_name)
            print(f"GCS URL: {public_url}")
            
            # 3. PDFの場合は画像化
            pdf_image_urls = []
            if is_pdf and PDF_SUPPORT:
                print("Converting PDF to images...")
                pdf_image_urls = convert_pdf_to_images(temp_path)
                print(f"PDF images created: {len(pdf_image_urls)}")
            
            # 4. Gemini 解析（リトライ機能付き）
            print("Starting Gemini analysis...")
            data_list = analyze_with_gemini_retry(temp_path, max_retries=3)
            
            # 5. サブコレクションに保存
            print("Saving to Firestore subcollection...")
            for item in (data_list if isinstance(data_list, list) else [data_list]):
                doc_id = str(int(time.time()*1000))
                time.sleep(0.001)
                item.update({
                    "image_url": public_url,
                    "id": doc_id,
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "is_pdf": is_pdf,
                    "pdf_images": pdf_image_urls if is_pdf else [],
                    "original_filename": original_filename,
                    "category": "その他"
                })
                # サブコレクションに保存
                db.collection(COL_USERS).document(u_id).collection("records").document(doc_id).set(item)
            
            # 使用回数をインクリメント
            db.collection(COL_USERS).document(u_id).update({
                "subscription.used": firestore.Increment(1)
            })
            
            # 6. 一時ファイルを削除
            os.remove(temp_path)
            
            all_results.append({
                "filename": original_filename,
                "status": "success",
                "records_count": len(data_list) if isinstance(data_list, list) else 1
            })
            print(f"✅ Success: {original_filename}")
            
        except Exception as e:
            print(f"❌ Error processing {file.filename}: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            all_results.append({
                "filename": str(file.filename),
                "status": "error",
                "error": str(e)
            })
    
    print(f"\n=== Upload complete ===")
    success_count = len([r for r in all_results if r['status'] == 'success'])
    error_count = len([r for r in all_results if r['status'] == 'error'])
    print(f"Success: {success_count}")
    print(f"Errors: {error_count}")
    
    return {
        "results": all_results,
        "summary": {
            "total": len(files),
            "success": success_count,
            "errors": error_count
        }
    }

# ===== レコード管理エンドポイント =====

@app.put("/api/records/{record_id}")
async def update_record(record_id: str, data: dict, u_id: str = Depends(get_current_user)):
    """レコードの情報を更新（サブコレクション対応）"""
    try:
        print(f"=== Update request for record: {record_id} ===")
        print(f"Update data: {data}")
        
        # サブコレクションからレコードを取得
        doc_ref = db.collection(COL_USERS).document(u_id).collection("records").document(record_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="レコードが見つかりません")
        
        # 更新するフィールドを準備
        update_data = {}
        
        if "date" in data:
            update_data["date"] = data["date"]
        
        if "vendor_name" in data:
            update_data["vendor_name"] = data["vendor_name"]
        
        if "total_amount" in data:
            try:
                amount = int(str(data["total_amount"]).replace(",", "").replace("¥", "").strip())
                update_data["total_amount"] = amount
            except ValueError:
                raise HTTPException(status_code=400, detail="金額は数値で指定してください")
        
        if "category" in data:
            update_data["category"] = data["category"]
        
        # Firestoreを更新
        if update_data:
            doc_ref.update(update_data)
            print(f"✅ Updated record {record_id}: {update_data}")
        
        return {"message": "更新しました", "id": record_id, "updated_fields": update_data}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Update error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"更新に失敗しました: {str(e)}")

@app.delete("/delete/{record_id}")
async def delete_record(record_id: str, u_id: str = Depends(get_current_user)):
    """レコードを削除（サブコレクション対応）"""
    try:
        # サブコレクションからレコード取得
        doc_ref = db.collection(COL_USERS).document(u_id).collection("records").document(record_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="レコードが見つかりません")
        
        record_data = doc.to_dict()
        
        # GCSから画像ファイルを削除
        image_url = record_data.get("image_url", "")
        if image_url and BUCKET_NAME in image_url:
            blob_name = image_url.split(f"{BUCKET_NAME}/")[-1]
            bucket = storage_client.bucket(BUCKET_NAME)
            blob = bucket.blob(blob_name)
            
            if blob.exists():
                blob.delete()
        
        # PDF画像も削除
        if record_data.get("is_pdf") and record_data.get("pdf_images"):
            for pdf_img_url in record_data["pdf_images"]:
                if BUCKET_NAME in pdf_img_url:
                    blob_name = pdf_img_url.split(f"{BUCKET_NAME}/")[-1]
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        blob.delete()
        
        # Firestoreからドキュメントを削除
        doc_ref.delete()
        
        # 使用カウントを減らす
        db.collection(COL_USERS).document(u_id).update({
            "subscription.used": firestore.Increment(-1)
        })
        
        return {"message": "削除しました", "id": record_id}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除に失敗しました: {str(e)}")

@app.delete("/api/records/{record_id}")
async def delete_record_api(record_id: str, u_id: str = Depends(get_current_user)):
    """レコードを削除（API用、サブコレクション対応）"""
    return await delete_record(record_id, u_id)

@app.post("/api/records/bulk-delete")
async def bulk_delete_records(data: dict, u_id: str = Depends(get_current_user)):
    """複数レコードを一括削除（サブコレクション対応）"""
    record_ids = data.get("record_ids", [])
    
    if not record_ids:
        raise HTTPException(status_code=400, detail="削除するレコードが指定されていません")
    
    deleted_count = 0
    failed_count = 0
    
    for record_id in record_ids:
        try:
            # サブコレクションから削除
            doc_ref = db.collection(COL_USERS).document(u_id).collection("records").document(record_id)
            doc = doc_ref.get()
            
            if doc.exists:
                record_data = doc.to_dict()
                
                # GCSから画像削除
                image_url = record_data.get("image_url", "")
                if image_url and BUCKET_NAME in image_url:
                    blob_name = image_url.split(f"{BUCKET_NAME}/")[-1]
                    bucket = storage_client.bucket(BUCKET_NAME)
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        blob.delete()
                
                # PDF画像も削除
                if record_data.get("is_pdf") and record_data.get("pdf_images"):
                    for pdf_img_url in record_data["pdf_images"]:
                        if BUCKET_NAME in pdf_img_url:
                            blob_name = pdf_img_url.split(f"{BUCKET_NAME}/")[-1]
                            blob = bucket.blob(blob_name)
                            if blob.exists():
                                blob.delete()
                
                doc_ref.delete()
                deleted_count += 1
        except Exception as e:
            print(f"Error deleting {record_id}: {e}")
            failed_count += 1
    
    # 使用カウントを減らす
    if deleted_count > 0:
        db.collection(COL_USERS).document(u_id).update({
            "subscription.used": firestore.Increment(-deleted_count)
        })
    
    return {
        "message": f"{deleted_count}件のレコードを削除しました",
        "deleted": deleted_count,
        "failed": failed_count
    }

# ===== エクスポートエンドポイント =====

@app.get("/api/export/csv")
async def export_csv(token: Optional[str] = None, u_id: Optional[str] = Depends(get_current_user_optional)):
    """CSV出力（サブコレクション対応）"""
    import pandas as pd
    
    # トークンパラメータがある場合はそれを使用
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            u_id = payload.get("sub")
        except JWTError:
            raise HTTPException(status_code=401, detail="無効なトークンです")
    
    if not u_id:
        raise HTTPException(status_code=401, detail="認証が必要です")
    
    # サブコレクションからレコードを取得
    records_ref = db.collection(COL_USERS).document(u_id).collection("records").stream()
    records = [r.to_dict() for r in records_ref]
    
    if not records:
        raise HTTPException(status_code=404, detail="データがありません")
    
    df = pd.DataFrame(records)
    csv_path = f"{UPLOAD_DIR}/export_{u_id}_{int(time.time())}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    
    return FileResponse(csv_path, media_type="text/csv", filename=f"receipts_{u_id}.csv")

@app.get("/api/export/excel")
async def export_excel(token: Optional[str] = None, u_id: Optional[str] = Depends(get_current_user_optional)):
    """Excel出力（サブコレクション対応）"""
    import pandas as pd
    
    # トークンパラメータがある場合はそれを使用
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            u_id = payload.get("sub")
        except JWTError:
            raise HTTPException(status_code=401, detail="無効なトークンです")
    
    if not u_id:
        raise HTTPException(status_code=401, detail="認証が必要です")
    
    # サブコレクションからレコードを取得
    records_ref = db.collection(COL_USERS).document(u_id).collection("records").stream()
    records = [r.to_dict() for r in records_ref]
    
    if not records:
        raise HTTPException(status_code=404, detail="データがありません")
    
    df = pd.DataFrame(records)
    
    # 列名を日本語に変更
    column_mapping = {
        "date": "日付",
        "vendor_name": "店舗名",
        "total_amount": "金額",
        "category": "カテゴリ"
    }
    
    # 必要な列のみ抽出して並び替え
    available_cols = [col for col in ["date", "vendor_name", "total_amount", "category"] if col in df.columns]
    df_export = df[available_cols].copy()
    df_export.rename(columns=column_mapping, inplace=True)
    
    excel_path = f"{UPLOAD_DIR}/export_{u_id}_{int(time.time())}.xlsx"
    df_export.to_excel(excel_path, index=False, engine='openpyxl')
    
    return FileResponse(excel_path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"receipts_{u_id}.xlsx")

@app.get("/api/export/pdf")
async def export_pdf(token: Optional[str] = None, u_id: Optional[str] = Depends(get_current_user_optional)):
    """PDF出力（サブコレクション対応）"""
    from fpdf import FPDF
    
    # トークンパラメータがある場合はそれを使用
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            u_id = payload.get("sub")
        except JWTError:
            raise HTTPException(status_code=401, detail="無効なトークンです")
    
    if not u_id:
        raise HTTPException(status_code=401, detail="認証が必要です")
    
    # サブコレクションからレコードを取得
    records_ref = db.collection(COL_USERS).document(u_id).collection("records").stream()
    records = [r.to_dict() for r in records_ref]
    
    if not records:
        raise HTTPException(status_code=404, detail="データがありません")
    
    # 日付でソート
    records.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    pdf = FPDF()
    pdf.add_page()
    
    # 日本語フォントを追加
    if JAPANESE_FONT_PATH and os.path.exists(JAPANESE_FONT_PATH):
        pdf.add_font("NotoSansJP", "", JAPANESE_FONT_PATH, uni=True)
        pdf.set_font("NotoSansJP", size=10)
    else:
        pdf.set_font("Arial", size=10)
    
    # タイトル
    pdf.set_font_size(16)
    pdf.cell(0, 10, "領収書一覧", ln=True, align="C")
    pdf.ln(5)
    
    # ヘッダー
    pdf.set_font_size(10)
    pdf.set_fill_color(245, 245, 245)
    pdf.cell(30, 8, "日付", border=1, fill=True)
    pdf.cell(80, 8, "店舗名", border=1, fill=True)
    pdf.cell(40, 8, "金額", border=1, fill=True, align="R")
    pdf.cell(40, 8, "カテゴリ", border=1, fill=True)
    pdf.ln()
    
    # データ行
    for record in records:
        date = record.get("date", "")
        vendor = record.get("vendor_name", "")[:25]
        amount = f"¥{record.get('total_amount', 0):,}"
        category = record.get("category", "その他")
        
        # 交互に背景色を変更
        if records.index(record) % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False
        
        pdf.cell(30, 8, date, border=1, fill=fill)
        pdf.cell(80, 8, vendor, border=1, fill=fill)
        pdf.cell(40, 8, amount, border=1, fill=fill, align="R")
        pdf.cell(40, 8, category, border=1, fill=fill)
        pdf.ln()
    
    # 合計金額を計算
    total = sum([record.get("total_amount", 0) for record in records])
    pdf.ln(5)
    pdf.set_font_size(12)
    pdf.cell(110, 10, "合計金額:", align="R")
    pdf.set_font_size(14)
    pdf.cell(40, 10, f"¥{total:,}", align="R")
    
    pdf_path = f"{UPLOAD_DIR}/export_{u_id}_{int(time.time())}.pdf"
    pdf.output(pdf_path)
    
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"receipts_{u_id}.pdf")

# ===== 管理者専用エンドポイント =====

@app.get("/admin/users")
async def get_all_users(admin_id: str = Depends(require_admin)):
    """全ユーザーの一覧を取得（管理者のみ）"""
    users_ref = db.collection(COL_USERS).stream()
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

@app.post("/admin/users")
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
    existing = db.collection(COL_USERS).where("email", "==", email).limit(1).stream()
    existing_list = list(existing)
    print(f"Existing users: {len(existing_list)}")
    
    if existing_list:
        print("❌ Email already exists")
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています")
    
    # ユーザーID生成
    user_id = generate_user_id()
    print(f"Generated user ID: {user_id}")
    
    # プラン情報取得
    plan_info = PLANS.get(plan, PLANS["free"])
    
    # ユーザー作成
    try:
        db.collection(COL_USERS).document(user_id).set({
            "email": email,
            "password": pwd_context.hash(password),
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

@app.delete("/admin/users/{user_id}")
async def delete_user(user_id: str, admin_id: str = Depends(require_admin)):
    """ユーザーを削除（管理者のみ）"""
    if user_id == "admin":
        raise HTTPException(status_code=403, detail="管理者アカウントは削除できません")
    
    user_ref = db.collection(COL_USERS).document(user_id)
    if not user_ref.get().exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    
    # サブコレクションのレコードを全削除
    records_ref = user_ref.collection("records").stream()
    for record in records_ref:
        record.reference.delete()
    
    # ユーザードキュメントを削除
    user_ref.delete()
    
    return {"message": "ユーザーを削除しました"}

@app.put("/admin/users/{user_id}/subscription")
async def update_user_subscription(user_id: str, data: dict, admin_id: str = Depends(require_admin)):
    """ユーザーのプランを変更（管理者のみ）"""
    plan_id = data.get("plan")
    
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail="無効なプランです")
    
    plan = PLANS[plan_id]
    
    # サブスク情報を更新
    db.collection(COL_USERS).document(user_id).update({
        "subscription.plan": plan_id,
        "subscription.limit": plan["limit"],
        "subscription.status": "active"
    })
    
    return {"message": "プランを更新しました"}

# ===== LINE連携エンドポイント（トークン方式） =====

@app.get("/api/line-token")
async def generate_line_token(u_id: str = Depends(get_current_user)):
    """LINE連携用トークンを生成"""
    # 既存のトークンを削除（1ユーザー1トークン）
    old_tokens = db.collection(COL_LINE_TOKENS).where("user_id", "==", u_id).stream()
    for old_token in old_tokens:
        old_token.reference.delete()
    
    # 新しいトークンを生成
    token = generate_token(8)
    
    # Firestoreに保存
    db.collection(COL_LINE_TOKENS).document(token).set({
        "user_id": u_id,
        "created_at": firestore.SERVER_TIMESTAMP,
        "used": False,
        "expires_at": firestore.SERVER_TIMESTAMP  # 24時間後に期限切れにする場合は別途処理
    })
    
    return {"token": token, "message": "LINEでこのトークンを送信してください"}

@app.get("/api/line-status")
async def get_line_status(u_id: str = Depends(get_current_user)):
    """LINE連携ステータスを取得"""
    user_doc = db.collection(COL_USERS).document(u_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    
    user_data = user_doc.to_dict()
    line_user_id = user_data.get("line_user_id")
    
    return {
        "connected": line_user_id is not None,
        "line_user_id": line_user_id
    }

@app.post("/api/line-disconnect")
async def disconnect_line(u_id: str = Depends(get_current_user)):
    """LINE連携を解除"""
    db.collection(COL_USERS).document(u_id).update({
        "line_user_id": None
    })
    
    return {"message": "LINE連携を解除しました"}

# ===== LINE Webhook =====

@app.post("/webhook")
async def webhook(request: Request):
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
        token_doc = db.collection(COL_LINE_TOKENS).document(text).get()
        
        if token_doc.exists:
            token_data = token_doc.to_dict()
            
            if not token_data.get("used", False):
                user_id = token_data["user_id"]
                
                # ユーザーにline_user_idを紐付け
                db.collection(COL_USERS).document(user_id).update({
                    "line_user_id": line_user_id
                })
                
                # トークンを使用済みにする
                db.collection(COL_LINE_TOKENS).document(text).update({"used": True})
                
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
        temp_path = os.path.join(UPLOAD_DIR, f"line_{int(time.time())}.jpg")
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
            db.collection(COL_USERS).document(user_id).collection("records").document(doc_id).set(item)
        
        # 使用回数をインクリメント
        db.collection(COL_USERS).document(user_id).update({
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

# ===== Stripe連携エンドポイント（将来実装） =====

@app.post("/api/checkout")
async def create_checkout_session(data: dict, u_id: str = Depends(get_current_user)):
    """Stripe Checkoutセッションを作成（将来実装）"""
    if not STRIPE_ENABLED:
        raise HTTPException(status_code=501, detail="決済機能は準備中です。管理者にお問い合わせください。")
    
    # Stripe実装時のコードをここに追加
    pass

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Stripeからのwebhook（将来実装）"""
    if not STRIPE_ENABLED:
        return {"status": "disabled"}
    
    # Stripe webhook処理をここに追加
    pass