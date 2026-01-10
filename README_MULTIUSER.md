# マルチユーザー化実装ガイド

## 📋 概要

このガイドでは、SmartBuilder AIをシングルユーザーからマルチユーザー対応に移行する手順を説明します。

---

## 🎯 実装内容

### **追加される機能**
1. ✅ ユーザー登録機能
2. ✅ サブスク管理（プラン、使用上限）
3. ✅ サブコレクション対応（users/{user_id}/records）
4. ✅ 管理者ページ（ユーザー管理）
5. ✅ LINE連携（トークン方式）
6. ✅ 使用上限チェック
7. ✅ Stripe決済の拡張性（将来対応）

### **変更されるデータ構造**

**Before（シングルユーザー）:**
```
records/
  ├─ record_001 { owner: "admin", ... }
  ├─ record_002 { owner: "admin", ... }
  └─ record_003 { owner: "admin", ... }

users/
  └─ admin { password, plan, limit, used }
```

**After（マルチユーザー）:**
```
users/
  ├─ admin/
  │   ├─ email, password, role, subscription
  │   └─ records/ (サブコレクション)
  │       ├─ record_001 { date, vendor_name, ... }
  │       └─ record_002 { ... }
  │
  └─ user_abc123/
      ├─ email, password, role, subscription
      └─ records/ (サブコレクション)
          └─ record_003 { ... }
```

---

## 📝 実装手順

### **Step 1: ファイルのバックアップ**

```bash
# 現在のmain.pyをバックアップ
cp main.py main_backup.py

# 現在のindex.htmlをバックアップ
cp index.html index_backup.html
```

---

### **Step 2: 新しいmain.pyを配置**

```bash
# 新しいファイルを配置
cp main_multiuser_complete.py main.py
```

**確認事項:**
- ✅ BUCKET_NAME が正しく設定されているか
- ✅ 環境変数(.env)が設定されているか

---

### **Step 3: データ移行スクリプトの実行**

⚠️ **重要: 本番環境で実行する前に、必ずFirestoreのバックアップを取得してください**

```bash
# 移行スクリプトを実行
python migrate.py
```

**移行スクリプトの動作:**
1. 既存のadminユーザーを確認
2. recordsコレクションの全レコードを取得
3. users/admin/recordsサブコレクションに移行
4. adminのsubscription.usedを更新
5. 古いrecordsコレクションは保持（手動削除推奨）

**実行例:**
```
============================================================
データ移行スクリプト
============================================================

ステップ1: 管理者アカウントの確認...
✅ 管理者アカウント確認: admin@smartbuilder.ai

ステップ2: 既存レコードの取得...
📊 既存レコード数: 15件

============================================================
⚠️  重要: この操作は元に戻せません
============================================================

以下の操作を実行します:
  1. 15件のレコードを users/admin/records に移行
  2. adminユーザーのサブスク情報を更新
  3. 古いrecordsコレクションは保持（手動削除推奨）

続行しますか？ (yes/no): yes

ステップ4: レコードの移行開始...
✅ 移行完了: 1234567890 (1/15)
✅ 移行完了: 1234567891 (2/15)
...
```

---

### **Step 4: 動作確認**

1. **サーバーを起動:**
   ```bash
   uvicorn main:app --reload
   ```

2. **ブラウザでアクセス:**
   ```
   http://localhost:8000
   ```

3. **ログイン:**
   - メールアドレス: `admin@smartbuilder.ai`
   - パスワード: `password`

4. **確認項目:**
   - ✅ ログインできるか
   - ✅ 既存のレコードが表示されるか
   - ✅ 新規アップロードが動作するか
   - ✅ 編集・削除が動作するか
   - ✅ エクスポートが動作するか

---

### **Step 5: 古いデータの削除（任意）**

動作確認後、古いrecordsコレクションを削除:

1. Firebaseコンソールを開く
2. Firestore Database → records コレクション
3. 全ドキュメントを選択して削除

---

### **Step 6: フロントエンドの更新**

次のステップで、以下の機能を持つindex.htmlを提供します:
- ユーザー登録画面
- 管理者ページ
- LINE連携UI
- サブスク情報表示

---

## 🔐 初期アカウント情報

### **管理者アカウント**
- メールアドレス: `admin@smartbuilder.ai`
- パスワード: `password`
- 権限: 管理者（unlimited プラン）

⚠️ **セキュリティ:**
本番環境では必ずパスワードを変更してください

---

## 🎛️ プラン設定

### **利用可能なプラン**

| プラン | 月間上限 | 価格 | 備考 |
|--------|---------|------|------|
| Free | 10件 | ¥0 | 新規ユーザーのデフォルト |
| Premium | 100件 | ¥980 | 全機能利用可能 |
| Enterprise | 1000件 | ¥4,980 | API連携対応 |
| Unlimited | 無制限 | ¥0 | 管理者専用 |

プラン情報は `main.py` の `PLANS` 辞書で管理されています。

---

## 🔧 管理者機能

### **ユーザー管理**

管理者は以下の操作が可能:
- ユーザー一覧の表示
- 新規ユーザーの作成
- ユーザーの削除
- プランの変更

### **APIエンドポイント**

```python
# ユーザー一覧取得
GET /admin/users

# ユーザー作成
POST /admin/users
{
  "email": "user@example.com",
  "password": "password123",
  "plan": "free"
}

# ユーザー削除
DELETE /admin/users/{user_id}

# プラン変更
PUT /admin/users/{user_id}/subscription
{
  "plan": "premium"
}
```

---

## 📱 LINE連携（トークン方式）

### **連携フロー**

1. **ユーザーがWebアプリでトークンを生成:**
   ```
   GET /api/line-token
   → { "token": "AB12CD34" }
   ```

2. **ユーザーがLINEでトークンを送信:**
   ```
   ユーザー: AB12CD34
   Bot: ✅ LINE連携が完了しました！
   ```

3. **以降、画像を送信すると自動解析:**
   ```
   ユーザー: [領収書画像]
   Bot: ✅ 解析完了しました！
        📅 日付: 2025-01-09
        🏪 店舗: コンビニ
        💰 金額: ¥500
   ```

### **LINE連携の確認**

```python
# 連携ステータス確認
GET /api/line-status
→ {
    "connected": true,
    "line_user_id": "U1234567890abcdef"
  }

# 連携解除
POST /api/line-disconnect
```

---

## 🚀 Stripe決済（将来実装）

データ構造は既にStripe対応済みです。Stripe契約完了後:

1. `STRIPE_ENABLED = True` に変更
2. `STRIPE_SECRET_KEY` を設定
3. 各プランの `stripe_price_id` を設定
4. Checkoutエンドポイントを有効化

---

## 🐛 トラブルシューティング

### **問題: ログインできない**

**原因:** 古いトークンが残っている

**解決策:**
```javascript
// ブラウザのコンソールで実行
localStorage.clear()
location.reload()
```

---

### **問題: レコードが表示されない**

**原因:** データ移行が完了していない

**確認方法:**
1. Firebaseコンソールを開く
2. `users/admin/records` にデータがあるか確認

**解決策:**
```bash
# 移行スクリプトを再実行
python migrate.py
```

---

### **問題: アップロードが失敗する**

**原因:** 使用上限に達している

**確認方法:**
```python
# ユーザー情報を確認
GET /api/subscription
→ {
    "limit": 10,
    "used": 10,
    "remaining": 0
  }
```

**解決策:**
```python
# 管理者がプランを変更
PUT /admin/users/{user_id}/subscription
{
  "plan": "premium"
}
```

---

## 📊 次のステップ

1. ✅ バックエンドの実装（完了）
2. ⏳ フロントエンドの実装（次のステップ）
3. ⏳ テストとデバッグ
4. ⏳ 本番デプロイ

---

## 📞 サポート

問題が発生した場合:
1. エラーログを確認
2. Firebaseコンソールでデータ構造を確認
3. バックアップから復元

---

以上でバックエンドのマルチユーザー化は完了です！🎉