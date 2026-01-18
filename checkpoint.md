# SmartBuilder AI - プロジェクトチェックポイント

**作成日時**: 2026-01-18
**ブランチ**: main
**最新コミット**: 2c8ecdb

---

## 1. プロジェクトの概要と目的

### **プロジェクト名**: SmartBuilder AI
### **目的**:
- 経費管理・レシート管理アプリケーション
- AIによる自動レシート解析（Gemini API使用）
- マルチユーザー対応
- LINE連携機能
- サブスクリプション管理

### **技術スタック**:
- **バックエンド**: FastAPI (Python)
- **フロントエンド**: Vanilla JavaScript + Tailwind CSS
- **データベース**: Google Cloud Firestore
- **ストレージ**: Google Cloud Storage
- **AI**: Google Gemini API
- **認証**: JWT (jose)
- **パスワードハッシュ**: pbkdf2_sha256 (bcrypt削除済み - Windows互換性問題のため)

---

## 2. 現在の進捗状況

### **完了した作業**

#### **Phase 0: リファクタリング（完了✅）**
- バックエンドの完全モジュール化
  - main.py: 1,343行 → 65行 (95%削減)
  - 14ファイルに分割
  - ファイル構成:
    ```
    main.py (65行)
    config.py (98行)
    database.py (40行)
    routers/
      ├── auth.py (159行)
      ├── records.py (250行)
      ├── line.py (200行)
      ├── export.py (170行)
      └── admin.py (130行)
    services/
      ├── gemini_service.py (50行)
      ├── storage_service.py (30行)
      ├── image_service.py (90行)
      └── auth_service.py (47行)
    utils/
      └── helpers.py (50行)
    models/ (プレースホルダー)
    ```

- フロントエンドモジュールの準備
  - 7ファイル作成（695行）
  - 段階的移行のための基盤
  - 現在のindex.htmlは安定性のため保持

#### **Phase 1: 管理者機能実装（完了✅）**
- **ブランチ**: feature/admin-user-management → main にマージ済み
- **実装内容**:
  1. ✅ プラン変更モーダルUI追加
  2. ✅ プラン変更機能（prompt → 洗練されたモーダル）
  3. ✅ ユーザー一覧表示の改善
  4. ✅ ロール表示（管理者/ユーザー）
  5. ✅ 使用状況の詳細表示
  6. ✅ ユーザー削除機能

- **バグ修正**:
  1. ✅ bcryptエラー修正（pbkdf2_sha256のみ使用）
  2. ✅ Windowsエンコーディングエラー修正（emoji → ASCII）
  3. ✅ 管理者アカウント再作成

- **コミット履歴**:
  - f3159e3: feat: 管理者機能の完全実装
  - 2c8ecdb: fix: bcryptエラーとWindowsエンコーディング問題を修正

---

## 3. 決定事項・重要ルール

### **技術選定の決定事項**

#### **パスワードハッシュ**
- **決定**: `pbkdf2_sha256`のみ使用
- **理由**: Windows環境でbcryptのビルド問題が発生
- **実装場所**:
  - `database.py`: `pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")`
  - `services/auth_service.py`: 72バイト制限対応のエラーハンドリング追加

#### **Windowsエンコーディング対応**
- **決定**: print文でemojiを使用しない
- **理由**: Windowsコンソール(cp932)でUnicodeEncodeError発生
- **対応**:
  - ✅ → [OK]
  - ❌ → [ERROR]
  - ⚠️ → [WARNING]

#### **フロントエンドのアプローチ**
- **決定**: ハイブリッドアプローチ採用
- **理由**: 安定性を維持しながら段階的にモジュール化
- **現状**:
  - index.html: 既存のまま保持（1,000+行）
  - static/js/modules/: 将来の移行用モジュール準備完了
  - 段階的移行が可能な状態

### **ブランチ戦略**
- **mainブランチ**: 安定版、本番デプロイ用
- **feature/***ブランチ: 機能追加用
- **各機能ごとに別ブランチを作成** → 完了後mainにマージ

### **管理者アカウント情報**
```
メールアドレス: admin@smartbuilder.ai
パスワード: password
ロール: admin
プラン: unlimited (99999件/月)
```

### **APIエンドポイント（管理者機能）**
```
GET  /admin/users              - ユーザー一覧取得
PUT  /admin/users/{id}/plan    - プラン変更
DELETE /admin/users/{id}       - ユーザー削除
POST /admin/users              - 新規ユーザー作成（未実装）
```

---

## 4. 最新のコード/成果物

### **主要ファイルの構成**

#### **main.py** (最新版)
```python
"""
SmartBuilder AI - メインアプリケーション
シンプル化されたエントリーポイント
"""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 設定とデータベース初期化
import config
from database import init_admin

# ルーター
from routers import auth, records, line, export, admin

# ディレクトリ作成
os.makedirs(config.UPLOAD_DIR, exist_ok=True)
os.makedirs(config.FONT_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)

# FastAPI アプリケーション初期化
app = FastAPI(title="SmartBuilder AI", version="2.0.0")

# 静的ファイルの配信
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(auth.router, tags=["認証"])
app.include_router(records.router, tags=["レコード管理"])
app.include_router(line.router, tags=["LINE連携"])
app.include_router(export.router, tags=["エクスポート"])
app.include_router(admin.router, tags=["管理者"])

@app.on_event("startup")
async def startup_event():
    print("=" * 50)
    print("SmartBuilder AI - Starting...")
    print("=" * 50)
    init_admin()
    print("[OK] Application ready!")
    print("=" * 50)

@app.get("/")
async def root():
    return FileResponse("index.html")
```

#### **database.py** (最新版 - pbkdf2_sha256使用)
```python
"""
データベース接続・初期化
Firestore と Cloud Storage の初期化
"""
from google.cloud import firestore, storage
from passlib.context import CryptContext
import config

# === Firestore / Cloud Storage 初期化 ===
db = firestore.Client()
storage_client = storage.Client()

# === パスワードハッシュ設定 ===
# pbkdf2_sha256を使用（Windows環境でbcryptのビルド問題を回避）
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def init_admin():
    """管理者アカウントの初期化（マルチユーザー構造）"""
    admin_ref = db.collection(config.COL_USERS).document("admin")
    if not admin_ref.get().exists:
        admin_ref.set({
            "email": "admin@smartbuilder.ai",
            "password": pwd_context.hash("password"),
            "role": "admin",
            "created_at": firestore.SERVER_TIMESTAMP,
            "line_user_id": None,
            "subscription": {
                "plan": "unlimited",
                "status": "active",
                "limit": 99999,
                "used": 0,
                # ...
            }
        })
        print("[OK] 管理者アカウントを初期化しました")
```

#### **index.html - 管理者機能部分** (Phase 1で追加)

**プラン変更モーダル**:
```html
<!-- プラン変更モーダル -->
<div id="changePlanModal" class="hidden fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
    <div class="bg-white rounded-[2rem] shadow-2xl max-w-md w-full p-8">
        <h2 class="text-2xl font-bold mb-6">プラン変更</h2>

        <div class="mb-6">
            <p class="text-sm text-slate-600 mb-2">ユーザー</p>
            <p class="font-bold text-lg" id="changePlanEmail"></p>
        </div>

        <div class="mb-6">
            <label class="block text-sm font-bold text-slate-700 mb-2">新しいプラン</label>
            <select id="newPlanSelect" class="w-full p-3 bg-slate-50 border rounded-xl">
                <option value="free">無料プラン (10件/月)</option>
                <option value="premium">プレミアムプラン (100件/月)</option>
                <option value="enterprise">エンタープライズプラン (1,000件/月)</option>
                <option value="unlimited">無制限プラン</option>
            </select>
        </div>

        <div class="flex gap-3">
            <button onclick="closeChangePlanModal()"
                    class="flex-1 bg-slate-100 text-slate-700 px-6 py-3 rounded-xl font-bold hover:bg-slate-200 transition">
                キャンセル
            </button>
            <button onclick="confirmChangePlan()"
                    class="flex-1 bg-blue-600 text-white px-6 py-3 rounded-xl font-bold hover:bg-blue-700 transition">
                変更する
            </button>
        </div>
    </div>
</div>
```

**JavaScript - プラン変更機能**:
```javascript
// プラン変更用のグローバル変数
let changingUserId = null;
let changingUserEmail = null;

// プラン変更モーダルを表示
function changePlan(userId, email, currentPlan) {
    changingUserId = userId;
    changingUserEmail = email;
    document.getElementById('changePlanEmail').textContent = email;
    document.getElementById('newPlanSelect').value = currentPlan || 'free';
    document.getElementById('changePlanModal').classList.remove('hidden');
}

// プラン変更を実行
async function confirmChangePlan() {
    if (!changingUserId) return;

    const newPlan = document.getElementById('newPlanSelect').value;
    showLoading('プランを変更中...', '');

    try {
        const res = await authFetch(`/admin/users/${changingUserId}/plan`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan: newPlan })
        });

        if (res.ok) {
            alert('プランを変更しました');
            closeChangePlanModal();
            loadUsers();
        } else {
            const error = await res.json();
            alert(`プラン変更に失敗しました: ${error.detail}`);
        }
    } catch (e) {
        console.error(e);
        alert('プラン変更に失敗しました');
    } finally {
        hideLoading();
    }
}
```

**JavaScript - 改善されたloadUsers関数**:
```javascript
async function loadUsers() {
    try {
        const res = await authFetch('/admin/users');
        const data = await res.json();

        const usersList = document.getElementById('usersList');

        if (!data.users || data.users.length === 0) {
            usersList.innerHTML = `
                <div class="text-center py-8 text-slate-400">
                    <p>登録されているユーザーはいません</p>
                </div>
            `;
            return;
        }

        usersList.innerHTML = data.users.map(user => {
            const sub = user.subscription || {};
            const planNames = {
                'free': '無料プラン',
                'premium': 'プレミアムプラン',
                'enterprise': 'エンタープライズプラン',
                'unlimited': '無制限プラン'
            };
            const planName = planNames[sub.plan] || sub.plan;
            const roleLabel = user.role === 'admin' ? '🔑 管理者' : '👤 ユーザー';
            const used = sub.used || 0;
            const limit = sub.limit || 10;

            return `
            <div class="bg-slate-50 p-6 rounded-xl flex justify-between items-center">
                <div class="flex-1">
                    <div class="flex items-center gap-3 mb-2">
                        <p class="font-bold text-lg">${user.email}</p>
                        <span class="text-xs bg-slate-200 px-3 py-1 rounded-full">${roleLabel}</span>
                    </div>
                    <p class="text-sm text-slate-600 mb-1">プラン: ${planName}</p>
                    <p class="text-sm text-slate-500">使用状況: ${used} / ${limit}件</p>
                    ${user.line_user_id ? '<p class="text-xs text-green-600 mt-1">✅ LINE連携済み</p>' : ''}
                </div>
                <div class="flex gap-3">
                    ${user.role !== 'admin' ? `
                        <button onclick="changePlan('${user.id}', '${user.email}', '${sub.plan || 'free'}')"
                                class="bg-blue-50 text-blue-600 px-4 py-2 rounded-xl text-sm font-bold hover:bg-blue-100 transition">
                            プラン変更
                        </button>
                        <button onclick="deleteUser('${user.id}', '${user.email}')"
                                class="bg-red-50 text-red-600 px-4 py-2 rounded-xl text-sm font-bold hover:bg-red-100 transition">
                            削除
                        </button>
                    ` : '<span class="text-slate-400 text-sm px-4 py-2">管理者は削除不可</span>'}
                </div>
            </div>
            `;
        }).join('');
    } catch (e) {
        console.error(e);
        alert('ユーザー一覧の取得に失敗しました');
    }
}
```

---

## 5. 最新の修正（2026-01-18）

### **ログイン承認問題の修正** 【完了✅】

#### **修正内容**:
1. **バックエンドの改善**:
   - `services/auth_service.py`: パスワード検証とハッシュ化のコード改善
   - `routers/auth.py`: 詳細なログ出力を追加（デバッグ容易化）

2. **フロントエンドの改善**:
   - `index.html - login()`: エラーハンドリング強化、詳細なログ出力追加
   - `index.html - 初期化処理`: トークンの有効性を自動検証

3. **診断スクリプトの追加**:
   - `check_admin.py`: 管理者アカウントの確認
   - `test_login.py`: ログイン処理のテスト
   - `LOGIN_FIX_REPORT.md`: 修正の詳細レポート

#### **主な改善点**:
- ✅ ログイン時のエラーメッセージを具体的に表示
- ✅ サーバー側のログを詳細化（デバッグが容易に）
- ✅ ページリロード時にトークンを自動検証
- ✅ トークンが無効な場合は自動的にクリア
- ✅ メールアドレスの前後の空白を自動削除（trim処理）

#### **修正ファイル**:
- `services/auth_service.py` - パスワード検証ロジック改善
- `routers/auth.py` - ログ出力強化
- `index.html` - ログイン関数とトークン検証改善

---

### **Phase 2: レコード編集機能の改善** 【完了✅】

#### **実装内容**:
1. **編集モーダルのUI改善**:
   - 2カラムレイアウト（フォーム + 画像プレビュー）
   - 必須項目マーク（赤い*）
   - レスポンシブデザイン対応
   - max-widthを`2xl`に拡大

2. **JavaScript関数の改善**:
   - `openEditModal()`: 画像プレビュー機能追加
   - `viewEditImage()`: 画像拡大表示機能追加
   - `saveEdit()`: 詳細なバリデーションとログ出力

3. **ユーザビリティ向上**:
   - 詳細なバリデーションメッセージ
   - エラー時の自動フォーカス
   - 入力値のtrim処理
   - レシート画像の確認が容易に

#### **修正ファイル**:
- `index.html` (280-334行: 編集モーダルHTML)
- `index.html` (900-1004行: 編集機能JavaScript)

#### **詳細レポート**:
`PHASE2_RECORD_EDIT_REPORT.md` - 実装の詳細と使い方

---

### **Phase 3: 一括操作機能の拡張** 【完了✅】

#### **実装内容**:
1. **バックエンド**:
   - `routers/records.py`: 一括更新API追加
     - `POST /api/records/bulk-update` - カテゴリ・日付の一括変更
   - `routers/export.py`: 選択エクスポートAPI追加
     - `POST /api/export/selected/csv`
     - `POST /api/export/selected/excel`
     - `POST /api/export/selected/pdf`

2. **フロントエンド**:
   - 一括編集モーダル追加（カテゴリ・日付のチェックボックス選択式）
   - 「一括削除」→「一括操作」モードに拡張
   - 選択エクスポート機能（CSV/Excel/PDF）

#### **UI変更点**:
- 「一括削除」ボタン → 「一括操作」ボタンに変更
- 一括操作モードで以下の操作が可能:
  - ✏️ 一括編集（カテゴリ・日付）
  - 🗑️ 一括削除
  - 📊 選択分をCSV
  - 📗 選択分をExcel
  - 📕 選択分をPDF

#### **JavaScript新規関数**:
```javascript
toggleBulkMode()        // 一括操作モードの切り替え
openBulkEditModal()     // 一括編集モーダルを開く
closeBulkEditModal()    // 一括編集モーダルを閉じる
toggleBulkEditField()   // 編集フィールドの有効/無効切り替え
confirmBulkEdit()       // 一括編集を実行
exportSelectedCSV()     // 選択レコードをCSV出力
exportSelectedExcel()   // 選択レコードをExcel出力
exportSelectedPDF()     // 選択レコードをPDF出力
```

---

## 6. 未解決の課題・次のタスク

### **Phase 4: フロントエンドの完全モジュール化** 【優先度: 中】

#### **実装手順**:
1. **CSS移行**:
   - `index.html`の`<style>`タグを削除
   - `<link rel="stylesheet" href="/static/css/main.css">`を追加

2. **JavaScript移行（段階的）**:
   - 認証機能から開始（`static/js/modules/auth.js`活用）
   - レコード管理機能（`static/js/modules/records.js`活用）
   - LINE連携機能（`static/js/modules/line.js`活用）

3. **HTMLテンプレート化**:
   - モーダルをコンポーネント化
   - 再利用可能なパーツに分割

#### **参考ドキュメント**:
`FRONTEND_MODULES_README.md` - 詳細な移行手順書

---

### **Phase 5: UI/UXの改善** 【優先度: 低】

#### **実装内容**:
1. **検索機能の強化**:
   - 全文検索（取引先名、金額、日付）
   - 検索結果のハイライト

2. **ダッシュボードの拡張**:
   - 月別支出グラフ
   - カテゴリ別円グラフ
   - 統計情報の追加

3. **レスポンシブ対応の強化**:
   - モバイル表示の最適化
   - タブレット表示の調整

---

### **Phase 6: セキュリティ強化** 【優先度: 低】

#### **実装内容**:
1. **レート制限の実装**:
   - API呼び出し回数制限
   - ログイン試行回数制限

2. **監査ログの実装**:
   - 重要操作のログ記録
   - 管理者操作の追跡

3. **トークンリフレッシュ機能**:
   - アクセストークンの自動更新

---

## 6. 重要な注意事項

### **既知の問題・制約**

1. **bcryptは使用しない**:
   - Windows環境でビルド問題が発生
   - pbkdf2_sha256のみ使用

2. **Windowsコンソールでemojiを使用しない**:
   - cp932エンコーディングエラーが発生
   - ASCII文字で代替

3. **フロントエンドは段階的移行**:
   - 既存のindex.htmlは安定性のため保持
   - モジュールは準備完了しているが、移行は慎重に

4. **管理者アカウントの再作成**:
   - パスワードハッシュ方式変更のため、既存ユーザーは再登録が必要
   - または、`create_admin.py`を実行して管理者を再作成

---

## 7. 開発環境・起動方法

### **ローカル開発サーバー起動**:
```bash
uvicorn main:app --reload
```

### **管理者アカウント作成**:
```bash
python create_admin.py
```

### **ngrok（外部アクセス用）**:
```bash
./ngrok.exe http 8000
```

### **テスト用URL**:
- ローカル: `http://localhost:8000`
- ngrok: `https://ling-pointless-unseverely.ngrok-free.dev` (動的に変わる)

---

## 8. GitHubリポジトリ

- **URL**: https://github.com/ryom080502-dev/tyushoAI
- **メインブランチ**: main
- **最新コミット**: 2c8ecdb

### **最近のコミット履歴**:
```
2c8ecdb - fix: bcryptエラーとWindowsエンコーディング問題を修正
f3159e3 - feat: 管理者機能の完全実装
27fe660 - Feature: フロントエンドモジュール基盤の作成
537f15d - Refactor: バックエンドファイル構造の最適化
0bfe7d9 - Chore: 不要なバックアップファイルを削除
252af57 - Chore: .gitignoreに.claude/ディレクトリを追加
```

---

## 9. 次のセッションで最初に実施すること

1. **Phase 2の開始準備**:
   ```bash
   git checkout -b feature/record-edit
   ```

2. **レコード編集APIの実装**:
   - `routers/records.py`に`PUT /api/records/{record_id}`エンドポイント追加

3. **編集モーダルの実装**:
   - `index.html`に編集モーダルのHTML追加
   - `saveEdit()`関数の完全実装

4. **テスト**:
   - ローカルサーバー起動
   - 編集機能の動作確認

5. **コミット＆プッシュ**:
   ```bash
   git add -A
   git commit -m "feat: レコード編集機能の実装"
   git push origin feature/record-edit
   ```

---

## 10. 参考ドキュメント

- `REFACTORING_REPORT.md` - バックエンドリファクタリングの詳細
- `FRONTEND_MODULES_README.md` - フロントエンドモジュール化の詳細
- `README_MULTIUSER.md` - マルチユーザー機能の詳細

---

**このファイルを新しいチャットで読み込ませることで、プロジェクトの完全な状態を把握し、すぐに作業を再開できます。**
