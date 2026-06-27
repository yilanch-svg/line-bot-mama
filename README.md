# 媽媽的 LINE Bot 助手

## 第一階段：天氣預報 + 生活問答

---

## 需要申請的 API Key（共 3 個）

### 1. LINE Bot（免費）

1. 開啟 [LINE Developers](https://developers.line.biz/)，用 LINE 帳號登入
2. 點「Create a new provider」→ 輸入任意名稱（例如：媽媽助手）
3. 點「Create a Messaging API channel」
4. 填寫：
   - Channel name：媽媽助手（或任意名稱）
   - Channel description：隨便填
   - Category/Subcategory：隨便選
5. 建立後進入 channel 頁面：
   - **Basic settings** 頁籤 → 複製 `Channel secret`
   - **Messaging API** 頁籤 → 最底下點「Issue」生成 `Channel access token`
6. 把這兩個值填入 `.env`

### 2. Gemini API（免費）

1. 開啟 [Google AI Studio](https://aistudio.google.com/)，用 Google 帳號登入
2. 左側點「Get API Key」→「Create API key」
3. 複製 API Key，填入 `.env` 的 `GEMINI_API_KEY`

### 3. 中央氣象署 API（免費）

1. 開啟 [中央氣象署開放資料平台](https://opendata.cwa.gov.tw/)
2. 右上角「會員登入」→「立即加入」免費註冊
3. 登入後點右上角頭像 →「取得授權碼」
4. 複製授權碼，填入 `.env` 的 `CWB_API_KEY`

---

## 本機測試步驟

```bash
# 1. 安裝 Python 套件
pip install -r requirements.txt

# 2. 複製並填入 API Key
cp .env.example .env
# 用記事本或 VS Code 開啟 .env，填入三個 Key

# 3. 啟動伺服器
python main.py

# 4. 另開一個終端，用 ngrok 讓 LINE 連到你的電腦（測試用）
# 先安裝 ngrok：https://ngrok.com/download
ngrok http 5000

# ngrok 會顯示一個網址，例如：https://abc123.ngrok.io
# 把這個網址 + /callback 填入 LINE Developers 的 Webhook URL
# 例如：https://abc123.ngrok.io/callback
```

---

## 部署到 Railway（免費，正式上線用）

1. 開啟 [Railway](https://railway.app/)，用 GitHub 帳號登入
2. 點「New Project」→「Deploy from GitHub repo」
3. 把這個資料夾上傳到你的 GitHub（或直接 Deploy from local）
4. 部署後點「Settings」→「Environment」，把 `.env` 裡的三個 Key 一一填入
5. 點「Deployments」查看部署狀態，綠色表示成功
6. 點「Settings」→「Domains」複製你的網址（例如 `https://xxx.railway.app`）
7. 回到 LINE Developers，把 Webhook URL 改成 `https://xxx.railway.app/callback`
8. 點「Verify」確認連線成功

---

## 檔案結構

```
line-bot-mama/
├── main.py              # 主程式，LINE Webhook 處理
├── modules/
│   ├── intent.py        # 意圖判斷（用 Gemini 分辨使用者要問什麼）
│   ├── weather.py       # 天氣預報（中央氣象署 API）
│   └── qa.py            # 生活問答（Gemini API）
├── requirements.txt     # Python 套件清單
├── railway.toml         # Railway 部署設定
└── .env.example         # API Key 範本（複製成 .env 後填入真實值）
```

---

## 開發進度

- [x] 第一階段：天氣預報 + 生活問答
- [ ] 第二階段：公車即時到站（TDX API）
- [ ] 第三階段：交通路線查詢（Google Maps API）
- [ ] 第四階段：記事本文字版 + 網頁版（Supabase）
- [ ] 第五階段：記事本圖片/影片 + Google Drive
- [ ] 第六階段：提醒功能
- [ ] 第七階段：圖文選單（Rich Menu）
