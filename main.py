import pandas as pd
import re
import json
import time
from selenium import webdriver
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from datetime import datetime

# 🚀 運維自動化引擎初始化配置
chrome_options = Options()

# 🌐 確保 Session 持續性：
# 啟用 detach 模式防止 WebDriver 腳本執行完畢後流暢關閉瀏覽器。
# 允許維修人員在自動化巡檢結束後，能直接接管當前網頁畫面，進行人工複查或二次調試，提升 Ops 協作效率。
chrome_options.add_experimental_option("detach", True)

# 📡 傳輸層響應攔截（Response Interception）核心配置：
# 注入 CDP (Chrome DevTools Protocol) 的效能監聽偏好設定，強制捕獲全量 Network Performance Logs。
# 繞過 SPA (單頁應用) 脆弱且不可靠的 DOM/UI 結構，直接在水庫端攔截後台實時的 JSON 數據封包。
chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

# 🤖 實例化自動化驅動底座
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
# 📊 資料存取層（Data Persistence Layer）初始化
input_file = 'Sites.xlsx'
# 🗃️ 確保資料完整性（Data Integrity）：
# 強制將 Excel 來源端欄位全數以字串（String）型態載入 DataFrame。
# 徹底杜絕 Pandas 預設對設備序號（如純數字或帶 0 序號）與通訊 ID 進行錯誤的數值型態轉換，防止髒資料污染。
df_input = pd.read_excel(input_file, dtype=str)

print("============ [開始自動化巡檢] ============")


def check_address_format(address_str):  #檢查地址格式模塊
        if not address_str or address_str.lower() == "nan":
            return False, "空值"
        address_str = str(address_str).strip()
        pattern_decimal = r'^-?\d+(\.\d+)?$'
        is_decimal = bool(re.match(pattern_decimal, address_str))
        pattern_plus_code = r'^(?:[23456789CDEFGHJKLMNPQRTVWXYZ]{4}|[23456789CDEFGHJKLMNPQRTVWXYZ]{6}|[23456789CDEFGHJKLMNPQRTVWXYZ]{8})\+[23456789CDEFGHJKLMNPQRTVWXYZ]{2,5}$'
        is_plus_code = bool(re.match(pattern_plus_code, address_str, re.IGNORECASE))
        taiwan_keywords_pattern = r'(台灣|Taiwan|縣|市|鄉|鎮|區|路|街|巷|弄|村|里|號)'
        is_taiwan_address = len(address_str) >= 5 and bool(re.search(taiwan_keywords_pattern, address_str))
        if is_decimal or is_plus_code or is_taiwan_address:
            return True, "正常"
        else:
            return False, f"格式不符({address_str})"
def extract_devices(pdm):  #抓逆變器序號序列 優化器序號 (純)
    results = []
    inverters_pocket = pdm.get('inverters', {})
    optimizers_pocket = pdm.get('optimizers', {})

    #這裡的 'strings' 是因為 API 欄位叫 strings，這不能亂改
    strings_pocket = pdm.get('strings', {})

    for string_id, string_data in strings_pocket.items():

        #拿著這串資料去抓它屬於哪台逆變器 ID
        target_inv_id = string_data.get('inverterId')
        inv_info = inverters_pocket.get(target_inv_id, {})
        inv_sn = inv_info.get('serialNumber', '未知逆變器序號')
        inv_seq = inv_info.get('sequenceNumber', '未知')

        # 把這一串下面綁定的所有優化器 ID 抓出來
        for opt_id in string_data.get('optimizerIds', []):
            opt_info = optimizers_pocket.get(opt_id, {})
            opt_sn = opt_info.get('serialNumber', '未知優化器序號')
            results.append({
                '逆變器編號': str(inv_seq),
                '逆變器序號': str(inv_sn),
                '優化器序號': str(opt_sn)
            })

    return results
def fetch_site_id_from_lobby(driver, wait, target_name):  #大廳封包攔截器
    print(f"📡 [GPS 導航] 正在重新定位案場: {target_name}")
    try:
        search_input = wait.until(EC.element_to_be_clickable((By.ID, "nameFilter-input-field")))
        search_input.clear()
        search_input.send_keys(target_name)
        search_input.send_keys(Keys.ENTER)
        time.sleep(4)
        logs = driver.get_log('performance')
        for entry in logs:
            log_json = json.loads(entry['message'])['message']

            #只抓取「網路回應(responseReceived)」且網址包含「searchSites」的封包
            if log_json['method'] == 'Network.responseReceived' and 'searchSites' in log_json['params']['response']['url']:
                #透過 CDP 指令直接讀取該封包的 Response Body (JSON 內容)
                resp = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': log_json['params']['requestId']})
                data = json.loads(resp['body'])
                for site in data.get('page', []):
                    if site.get('name') == target_name:
                        return str(site.get('solarFieldId')), str(site.get('address'))
    except Exception as e:
        print(f"❌ 導航失敗: {e}")
    return None, None
def parse_children_tree(node, current_inv="未知逆變器", current_str="未知組串"):  #步驟2挖children模塊(後續跟步驟3合併)
    layout_map = {}
    node_type = node.get('type', '')
    node_name = node.get('name', '')
    if node_type == 'INVERTER':
        current_inv = node.get('serial', node_name)
    elif node_type == 'STRING':
        current_str = node_name
    if node_type == 'OPTIMIZER' and node.get('serial'):
        if node.get('status') == 'ACTIVE':
            sn = str(node['serial']).strip()
            layout_map[sn] = {
                "逆變器": current_inv,
                "組串": current_str,
                "位置名稱": node_name
            }
    if 'children' in node and isinstance(node['children'], list):
        for child in node['children']:
            layout_map.update(parse_children_tree(child, current_inv, current_str))

    return layout_map
def execute_step2_get_layout(logs, site_id):  #攔截佈局封包
    for entry in logs:
        try:
            log_json = json.loads(entry['message'])['message']
            if log_json['method'] == 'Network.responseReceived':
                url = log_json['params']['response']['url']
                if 'include-optimizers' in url and str(site_id) in url:
                    request_id = log_json['params']['requestId']
                    return url, request_id
        except:
            continue
    return None, None
def is_real_active_optimizer(node):  #檢查發電中的優化器
    props = node.get("properties", {})
    count = props.get("panelsCount")
    return count is not None and isinstance(count, int) and count > 0


def build_optimizer_df(pdm, site_name, site_id, address, inverters): # 1. 這裡加參數
        inv_map = {
            inv_id: {"sn": v.get('serialNumber'), "seq": v.get('sequenceNumber')}
            for inv_id, v in inverters.items()
    }
        new_rows = []
        if not pdm.get('optimizers'):
            print(f"❌ [致命除錯] 案場 {site_name} 的 PDM 內沒有 'optimizers' 節點！請檢查封包結構。")
            return pd.DataFrame() # 回傳空的
        print(f"DEBUG: 正在處理案場 {site_name}")
        print(f"DEBUG: 包含優化器數量: {len(pdm.get('optimizers', {}))}")
        print(f"DEBUG: 包含組串數量: {len(pdm.get('strings', {}))}")
        for str_id, str_data in pdm.get('strings', {}).items():
            inv_id = str_data.get('inverterId')
            inv_info = inv_map.get(inv_id, {"sn": "未知", "seq": "未知"})
            for opt_id in str_data.get('optimizerIds', []):
                opt_info = pdm.get('optimizers', {}).get(opt_id)
                if opt_info:
                    new_rows.append({
                        '案場名稱': site_name,
                        '案場ID': site_id,
                        '地址': address,
                        '優化器序列': "",
                        '優化器序號': opt_info.get('serialNumber'),
                        '逆變器序號': inv_info['sn'],
                        '逆變器編號': str(inv_info['seq'])
                    })

        return pd.DataFrame(new_rows)

def get_site_data():
    global df_input

    #登入到SolarEdge官網
    driver.get("https://monitoring.solaredge.com/mfe/auth/")
    print("🔑 請在瀏覽器完成登入 ")
    print("⚠️ 確保畫面乾淨後，即可回到這裡按下 [Enter] 開放傳輸帶...")
    input()


    #記錨點區
    home_url = driver.current_url
    print(f"📍 [原座標記錄成功] 目前大廳主頁面網址為: {home_url}")
    print("\n🚀 強行啟動網頁全螢幕化...")
    driver.maximize_window()    #防止視窗太小不顯示UI界面
    time.sleep(0.5)
    print("🔄 [優雅重整] 發動網頁原生刷新，逼迫大廳列表重頭加載渲染...")
    driver.refresh()            #不管大小一律放大重新整理
    time.sleep(5)
    print("\n🕵️‍♂️ 啟動第一階段：動態偵測網址格式...")
    wait = WebDriverWait(driver, 15)
    url_pattern = ""    #清空準備塞資料

    try:    #這裡前往預覽案場的Digital-twin(數位分身-電站佈局圖)來偵測今日網址格式
        print("🔍 正在尋找大廳列表中第一個案場的點擊超連結...")

        first_site_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'MuiDataGrid')]//a[contains(@href, 'siteId=')]")))
        captured_first_name = first_site_link.text  #直接抓預覽案場來操作
        print(f"🎯 成功鎖定首頁第一間案場：【{captured_first_name}】，正在點擊前進...")
        first_site_link.click()

        print("🔮 正在 Dashboard 內尋找 Layout (數位分身) 的工作頁面入口...")
        layout_menu_btn = wait.until(EC.element_to_be_clickable((By.XPATH,
            "//span[contains(text(), 'Layout')] | //span[contains(text(), '物理配置')] | //span[contains(text(), '佈局')] | //a[contains(@href, 'digital-twin')]"
        )))
        layout_menu_btn.click()
        print("🚀 成功降落 Digital-twin 工作介面！")
        wait.until(EC.url_contains("siteId="))  #用EC偵測到網址變形直接退出

        sample_url = driver.current_url
        if "siteId=" in sample_url:     #利用 driver.current_url 即時抓取此時此刻網址列上最真實的 URL 路標
            url_pattern = sample_url.split("siteId=")[0] + "siteId={}"  #將siteId剝離，重構為迴圈的萬用範本
            print(f"✨ [動態偵測成功] 本日數位分身範本網址已解鎖：{url_pattern}")
            print(f"🔄 第一階段結束，利用原座標返回大廳...")
            driver.get(home_url)    #回錨點
            time.sleep(5)
        else:
            url_pattern = "https://monitoring.solaredge.com/one#/residential/digital-twin?siteId={}"
            print(f"⚠️ 網址列未如預期變形，已自動採用保底格式：{url_pattern}")

    except Exception as e:
        print(f"⚠️ 第一階段挺進Layout失敗（將使用保底格式繼續巡檢）：{e}")
        url_pattern = "https://monitoring.solaredge.com/one#/residential/digital-twin?siteId={}"
        print(f"🔄 [異常保底] 強制利用原座標撤退回大廳...")
        driver.get(home_url)    #回錨點
        time.sleep(3)


    missing_id_df = df_input[df_input['案場ID'].isna() | (df_input['案場ID'].str.strip() == "") | (df_input['案場ID'].str.lower() == "nan")]
    missing_id_sites = missing_id_df['案場名稱'].unique()

    #補其缺失ID的案場 方便後續直接跳轉
    if len(missing_id_sites) > 0:
        print(f"🔍 發現有 {len(missing_id_sites)} 間案場缺失身分證，大廳快搜盲搜啟動...")
        fetched_name_pool = {}
        fetched_id_pool = {}
        fetched_address_pool = {}
        broken_sites = []

        for target_site in missing_id_sites:
            if pd.isna(target_site) or str(target_site).strip() == "" or str(target_site).lower() == "nan":
                continue
            print(f"\n🔎 大廳快搜 ➔ 【{target_site}】")


            try:    #定位搜尋框並清空舊字串
                search_input = wait.until(EC.element_to_be_clickable((By.ID, "nameFilter-input-field")))
                search_input.send_keys(Keys.CONTROL, "a")
                search_input.send_keys(Keys.BACKSPACE)
                time.sleep(0.5)

                site_str = str(target_site).strip()
                if len(site_str) > 2:
                    search_input.send_keys(site_str[0:2])  #輸入前2字，不觸發前端3字的關鍵字要求
                    time.sleep(0.3)
                    driver.get_log('performance')       #物理清空快取 防止預覽案場封包灌進來
                    print(f"⚡ [滿三字前預警] 已在輸入第2個字【{site_str[1]}】後，強行固化極淨防線...")
                    search_input.send_keys(site_str[2:])    #補齊剩餘字串並送出
                    search_input.send_keys(Keys.ENTER)
                else:   # 🚨 異常防線：若 searchSites 返回空陣列（平台查無此案場），啟動安全熔斷
                    print(f"\n❌ [資料源污染] 案場【{target_site}】名稱長度僅有 {len(site_str)} 字，未達官網 3 字搜尋門檻！")
                    print(f"🛑 [自動化安全攔截] 將從本次巡檢名單中剔除該案場，避免引發後續翻車。")
                    broken_sites.append(target_site)
                    continue
                print(f"⏳ 等待大廳 searchSites 封包加載傳輸...")
                time.sleep(4.5)
                lobby_logs = driver.get_log('performance')
                captured_id = None
                captured_addr = None


                for entry in lobby_logs:    #撈ID跟地址的邏輯在這裡
                    log_json = json.loads(entry['message'])['message']
                    if log_json['method'] == 'Network.responseReceived':
                        url = log_json['params']['response']['url']
                        request_id = log_json['params']['requestId']

                        if 'searchSites' in url:
                            try:
                                resp_body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                                response_json = json.loads(resp_body['body'])

                                if 'page' in response_json and isinstance(response_json['page'], list) and len(response_json['page']) > 0:
                                    site_data_packet = response_json['page'][0]
                                    captured_name = str(site_data_packet.get('name', '')).strip()
                                    captured_id = str(site_data_packet.get('solarFieldId', '')).strip()
                                    captured_addr = str(site_data_packet.get('address', '')).strip()
                                    break # 抓到了就提早熄火
                            except Exception as e:
                                pass
                if captured_id and captured_id != "" and captured_id != "None":
                    is_valid, msg = check_address_format(captured_addr)
                    if not is_valid:
                        print(f"⚠️ [數據污染警告] 案場【{target_site}】抓到的地址有問題: {msg}！請聯繫官方建置！")
                    fetched_name_pool[target_site] = captured_name
                    fetched_id_pool[target_site] = captured_id
                    fetched_address_pool[target_site] = captured_addr
                    print(f"🎯 [大廳封包攔截成功] 案場【{target_site}】")
                    print(f"   ➔ 官網全名: {captured_name}")
                    print(f"   ➔ 實時 ID : {captured_id}")
                    print(f"   ➔ 實時地址: {captured_addr} {'(✅格式正確)' if is_valid else ' (❌格式異常)'}")
                else:
                    print(f"\n⚠️ [大廳快搜失敗] 案場【{target_site}】的 searchSites 封包未返回有效 ID！")
                    print(f"🛑 [自動化安全攔截] 將從本次巡檢名單中剔除該案場。")
                    broken_sites.append(target_site)
                    continue  # 直接結束這一輪，換下一間案場，不往下走

            except Exception as search_err:
                print(f"\n❌ [大廳快搜失敗] 無法定位案場【{target_site}】，原因：{search_err}")
                print(f"🛑 [自動化安全攔截] 將從本次巡檢名單中剔除該案場，避免引發後續翻車。")
                broken_sites.append(target_site)


        if broken_sites:    #防止寫入錯誤案場
            print("\n🧹 啟動資料庫清理機制，正在將錯誤案場從 Excel 中移除...")
            df_disk = pd.read_excel(input_file, dtype=str)      #即時讀取，而非直接操作內存，確保多執行緒或外部異動時的資料一致性
            df_disk = df_disk[~df_disk['案場名稱'].isin(broken_sites)]      #利用 ~ 運算子發動排除法，將大廳查無此案場、或長度小於 3 字的「髒資料」從物理硬碟中徹底剔除
            df_disk.to_excel(input_file, index=False)
            df_input = df_disk      # 同步更新內存 DataFrame，確保後續迴圈狀態解耦
            print("💾 [Excel 錯誤剔除完畢] 資料庫已即時落盤。")
            print("=" * 60)
            print("🚨 【人工介入通知】🚨")
            for b_site in broken_sites:
                print(f"❌ 案場：【{b_site}】已被強制下架並從表格中移除！")
            print("💡 請重新確認 Excel 內上述案場的『名稱』是否與 SolarEdge 官網完全一致！")
            print("=" * 60 + "\n")


        if fetched_id_pool:     #將ID地址寫入Excel
            print("\n💾 正在將大廳封包搜集到的新 ID 與 案場地址 同步寫入 Excel...")
            for s_name, s_id in fetched_id_pool.items():
                corr_name = fetched_name_pool.get(s_name, s_name)
                # 僅鎖定「案場ID為空」且「名稱完全匹配」的列，避免誤傷其餘已有正確金鑰的既有資料
                target_rows_mask = df_input['案場ID'].isna() & (df_input['案場名稱'] == s_name)
                df_input.loc[target_rows_mask, '案場ID'] = s_id

                s_addr = fetched_address_pool.get(s_name, "")
                if s_addr:
                    df_input.loc[target_rows_mask, '地址'] = s_addr

                # 若發現 Excel 的原始名稱與大廳封包返回的官網名稱不同
                # 發動自動校正，以官方資料源（Single Source of Truth）強制洗白表格，完成動態數據清洗
                if corr_name != s_name:
                    print(f"🔄 [名稱校正發動] 表格舊名【{s_name}】➔ 官網權威全名【{corr_name}】")
                    df_input.loc[target_rows_mask, '案場名稱'] = corr_name

            df_input.to_excel(input_file, index=False)
            print("✅ [資料庫補辦完成] 缺失案場的 ID 與地址已永久綁定更新！")

            try:
                # 大廳快搜階段結束，強行清空網頁搜尋框並敲擊 Enter，使 SolarEdge 大廳列表完全復原
                # 防止殘留關鍵字導致大廳畫面鎖死在特定案場，確保第三階段「網址直達流」能順利發動
                search_input = wait.until(EC.element_to_be_clickable((By.ID, "nameFilter-input-field")))
                search_input.send_keys(Keys.CONTROL, "a")
                search_input.send_keys(Keys.BACKSPACE)
                search_input.send_keys(Keys.ENTER)
                time.sleep(3)
            except:
                pass
    else:
        print("🎉 檢查完畢！Excel 所有案場均已有 ID 鑰匙，跳過大廳快搜階段。")

    # 💡 提取所有不重複的案場名稱
    df_input = pd.read_excel(input_file, dtype=str)
    unique_sites = df_input['案場名稱'].unique()

    for site_name in unique_sites:      #計時器防錯誤時間(暫時先關))
        # loop_start_check_time = datetime.now()
        # if loop_start_check_time.hour >= 21:
        #     print(f"\n🚨 [計時器死線引爆] 強制安全煞車，收工！")
        #     break

        print(f"\n🚀 【大迴圈切場】目前目標案場：{site_name}")
        wait = WebDriverWait(driver, 15)

        #進入工作區最後檢查是否有缺失以利後續作業
        df_current_site = df_input[df_input['案場名稱'] == site_name]
        first_row = df_current_site.iloc[0]
        site_id = str(first_row['案場ID']).strip() if pd.notna(first_row['案場ID']) else ""
        first_sn = str(first_row['優化器序號']).strip() if pd.notna(first_row['優化器序號']) else ""
        site_address = str(first_row['地址']).strip() if pd.notna(first_row['地址']) else ""
        is_valid, msg = check_address_format(site_address)
        if not is_valid:
            print(f"⚠️ 警告：案場 '{site_name}' 的地址異常 ({msg})！(將繼續執行後續 ID 邏輯)")
        if site_id == "" or site_id.lower() == "nan":   # 如果連大廳盲搜都沒補到這間的 ID，防呆跳過，不盲目直達
            print(f"❌ [跳過案場] 【{site_name}】在 Excel 與大廳中皆查無 ID，無法發動空投！")
            continue

        try:
            target_url = url_pattern.format(site_id)
            driver.get_log('performance')
            print(f"🏎️  網址直達 ➔ {target_url}")
            driver.get(target_url)
        except Exception as e:
            print(f"❌ 網址直達失敗：{e}")
            continue

        # 切場抵達後，抓封包前的這一刻清空快取
        time.sleep(10)
        logs = driver.get_log('performance')
        print(f"DEBUG: 抓到 {len(logs)} 筆 performance logs")
        current_fetched_id = site_id
        current_fetched_address = str(first_row['地址']).strip() if pd.notna(first_row['地址']) else ""
        current_active_optimizers = []
        print(f"🔎 開始撈取 【{site_name}】 的實時乾淨數據封包...")
        if not logs:
            print("⚠️ 警告：沒有抓到任何 performance logs，這通常是網址沒有觸發網路請求，或讀取時機太晚。")

        # if first_sn != "" and first_sn != "nan":      #有序號路線(施工中)

        # else:   #無序號路線(施工中)
            print(f"🌐 執行 ➔ 「新車初始化建檔模式」")
            packet_physical = None
            packet_siteStructure = None
            packet_energy_value = None

            for entry in logs:
                try:
                    log_json = json.loads(entry['message'])['message']
                    if log_json['method'] == 'Network.responseReceived':
                        url = log_json['params']['response']['url']
                        request_id = log_json['params']['requestId']

                        if str(site_id) in url:
                            if 'layout/physical/site/' in url and not packet_physical:
                                resp_body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                                packet_physical = json.loads(resp_body['body'])
                                print("🏢 成功捕獲 ➔ [步驟1：實體佈局/乾淨序號源封包]")
                            elif 'include-optimizers' in url and not packet_siteStructure:
                                    resp_body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                                    packet_siteStructure = json.loads(resp_body['body'])
                                    print("🎯 成功捕獲 ➔ [步驟2：siteStructure/Children關係封包]")
                            elif 'by-inverter' in url and not packet_energy_value:
                                    resp_body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                                    raw_body = json.loads(resp_body['body'])
                                    if raw_body is not None:
                                        packet_energy_value = raw_body
                                        print("⚡ 成功捕獲 ➔ [步驟3：發電量封包] (不論是否有 0 發電量都收)")
                except Exception as cdp_err:
                        print(f"⚠️ 讀取封包內文失敗: {cdp_err}")


            if not packet_siteStructure:
                print(f"❌ 警告：在案場 {site_name} 沒有抓到關鍵的邏輯結構封包，跳過。")
                continue
            else:
                print(f"✅ 成功抓取案場 {site_name} 的核心結構數據封包。")

            structure_node = packet_siteStructure.get('siteStructure', packet_siteStructure)
            real_web_name = structure_node.get('name', '').strip()                                                        #檢查是否在對的案場

            if real_web_name and real_web_name != site_name.strip():
                print(f"⚠️ [身分錯亂引爆] 預期到訪: {site_name} ➔ 實際到訪: {real_web_name}！")
                print(f"🧹 清除 Excel 錯誤 ID，啟動大廳緊急導航修正...")
                df_input.loc[df_input['案場名稱'] == site_name, '案場ID'] = pd.NA
                df_input.to_excel(input_file, index=False)
                driver.get(home_url)
                time.sleep(5)
                new_id, new_addr = fetch_site_id_from_lobby(driver, wait, site_name)

                if new_id:
                    print(f"✨ 成功修正 ID 為: {new_id}，寫入 Excel...")
                    df_input.loc[df_input['案場名稱'] == site_name, '案場ID'] = new_id
                    df_input.loc[df_input['案場名稱'] == site_name, '地址'] = new_addr
                    df_input.to_excel(input_file, index=False)
                    print(f"🔄 已重新綁定正確 ID，本輪跳過，下一輪迴圈將會精準空投。")
                    continue
                else:
                    print(f"❌ 無法修正 ID，該案場可能已下架，跳過此案場。")
                continue                                                                                                    #處理錯亂ID案場

                                         #(施工中)



    print("\n🏁 [全線結束] 全面空投巡檢與基準測試完畢")

if __name__ == "__main__":
    get_site_data()