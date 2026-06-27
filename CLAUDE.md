# LINE Bot 媽媽助手 — 專案記憶

## 專案說明
給媽媽用的 LINE Bot，長輩不熟悉 App，用 LINE 最方便。
Python 3.12 + Flask + LINE Messaging API v3

## 路徑規則
- **開發與執行**：`C:\line-bot-mama\`（.env 在這裡，Python 才能正確讀取）
- **備份/雲端同步**：`G:\我的雲端硬碟\claude工作室\line-bot-mama\`
- 修改後用 xcopy 從 C 槽同步回 G 槽

## 啟動方式
```
# 視窗1：Flask
cd C:\line-bot-mama
py -3.12 main.py

# 視窗2：ngrok
ngrok http 5000
```
ngrok 免費版每次重開會換網址，需更新 LINE Developers Webhook URL。

## Python 版本
- 必須用 `py -3.12`（3.14 太新，套件不相容）
- 測試用：`$env:PYTHONUTF8=1; py -3.12 test_bot.py`

## API Keys（C:\line-bot-mama\.env）
- LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET：已設定
- GROQ_API_KEY：生活問答，llama-3.3-70b-versatile
- GEMINI_API_KEY：台灣帳號額度 0，已棄用，改用 Groq
- CWB_API_KEY：CWA-F58AE4E0-CFB5-4734-BDBA-4BF46342703F（天氣）
- GOOGLE_MAPS_API_KEY：Directions API（交通路線）
- TDX_CLIENT_ID / TDX_CLIENT_SECRET：公車班距查詢

## 開發進度
- [x] Phase 1：天氣預報（中央氣象署）+ 生活問答（Groq）
- [x] Phase 2：公車即時到站（TDX API）
- [x] Phase 3：交通路線查詢（Google Maps Directions API）
- [ ] Phase 4：記事本文字版 + 網頁版（Supabase）
- [ ] Phase 5：記事本圖片/影片 + Google Drive
- [ ] Phase 6：提醒功能（單次 + 重複）
- [ ] Phase 7：圖文選單（Rich Menu，6 按鈕）
- [ ] Deploy：Railway，ngrok → Railway URL

---

## Phase 3 技術細節（modules/transit.py）

### LOCATION_ALIAS 地名對應
```python
"家裡" / "家" → 台北市信義區吳興街518巷
"吳興街總站" → 台北市信義區松仁路277號
"北門" → 台北市大同區塔城街10號
"西門" → 台北市萬華區西門町
"東門" → 台北市大安區東門
"輔大捷運站" / "輔大站" → 新北市新莊區輔大站
```

### Google Maps 查詢方式
- 查 4 次（fast / less_walking / bus偏好 / rail偏好）合併去重
- **不帶 departure_time**：路線結果穩定，不隨查詢時間飄移
- arrival_time 查詢時才帶 arrival_ts

### 路線排序評分
- 公車班距懲罰：最差公車班距（分鐘）× 300 秒
- 步行懲罰：第一/最後段步行超過 10 分鐘，每多 1 分鐘 +120 秒
- TDX 班距查詢：threading 平行查詢；時刻表路線預設 15 分；無資料 99 分

### 顯示規則
- 第一段步行 ≤ 3 分鐘：省略（出發點就在車站旁）
- 最後一段步行 ≤ 5 分鐘且目的地含「站」：省略（出站走幾步）
- 下車站名包含目的地關鍵字：省略最後步行
- 騎車提示：第一段步行 > 10 分鐘時，同行顯示「🚲 騎車約 X 分鐘」（walk_mins // 3）
- 總時間也同步顯示騎車替換版（只替換第一段步行時間）
- 最後一段步行不顯示騎車提示

### 意圖判斷（modules/intent.py）
- 「從A到B」/ 「X怎麼去」優先走 transit，不被「幾分鐘」BUS 關鍵字搶走
- parse_transit_query 會清理目的地尾端「大約幾分鐘」等雜訊

### 查詢類型
- `route`：標準路線規劃
- `departure_time`：幾點出發才來得及（計算用最短實際交通時間的路線）
- 到達時間：支援「8:00到達北門」「早上8點到」等格式
