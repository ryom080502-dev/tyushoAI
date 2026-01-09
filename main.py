import os, time, json, shutil, io
from datetime import datetime, timedelta
from typing import List
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
        # Google Fonts APIから最新のNoto Sans JPを取得
        url = "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf"
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            with open(font_path, "wb") as f:
                f.write(response.content)
            print("日本語フォントのダウンロードが完了しました")
            return font_path
        else:
            print(f"フォントダウンロード失敗: {response.status_code}")
            return None
    except Exception as e:
        print(f"フォントダウンロードエラー: {e}")
        return None

# 起動時にフォントをダウンロード
JAPANESE_FONT_PATH = download_japanese_font()

# --- PDF処理関数 ---
def convert_pdf_to_images(pdf_path):
    """PDFを画像に変換し、GCSにアップロード。画像URLのリストを返す"""
    if not PDF_SUPPORT:
        print("PDF画像化機能が無効です")
        return []
    
    try:
        # PDFを画像に変換（全ページ）
        images = convert_from_path(pdf_path, dpi=150)
        image_urls = []
        
        base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
        timestamp = int(time.time())
        
        for i, image in enumerate(images):
            # 一時的にJPEGとして保存
            temp_image_path = os.path.join(UPLOAD_DIR, f"{base_filename}_page{i+1}.jpg")
            image.save(temp_image_path, "JPEG", quality=85)
            
            # GCSにアップロード
            gcs_file_name = f"pdf_images/{timestamp}_{base_filename}_page{i+1}.jpg"
            public_url = upload_to_gcs(temp_image_path, gcs_file_name)
            image_urls.append(public_url)
            
            # 一時ファイル削除
            os.remove(temp_image_path)
        
        return image_urls
    except Exception as e:
        print(f"PDF画像化エラー: {e}")
        return []

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
async def upload_receipt(files: List[UploadFile] = File(...), u_id: str = Depends(get_current_user)):
    """複数ファイルのアップロードに対応（個別処理）"""
    print(f"=== Upload request received ===")
    print(f"User: {u_id}")
    print(f"Files count: {len(files) if files else 0}")
    
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="ファイルが選択されていません")
    
    all_results = []
    
    for idx, file in enumerate(files):
        print(f"\n--- Processing file {idx + 1}/{len(files)}: {file.filename} ---")
        try:
            # ★ 追加: ファイル名をサニタイズ（日本語・特殊文字対応）
            import unicodedata
            import re
            
            # 元のファイル名を保持
            original_filename = file.filename
            
            # 拡張子を取得
            file_ext = os.path.splitext(original_filename)[1]
            
            # 安全なファイル名を生成（タイムスタンプ + 拡張子）
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
            
            # 4. Gemini 解析（ファイルごとに個別処理）
            print("Starting Gemini analysis...")
            genai_file = genai.upload_file(path=temp_path)
            while genai_file.state.name == "PROCESSING": 
                time.sleep(1)
                genai_file = genai.get_file(genai_file.name)
            response = model.generate_content([genai_file, PROMPT])
            print(f"Gemini response received: {response.text[:100]}...")
            
            data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
            
            # 5. Firestore への保存
            print("Saving to Firestore...")
            for item in (data_list if isinstance(data_list, list) else [data_list]):
                doc_id = str(int(time.time()*1000))
                time.sleep(0.001)  # IDの重複を避けるため
                item.update({
                    "image_url": public_url,
                    "id": doc_id,
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "owner": u_id,
                    "is_pdf": is_pdf,
                    "pdf_images": pdf_image_urls if is_pdf else [],
                    "original_filename": original_filename  # 元のファイル名を保存
                })
                db.collection(COL_RECORDS).document(doc_id).set(item)
            
            db.collection(COL_USERS).document(u_id).update({"used": firestore.Increment(1)})
            
            # 6. 一時ファイルを削除
            os.remove(temp_path)
            
            all_results.append({
                "filename": original_filename,
                "status": "success",
                "data": data_list if isinstance(data_list, (list, dict)) else str(data_list)
            })
            print(f"✅ Success: {original_filename}")
            
        except Exception as e:
            print(f"❌ Error processing {file.filename}: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()  # 詳細なスタックトレースを出力
            all_results.append({
                "filename": file.filename,
                "status": "error",
                "error": f"{type(e).__name__}: {str(e)}"
            })
    
    print(f"\n=== Upload complete ===")
    print(f"Success: {len([r for r in all_results if r['status'] == 'success'])}")
    print(f"Errors: {len([r for r in all_results if r['status'] == 'error'])}")
    
    # ★ 追加: レスポンスをJSON安全な形式に変換
    safe_results = []
    for result in all_results:
        safe_result = {
            "filename": result["filename"],
            "status": result["status"]
        }
        if result["status"] == "success":
            # dataをJSON安全な形式に変換
            safe_result["data"] = result.get("data", [])
        else:
            safe_result["error"] = result.get("error", "Unknown error")
        safe_results.append(safe_result)
    
    return {"results": safe_results}

@app.delete("/delete/{record_id}")
async def delete_record(record_id: str, u_id: str = Depends(get_current_user)):
    """レコードを削除（Firestore + GCS）"""
    try:
        # 1. Firestoreからレコード取得
        doc_ref = db.collection(COL_RECORDS).document(record_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="レコードが見つかりません")
        
        record_data = doc.to_dict()
        
        # 2. 権限チェック（adminまたは所有者のみ削除可能）
        if u_id != "admin" and record_data.get("owner") != u_id:
            raise HTTPException(status_code=403, detail="削除権限がありません")
        
        # 3. GCSから画像ファイルを削除
        image_url = record_data.get("image_url", "")
        if image_url and BUCKET_NAME in image_url:
            # URLからファイル名を抽出: https://storage.googleapis.com/BUCKET_NAME/path/to/file.jpg
            blob_name = image_url.split(f"{BUCKET_NAME}/")[-1]
            bucket = storage_client.bucket(BUCKET_NAME)
            blob = bucket.blob(blob_name)
            
            # ファイルが存在する場合のみ削除
            if blob.exists():
                blob.delete()
        
        # 4. Firestoreからドキュメントを削除
        doc_ref.delete()
        
        # 5. ユーザーの使用カウントを減らす
        db.collection(COL_USERS).document(u_id).update({"used": firestore.Increment(-1)})
        
        return {"message": "削除しました", "id": record_id}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除に失敗しました: {str(e)}")

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return "OK"

@app.delete("/api/records/{record_id}")
async def delete_record(record_id: str, u_id: str = Depends(get_current_user)):
    """レコードを削除（Firestore + GCS）"""
    try:
        # 1. Firestoreからレコード取得
        doc_ref = db.collection(COL_RECORDS).document(record_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="レコードが見つかりません")
        
        record_data = doc.to_dict()
        
        # 2. GCSから画像削除
        image_url = record_data.get("image_url", "")
        if image_url:
            # URLからファイルパスを抽出（例: https://storage.googleapis.com/bucket/path/file.jpg → path/file.jpg）
            try:
                blob_name = image_url.split(f"{BUCKET_NAME}/")[-1]
                bucket = storage_client.bucket(BUCKET_NAME)
                blob = bucket.blob(blob_name)
                if blob.exists():
                    blob.delete()
            except Exception as e:
                print(f"GCS削除エラー: {e}")
                # GCS削除に失敗してもFirestoreは削除する
        
        # 3. Firestoreからレコード削除
        doc_ref.delete()
        
        # 4. ユーザーの使用回数を減らす
        db.collection(COL_USERS).document(u_id).update({"used": firestore.Increment(-1)})
        
        return {"message": "削除しました", "id": record_id}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除に失敗しました: {str(e)}")

@app.post("/api/records/bulk-delete")
async def bulk_delete_records(data: dict, u_id: str = Depends(get_current_user)):
    """複数レコードを一括削除（Firestore + GCS）"""
    record_ids = data.get("record_ids", [])
    
    if not record_ids:
        raise HTTPException(status_code=400, detail="削除対象が選択されていません")
    
    deleted_count = 0
    failed_ids = []
    
    for record_id in record_ids:
        try:
            # 1. Firestoreからレコード取得
            doc_ref = db.collection(COL_RECORDS).document(record_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                failed_ids.append(record_id)
                continue
            
            record_data = doc.to_dict()
            
            # 2. GCSから画像削除
            image_url = record_data.get("image_url", "")
            if image_url:
                try:
                    blob_name = image_url.split(f"{BUCKET_NAME}/")[-1]
                    bucket = storage_client.bucket(BUCKET_NAME)
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        blob.delete()
                except Exception as e:
                    print(f"GCS削除エラー (ID: {record_id}): {e}")
            
            # 3. Firestoreからレコード削除
            doc_ref.delete()
            deleted_count += 1
            
        except Exception as e:
            print(f"削除エラー (ID: {record_id}): {e}")
            failed_ids.append(record_id)
    
    # 4. ユーザーの使用回数を減らす
    if deleted_count > 0:
        db.collection(COL_USERS).document(u_id).update({"used": firestore.Increment(-deleted_count)})
    
    result = {
        "message": f"{deleted_count}件のレコードを削除しました",
        "deleted_count": deleted_count,
        "failed_count": len(failed_ids)
    }
    
    if failed_ids:
        result["failed_ids"] = failed_ids
    
    return result

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

# --- 出力機能エンドポイント ---

@app.get("/api/export/csv")
async def export_csv(u_id: str = Depends(get_current_user)):
    """CSVファイルとしてエクスポート"""
    import pandas as pd
    from fastapi.responses import StreamingResponse
    
    # Firestoreからデータ取得
    recs_query = db.collection(COL_RECORDS).order_by("date", direction=firestore.Query.DESCENDING).stream()
    records = [doc.to_dict() for doc in recs_query]
    
    if not records:
        raise HTTPException(status_code=404, detail="エクスポートするデータがありません")
    
    # DataFrameに変換
    df = pd.DataFrame(records)
    # 必要な列のみ抽出
    columns = ['date', 'vendor_name', 'total_amount', 'owner']
    df = df[[col for col in columns if col in df.columns]]
    
    # CSVに変換
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')  # Excel用にBOM付き
    csv_buffer.seek(0)
    
    return StreamingResponse(
        io.BytesIO(csv_buffer.getvalue().encode('utf-8-sig')),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename=receipts_{datetime.now().strftime("%Y%m%d")}.csv'}
    )

@app.get("/api/export/excel")
async def export_excel(u_id: str = Depends(get_current_user)):
    """Excelファイルとしてエクスポート"""
    import pandas as pd
    from fastapi.responses import StreamingResponse
    
    # Firestoreからデータ取得
    recs_query = db.collection(COL_RECORDS).order_by("date", direction=firestore.Query.DESCENDING).stream()
    records = [doc.to_dict() for doc in recs_query]
    
    if not records:
        raise HTTPException(status_code=404, detail="エクスポートするデータがありません")
    
    # DataFrameに変換
    df = pd.DataFrame(records)
    # 必要な列のみ抽出・並び替え
    columns = ['date', 'vendor_name', 'total_amount', 'owner']
    df = df[[col for col in columns if col in df.columns]]
    
    # 列名を日本語に変更
    df.columns = ['日付', '店舗名', '合計金額', '所有者']
    
    # Excelに変換
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='領収書データ')
        
        # 列幅を自動調整
        worksheet = writer.sheets['領収書データ']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    excel_buffer.seek(0)
    
    return StreamingResponse(
        excel_buffer,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename=receipts_{datetime.now().strftime("%Y%m%d")}.xlsx'}
    )

@app.get("/api/export/pdf")
async def export_pdf(u_id: str = Depends(get_current_user)):
    """PDFファイルとしてエクスポート（日本語対応）"""
    from fpdf import FPDF
    from fastapi.responses import StreamingResponse
    
    # Firestoreからデータ取得
    recs_query = db.collection(COL_RECORDS).order_by("date", direction=firestore.Query.DESCENDING).stream()
    records = [doc.to_dict() for doc in recs_query]
    
    if not records:
        raise HTTPException(status_code=404, detail="エクスポートするデータがありません")
    
    # PDF作成
    pdf = FPDF()
    pdf.add_page()
    
    # 日本語フォント設定
    if JAPANESE_FONT_PATH and os.path.exists(JAPANESE_FONT_PATH):
        pdf.add_font("NotoSansJP", "", JAPANESE_FONT_PATH, uni=True)
        pdf.set_font("NotoSansJP", size=12)
        font_name = "NotoSansJP"
    else:
        # フォールバック（日本語が文字化けする可能性あり）
        pdf.set_font("Helvetica", size=12)
        font_name = "Helvetica"
    
    # タイトル
    pdf.set_font(font_name, size=16)
    pdf.cell(0, 10, '領収書データ一覧', ln=True, align='C')
    pdf.ln(5)
    
    # ヘッダー
    pdf.set_font(font_name, size=10)
    pdf.set_fill_color(220, 220, 220)  # 背景色（グレー）
    pdf.cell(30, 10, '日付', border=1, fill=True)
    pdf.cell(80, 10, '店舗名', border=1, fill=True)
    pdf.cell(40, 10, '金額', border=1, fill=True)
    pdf.cell(40, 10, '所有者', border=1, fill=True)
    pdf.ln()
    
    # データ行
    pdf.set_font(font_name, size=9)
    for i, record in enumerate(records):
        date = record.get('date', '')
        vendor = record.get('vendor_name', '')
        # 長すぎる場合は切り詰め
        if len(vendor) > 25:
            vendor = vendor[:25] + '...'
        amount = f"¥{record.get('total_amount', 0):,}"
        owner = record.get('owner', '')
        
        # 交互に背景色を変更（見やすくするため）
        if i % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False
        
        pdf.cell(30, 8, date, border=1, fill=fill)
        pdf.cell(80, 8, vendor, border=1, fill=fill)
        pdf.cell(40, 8, amount, border=1, fill=fill)
        pdf.cell(40, 8, owner, border=1, fill=fill)
        pdf.ln()
    
    # 合計金額を計算して追加
    total = sum(record.get('total_amount', 0) for record in records)
    pdf.ln(5)
    pdf.set_font(font_name, size=10)
    pdf.cell(110, 10, '合計金額:', align='R')
    pdf.set_font(font_name, size=12)
    pdf.cell(40, 10, f"¥{total:,}", align='R')
    
    # PDFをバイナリとして出力
    pdf_buffer = io.BytesIO(pdf.output())
    pdf_buffer.seek(0)
    
    return StreamingResponse(
        pdf_buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=receipts_{datetime.now().strftime("%Y%m%d")}.pdf'}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))