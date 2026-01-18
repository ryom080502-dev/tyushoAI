"""
レコード管理ルーター
アップロード・編集・削除機能
"""
import os
import time
import shutil
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from google.cloud import firestore
from database import db
from services.auth_service import get_current_user
from services.gemini_service import analyze_with_gemini_retry
from services.image_service import compress_image, convert_pdf_to_images
from services.storage_service import upload_to_gcs, delete_from_gcs
from utils.helpers import check_usage_limit
import config

router = APIRouter()

@router.post("/upload")
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
            temp_path = os.path.join(config.UPLOAD_DIR, safe_filename)
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
            if is_pdf:
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
                    "category": "その他",
                    "source": "web"
                })
                # サブコレクションに保存
                db.collection(config.COL_USERS).document(u_id).collection("records").document(doc_id).set(item)

            # 使用回数をインクリメント
            db.collection(config.COL_USERS).document(u_id).update({
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

@router.put("/api/records/{record_id}")
async def update_record(record_id: str, data: dict, u_id: str = Depends(get_current_user)):
    """レコードの情報を更新（サブコレクション対応）"""
    try:
        print(f"=== Update request for record: {record_id} ===")
        print(f"Update data: {data}")

        # サブコレクションからレコードを取得
        doc_ref = db.collection(config.COL_USERS).document(u_id).collection("records").document(record_id)
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

@router.delete("/delete/{record_id}")
@router.delete("/api/records/{record_id}")
async def delete_record(record_id: str, u_id: str = Depends(get_current_user)):
    """レコードを削除（サブコレクション対応）"""
    try:
        # サブコレクションからレコード取得
        doc_ref = db.collection(config.COL_USERS).document(u_id).collection("records").document(record_id)
        doc = doc_ref.get()

        if not doc.exists:
            raise HTTPException(status_code=404, detail="レコードが見つかりません")

        record_data = doc.to_dict()

        # GCSから画像ファイルを削除
        image_url = record_data.get("image_url", "")
        if image_url:
            delete_from_gcs(image_url)

        # PDF画像も削除
        if record_data.get("is_pdf") and record_data.get("pdf_images"):
            for pdf_img_url in record_data["pdf_images"]:
                delete_from_gcs(pdf_img_url)

        # Firestoreからドキュメントを削除
        doc_ref.delete()

        # 使用カウントを減らす
        db.collection(config.COL_USERS).document(u_id).update({
            "subscription.used": firestore.Increment(-1)
        })

        return {"message": "削除しました", "id": record_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除に失敗しました: {str(e)}")

@router.post("/api/records/bulk-delete")
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
            doc_ref = db.collection(config.COL_USERS).document(u_id).collection("records").document(record_id)
            doc = doc_ref.get()

            if doc.exists:
                record_data = doc.to_dict()

                # GCSから画像削除
                image_url = record_data.get("image_url", "")
                if image_url:
                    delete_from_gcs(image_url)

                # PDF画像も削除
                if record_data.get("is_pdf") and record_data.get("pdf_images"):
                    for pdf_img_url in record_data["pdf_images"]:
                        delete_from_gcs(pdf_img_url)

                doc_ref.delete()
                deleted_count += 1
        except Exception as e:
            print(f"Error deleting {record_id}: {e}")
            failed_count += 1

    # 使用カウントを減らす
    if deleted_count > 0:
        db.collection(config.COL_USERS).document(u_id).update({
            "subscription.used": firestore.Increment(-deleted_count)
        })

    return {
        "message": f"{deleted_count}件のレコードを削除しました",
        "deleted": deleted_count,
        "failed": failed_count
    }

@router.post("/api/records/bulk-update")
async def bulk_update_records(data: dict, u_id: str = Depends(get_current_user)):
    """複数レコードを一括更新（カテゴリ・日付の変更）"""
    record_ids = data.get("record_ids", [])
    update_fields = data.get("update_fields", {})

    if not record_ids:
        raise HTTPException(status_code=400, detail="更新するレコードが指定されていません")

    if not update_fields:
        raise HTTPException(status_code=400, detail="更新するフィールドが指定されていません")

    print(f"=== Bulk update request ===")
    print(f"User: {u_id}")
    print(f"Record IDs: {record_ids}")
    print(f"Update fields: {update_fields}")

    updated_count = 0
    failed_count = 0

    # 更新データを準備
    update_data = {}

    if "category" in update_fields and update_fields["category"]:
        update_data["category"] = update_fields["category"]

    if "date" in update_fields and update_fields["date"]:
        update_data["date"] = update_fields["date"]

    if not update_data:
        raise HTTPException(status_code=400, detail="有効な更新フィールドがありません")

    for record_id in record_ids:
        try:
            doc_ref = db.collection(config.COL_USERS).document(u_id).collection("records").document(record_id)
            doc = doc_ref.get()

            if doc.exists:
                doc_ref.update(update_data)
                updated_count += 1
                print(f"[OK] Updated record {record_id}")
            else:
                print(f"[WARNING] Record {record_id} not found")
                failed_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to update {record_id}: {e}")
            failed_count += 1

    print(f"=== Bulk update complete ===")
    print(f"Updated: {updated_count}, Failed: {failed_count}")

    return {
        "message": f"{updated_count}件のレコードを更新しました",
        "updated": updated_count,
        "failed": failed_count,
        "update_fields": update_data
    }
