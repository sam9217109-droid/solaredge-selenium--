🚀 SolarEdge 太陽能案場自動化巡檢與資料校正系統

(Ops & QA Automation Framework)

本專案為基於 Python + Selenium + Chrome DevTools Protocol (CDP) 所打造的自動化巡檢與資料一致性校正系統，用於 SolarEdge 太陽能監控平台之多案場維運流程。

系統主要解決以下實務問題：

案場 ID 錯誤或缺失
UI 操作流程繁瑣且不穩定
前端 DOM 結構變動導致自動化失效
Excel / API / Lobby 多來源資料不一致
人工巡檢成本高且易錯
📊 系統架構流程 (System Workflow)
🔁 End-to-End Data Pipeline
flowchart TD
    A[Excel 案場清單] --> B[Selenium 登入 + Lobby 導覽]
    B --> C[CDP Network Logs 擷取]
    C --> D[API JSON 資料解析]
    D --> E[Identity Gate 驗證機制]
    E --> F{是否一致?}
    F -->|Yes| G[直接進入巡檢]
    F -->|No| H[Lobby Recovery 修復 ID]
    H --> I[更新 runtime state + Excel]
    I --> G
    G --> J[設備數據分析與統計]
    J --> K[發電基準值計算]
    K --> L[PDF 報表生成]
🧠 核心功能模組
1️⃣ Identity Gate（資料一致性驗證）

系統會比對：

Excel 中的 site_name / site_id
API 回傳的實際 site name
行為：
一致 → 進入巡檢流程
不一致 → 觸發 Recovery 機制

Recovery 流程：

返回 Lobby
重新搜尋案場
取得正確 site_id
更新 runtime state
回寫 Excel（永久修正）
2️⃣ CDP Network 擷取（低依賴資料來源）

透過 Chrome DevTools Protocol：

攔截 network response
解析 JSON API 資料
避免依賴脆弱 DOM selector

用途：

設備數據
案場 metadata
performance logs
3️⃣ Lobby Fallback 機制

當 API 或 URL ID 不可靠時：

自動回到 Lobby
使用搜尋功能定位案場
從 searchSites 封包取得正確資料
4️⃣ 資料清洗與型別控制
site_id 強制 string 處理
避免 Excel / Pandas 自動型別轉換
防止 ID 前導 0 遺失
統一資料格式寫入
5️⃣ 報表自動生成

系統會輸出 PDF 報表，包含：

案場基本資訊
發電數據統計
異常設備標記
維運建議
🔁 Identity Gate + Recovery Flow

完整流程如下：

使用 site_id 呼叫 API
比對 API name 與 Excel name
若 mismatch：
返回 Lobby
重新搜尋案場
取得正確 site_id
更新 runtime state
回寫 Excel（永久修正）
再次驗證新 site_id
通過後進入巡檢
⚙️ 技術亮點
1. Automation Layer（Selenium）
用於流程控制與頁面導覽
處理登入與 UI flow
2. Data Layer（CDP + API）
CDP：擷取 network logs
API：取得 site metadata
Lobby：fallback source
3. Data Integrity System
多來源交叉驗證
防止錯誤 site mapping
自動修復並寫回資料庫（Excel）
4. Hierarchical Data Processing

針對設備資料進行：

分層解析
發電統計
異常標記
5. Human-in-the-loop Debug Support
支援 browser session 保留
可人工接管 debug flow
📊 系統實際執行畫面
![System Workflow](workflow.png)

📄 自動化報表輸出
![Report Example](report.png)

🚀 快速開始
1. 安裝依賴
pip install -r requirements.txt
2. 準備 Excel

需包含欄位：

site_name
site_id
3. 執行系統
python main.py
🔒 資安與使用聲明

本系統已移除所有真實憑證與敏感資料，僅保留流程與架構示意。實際使用需於授權環境中執行。

🧾 系統總結

本系統是一個：

具備資料一致性校正能力的自動化巡檢與報表生成引擎

核心能力包含：

自動化巡檢流程
多來源資料驗證
錯誤 ID 自動修復
Excel 永久同步更新
自動化 PDF 報表生成
### 巡檢三大核心步驟：
1. **第一階段 (Dynamic URL Detection)**：登入後動態偵測並重構當日數位分身（Digital-twin）的萬用網址範本。
2. **第二階段 (Data Cleaning & ID Sync)**：掃描 Excel 缺失資料，自動發動大廳盲搜，對齊官方全名、實時 ID 與地址，完成數據清洗。
3. **第三階段 (Deep Inspection Loop)**：精準空投案場，監聽並捕獲「實體佈局」、「邏輯結構（Children 關係）」與「發電量」三大核心封包。
