"""
Gemini AI サービス
画像解析機能を提供
"""
import time
import json
import google.generativeai as genai
import config

# Gemini 設定
genai.configure(api_key=config.GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-pro')

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
            response = model.generate_content([genai_file, config.GEMINI_PROMPT])

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
