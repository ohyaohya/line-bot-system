# -*- coding: utf-8 -*-
import os, math, csv, json, requests, time # 匯入必要模組
from flask import Flask, request, jsonify # 匯入 Flask 相關
from flask_cors import CORS # 處理跨來源請求
from functools import lru_cache # 用於快取 Geocoding 結果

# ---------------------------
# 基本設定
# ---------------------------
app = Flask(__name__)
CORS(app) # 允許所有來源的前端呼叫

# --- 路徑設定 ---
# 假設 data 資料夾與 app.py 在同一層級 (例如都在 backend 資料夾內)
DATA_DIR = "data" 
GEOCODE_CACHE_FILE = os.path.join(DATA_DIR, "geocode_cache.json")
# 【【【重要】】】 假設 CSV 檔案與 app.py 在同一層級
STORES_CSV_FILE = "全國5大超商資料集.csv" 
# 預處理後的台北便利商店檔案路徑
STORES_TAIPEI_CSV = os.path.join(DATA_DIR, "stores_taipei.csv") 
os.makedirs(DATA_DIR, exist_ok=True) # 確保 data 目錄存在

# --- API 金鑰與網址 ---
# 【【【關鍵！！！】】】 請在此處貼上您安全的 Google API 金鑰
# (正式部署時，強烈建議改用環境變數！)
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "【請在這裡貼上您安全的Google_API金鑰】")
# --- --- --- --- --- --- --- --- --- --- --- ---
GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"
TAIPEI_POLICE_API_URL = "https://data.taipei/api/v1/dataset/a90ae184-c39e-4242-b2d6-d7a0403c0632?scope=resourceAquire"

# 預處理時最多 Geocode 多少筆便利商店 (None=不限制，設數字可加速啟動)
TAIPEI_BUILD_LIMIT = None 

# ---------------------------
# 公用函式 (您的版本)
# ---------------------------
def get_distance(lat1, lon1, lat2, lon2):
    R = 6371.0; try: lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2]); dLat = math.radians(lat2 - lat1); dLon = math.radians(lon2 - lon1); a = math.sin(dLat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2)**2; return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)); except: return float("inf")

def normalize_text(t: str) -> str:
    if not t: return ""; t = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in t); return t.replace(" ", "").replace("t", "").lower()

# ---------------------------
# Geocoding 快取 (您的版本，稍作優化)
# ---------------------------
try:
    with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8-sig") as f: # 改用 utf-8-sig
        GEO_CACHE = json.load(f)
    print(f"✅ Geocode 快取載入成功，共 {len(GEO_CACHE)} 筆記錄。")
except Exception:
    GEO_CACHE = {}; print("ℹ️ 未找到或無法讀取 Geocode 快取檔案，將建立新的快取。")

def save_cache():
    try:
        with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(GEO_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e: print(f"❌ 儲存 Geocode 快取失敗: {e}")

# ---------------------------
# 智慧 Geocode（您的版本，稍作優化）
# ---------------------------
@lru_cache(maxsize=512) # 使用內建快取，增加大小
def geocode(address: str):
    if not address: return None
    address = address.strip() # 增加 strip
    if address in GEO_CACHE: return GEO_CACHE[address]
    
    # 檢查金鑰
    api_key_valid = GOOGLE_MAPS_API_KEY and not GOOGLE_MAPS_API_KEY.startswith("【請在這裡")
    if not api_key_valid:
        if not hasattr(geocode, 'api_key_warning_printed'): print("❌ Geocode 錯誤：未設定有效 Google API 金鑰"); geocode.api_key_warning_printed = True
        return None

    # Step 1: 清理地址 (您的邏輯)
    clean = address.replace("　", "").replace(" ", "")
    for token in ["地下一層","地下1樓","地下二樓","B1","B2","B3","1樓","2樓","3樓","4樓","5樓","6樓"]: clean = clean.replace(token, "")
    for sep in ["、", "，", ","]: 
        if sep in clean: clean = clean.split(sep)[0]
    clean = clean.replace("之", "")
    
    # Step 2: 呼叫 Google API (您的邏輯)
    def query_google(addr):
        if not addr: return None # 增加空地址檢查
        print(f"  Geocoding API Call: {addr[:40]}...") # 增加日誌
        try:
            r = requests.get(GEOCODING_API_URL, params={ "address": addr, "key": GOOGLE_MAPS_API_KEY, "language": "zh-TW", "region": "tw", "components": "country:TW" }, timeout=8)
            r.raise_for_status(); data = r.json()
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]; return {"lat": loc["lat"], "lng": loc["lng"]}
            else: # 更詳細的錯誤日誌
                status = data.get('status', '未知'); error_msg = data.get('error_message', '')
                if status == 'ZERO_RESULTS': print(f"  ⚠️ Geocode 查無結果 for: {addr}")
                else: print(f"  ❌ Geocode 失敗 for: {addr} ({status} - {error_msg})")
        except requests.exceptions.Timeout: print(f"  ❌ Geocode 錯誤: {addr} => 請求超時")
        except requests.exceptions.RequestException as e: print(f"  ❌ Geocode 錯誤: {addr} => 網路請求失敗 - {e}")
        except Exception as e: print(f"  ❌ Geocode 錯誤: {addr} => 未預期錯誤 - {e}")
        return None

    # Step 3: 先試原始 → 再試簡化 (您的邏輯)
    coords = query_google(address)
    if not coords and clean != address: # 只有在清理後地址不同時才重試
        print(f"  > 原始地址失敗，嘗試清理後地址: {clean[:40]}...")
        coords = query_google(clean)
        if coords: print(f"  ✅ 簡化後成功!")
        else: print(f"  ⚠️ 簡化後仍失敗")
    elif coords: print(f"  ✅ Geocode 成功 (原始)")
    else: print(f"  ⚠️ Geocode 失敗 (原始)") # 如果原始地址就是空的或查詢失敗

    GEO_CACHE[address] = coords # 無論成功(coords)或失敗(None)都存入快取
    save_cache()
    return coords

# ---------------------------
# 品牌偵測 (您的版本)
# ---------------------------
def detect_brand(company: str, name: str) -> str: # 加入 name 參數
    text_to_check = f"{company} {name}".lower().replace(" ", "").replace("股份有限公司","")
    if not text_to_check: return "其他"
    if any(k in text_to_check for k in ["統一超商", "7-eleven", "seven-eleven"]): return "7-ELEVEN"
    if "全家" in text_to_check or "familymart" in text_to_check: return "全家"
    if "全聯" in text_to_check or "px mart" in text_to_check or "pxmart" in text_to_check: return "全聯"
    if "萊爾富" in text_to_check or "hi-life" in text_to_check: return "萊爾富"
    if "來來超商" in text_to_check or "ok mart" in text_to_check or "okmart" in text_to_check: return "OK mart"
    return "其他"

# ---------------------------
# 產生台北店舖檔 (您的版本，修正路徑和錯誤處理)
# ---------------------------
def ensure_taipei_stores():
    print("\n⏳ 開始預處理台北市便利商店資料...")
    start_time = time.time()
    
    # 【修正】直接使用檔名，假設 CSV 與 app.py 同目錄
    if not os.path.exists(STORES_CSV_FILE): 
        print(f"❌ 錯誤：找不到來源 CSV 檔案 '{STORES_CSV_FILE}'")
        return {"ok": False, "error": "Source CSV not found"}
        
    rows = []
    try:
        with open(STORES_CSV_FILE, "r", encoding="utf-8-sig", newline='') as f: # 使用 utf-8-sig
             # 自動偵測格式
            try: sample = f.read(4096); dialect = csv.Sniffer().sniff(sample, delimiters=',\t'); f.seek(0); reader = csv.DictReader(f, dialect=dialect)
            except csv.Error: print("⚠️ CSV Sniffer 失敗，使用預設格式"); f.seek(0); reader = csv.DictReader(f)
            
            # 檢查必要欄位
            required_cols = ["分公司地址", "分公司名稱", "公司名稱"]
            if not reader.fieldnames or not all(col in reader.fieldnames for col in required_cols):
                 print(f"❌ 錯誤：CSV 缺少必要欄位 {required_cols}。偵測到的欄位: {reader.fieldnames}")
                 return {"ok": False, "error": "Missing required CSV columns"}

            for i, row in enumerate(reader):
                try:
                    addr = (row.get("分公司地址") or "").strip()
                    name = (row.get("分公司名稱") or "").strip() 
                    company = (row.get("公司名稱") or "").strip()
                    display_name = name if name else company # 無分公司名則用公司名
                    
                    # 篩選台北市
                    if addr and display_name and ("台北市" in addr or "臺北市" in addr):
                        brand = detect_brand(company, name)
                        rows.append({"brand": brand, "name": display_name, "address": addr})
                except Exception as row_err: print(f"⚠️ 處理 CSV 第 {i+2} 行時出錯: {row_err}")

    except FileNotFoundError: print(f"❌ 錯誤：找不到來源 CSV 檔案 '{STORES_CSV_FILE}'"); return {"ok": False, "error": "Source CSV not found"}
    except UnicodeDecodeError: print(f"❌ 錯誤: CSV 檔案 '{STORES_CSV_FILE}' 編碼錯誤，請確保為 UTF-8 或 UTF-8-SIG。"); return {"ok": False, "error": "CSV encoding error"}
    except Exception as e: print(f"❌ 讀取 CSV 時發生嚴重錯誤: {e}"); return {"ok": False, "error": f"CSV read error: {e}"}

    print(f"🔎 台北市便利商店原始筆數：{len(rows)}")
    
    out_rows = []
    limit = len(rows) if not TAIPEI_BUILD_LIMIT else min(TAIPEI_BUILD_LIMIT, len(rows))
    print(f"ℹ️ 開始 Geocoding (上限 {limit} 筆)...")
    
    # 檢查 API 金鑰是否有效，無效則跳過 Geocoding
    api_key_valid = GOOGLE_MAPS_API_KEY and not GOOGLE_MAPS_API_KEY.startswith("【請在這裡")
    if not api_key_valid:
        print("⚠️ 警告：未設定 Google API 金鑰，無法進行 Geocoding，將只儲存地址。")

    for i, r in enumerate(rows[:limit]):
        if api_key_valid:
            coords = geocode(r["address"]) # 使用優化的 geocode
            if coords:
                out_rows.append({
                    "brand": r["brand"], "name": r["name"], "address": r["address"],
                    "lat": coords["lat"], "lng": coords["lng"]
                })
            # else: # 可取消註解觀察失敗情況
            #     print(f"    - Geocode 失敗 for {r['address']}")
        else:
             # 無金鑰時，只儲存基本資訊 (不含座標)
             out_rows.append({"brand": r["brand"], "name": r["name"], "address": r["address"], "lat": None, "lng": None})

        # 進度提示 (每 50 筆)
        if (i + 1) % 50 == 0:
            print(f"    ...已處理 {i+1} / {limit} 筆")

    # 【修正】寫入預處理檔案
    try:
        with open(STORES_TAIPEI_CSV, "w", encoding="utf-8", newline="") as f:
            fieldnames = ["brand", "name", "address", "lat", "lng"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
        duration = time.time() - start_time
        print(f"✅ 台北市便利商店預處理完成：成功 Geocode {len([r for r in out_rows if r.get('lat')])} / {len(out_rows)} 筆 → {STORES_TAIPEI_CSV}。耗時 {duration:.2f} 秒")
        return {"ok": True, "count": len(out_rows), "geocoded": len([r for r in out_rows if r.get('lat')])}
    except Exception as e:
        print(f"❌ 儲存預處理檔案 {STORES_TAIPEI_CSV} 失敗: {e}")
        return {"ok": False, "error": f"Failed to write preprocessed CSV: {e}"}

# ---------------------------
# 資料載入 (您的版本，稍作優化)
# ---------------------------
def load_taipei_stores():
    """ 從預處理的 CSV 載入台北市便利商店資料 """
    if not os.path.exists(STORES_TAIPEI_CSV):
        print(f"ℹ️ 未找到預處理檔案 {STORES_TAIPEI_CSV}，嘗試立即建立...")
        build_result = ensure_taipei_stores() # 嘗試建立
        if not build_result.get("ok") or not os.path.exists(STORES_TAIPEI_CSV):
             print(f"❌ 無法建立或找到預處理檔案，便利商店功能將無法使用座標。")
             return [] # 返回空列表

    stores = []
    try:
        with open(STORES_TAIPEI_CSV, "r", encoding="utf-8", newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames: # 處理空檔案
                 print(f"⚠️ 預處理檔案 {STORES_TAIPEI_CSV} 為空。")
                 return []
                 
            required_cols = ["name", "address", "lat", "lng"] # brand 是可選的
            if not all(col in reader.fieldnames for col in required_cols):
                 print(f"❌ 錯誤：預處理檔案 {STORES_TAIPEI_CSV} 缺少必要欄位 {required_cols}。")
                 return []

            for r in reader:
                try:
                    # 只有包含有效經緯度的資料才加入
                    lat = float(r["lat"])
                    lng = float(r["lng"])
                    stores.append({
                        "brand": r.get("brand") or detect_brand(r.get("name", ""), ""), # 如果 brand 欄位不存在或為空，嘗試偵測
                        "name": r["name"].strip(),
                        "address": r["address"].strip(),
                        "lat": lat,
                        "lng": lng
                    })
                except (ValueError, TypeError, KeyError) as row_err:
                    print(f"⚠️ 解析預處理檔案行失敗: {row_err} - 資料: {r}") # 跳過格式錯誤的行
                    pass 
        print(f"📦 台北市便利商店載入成功：{len(stores)} 筆")
    except FileNotFoundError:
         print(f"❌ 錯誤：找不到預處理檔案 {STORES_TAIPEI_CSV} (載入階段)") # 理論上不應發生
    except Exception as e:
        print(f"❌ 讀取預處理檔案 {STORES_TAIPEI_CSV} 時發生嚴重錯誤：{e}")
    return stores

def load_police():
    """ 從 data.taipei API 載入警察局資料 """
    try:
        print("⏳ 正在從 data.taipei 載入警察局資料...")
        r = requests.get(TAIPEI_POLICE_API_URL, timeout=15, verify=False) # 【注意】暫時忽略 SSL 驗證
        r.raise_for_status()
        data = r.json()
        lst = data.get("result", {}).get("results", [])
        if not lst: print("⚠️ API 回傳的警察局列表為空。")
        print(f"✅ 警察局資料載入成功：{len(lst)} 筆")
        return lst
    except requests.exceptions.SSLError as ssl_err:
         print(f"❌❌❌ 載入警察局資料失敗 (SSL錯誤): {ssl_err}")
         print("   > 可能是 data.taipei 憑證問題或本機缺少根憑證。已嘗試忽略驗證，若仍失敗請檢查網路或 API 狀態。")
         return []
    except Exception as e:
        print(f"❌ 載入警察局資料失敗：{e}")
        return []

# --- 【重要】伺服器啟動時執行的動作 ---
print("==============================================")
print("🚀 啟動 Guardian Light 後端 (您的版本優化)...")
print("==============================================")
# 1. 載入警察局資料
POLICE_DATA = load_police() 
# 2. 載入 (或預處理) 便利商店資料
TAIPEI_STORES = load_taipei_stores() 
print("----------------------------------------------")

# ---------------------------
# API 路由 (您的版本，稍作優化)
# ---------------------------
@app.route("/api/nearby")
def api_nearby():
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    tp = request.args.get("type", "store") # 改為預設 store
    brand_filter = request.args.get("brand", "")
    limit = int(request.args.get("limit", 10))

    if not lat or not lng: return jsonify({"error": "缺少 lat 或 lng 參數"}), 400

    print(f"\n🚀 收到 /api/nearby 請求: lat={lat}, lng={lng}, type={tp}, brand={brand_filter}, limit={limit}")

    results = []
    if tp == "police":
        print(f"  > 處理警察局 (共 {len(POLICE_DATA)} 筆)...")
        processed_count = 0
        for item in POLICE_DATA: # 假設 POLICE_DATA 已載入
            name = item.get("name", "")
            addr = item.get("poi_addr") or item.get("display_addr")
            processed_count += 1
            if not addr: continue
            
            # 即時 Geocoding (使用快取)
            coords = geocode(addr) 
            if not coords: continue

            try:
                dist = get_distance(lat, lng, coords["lat"], coords["lng"])
                if dist != float("inf"):
                    results.append({
                        "brand": "警察局", "name": name, "address": addr,
                        "lat": coords["lat"], "lng": coords["lng"], "distance": round(dist, 2)
                    })
            except Exception as dist_err:
                 print(f"⚠️ 計算警察局 {name} 距離時出錯: {dist_err}")
                 
            # 可以在此加入處理筆數限制，避免 Geocode 過多
            # if processed_count >= 300: break 

    elif tp == "store":
        print(f"  > 處理便利商店 (共 {len(TAIPEI_STORES)} 筆)...")
        for item in TAIPEI_STORES: # 使用預處理好的資料
            # 套用品牌篩選
            if brand_filter and brand_filter != "全部" and item["brand"] != brand_filter:
                continue
            
            try:
                dist = get_distance(lat, lng, item["lat"], item["lng"])
                # 移除過遠篩選 (距離由前端處理)
                # if dist > 30: continue 
                if dist != float("inf"):
                    results.append({
                        "brand": item["brand"], "name": item["name"], "address": item["address"],
                        "lat": item["lat"], "lng": item["lng"], "distance": round(dist, 2)
                    })
            except Exception as dist_err:
                 print(f"⚠️ 計算便利商店 {item.get('name')} 距離時出錯: {dist_err}")
                 
    else:
        return jsonify({"error": "type 參數僅支援 police 或 store"}), 400

    if not results:
         print("ℹ️ 處理完成，但附近沒有找到符合條件的地點。")
         return jsonify([])

    print(f"  計算完成 {len(results)} 筆有效資料，正在排序...")
    results.sort(key=lambda x: x["distance"])
    final_results = results[:min(limit, 50)] # 套用數量限制
    
    print(f"✅ 完成！回傳最近的 {len(final_results)} 筆結果。")
    return jsonify(final_results)

@app.route("/api/brands")
def api_brands():
    """ 回傳所有便利商店的品牌與數量 """
    from collections import Counter
    # 計算預處理資料中的品牌
    cnt = Counter([s.get("brand","其他") for s in TAIPEI_STORES])
    # 增加 "全部" 選項
    brands_dict = {"全部": len(TAIPEI_STORES)}
    brands_dict.update(dict(cnt))
    return jsonify({"brands": brands_dict, "total": len(TAIPEI_STORES)})

@app.route("/api/rebuild_stores")
def api_rebuild_stores():
    """ 手動觸發重建台北市便利商店預處理檔案 """
    print("\n🔥 手動觸發重建台北市便利商店資料...")
    out = ensure_taipei_stores() # 執行重建
    global TAIPEI_STORES
    TAIPEI_STORES = load_taipei_stores() # 重建後立刻重新載入
    return jsonify({"ok": out.get("ok", False), "result": out, "loaded": len(TAIPEI_STORES)})

@app.route("/api/config")
def api_config():
    """ 提供 Google Maps API Key 給前端 """
    safe_key = GOOGLE_MAPS_API_KEY if GOOGLE_MAPS_API_KEY and not GOOGLE_MAPS_API_KEY.startswith("【請在這裡") else ""
    return jsonify({"GOOGLE_MAPS_API_KEY": safe_key})

# 【移除】不再需要由 Flask 提供前端檔案
# @app.route("/nearby.html")
# def serve_nearby():
#     return send_from_directory(PUBLIC_DIR, "nearby.html")

# ---------------------------
# 啟動伺服器
# ---------------------------
if __name__ == "__main__": 
    print("\n🌍 伺服器準備就緒，開始監聽請求...")
    # 使用 host="0.0.0.0" 讓區域網路可以連線
    # debug=True 方便開發時自動重載，部署到 Render 時應考慮關閉
    app.run(debug=True, host="0.0.0.0", port=5001)
