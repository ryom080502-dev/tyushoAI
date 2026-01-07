import os, time, json, shutil, io
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
import google.generativeai as genai
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext
import pandas as pd
from fpdf import FPDF

# --- LINE Bot 用のインポート ---
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

# --- LINE Bot 設定 ---
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()
UPLOAD_DIR, DB_FILE, USERS_FILE = "uploads", "records.json", "users.json"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# --- ユーティリティ ---
def load_json(path):
    if not os.path.exists(path): return [] if "records" in path else {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        return json.loads(content) if content else ([] if "records" in path else {})

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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

# --- 初期ユーザー作成 ---
def init_admin():
    users = load_json(USERS_FILE)
    if "admin" not in users:
        users["admin"] = {
            "password": pwd_context.hash("password"),
            "plan": "premium",
            "limit": 100,
            "used": 0
        }
        save_json(USERS_FILE, users)
init_admin()

# --- エンドポイント ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.post("/login")
async def login(data: dict):
    users = load_json(USERS_FILE)
    user_id = data.get("id") or data.get("username")
    password = data.get("password")
    
    if user_id in users and pwd_context.verify(password, users[user_id]["password"]):
        token = create_access_token(data={"sub": user_id})
        return {"token": token}
    raise HTTPException(status_code=401, detail="IDまたはパスワードが違います")

@app.get("/api/status")
async def get_status(user_id: str = Depends(get_current_user)):
    return {"records": load_json(DB_FILE), "users": load_json(USERS_FILE)}

# 解析用共通プロンプト
PROMPT = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON配列で返せ。
※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈すること。和暦は禁止。"""

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    genai_file = genai.upload_file(path=path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    # カウントアップ
    users = load_json(USERS_FILE)
    users[user_id]["used"] += 1
    save_json(USERS_FILE, users)
    
    data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    records = load_json(DB_FILE)
    for item in (data_list if isinstance(data_list, list) else [data_list]):
        item.update({"image_url": f"/uploads/{os.path.basename(path)}", "id": int(time.time()*1000)})
        records.append(item)
    save_json(DB_FILE, records)
    return {"data": data_list}

# --- LINE Webhook エンドポイント ---
@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # 画像の取得と保存
    message_content = line_bot_api.get_message_content(event.message.id)
    file_path = os.path.join(UPLOAD_DIR, f"{event.message.id}.jpg")
    with open(file_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)
    
    # Gemini 解析
    genai_file = genai.upload_file(path=file_path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    # 解析結果を保存 (LINE経由はデフォルトで admin ユーザーにカウント)
    user_id = "admin"
    users = load_json(USERS_FILE)
    users[user_id]["used"] += 1
    save_json(USERS_FILE, users)
    
    try:
        data_text = response.text.strip().replace('```json', '').replace('```', '')
        data_list = json.loads(data_text)
        records = load_json(DB_FILE)
        
        reply_txt = "【解析結果】\n"
        for item in (data_list if isinstance(data_list, list) else [data_list]):
            item.update({"image_url": f"/uploads/{os.path.basename(file_path)}", "id": int(time.time()*1000)})
            records.append(item)
            reply_txt += f"日付: {item.get('date')}\n店名: {item.get('vendor_name')}\n金額: ¥{item.get('total_amount'):,}\n"
        
        save_json(DB_FILE, records)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_txt))
        
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="解析に失敗しました。"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)