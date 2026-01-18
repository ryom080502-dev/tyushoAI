"""
Cloud Storage サービス
ファイルのアップロード・削除を管理
"""
import os
from database import storage_client
import config

def upload_to_gcs(file_path: str, destination_blob_name: str) -> str:
    """ファイルをCloud Storageにアップロードし、公開URLを返す"""
    bucket = storage_client.bucket(config.BUCKET_NAME)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(file_path)
    return f"https://storage.googleapis.com/{config.BUCKET_NAME}/{destination_blob_name}"

def delete_from_gcs(image_url: str) -> bool:
    """Cloud Storageからファイルを削除"""
    try:
        if config.BUCKET_NAME in image_url:
            blob_name = image_url.split(f"{config.BUCKET_NAME}/")[-1]
            bucket = storage_client.bucket(config.BUCKET_NAME)
            blob = bucket.blob(blob_name)

            if blob.exists():
                blob.delete()
                return True
        return False
    except Exception as e:
        print(f"GCS削除エラー: {e}")
        return False
