"""
画像処理サービス
画像の圧縮・PDF変換を管理
"""
import os
import time
from PIL import Image, ImageOps
from services.storage_service import upload_to_gcs
import config

# PDF処理用インポート
try:
    from pdf2image import convert_from_path
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("警告: pdf2imageがインストールされていません。PDF画像化機能は無効です。")

def compress_image(input_path: str, output_path: str = None, max_size: tuple = (1920, 1080), quality: int = 85) -> str:
    """画像を圧縮してファイルサイズを削減"""
    if output_path is None:
        output_path = input_path

    try:
        with Image.open(input_path) as img:
            # EXIF情報に基づいて画像を回転
            try:
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
            temp_image_path = os.path.join(config.UPLOAD_DIR, f"{base_filename}_page{i+1}.jpg")
            image.save(temp_image_path, "JPEG", quality=85)

            gcs_file_name = f"pdf_images/{timestamp}_{base_filename}_page{i+1}.jpg"
            public_url = upload_to_gcs(temp_image_path, gcs_file_name)
            image_urls.append(public_url)

            os.remove(temp_image_path)

        return image_urls
    except Exception as e:
        print(f"PDF画像化エラー: {e}")
        return []
