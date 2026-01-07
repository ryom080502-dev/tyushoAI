import os, time, json, shutil, io
from datetime import datetime, timedelta
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
from linebot.models import MessageEvent, ImageMessage, TextSendMessage

load_dotenv()

# --- 認証設定 ---
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-123")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

COL_RECORDS = "records"
COL_USERS = "users"

app = FastAPI()
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- ユーティリティ関数 ---

def upload_to_gcs(file_path, destination_blob_name):
    """ファイルをCloud Storageにアップロードし、公開URLを返す"""
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(file_path)
    # 権限設定でallUsersを閲覧者にしているので、以下のURLでアクセス可能になります
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{destination_blob_name}"

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

def init_admin():
    user_ref = db.collection(COL_USERS).document("admin")
    if not user_ref.get().exists:
        user_ref.set({
            "password": pwd_context.hash("password"),
            "plan": "premium",
            "limit": 100,
            "used": 0
        })
init_admin()

PROMPT = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON形式で返せ。
※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈。和暦禁止。"""

# --- エンドポイント ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.post("/login")
async def login(data: dict):
    u_id = data.get("id") or data.get("username")
    user_ref = db.collection(COL_USERS).document(u_id).get()
    if user_ref.exists:
        user_data = user_ref.to_dict()
        if pwd_context.verify(data.get("password"), user_data["password"]):
            return {"token": create_access_token(data={"sub": u_id})}
    raise HTTPException(status_code=401, detail="認証失敗")

@app.get("/api/status")
async def get_status(u_id: str = Depends(get_current_user)):
    recs_query = db.collection(COL_RECORDS).order_by("date", direction=firestore.Query.DESCENDING).stream()
    records = [doc.to_dict() for doc in recs_query]
    users_query = db.collection(COL_USERS).stream()
    users = {doc.id: doc.to_dict() for doc in users_query}
    return {"records": records, "users": users}

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...), u_id: str = Depends(get_current_user)):
    # 1. 一時保存
    temp_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(temp_path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    # 2. Cloud Storageへアップロード
    gcs_file_name = f"receipts/{int(time.time())}_{file.filename}"
    public_url = upload_to_gcs(temp_path, gcs_file_name)
    
    # 3. Gemini 解析
    genai_file = genai.upload_file(path=temp_path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    
    # 4. Firestore への保存 (公開URLを保存)
    for item in (data_list if isinstance(data_list, list) else [data_list]):
        doc_id = str(int(time.time()*1000))
        item.update({
            "image_url": public_url,
            "id": doc_id,
            "created_at": firestore.SERVER_TIMESTAMP,
            "owner": u_id
        })
        db.collection(COL_RECORDS).document(doc_id).set(item)
    
    db.collection(COL_USERS).document(u_id).update({"used": firestore.Increment(1)})
    
    # 5. 一時ファイルを削除してサーバーを綺麗に保つ
    os.remove(temp_path)
    return {"data": data_list}

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # 1. LINEから画像取得
    msg_content = line_bot_api.get_message_content(event.message.id)
    temp_fname = f"{event.message.id}.jpg"
    temp_path = os.path.join(UPLOAD_DIR, temp_fname)
    with open(temp_path, "wb") as f:
        for chunk in msg_content.iter_content(): f.write(chunk)
    
    # 2. Cloud Storageへアップロード
    gcs_file_name = f"line_uploads/{temp_fname}"
    public_url = upload_to_gcs(temp_path, gcs_file_name)
    
    # 3. Gemini 解析
    genai_file = genai.upload_file(path=temp_path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    try:
        data_text = response.text.strip().replace('```json', '').replace('```', '')
        data_list = json.loads(data_text)
        
        reply_txt = "【解析成功】\n"
        for item in (data_list if isinstance(data_list, list) else [data_list]):
            doc_id = str(int(time.time()*1000))
            item.update({
                "image_url": public_url,
                "id": doc_id,
                "created_at": firestore.SERVER_TIMESTAMP,
                "owner": "admin"
            })
            db.collection(COL_RECORDS).document(doc_id).set(item)
            reply_txt += f"📅 {item.get('date')}\n🏢 {item.get('vendor_name')}\n💰 ¥{item.get('total_amount'):,}\n"
        
        db.collection(COL_USERS).document("admin").update({"used": firestore.Increment(1)})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_txt))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="保存に失敗しました。"))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    # ahaha