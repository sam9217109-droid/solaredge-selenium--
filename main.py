import pandas as pd
import re
import json
import time
import statistics
import requests
import urllib.parse
from selenium import webdriver
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from collections import defaultdict
from datetime import datetime
from fpdf import FPDF

# 運維自動化引擎初始化配置
chrome_options = Options()

# 確保 Session 持續性：
# 啟用 detach 模式防止 WebDriver 腳本執行完畢後流暢關閉瀏覽器。
# 允許維修人員在自動化巡檢結束後，能直接接管當前網頁畫面，進行人工複查或二次調試，提升 Ops 協作效率。
chrome_options.add_experimental_option("detach", True)

# 傳輸層響應攔截（Response Interception）核心配置：
# 注入 CDP (Chrome DevTools Protocol) 的效能監聽偏好設定，強制捕獲全量 Network Performance Logs。
# 繞過 SPA (單頁應用) 脆弱且不可靠的 DOM/UI 結構，直接在水庫端攔截後台實時的 JSON 數據封包。
chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

# 實例化自動化驅動底座
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
# 資料存取層（Data Persistence Layer）初始化
input_file = 'Sites.xlsx'
# 確保資料完整性（Data Integrity）：
# 強制將 Excel 來源端欄位全數以字串（String）型態載入 DataFrame。
# 徹底杜絕 Pandas 預設對設備序號（如純數字或帶 0 序號）與通訊 ID 進行錯誤的數值型態轉換，防止髒資料污染。
df_input = pd.read_excel(input_file, dtype=str)
SITE_DEVICE_MAPS = {}
SITE_PACKET_CONTAINER = {}
print("============ [開始自動化巡檢] ============")


def login_to_solaredge(driver):  # 登入SolarEdge平台

    # =========================
    # 登入SolarEdge平台
    # 建立 Selenium Session 並登入 SolarEdge 平台
    # 回傳登入完成後的大廳首頁 URL
    # =========================

    driver.maximize_window()  #避免視窗過小導致UI元件未顯示
    driver.get("https://monitoring.solaredge.com/mfe/auth/")
    print("請在瀏覽器完成登入")
    input("登入完成後請按 Enter")
    return driver.current_url
def detect_url_pattern(driver, wait, home_url):  #紀錄首頁URL/取得Digital-twin URL格式

    # =========================
    # URL Pattern Detection
    #
    # 利用第一個案場進入 Digital Twin，
    # 擷取可重複使用的 URL Pattern，
    # 並恢復至首頁，作為後續流程的統一入口
    # =========================

    print(f"首頁網址：{home_url}")
    time.sleep(0.5)
    print("開始偵測 Digital Twin URL 格式...")

    fallback_url = (
        "https://monitoring.solaredge.com/"
        "one#/residential/digital-twin?siteId={}"
    )

    url_pattern = fallback_url

    try:
        # =========================
        # Step 1：進入第一個案場
        # =========================
        print("正在定位第一個案場...")

        first_site_link = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class, 'MuiDataGrid')]//a[contains(@href, 'siteId=')]"
            ))
        )
        captured_first_name = first_site_link.text
        print(
            f"已鎖定案場："
            f"{captured_first_name}"
        )

        first_site_link.click()
        time.sleep(5)

        # =========================
        # Step 2：進入 Digital Twin 並偵測 URL Pattern
        # =========================

        print("尋找 Layout 頁面...")

        layout_menu_btn = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//span[contains(text(), 'Layout')] "
                "| //span[contains(text(), '物理配置')] "
                "| //span[contains(text(), '佈局')] "
                "| //a[contains(@href, 'digital-twin')]"
            ))
        )
        layout_menu_btn.click()
        wait.until(     #確保渲染UI防止掉封包
            EC.visibility_of_element_located((
                By.CSS_SELECTOR,
                "canvas.wsd-canvas-base-component-module-modelCanvas"
            ))
        )
        print("Canvas 已載入")

        # 等網址變化完成
        wait.until(
            lambda d: "siteId=" in d.current_url
        )

        sample_url = driver.current_url

        if "siteId=" in sample_url:
            url_pattern = (
                sample_url.split("siteId=")[0]
                + "siteId={}"
            )

            print(
                f"偵測成功："
                f"{url_pattern}"
            )

        else:
            print("URL 格式異常，使用保底格式")
            url_pattern = fallback_url

    except Exception as e:
        print(
            f"Layout 偵測失敗：{e}"
        )
        url_pattern = fallback_url

    # =========================
    # Step 3：返回首頁
    # =========================

    # 統一恢復至首頁，作為後續巡檢入口
    print("返回首頁...")
    driver.get(home_url)
    time.sleep(3)

    return url_pattern
def resolve_missing_site_info(missing_id_sites, driver, wait):  #檢查缺失ID

    # =========================
    # Site Metadata Recovery
    #
    # 從大廳查詢缺失案場資訊，
    # 補齊 Site Name、Site ID 與 Address，
    # 並回傳查詢失敗的案場清單。
    # =========================

    if not missing_id_sites:
        return {}, {}, {}, []

    fetched_name_pool = {}
    fetched_id_pool = {}
    fetched_address_pool = {}
    broken_sites = []

    for target_site in missing_id_sites:

        # =========================
        # Step 1：驗證輸入資料
        # =========================

        if (
            pd.isna(target_site)
            or str(target_site).strip() == ""
            or str(target_site).lower() == "nan"
        ):
            continue

        # =========================
        # Step 2：查詢案場資訊
        # =========================

        result = fetch_site_info_from_lobby(
            target_site,
            driver,
            wait
        )

        if result is None:
            broken_sites.append(target_site)
            continue

        fetched_name_pool[target_site] = result["name"]
        fetched_id_pool[target_site] = result["id"]
        fetched_address_pool[target_site] = result["address"]

    return fetched_name_pool, fetched_id_pool, fetched_address_pool, broken_sites
def fetch_site_info_from_lobby(target_site,driver,wait):  #在大廳抓名稱/ID/地址

    # =========================
    # Lobby Site Search & Metadata Extraction
    #
    # 在 Lobby（大廳搜尋頁）透過 site name 觸發 searchSites API，
    # 攔截 Network response（Chrome performance log + CDP），
    # 解析並提取：
    #   - Site Name
    #   - Solar Field ID (site_id)
    #   - Address
    #
    # 若成功則回傳 dict，
    # 若搜尋失敗或 API 無回應則回傳 None。
    # =========================

    print(f"\n大廳快搜 ➔ 【{target_site}】")

    try:
        # =========================
        # Step 1：定位搜尋框 + 清空輸入
        # =========================
        search_input = wait.until(
            EC.element_to_be_clickable((By.ID, "nameFilter-input-field"))
        )

        # Ctrl + A 全選 + Backspace 清空
        search_input.send_keys(Keys.CONTROL, "a")
        search_input.send_keys(Keys.BACKSPACE)

        time.sleep(0.5)

        site_str = str(target_site).strip()

        # =========================
        # Step 2：前處理（限制搜尋條件）
        # =========================
        # 官網限制至少 3 字搜尋，避免 API 不觸發
        if len(site_str) <= 2:
            print(f"案場【{target_site}】名稱長度不足")
            return None

        # =========================
        # Step 3：分段輸入（降低漏封包機率）
        # =========================
        # 先輸入前兩字 → 讓系統開始觸發 debounce / API request
        search_input.send_keys(
            site_str[0:2]
        )

        time.sleep(0.3)

        # 清掉舊 performance log（避免混到舊 request）
        driver.get_log('performance')

        # 補齊剩餘字串
        search_input.send_keys(site_str[2:])
        search_input.send_keys(Keys.ENTER)

        print("等待 searchSites 封包...")

        # 等待 API 回來（這裡是硬等，之後可優化 WebDriverWait）
        time.sleep(4.5)

        # =========================
        # Step 4：抓 Chrome performance log
        # =========================
        lobby_logs = driver.get_log('performance')

        captured_name = ""
        captured_id = ""
        captured_addr = ""

        # =========================
        # Step 5：解析 Network response
        # =========================
        for entry in lobby_logs:

            log_json = json.loads(entry['message'])['message']

            # 只關心 Network response
            if log_json['method'] != 'Network.responseReceived':
                continue

            url = log_json['params']['response']['url']
            request_id = log_json['params']['requestId']

            # 只抓 searchSites API
            if 'searchSites' not in url:
                continue

            try:
                # 用 CDP 直接拿 response body
                resp_body = driver.execute_cdp_cmd(
                    'Network.getResponseBody',
                    {'requestId': request_id}
                )

                response_json = json.loads(resp_body['body'])

                # =========================
                # Step 6：JSON 結構防呆解析
                # =========================
                if (
                    'page' in response_json
                    and isinstance(response_json['page'], list)
                    and len(response_json['page']) > 0
                ):

                    site_packet = response_json['page'][0]

                    captured_name = str(site_packet.get('name', '')).strip()
                    captured_id = str(site_packet.get('solarFieldId', '')).strip()
                    captured_addr = str(site_packet.get('address', '')).strip()

                    break

            except Exception:
                # 單筆 log 壞掉不影響整體流程
                pass

        # =========================
        # Step 7：結果驗證
        # =========================
        if not captured_id:
            print(f"查無有效 ID：{target_site}")
            return None

        # =========================
        # Step 8：成功輸出
        # =========================
        print(f"封包攔截成功 【{target_site}】")
        print(f"   ➔ 名稱: {captured_name}")
        print(f"   ➔ ID: {captured_id}")
        print(f"   ➔ 地址: {captured_addr}")

        return {
            "name": captured_name,
            "id": captured_id,
            "address": captured_addr
        }

    except Exception as e:

        # =========================
        # ❌ 全域例外保護
        # =========================
        print(f"大廳快搜失敗 【{target_site}】")
        print(f"原因：{e}")
        return None
def reset_search_box(wait):  #清空搜尋框工具

    # =========================
    # Search Box Reset
    #
    # 清空 Lobby 搜尋框，
    # 並回傳可操作的搜尋元件。
    # =========================
    search_input = wait.until(
        EC.element_to_be_clickable(
            (By.ID, "nameFilter-input-field")
        )
    )

    search_input.click()

    search_input.send_keys(Keys.CONTROL, "a")
    search_input.send_keys(Keys.DELETE)

    time.sleep(0.5)

    return search_input
def update_excel_from_lobby_results(  #ID地址寫入SITES/防錯誤案場
    df_input,
    input_file,
    broken_sites,
    fetched_id_pool,
    fetched_name_pool,
    fetched_address_pool
):

    # =========================
    # Excel Synchronization
    #
    # 將 Lobby 查詢結果同步回 Excel：
    #   1. 移除查詢失敗的失效案場
    #   2. 回填缺失的 Site ID 與 Address
    #   3. 使用官網名稱校正 Excel 案場名稱
    #   4. 統一將變更寫回 Excel
    # =========================

    has_changes = False

    # =========================
    # Step 1：移除失效案場
    # =========================

    if broken_sites:

        print(
            "\n 啟動資料庫清理機制，"
            "正在將錯誤案場從 Excel 中移除..."
        )

        df_input = df_input[
            ~df_input['案場名稱'].isin(
                broken_sites
            )
        ]

        has_changes = True

        print("=" * 60)
        print("人工介入通知】")

        for b_site in broken_sites:
            print(
                f"案場：【{b_site}】"
                "已自 Excel 移除。"
            )

        print(
            "請確認 Excel 中的案場名稱是否與 SolarEdge 官網一致，"
            "修正後重新執行即可。"
        )

        print("=" * 60 + "\n")

    # =========================
    # Step 2：同步 Site Metadata
    # =========================

    if fetched_id_pool:

        print(
            "\n正在將大廳封包搜集到的新 ID "
            "與案場地址同步寫入 Excel..."
        )

        for s_name, s_id in fetched_id_pool.items():

            corr_name = (
                fetched_name_pool.get(
                    s_name,
                    s_name
                )
            )

            target_rows_mask = (
                df_input['案場ID'].isna()
            ) & (
                df_input['案場名稱'] == s_name
            )

            df_input.loc[
                target_rows_mask,
                '案場ID'
            ] = s_id

            s_addr = (
                fetched_address_pool.get(
                    s_name,
                    ""
                )
            )

            if s_addr:

                df_input.loc[
                    target_rows_mask,
                    '地址'
                ] = s_addr

            if corr_name != s_name:

                print(
                    f" [名稱校正] "
                    f"表格名稱【{s_name}】"
                    f"➔ 官網名稱【{corr_name}】"
                )

                df_input.loc[
                    target_rows_mask,
                    '案場名稱'
                ] = corr_name

        has_changes = True

    # =========================
    # Step 3：統一寫回 Excel
    # =========================

    if has_changes:

        df_input.to_excel(
            input_file,
            index=False
        )

        print(
            "Excel 已更新並儲存"
        )

    return df_input
def init_collector():  #開工前準備
    return {
        "success_sites": [],  #成功案場
        "missing_id_sites": [],  #找不到ID
        "error_sites": [],  #執行失敗
        "recovered_sites": []  #修復成功
    }
def recover_site_id(driver, wait, home_url, site_name):  #修正錯誤ID

    # =========================
    # Site ID Recovery
    #
    # 重新進入 Lobby，
    # 重新查詢指定案場，
    # 修復缺失或失效的 Site ID 與 Address。
    # =========================
    print(f"開始修復 site_id: {site_name}")

    try:

        # =========================
        # Step 1：返回 Lobby
        # =========================

        driver.get(home_url)
        time.sleep(5)
        print("返回 Lobby 並等待頁面載入...")
        # =========================
        # Step 2：等待 Lobby 載入完成
        # =========================

        wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        wait.until(
            EC.presence_of_element_located((By.ID, "nameFilter-input-field"))
        )

        wait.until(
            EC.element_to_be_clickable((By.ID, "nameFilter-input-field"))
        )

        # =========================
        # Step 3：重新搜尋案場資訊
        # =========================

        reset_search_box(wait)

        site_info = fetch_site_info_from_lobby(site_name, driver, wait)

        if not site_info:
            print(f"❌ Site ID 修復失敗：【{site_name}】")
            return False, None, None

        return True, site_info["id"], site_info["address"]

    except Exception as e:
        print(f"❌ 修復流程發生錯誤：{e}")
        return False, None, None
def collect_site_performance_logs(driver, site_name, wait_sec=10):  # 抓取 performance logs（網路封包）

    time.sleep(wait_sec)
    print(f"Collecting performance logs for 【{site_name}】...")

    performance_logs = driver.get_log('performance')

    if not performance_logs:
        print(
            "⚠️ No performance logs captured. "
            "Possible causes: no network request triggered or timing issue."
        )

    return performance_logs
def add_missing_id(collector, site_name, site_id):  # 將缺失 Site ID 的案場記錄進錯誤彙總 collector
    collector["missing_id_sites"].append({
        "site_name": site_name,
        "site_id": site_id,
        "stage": "ID_NOT_FOUND"
    })
def add_recovered_site(collector, site_name, old_site_id, new_site_id, note):  #修復成功紀錄
    # 記錄 Site ID 修復成功（舊ID → 新ID）
    # 用於追蹤 recovery flow
    collector["recovered_sites"].append({
        "site_name": site_name,
        "old_site_id": old_site_id,
        "new_site_id": new_site_id,
        "stage": "SITE_ID_RECOVERED",
        "note": note
    })
def add_error(collector, site_name, site_id, stage, error):  #錯誤紀錄
    # 通用錯誤收集器
    # 將異常事件分類寫入 collector (error tracking system)
    collector["error_sites"].append({
        "site_name": site_name,
        "site_id": site_id,
        "stage": stage,
        "error": str(error)
    })
def get_optimizer_model_fallback(sn, optimizer_asset_map):  #PDF型號查詢備援
    # Optimizer model lookup fallback
    # 從 asset map 查詢 SN 對應 model
    # 若無資料則回傳 "未知"
    # 只取 model 主型號（去除 suffix）
    model = optimizer_asset_map.get(sn, {}).get("model", "未知")
    return str(model).split("-")[0]
def fetch_site_id_from_lobby(driver, wait, target_name):  # TODO:大廳封包攔截器副本

    # =========================
    # Lobby Site ID Resolver (CDP Network Interception)
    #
    # 透過 Lobby 搜尋觸發 searchSites API，
    # 使用 Chrome DevTools Protocol (CDP) 攔截 Network response，
    # 解析回傳 JSON 取得：
    #   - solarFieldId
    #   - address
    #
    # 用於 Site ID recovery / correction pipeline
    # =========================

    print(f"正在從 Lobby 解析 Site ID：【{target_name}】")

    try:
        # =========================
        # Step 1: Trigger Lobby Search
        # =========================

        search_input = wait.until(EC.element_to_be_clickable((By.ID, "nameFilter-input-field")))
        search_input.clear()
        search_input.send_keys(target_name)
        search_input.send_keys(Keys.ENTER)

        # =========================
        # Step 2: Capture Network Logs (searchSites API)
        # =========================

        time.sleep(4)
        performance_logs = driver.get_log('performance')
        for entry in performance_logs:
            log_json = json.loads(entry['message'])['message']

            # =========================
            # Step 3: Parse CDP Response Body
            # =========================

            #只抓取「網路回應(responseReceived)」且網址包含「searchSites」的封包
            if log_json['method'] == 'Network.responseReceived' and 'searchSites' in log_json['params']['response']['url']:
                #透過 CDP 指令直接讀取該封包的 Response Body (JSON 內容)
                resp = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': log_json['params']['requestId']})
                data = json.loads(resp['body'])

                # =========================
                # Step 4: Match Target Site & Extract Metadata
                # =========================

                for site in data.get('page', []):
                    if site.get('name') == target_name:
                        # success → (site_id, address)
                        # failure → (None, None)
                        return str(site.get('solarFieldId')), str(site.get('address'))
    except Exception as e:
        print(f"❌ Site ID 解析失敗: {e}")
    return None, None
def fetch_site_identity(driver, site_id): #抓身份包

    # =========================
    # Site Identity Fetcher (Authenticated API Call)
    #
    # 使用 Selenium session cookies 轉移至 requests，
    # 呼叫 SolarEdge monitoring API 取得 site identity 資訊。
    #
    # 用途：
    #   - 補充 site metadata
    #   - 驗證 / 修正 site_id 對應資料
    # =========================

    try:
        url = f"https://monitoring.solaredge.com/services/sitelist/{site_id}"
        print(f"Fetching site identity for site_id={site_id}")

        session = requests.Session()

        # =========================
        # Step 1: Transfer auth cookies from Selenium
        # =========================

        # 把 Selenium 目前登入後的 cookie 複製給 requests
        for cookie in driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"])

        # =========================
        # Step 2: Call API
        # =========================
        resp = session.get(url, timeout=15)

        # =========================
        # Step 3: Validate response type
        # =========================
        if "application/json" not in resp.headers.get("Content-Type", ""):
            print(f"⚠️ Non-JSON response: {resp.text[:300]!r}")
            return None

        # =========================
        # Step 4: Parse JSON response
        # =========================
        return resp.json()

    except Exception as e:
        print(f"❌ fetch_site_identity 執行錯誤: {e}")
        return None
def is_site_identity_valid(site_info_json, expected_name):  #檢查身份
    # Compare API-returned site name with expected site name
    actual = site_info_json.get("name", "").strip()
    # return: (is_valid, actual_name)
    return actual == expected_name.strip(), actual
def identity_gate(driver, wait, home_url, site_name, site_id):  # Site Identity Consistency Gate

    # =========================
    # Site Identity Gate (Validation + Recovery Loop)
    #
    # 目的：
    #   確保 site_id 與 site_name 一致性，
    #   防止錯誤 ID 導致資料污染。
    #
    # 流程：
    #   1. 驗證 site identity (API check)
    #   2. 若 mismatch → 觸發 recovery (重新從 Lobby 查 ID)
    #   3. 最多 retry N 次避免無限迴圈
    #
    # 回傳：
    #   - (site_id, address)
    #   - 或 (None, None)
    # =========================

    # Prevent infinite recovery loop
    max_retry = 3
    retry = 0

    while retry < max_retry:

        # =========================
        # Step 1: Validate current site identity
        # =========================
        site_info = fetch_site_identity(driver, site_id)

        if site_info:

            is_valid, actual_name = is_site_identity_valid(
                site_info,
                site_name
            )

            if is_valid:
                # consistent return format: (site_id, address)
                return str(site_info["solarFieldId"]), site_info["address"]
            print(f"Identity mismatch: Excel='{site_name}', API='{actual_name}'")
        # =========================
        # Step 2: Trigger recovery (Lobby re-fetch)
        # =========================
        ok, new_id, new_address = recover_site_id(
            driver, wait, home_url, site_name
        )

        if not ok:
            retry += 1
            continue
        # =========================
        # Step 3: Update state & retry
        # =========================
        site_id = new_id
        retry += 1

    return None, None
def capture_site_packets(logs, site_id):   # CDP packet aggregator

    # =========================
    # Site Packet Aggregator (CDP Network Collector)
    #
    # 從 Chrome DevTools Protocol logs 中，
    # 依據 URL pattern 擷取三種 site data packets：
    #
    #   1. Physical Layout Packet
    #   2. Site Structure (Optimizer Mapping) Packet
    #   3. Energy / Inverter Data Packet
    #
    # 用於後續：
    #   - Site topology analysis
    #   - energy mapping
    #   - system reconstruction
    # =========================

    physical_packet = None
    site_structure_packet = None
    energy_packet = None

    # =========================
    # Step 1: Filter CDP Network Responses
    # =========================
    for entry in logs:
        try:
            log_json = json.loads(entry['message'])['message']
            if log_json['method'] != 'Network.responseReceived': continue

            url = log_json['params']['response']['url']
            request_id = log_json['params']['requestId']

            if str(site_id) not in url: continue

            # =========================
            # Step 2: Match Site-related URLs
            # =========================

            # 現場配置封包
            if 'layout/physical/site/' in url and not physical_packet:
                # =========================
                # Step 3：呼叫 CDP 取得 response
                # =========================
                resp = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                # =========================
                # Step 4：解析 JSON
                # =========================
                physical_packet = json.loads(resp['body'])

            # 系統結構封包
            elif 'include-optimizers' in url and not site_structure_packet:
                resp = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                site_structure_packet = json.loads(resp['body'])

            # 發電數據封包
            elif 'by-inverter' in url and not energy_packet:
                resp = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                energy_packet = json.loads(resp['body'])
        except:
            continue

    return physical_packet, site_structure_packet, energy_packet
def build_physical_device_index(pdm):  # 建立逆變器/優化器索引

    # =========================
    # Physical Device Index Builder
    #
    # 從 physicalDevicesMap 封包建立設備對照表：
    #
    # 1. 逆變器（Inverter）
    #    → sequenceNumber → serialNumber mapping
    #
    # 2. 優化器（Optimizer）
    #    → serialNumber 清單
    #
    # 用途：
    #   - 設備關聯解析
    #   - PDF / 報表生成
    #   - system mapping
    # =========================

    physical_map = pdm.get("physicalDevicesMap", {})

    # =========================
    # Step 1：解析逆變器資料
    # =========================
    inverters = physical_map.get("inverters", {})

    inverter_sn_map = {str(info.get("sequenceNumber")): info.get("serialNumber")
               for info in inverters.values() if info.get("sequenceNumber") is not None}

    # =========================
    # Step 2：解析優化器資料
    # =========================
    optimizers = physical_map.get("optimizers", {})
    optimizer_serial_list = [info.get("serialNumber") for info in optimizers.values()]

    # =========================
    # Step 3：輸出統計資訊
    # =========================
    print(
            f"[Device Index Ready] "
            f"Inverters: {len(inverter_sn_map)} / "
            f"Optimizers: {len(optimizer_serial_list)}"
        )

    return {
        "inv_map": inverter_sn_map,
        "opt_list": optimizer_serial_list
    }
def build_optimizer_asset_map_from_site_structure(node, optimizer_asset_map, current_inverter="未知"):  #site structure tree的索引表包含inverter歸屬與設備屬性

    # =========================
    # Site Structure Tree Parser (DFS Traversal)
    #
    # 功能：
    #   遍歷 site structure tree，
    #   建立 optimizer → inverter 的歸屬對照表
    #
    # 核心概念：
    #   - INVERTER 會更新當前 context（current_inverter）
    #   - OPTIMIZER 會繼承當前 inverter context
    #   - 使用 DFS 遞迴遍歷整棵樹
    # =========================

    if not node:
        return

    # =========================
    # Step 1：更新 inverter context
    # =========================
    if node.get("type") == "INVERTER":
        current_inverter = node.get("name", "未知")

    # =========================
    # Step 2：建立 optimizer mapping
    # =========================
    if node.get("type") == "OPTIMIZER" and node.get("serial"):
        opt_serial = node.get("serial")
        node_properties = node.get("properties", {})

        optimizer_asset_map[opt_serial] = {
            "name": node.get("name", ""),
            "status": node_properties.get("status", ""),
            "model": node_properties.get("model", "Unknown"),
            "inverter": current_inverter  # <--- 順便把逆變器歸屬記住！
        }

    # =========================
    # Step 3：遞迴遍歷子節點
    # =========================
    if "children" in node and isinstance(node["children"], list):
        for child in node["children"]:
            build_optimizer_asset_map_from_site_structure(child, optimizer_asset_map, current_inverter)
def build_optimizer_status_map(energy_packet, physical_devices_map, optimizer_asset_map):  # 計算 optimizer 發電狀態與健康度分級

    # =========================
    # Optimizer Status Analyzer
    #
    # 功能：
    #   1. 從 energy packet 取出每顆 optimizer 的發電量
    #   2. 依 model 分組建立基準值（benchmark）
    #   3. 根據基準值判斷每顆 optimizer 狀態
    #
    # 狀態分類：
    #   - 通訊遺失（API 無資料）
    #   - 無發電（energy = 0）
    #   - 發電異常（< 25% benchmark）
    #   - 發電偏低（< 56% benchmark）
    #   - 正常
    # =========================

    # =========================
    # Step 1：整理 optimizer 發電數據
    # =========================
    optimizer_energy_map = {}
    inverters_list = energy_packet.get("inverters", [])
    for inv in inverters_list:
        for opt in inv.get("optimizers", []):
            if opt.get("serial"):
                optimizer_energy_map[opt.get("serial")] = opt.get("energy", {}).get("value", 0)

    # =========================
    # Step 2：依 model 分組 energy
    # =========================
    energy_groups_by_model = {}
    for optimizer_info in physical_devices_map.get(
        "optimizers",
        {}
    ).values():

        sn = str(
            optimizer_info.get(
                "serialNumber",
                ""
            )
        ).strip()
        if not sn: continue
        energy = optimizer_energy_map.get(sn, 0)
        if energy <= 0: continue

        # 取得型號
        model = str(optimizer_asset_map.get(sn, {}).get("model", "Unknown")).split('-')[0]
        if model not in energy_groups_by_model: energy_groups_by_model[model] = []
        energy_groups_by_model[model].append(energy)

    # =========================================================================
    # Step 3：動態計算 Model Benchmark（效能基準線）
    # 為了避免現場已有大量損壞優化器拉低平均值，導致誤判，
    # 策略性「取該型號前 50% 發電量高的設備」計算平均值，作為該型號的健康基準線。
    # =========================================================================
    model_benchmark_map = {}
    for model, values in energy_groups_by_model.items():
        values.sort(reverse=True)
        top_half = values[:max(1, int(len(values) * 0.5))]
        model_benchmark_map[model] = sum(top_half) / len(top_half)

    # debug：輸出各 model 基準值
    for model, benchmark in model_benchmark_map.items():
        print(model, round(benchmark, 2))

    # =========================
    # Step 4：依 benchmark 判斷 optimizer 狀態
    # =========================
    optimizer_status_map = {}

    for optimizer_info in physical_devices_map.get(
        "optimizers",
        {}
    ).values():

        sn = str(
            optimizer_info.get(
                "serialNumber",
                ""
            )
        ).strip()

        if not sn:
            continue

        energy = optimizer_energy_map.get(sn, 0)

        model = str(
            optimizer_asset_map.get(
                sn,
                {}
            ).get(
                "model",
                "Unknown"
            )
        ).split('-')[0]

        benchmark = model_benchmark_map.get(
            model,
            0
        )

        # =========================
        # 狀態判斷邏輯（由壞到好）
        # =========================
        if sn not in optimizer_energy_map:      #TOODO 跟step3結合
            optimizer_status_map[sn] = "通訊遺失"

        elif energy <= 0:
            optimizer_status_map[sn] = "無發電"

        elif benchmark > 0 and energy < benchmark * 0.25:
            optimizer_status_map[sn] = "發電異常"
        elif benchmark > 0 and energy < benchmark * 0.56:
            optimizer_status_map[sn] = "發電偏低"
        else:
            optimizer_status_map[sn] = "正常"

    return optimizer_status_map
def build_site_mapping(
    physical_packet,
    site_structure_packet,
    energy_packet
):

    # =========================
    # Site Mapping Orchestrator
    #
    # 功能：
    #   組合三種 API packet，建立完整 site model：
    #
    #   1. site_structure → 建立 optimizer ↔ inverter mapping
    #   2. physical_packet → 補充設備結構資訊
    #   3. energy_packet → 計算 optimizer 發電狀態
    #
    # 最終輸出：
    #   - optimizer_asset_map（設備結構）
    #   - optimizer_status_map（健康狀態）
    # =========================

    optimizer_asset_map = {}

    build_optimizer_asset_map_from_site_structure(
        site_structure_packet.get("siteStructure", {}),
        optimizer_asset_map
    )

    optimizer_status_map = build_optimizer_status_map(
        energy_packet,
        physical_packet.get("physicalDevicesMap", {}),
        optimizer_asset_map
    )

    return optimizer_asset_map, optimizer_status_map
def detect_outage_type(grouped_by_inverter):  # 判定是否有迴路異常

    # =========================
    # Outage Detection Engine
    #
    # 功能：
    #   針對 inverter / string 兩個層級進行失聯判定：
    #
    #   1. Inverter-level 判定（整機故障）
    #   2. String-level 判定（局部迴路異常）
    #
    # 判定規則：
    #   - missing ratio > 80% → major outage
    #   - string missing ratio ≥ 50% → string anomaly
    #
    # 輸出：
    #   alerts list（異常事件清單）
    # =========================

    alerts = []

    for prefix, group in grouped_by_inverter.items():
        total_in_group = len(group)
        if total_in_group == 0: continue

        inverter_sn = group[0].get('逆變器序號', '未知')

        # =========================
        # Step 1：補齊 is_missing 欄位（防呆）
        # =========================
        for item in group:
            if "is_missing" not in item:
                item["is_missing"] = (item.get('目前狀態') != '正常')

        # TODO: consider precomputing is_missing during data ingestion stage

        # =========================
        # Step 2：Inverter-level failure check
        # =========================
        missing_in_group = sum(1 for item in group if item.get("is_missing", False))

        if missing_in_group / total_in_group > 0.8:

            alerts.append({
                "type": "major",
                "inv_num": prefix,
                "inv_sn": inverter_sn,
                "message": "【重大故障】: 全線失聯 (整機崩潰)"
            })

            continue

        # TODO: make 0.8 threshold configurable (major outage rule)

        # =========================
        # Step 3：String-level grouping
        # =========================
        string_groups = defaultdict(list)

        for item in group:
            string_id = ".".join(
                item['優化器序列'].split('.')[:2])
            string_groups[string_id].append(item)

        # =========================
        # Step 4：String anomaly detection
        # =========================
        for string_id, items_in_string in string_groups.items():
            # 【關鍵修復】這裡同樣使用 .get 確保安全
            missing_in_string = sum(
                1 for item in items_in_string
                if item.get("is_missing", False)
            )
            ratio = (
                missing_in_string /
                len(items_in_string)
            )

            if ratio >= 0.5:
                alerts.append({
                    "type": "string",
                    "message":
                        f"【串列異常】: "
                        f"迴路 {string_id} "
                        f"(失聯率: {ratio:.0%})"
                })

    return alerts
def build_site_rows(site_name, site_id, site_address, packet_physical, optimizer_asset_map, status_map):  # 將 site data 展平為 Excel rows

    # =========================
    # Site Row Builder (Data Flatten Layer)
    #
    # 功能：
    #   將 hierarchical site data（inverter / string / optimizer）
    #   轉換成 Excel 可用的 flat table rows
    #
    # 資料來源：
    #   - physicalDevicesMap（拓樸結構）
    #   - optimizer_asset_map（設備資訊）
    #   - status_map（健康狀態）
    #
    # 輸出：
    #   - rows (list of dict)
    # =========================

    # =========================
    # Step 1：解析 physical device structure
    # =========================
    pdm_map = packet_physical.get("physicalDevicesMap", {})
    optimizers = pdm_map.get('optimizers', {})
    inverters = pdm_map.get('inverters', {})

    # =========================
    # Step 2：建立 optimizer → inverter mapping
    # =========================
    sn_to_inv = {}

    for string_data in pdm_map.get('strings', {}).values():

        inv_id = string_data.get('inverterId')
        inv_info = inverters.get(inv_id, {})

        for opt_id in string_data.get('optimizerIds', []):

            opt_sn = optimizers.get(opt_id, {}).get('serialNumber')
            sn_to_inv[opt_sn] = inv_info

    # TODO: consider caching sn_to_inv (avoid recomputation per run)
    # =========================
    # Step 3：Flatten optimizer records
    # =========================
    rows = []
    for opt_info in optimizers.values():
        sn = opt_info.get("serialNumber")

        if pd.isna(sn) or not sn:

            continue

        sn = str(sn).strip()

        # =========================
        # Step 3.1：enrich metadata
        # =========================
        pos = optimizer_asset_map.get(sn, {}).get("name", "未知")
        status = status_map.get(sn, "未知")

        # =========================
        # Step 3.2：resolve inverter relation
        # =========================
        inv_info = sn_to_inv.get(sn, {})
        inv_sn = inv_info.get('serialNumber', '未知')
        inv_seq = inv_info.get('sequenceNumber', '未知')

        # =========================
        # Step 3.3：build row
        # =========================
        rows.append({
            '案場名稱': site_name,
            '案場ID': site_id,
            '地址': site_address,
            '優化器序列': pos,
            '優化器序號': sn,
            '逆變器序號': inv_sn,
            '逆變器編號': str(inv_seq),
            '目前狀態': status
        })

    return rows
def update_sites_excel(df, site_name, new_data_rows):  # 更新單一 site 的 Excel 資料（replace + append）

    # =========================
    # Site Data Upsert Layer
    #
    # 功能：
    #   針對單一 site 進行資料更新：
    #   1. 移除舊 site 資料（避免重複）
    #   2. 插入最新抓取結果
    #
    # 特性：
    #   - 採用 full replace 策略（non-incremental）
    #   - 確保 site-level data consistency
    # =========================

    # =========================
    # Step 1：移除舊資料（避免重複）
    # =========================
    df_clean = df[df['案場名稱'] != site_name]

    # =========================
    # Step 2：防呆（無新資料則不更新）
    # =========================
    if not new_data_rows:
        return df

    # =========================
    # Step 3：建立新資料 DataFrame
    # =========================
    new_df = pd.DataFrame(new_data_rows)

    # =========================
    # Step 4：合併資料
    # =========================
    updated_df = pd.concat([df_clean, new_df], ignore_index=True)

    return updated_df
def save_site_result(df_input, input_file, site_name, new_rows, collector):

    # =========================
    # Site Persistence Layer
    #
    # 功能：
    #   將單一 site 的更新結果寫回 Excel，
    #   並更新成功紀錄 collector
    # =========================

    df_input = update_sites_excel(df_input, site_name, new_rows)
    df_input.to_excel(input_file, index=False)

    collector["success_sites"].append(site_name)

    print(f"{site_name} 資料同步完成。")

    return df_input
def draw_unfinished_sites_page(pdf, report_data):  # 產生巡檢異常 / 補救結果頁（PDF）

    # =========================
    # Report Exception Summary Page
    #
    # 功能：
    #   產生巡檢報告的「例外與補救頁」
    #
    # 包含三種資訊：
    #   1. 已自動修復的 site（ID correction）
    #   2. 缺少 ID 或無法解析的 site
    #   3. 巡檢過程中發生錯誤的 site
    #
    # 用途：
    #   - 提供人工檢查依據
    #   - 補強自動化流程透明度
    # =========================

    missing_sites = report_data.get("missing_id_sites", [])
    error_sites = report_data.get("error_sites", [])
    recovered_sites = report_data.get("recovered_sites", [])

    # 如果完全沒有任何需要補充的案場，就不用加這頁
    if not missing_sites and not error_sites and not recovered_sites:
        return

    pdf.add_page()

    # 標題
    pdf.set_font('MicrosoftJhengHei', 'B', 16)
    pdf.cell(0, 12, "巡檢補充說明", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # =========================
    # 1) 已自動修正的案場
    # =========================
    if recovered_sites:
        pdf.set_font('MicrosoftJhengHei', 'B', 13)
        pdf.cell(0, 10, "【已自動修正案場 ID】", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font('MicrosoftJhengHei', '', 11)
        for item in recovered_sites:
            site_name = item.get("site_name", "未知案場")
            old_site_id = item.get("old_site_id", "未知")
            new_site_id = item.get("new_site_id", "未知")

            line = (
                f"{site_name}：原案場 ID {old_site_id} 與實際案場不符，"
                f"系統已重新搜尋並修正為 {new_site_id}"
            )
            pdf.multi_cell(0, 8, line)

        pdf.ln(3)

    # =========================
    # 2) 缺少 ID / 無法取得有效案場資訊
    # =========================
    if missing_sites:
        pdf.set_font('MicrosoftJhengHei', 'B', 13)
        pdf.cell(0, 10, "【無法取得有效案場資訊】", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font('MicrosoftJhengHei', '', 11)
        for item in missing_sites:
            site_name = item.get("site_name", "未知案場")
            line = f"{site_name}：查無有效案場 ID，已跳過本次巡檢"
            pdf.multi_cell(0, 8, line)

        pdf.ln(3)

    # =========================
    # 3) 其他巡檢失敗案場
    # =========================
    if error_sites:
        pdf.set_font('MicrosoftJhengHei', 'B', 13)
        pdf.cell(0, 10, "【巡檢失敗】", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font('MicrosoftJhengHei', '', 11)

        stage_message_map = {
            "URL_NAVIGATION_FAILED": "案場導頁失敗，未完成巡檢",
            "SITE_ID_RECOVERY_FAILED": "案場 ID 修正失敗，未完成巡檢",
            "PACKET_FETCH_FAILED": "封包抓取失敗，未完成巡檢",
            "PARSE_FAILED": "資料解析失敗，未完成巡檢",
        }

        for item in error_sites:
            site_name = item.get("site_name", "未知案場")
            stage = item.get("stage", "UNKNOWN_STAGE")
            reason = stage_message_map.get(stage, f"巡檢中斷（{stage}）")
            line = f"{site_name}：{reason}"
            pdf.multi_cell(0, 8, line)

        pdf.ln(3)
def export_all_sites_pdf(df_all_data, full_packet_container, report_data=None):

    # =========================
    # Full Pipeline Report Generator (Site-wide PDF Export)
    #
    # 功能：
    #   將全案場巡檢結果轉換為 PDF 報告
    #
    # 輸入來源：
    #   1. df_all_data → 巡檢結果資料表（主資料來源）
    #   2. full_packet_container → 各 site 原始 API / CDP packet cache
    #   3. report_data → 巡檢異常 / 修復 / error summary
    #
    # 輸出內容：
    #   - 每個 site 的設備狀態
    #   - inverter / optimizer 異常分析
    #   - outage alerts（major / string level）
    #   - 最終補充異常報告頁
    #
    # 設計目的：
    #   - 將 ETL + anomaly detection + reporting 整合輸出
    #   - 提供可追溯的巡檢結果 PDF
    # =========================

    pdf = FPDF()
    pdf.add_font('MicrosoftJhengHei', '', 'msjh.ttf', uni=True)
    pdf.add_font('MicrosoftJhengHei', 'B', 'msjhbd.ttf', uni=True)

    sites = df_all_data.groupby('案場名稱')

    for site_name, group_data in sites:
        # 1. 確保拿到正確的 site_packet
        site_packet = full_packet_container.get(site_name)

        if site_packet is None:
            file_path = f"data/{site_name}.json"
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    import json
                    site_packet = json.load(f)
            except:
                site_packet = {}


        sn_to_model = {}

        # =========================================================================
        # 關鍵結構解析：型號資產地毯式檢索（BFS Traversal）
        # 為了繞過 SolarEdge 階層結構中可能存在的未知 FOLDER 或動態層級，
        # 捨棄傳統遞迴，改用佇列（Queue）進行廣度優先搜尋，徹底清查所有 OPTIMIZER 節點。
        # =========================================================================
        root_node = site_packet.get("siteStructure", site_packet)

        queue = [root_node]
        while queue:
            node = queue.pop(0)
            if not isinstance(node, dict):
                continue

            # 如果發現目標，存入字典
            if node.get("type") == "OPTIMIZER" and node.get("serial"):
                model = node.get("properties", {}).get("model", "Unknown")
                sn_to_model[node.get("serial")] = str(model).split('-')[0]

            children = node.get("children", [])
            if isinstance(children, list):
                queue.extend(children)

        # 3. 繪製 PDF
        physical_devices_map = SITE_DEVICE_MAPS.get(site_name, {})
        pdf.add_page()
        draw_header(pdf, site_name, group_data['地址'].iloc[0])

        if not (group_data['目前狀態'] != '正常').any():
            pdf.set_text_color(0, 150, 0)
            pdf.cell(0, 10, "本案場全數運作正常", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            continue

        # 4. 處理故障
        for inv_num, inv_data in group_data.groupby('逆變器編號'):

            items = inv_data.to_dict('records')
            items.sort(key=get_string_and_num_key)

            first_item = items[0]
            inverter_sn = first_item.get('逆變器序號', '未知')

            alerts = detect_outage_type({inv_num: items})

            # 是否有重大故障
            has_major_alert = any(
                alert["type"] == "major"
                for alert in alerts
            )

            # 沒有異常 optimizer 就跳過
            if not any(
                item.get('is_missing')
                for item in items
            ):
                continue
            # ===== 沒有重大故障才印一般 header =====
            if not has_major_alert:

                pdf.set_font(
                    'MicrosoftJhengHei',
                    'B',
                    12
                )

                pdf.set_text_color(0, 0, 0)

                header_text = (
                    f"逆變器編號"
                    f"{inv_num}    "
                    f"{inverter_sn}"
                )

                pdf.cell(
                    0,
                    10,
                    header_text,
                    new_x="LMARGIN",
                    new_y="NEXT"
                )

            # ===== 印 alerts =====
            for alert in alerts:

                # 重大故障
                if alert["type"] == "major":

                    pdf.set_font(
                        'MicrosoftJhengHei',
                        'B',
                        12
                    )

                    pdf.set_text_color(0, 0, 0)

                    header = (
                        f"逆變器編號"
                        f"{alert['inv_num']}    "
                        f"{alert['inv_sn']}"
                    )

                    pdf.cell(
                        0,
                        10,
                        header,
                        new_x="LMARGIN",
                        new_y="NEXT"
                    )

                    pdf.set_text_color(255, 0, 0)

                    pdf.cell(
                        0,
                        10,
                        alert["message"],
                        new_x="LMARGIN",
                        new_y="NEXT"
                    )

                # 串列異常
                else:

                    pdf.set_text_color(255, 120, 0)

                    pdf.cell(
                        0,
                        10,
                        alert["message"],
                        new_x="LMARGIN",
                        new_y="NEXT"
                    )

            pdf.set_text_color(0, 0, 0)

            # ===== 故障 optimizer =====
            pdf.set_font('MicrosoftJhengHei', '', 12)

            for item in items:

                if item['is_missing']:

                    sn = item.get('優化器序號')

                    dict_keys = list(sn_to_model.keys())

                    if sn not in sn_to_model:
                        print(
                            f"Excel的SN: '{sn}' | "
                            f"字典裡的樣本: {dict_keys[:5]}"
                        )

                    model = sn_to_model.get(
                        sn,
                        "查無此型號"
                    )

                    if model == "查無此型號":

                        model = resolve_optimizer_model_fallback(
                            sn,
                            physical_devices_map
                        )

                    text = (
                        f"{model}    "
                        f"{item['優化器序列']}    "
                        f"{sn}    "
                        f"{item['目前狀態']}"
                    )

                    pdf.cell(
                        0,
                        10,
                        text,
                        new_x="LMARGIN",
                        new_y="NEXT"
                    )

            pdf.ln(5)
    if report_data:
        draw_unfinished_sites_page(pdf, report_data)

    pdf.output("全案場故障總表.pdf")
    print("報表生成完畢：全案場故障總表.pdf")
def generate_google_maps_link(address_or_coords):  #報表地址超連結
    base_url = "https://www.google.com/maps/search/?api=1&query="
    encoded_addr = urllib.parse.quote(address_or_coords)
    return base_url + encoded_addr
def draw_header(pdf, site_name, address):  # PDF 首頁資訊（案場 / 地址 / 判定標準）

    # =========================
    # PDF Header Renderer
    #
    # 功能：
    #   繪製每個案場報告的頁首資訊，
    #   包含案場名稱、地址、Google Maps 導航連結，
    #   以及巡檢狀態判定標準。
    #
    # 特性：
    #   - 自動處理空地址（NaN）
    #   - 提供 Google Maps 超連結
    #   - 統一 PDF Header 樣式
    # =========================

    # =========================
    # Step 1：Normalize Address
    # =========================
    if pd.isna(address):
        address_str = ""
    else:
        address_str = str(address).strip()

    # =========================
    # Step 2：Render Site Information
    # =========================
    pdf.set_font('MicrosoftJhengHei', 'B', 16)
    pdf.cell(0, 10, f"案場名稱: {site_name}", new_x="LMARGIN", new_y="NEXT")

    if address_str:
        pdf.set_font('MicrosoftJhengHei', '', 12)
        link = generate_google_maps_link(address_str)

        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, "地址: " + address_str, link=link, new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(0, 0, 255)
        pdf.cell(0, 10, "點擊此處開啟 Google Maps 導航", link=link, new_x="LMARGIN", new_y="NEXT")

    # 無地址時顯示人工確認提示
    else:
        pdf.set_text_color(255, 0, 0)
        pdf.cell(0, 10, "地址: 案場未提供地址資料，請向原廠確認", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)


    # =========================
    # Step 3：Render Status Legend
    # =========================
    # TODO: Reduce duplicated PDF drawing code for status legend.
    pdf.set_xy(150, 10)

    pdf.set_font(
        'MicrosoftJhengHei',
        'B',
        10
    )

    pdf.cell(
        40,
        6,
        "判定標準",
        new_x="LMARGIN",
        new_y="NEXT"
    )

    pdf.set_x(150)

    pdf.set_font(
        'MicrosoftJhengHei',
        '',
        9
    )

    pdf.cell(
        40,
        5,
        "通訊遺失  < 0%",
        new_x="LMARGIN",
        new_y="NEXT"
    )

    pdf.set_x(150)



    pdf.set_x(150)

    pdf.cell(
        40,
        5,
        "發電異常  < 25%",
        new_x="LMARGIN",
        new_y="NEXT"
    )

    pdf.set_x(150)

    pdf.cell(
        40,
        5,
        "----------------",
        new_x="LMARGIN",
        new_y="NEXT"
    )

    pdf.set_x(150)

    pdf.cell(
        40,
        5,
        "發電偏低  < 55%",
        new_x="LMARGIN",
        new_y="NEXT"
    )
def get_string_and_num_key(item):  # Optimizer 排序 Key

    # =========================
    # Optimizer Position Sort Key
    #
    # 將優化器序列（例如 1.2.10）
    # 轉換為可排序的 tuple，
    # 避免字串排序造成：
    # 1.2.10 < 1.2.2 的問題。
    # =========================

    pos_str = item['優化器序列']
    try:
        parts = pos_str.split('.')
        prefix = ".".join(parts[:-1])
        last_num = int(parts[-1])
        return (prefix, last_num)
    except:
        return ("9.9", 999)
def resolve_optimizer_model_fallback(sn, optimizer_asset_map):  # 備援型號查詢

    # =========================
    # Optimizer Model Fallback
    #
    # 當主要型號來源查詢失敗時，
    # 從 optimizer asset map
    # 取得型號資訊作為備援。
    # =========================

    model = optimizer_asset_map.get(sn, {}).get("model", "未知")
    return str(model).split("-")[0]
def get_site_data():

    # =========================
    # SolarEdge Inspection Pipeline
    #
    # 功能：
    #   執行完整巡檢流程：
    #
    #   1. 初始化登入與工作環境
    #   2. 補齊缺失 Site Metadata
    #   3. 巡檢所有案場
    #   4. 擷取並解析 Site Packets
    #   5. 建立設備 Mapping 與狀態分析
    #   6. 同步結果至 Excel
    #
    # 回傳：
    #   collector（巡檢結果摘要）
    # =========================

    global df_input

    # =========================
    # Stage 1：Initialize Environment
    # =========================
    home_url = login_to_solaredge(driver)
    wait = WebDriverWait(driver, 15)

    url_pattern = detect_url_pattern(
        driver,
        wait,
        home_url
    )

    # =========================
    # Stage 2：Recover Missing Site Metadata
    # =========================
    missing_id_df = df_input[
        df_input['案場ID'].isna()
        | (df_input['案場ID'].str.strip() == "")
        | (df_input['案場ID'].str.lower() == "nan")
    ]

    missing_id_sites = (
        missing_id_df['案場名稱']
        .unique()
    )

    fetched_name_pool = {}
    fetched_id_pool = {}
    fetched_address_pool = {}
    broken_sites = []
    collector = init_collector()

    if len(missing_id_sites) > 0:

        print(f"發現 {len(missing_id_sites)} 間缺失案場ID，開始補資料")

        fetched_name_pool, fetched_id_pool, fetched_address_pool, broken_sites = \
            resolve_missing_site_info(
                missing_id_sites,
                driver,
                wait
            )
        update_excel_from_lobby_results(
            df_input=df_input,
            input_file=input_file,
            fetched_name_pool=fetched_name_pool,
            fetched_id_pool=fetched_id_pool,
            fetched_address_pool=fetched_address_pool,
            broken_sites=broken_sites
        )

        for b_site in broken_sites:
            add_missing_id(collector, b_site, "無ID")


    else:
        print("檢查完畢！Excel 所有案場均已有 ID 鑰匙，跳過大廳快搜階段。")

    # 💡 提取所有不重複的案場名稱
    df_input = pd.read_excel(input_file, dtype=str)
    unique_sites = df_input['案場名稱'].unique()

    # =========================
    # Stage 3：Site Inspection Loop
    # =========================

    for site_name in unique_sites:      #計時器防錯誤時間(視情況注解))

        # loop_start_check_time = datetime.now()
        # if loop_start_check_time.hour >= 21:
        #     print(f"\n [計時器死線引爆] 強制安全煞車，收工！")
        #     break


        # TODO:
        # 預留重新巡檢機制。
        # 後續設備更換或 Mapping 更新後，
        # 可透過 rerun=True 重新執行當前案場。
        rerun = True

        while rerun:
            rerun = False

            print(f"\n【大迴圈切場】目前目標案場：{site_name}")
            wait = WebDriverWait(driver, 15)

            # =========================
            # Step：Load Site Information
            # =========================
            #進入工作區最後檢查是否有缺失以利後續作業
            df_current_site = df_input[df_input['案場名稱'] == site_name]
            first_row = df_current_site.iloc[0]
            site_id = str(first_row['案場ID']).strip() if pd.notna(first_row['案場ID']) else ""
            site_address = str(first_row['地址']).strip() if pd.notna(first_row['地址']) else ""


            if site_id == "" or site_id.lower() == "nan":   # 如果連大廳盲搜都沒補到這間的 ID，防呆跳過，不盲目直達
                print(f"[跳過案場] 【{site_name}】在 Excel 與大廳中皆查無 ID，無法發動空投！")
                add_missing_id(collector, site_name, site_id)
                continue

            # =========================
            # Step：Navigate to Site Workspace
            # =========================

            try:
                target_url = url_pattern.format(site_id)
                driver.get_log('performance')
                print(f"網址直達 ➔ {target_url}")
                driver.get(target_url)
            except Exception as e:
                print(
                    f" [網址直達失敗] "
                    f"【{site_name}】site_id={site_id}：{e}"
                )

                add_error(
                    collector,
                    site_name,
                    site_id,
                    stage="URL_NAVIGATION_FAILED",
                    reason="網址直達失敗"
                )
                continue

            # =========================
            # Step：Validate Site Identity
            # =========================
            site_id, site_address = identity_gate(
                driver,
                wait,
                home_url,
                site_name,
                site_id
            )

            if not site_id:
                continue

            # =========================
            # Step：Open Site Workspace
            # =========================
            target_url = url_pattern.format(site_id)
            driver.get(target_url)

            time.sleep(5)  # 這裡是合理的，讓 SPA 出封包

            # =========================
            # Step：Capture Site Packets
            # =========================

            performance_logs = collect_site_performance_logs(driver, site_name)


            physical_packet, site_structure_packet, energy_packet = capture_site_packets(performance_logs, site_id)

            if not (physical_packet and site_structure_packet and energy_packet):
                print("錯誤：無法取得完整封包，跳過此案場。")
                continue

            # =========================
            # Step：Build Site Mapping
            # =========================

            # 建立設備索引與設備狀態
            SITE_PACKET_CONTAINER[site_name] = site_structure_packet
            optimizer_asset_map, optimizer_status_map = build_site_mapping(
                physical_packet,
                site_structure_packet,
                energy_packet
            )


            new_rows = build_site_rows(
                site_name,
                site_id,
                site_address,
                physical_packet,
                optimizer_asset_map,
                optimizer_status_map
            )

            # =========================
            # Step：Persist Site Result
            # =========================

            # 更新 Sites.xlsx 並記錄成功案場
            df_input = save_site_result(
                df_input,
                input_file,
                site_name,
                new_rows,
                collector
            )

    # =========================
    # Stage 4：Finish Inspection
    # =========================

    print("\n[全線結束] 全面巡檢與基準測試完畢")
    return collector
if __name__ == "__main__":
    print("\n開始執行全案場巡檢...")
    report_data = get_site_data()
    # 這裡用變數去接收 get_site_data 回傳的數據

    print("\n[報表生成階段] 正在讀取最新數據並轉化為維運 PDF...")

    df_final = pd.read_excel('Sites.xlsx', dtype=str)

    export_all_sites_pdf(df_final, SITE_PACKET_CONTAINER, report_data)

    print("所有流程順利完成！")