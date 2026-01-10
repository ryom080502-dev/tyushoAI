#!/usr/bin/env python3
"""
データ移行スクリプト
既存のシングルユーザー構造からマルチユーザー構造への移行

実行方法:
    python migrate.py

注意:
    - 本番環境で実行する前に、必ずバックアップを取得してください
    - 実行後は元に戻せません
"""

import os
from google.cloud import firestore
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

# ===== 認証情報を設定 =====
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "gen-lang-client-0553940805-e017df0cff23.json"

db = firestore.Client()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

COL_USERS = "users"
COL_RECORDS = "records"

def migrate_to_multiuser():
    """既存データを新しいマルチユーザー構造に移行"""
    
    print("=" * 60)
    print("データ移行スクリプト")
    print("=" * 60)
    print()
    
    # ステップ1: 既存のadminユーザーをチェック
    print("ステップ1: 管理者アカウントの確認...")
    admin_ref = db.collection(COL_USERS).document("admin")
    admin_doc = admin_ref.get()
    
    if not admin_doc.exists:
        print("❌ エラー: adminユーザーが見つかりません")
        print("先に新しいmain.pyを起動して、adminアカウントを初期化してください")
        return
    
    admin_data = admin_doc.to_dict()
    print(f"✅ 管理者アカウント確認: {admin_data.get('email', 'admin')}")
    
    # ステップ2: 既存のrecordsコレクションを取得
    print("\nステップ2: 既存レコードの取得...")
    old_records_ref = db.collection(COL_RECORDS).stream()
    old_records = list(old_records_ref)
    
    print(f"📊 既存レコード数: {len(old_records)}件")
    
    if len(old_records) == 0:
        print("⚠️  移行するレコードがありません")
        return
    
    # ステップ3: 確認プロンプト
    print("\n" + "=" * 60)
    print("⚠️  重要: この操作は元に戻せません")
    print("=" * 60)
    print(f"\n以下の操作を実行します:")
    print(f"  1. {len(old_records)}件のレコードを users/admin/records に移行")
    print(f"  2. adminユーザーのサブスク情報を更新")
    print(f"  3. 古いrecordsコレクションは保持（手動削除推奨）")
    print()
    
    confirm = input("続行しますか？ (yes/no): ")
    
    if confirm.lower() != "yes":
        print("\n❌ 移行をキャンセルしました")
        return
    
    # ステップ4: レコードを移行
    print("\nステップ4: レコードの移行開始...")
    migrated_count = 0
    failed_count = 0
    
    for record in old_records:
        try:
            record_id = record.id
            record_data = record.to_dict()
            
            # adminユーザーのサブコレクションに保存
            new_ref = db.collection(COL_USERS).document("admin").collection("records").document(record_id)
            new_ref.set(record_data)
            
            migrated_count += 1
            print(f"✅ 移行完了: {record_id} ({migrated_count}/{len(old_records)})")
            
        except Exception as e:
            failed_count += 1
            print(f"❌ 移行エラー: {record_id} - {str(e)}")
    
    # ステップ5: adminユーザーのサブスク情報を更新
    print("\nステップ5: 管理者サブスク情報の更新...")
    
    try:
        admin_ref.update({
            "subscription.used": migrated_count
        })
        print(f"✅ 使用回数を更新: {migrated_count}件")
    except Exception as e:
        print(f"❌ サブスク更新エラー: {str(e)}")
    
    # ステップ6: 結果サマリー
    print("\n" + "=" * 60)
    print("移行完了")
    print("=" * 60)
    print(f"\n📊 結果:")
    print(f"  - 成功: {migrated_count}件")
    print(f"  - 失敗: {failed_count}件")
    print(f"  - 合計: {len(old_records)}件")
    
    # ステップ7: 次のアクション
    print("\n📝 次のステップ:")
    print("  1. ブラウザでアプリにアクセスして動作確認")
    print("  2. 正常に動作することを確認したら、古いrecordsコレクションを手動削除")
    print("     (Firebaseコンソール → Firestore → recordsコレクション → 削除)")
    print("  3. main.pyをデプロイ")
    print()
    
    # 古いコレクション削除の警告
    print("⚠️  重要: 古いrecordsコレクションはまだ残っています")
    print("    動作確認後、手動で削除してください")
    print()

def verify_migration():
    """移行結果を確認"""
    print("\n" + "=" * 60)
    print("移行結果の確認")
    print("=" * 60)
    
    # adminのサブコレクションを確認
    print("\nadminユーザーのレコード:")
    admin_records = db.collection(COL_USERS).document("admin").collection("records").stream()
    admin_count = len(list(admin_records))
    print(f"  - レコード数: {admin_count}件")
    
    # 古いコレクションを確認
    print("\n古いrecordsコレクション:")
    old_records = db.collection(COL_RECORDS).stream()
    old_count = len(list(old_records))
    print(f"  - レコード数: {old_count}件")
    
    if old_count > 0:
        print("  ⚠️  古いコレクションがまだ存在します")
        print("     動作確認後、手動で削除してください")
    
    print()

if __name__ == "__main__":
    try:
        migrate_to_multiuser()
        verify_migration()
    except KeyboardInterrupt:
        print("\n\n❌ 移行が中断されました")
    except Exception as e:
        print(f"\n\n❌ 予期しないエラーが発生しました: {str(e)}")
        import traceback
        traceback.print_exc()