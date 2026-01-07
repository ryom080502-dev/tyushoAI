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

# --- LINE Bot 用 ---
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

# --- 認証 ---
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

# --- 初期設定 ---
def init_admin():
    users = load_json(USERS_FILE)
    if "admin" not in users:
        users["admin"] = {"password": pwd_context.hash("password"), "plan": "premium", "limit": 100, "used": 0}
        save_json(USERS_FILE, users)
init_admin()

# 解析プロンプト
PROMPT = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON形式で返せ。
※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈。和暦禁止。"""

# --- エンドポイント ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.post("/login")
async def login(data: dict):
    users = load_json(USERS_FILE)
    u_id = data.get("id") or data.get("username")
    if u_id in users and pwd_context.verify(data.get("password"), users[u_id]["password"]):
        return {"token": create_access_token(data={"sub": u_id})}
    raise HTTPException(status_code=401, detail="認証失敗")

@app.get("/api/status")
async def get_status(u_id: str = Depends(get_current_user)):
    # 常に最新の records.json を返す
    return {"records": load_json(DB_FILE), "users": load_json(USERS_FILE)}

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...), u_id: str = Depends(get_current_user)):
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    genai_file = genai.upload_file(path=path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    records = load_json(DB_FILE)
    for item in (data_list if isinstance(data_list, list) else [data_list]):
        item.update({"image_url": f"/uploads/{file.filename}", "id": int(time.time()*1000)})
        records.append(item)
    save_json(DB_FILE, records)
    return {"data": data_list}

# --- LINE Webhook ---
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
    msg_content = line_bot_api.get_message_content(event.message.id)
    fname = f"{event.message.id}.jpg"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        for chunk in msg_content.iter_content(): f.write(chunk)
    
    genai_file = genai.upload_file(path=path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, PROMPT])
    
    try:
        data_text = response.text.strip().replace('```json', '').replace('```', '')
        data_list = json.loads(data_text)
        records = load_json(DB_FILE)
        
        reply_txt = "【解析成功】\n"
        for item in (data_list if isinstance(data_list, list) else [data_list]):
            # Web版と同じ形式でデータを補完して records.json に追加
            item.update({"image_url": f"/uploads/{fname}", "id": int(time.time()*1000)})
            records.append(item)
            reply_txt += f"📅 {item.get('date')}\n🏢 {item.get('vendor_name')}\n💰 ¥{item.get('total_amount'):,}\n"
        
        save_json(DB_FILE, records)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_txt))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="解析データの保存に失敗しました。"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))