# -*- coding: utf-8 -*-
import os, math, csv, json, requests
from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
from collections import defaultdict

# ---------------------------
# 基本設定
# ---------------------------
app = Flask(__name__)
CORS(app)

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")
DATA_DIR = os.path.join(PUBLIC_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

STORES_CSV = os.path.join(DATA_DIR, "stores.csv")
STORES_TAIPEI_CSV = os.path.join(DATA_DIR, "stores_taipei.csv")
GEOCODE_CACHE_FILE = os.path.join(DATA_DIR, "geocode_cache.json")

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"
TAIPEI_POLICE_API_URL = "https://data.taipei/api/v1/dataset/a90ae184-c39e-4242-b2d6-d7a0403c0632?scope=resourceAquire"

TAIPEI_BUILD_LIMIT = None  # None = 不限制筆數

# ---------------------------
# 公用函式
# ---------------------------
def get_distance(lat1, lon1, lat2, lon2):
    """球面距離（公里）"""
    R = 6371.0
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        a = math.sin(dLat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    except Exception:
        return float("inf")

def normalize_text(t: str) -> str:
    """全形轉半形 + 去空白 + 小寫"""
    if not t:
        return ""
    # 把全形字轉成半形
    t = ''.join(
        chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c
        for c in t
    )
    # 去除所有空白、轉成小寫
    t = t.replace(" ", "").replace("　", "").lower()
    return t

# ---------------------------
# Geocoding 快取
# ---------------------------
if os.path.exists(GEOCODE_CACHE_FILE):
    with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
        GEO_CACHE = json.load(f)
else:
    GEO_CACHE = {}

def save_cache():
    with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(GEO_CACHE, f, ensure_ascii=False, indent=2)

# ---------------------------
# 智慧 Geocode（台灣限定）
# ---------------------------
def geocode(address: str):
    """智慧化 Geocode（含快取、清理樓層、重試、台灣限定）"""
    if not address:
        return None
    if address in GEO_CACHE:
        return GEO_CACHE[address]
    if not GOOGLE_MAPS_API_KEY:
        print("⚠️ 沒有設定 GOOGLE_MAPS_API_KEY，跳過 geocode")
        return None

    clean = address.replace("　", "").replace(" ", "")
    for token in ["地下一層", "地下1樓", "地下二樓", "B1", "B2", "B3", "1樓", "2樓", "3樓", "4樓", "5樓", "6樓"]:
        clean = clean.replace(token, "")
    for sep in ["、", "，", ","]:
        if sep in clean:
            clean = clean.split(sep)[0]
    clean = clean.replace("之", "")

    def query_google(addr):
        try:
            r = requests.get(GEOCODING_API_URL, params={
                "address": addr,
                "key": GOOGLE_MAPS_API_KEY,
                "language": "zh-TW",
                "region": "tw",
                "components": "country:TW"
            }, timeout=8)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                return {"lat": loc["lat"], "lng": loc["lng"]}
        except Exception as e:
            print(f"❌ Geocode error: {addr} => {e}")
        return None

    coords = query_google(address) or query_google(clean)
    GEO_CACHE[address] = coords
    save_cache()
    return coords

def detect_brand(company: str) -> str:
    """更準確的品牌偵測"""
    if not company:
        return "其他"

    c = normalize_text(company.replace("股份有限公司", ""))

    # 避免誤判，先判斷最特殊的
    if "統一超商" in c or "7-eleven" in c or "7_11" in c or "7－11" in c or "7-11" in c:
        brand = "7-ELEVEN"
    elif "全家便利商店" in c or "全家" in c:
        brand = "全家"
    elif "全聯福利中心" in c or ("全聯" in c and "超商" not in c):
        brand = "全聯"
    elif "萊爾富" in c or "hi-life" in c or "hilife" in c:
        brand = "萊爾富"
    elif "ok便利" in c or "okmart" in c or "ok" in c:
        brand = "OK便利店"
    else:
        brand = "其他"

    print(f"✅ 偵測品牌: {company} → {brand}")
    return brand



def ensure_taipei_stores():
    if not os.path.exists(STORES_CSV):
        print("⚠️ 找不到 stores.csv，無法重建")
        return {"ok": False}

    rows = []
    with open(STORES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = (row.get("分公司地址") or "").strip()
            company = (row.get("公司名稱") or "").strip()
            name = (row.get("分公司名稱") or "").strip()

            # 只抓台北市
            if ("台北市" in addr) or ("臺北市" in addr):
                brand = detect_brand(company) or detect_brand(name)
                print(f"✅ 偵測品牌: {company} / {name} → {brand}")
                rows.append({
                    "brand": brand,
                    "name": name or company,
                    "address": addr
                })

    print(f"🔎 台北市便利商店原始筆數：{len(rows)}")

    out_rows = []
    limit = len(rows) if not TAIPEI_BUILD_LIMIT else min(TAIPEI_BUILD_LIMIT, len(rows))
    for r in rows[:limit]:
        coords = geocode(r["address"])
        if coords:
            out_rows.append({
                "brand": r["brand"],
                "name": r["name"],
                "address": r["address"],
                "lat": coords["lat"],
                "lng": coords["lng"],
            })

    with open(STORES_TAIPEI_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["brand", "name", "address", "lat", "lng"])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"✅ 台北市便利商店完成：{len(out_rows)} 筆 → {STORES_TAIPEI_CSV}")
    return {"ok": True, "count": len(out_rows)}


def load_taipei_stores():
    if not os.path.exists(STORES_TAIPEI_CSV):
        return []
    stores = []
    with open(STORES_TAIPEI_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        has_brand = "brand" in (reader.fieldnames or [])
        for r in reader:
            brand = r.get("brand") if has_brand else detect_brand(r.get("name", ""))
            stores.append({
                "brand": brand or "其他",
                "name": r["name"],
                "address": r["address"],
                "lat": float(r["lat"]),
                "lng": float(r["lng"])
            })
    print(f"📦 台北市便利商店載入：{len(stores)} 筆")
    return stores

def load_police():
    local_file = os.path.join(DATA_DIR, "police.json")
    try:
        with open(local_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            lst = data.get("result", {}).get("results", [])
            print(f"✅ 本地警察局資料載入：{len(lst)} 筆")
            return lst
    except Exception as e:
        print(f"⚠️ 無法載入本地 police.json：{e}")
        return []

POLICE_DATA = load_police()
TAIPEI_STORES = load_taipei_stores()

# ---------------------------
# API 路由
# ---------------------------
@app.route("/")
def root_redirect():
    return redirect("/nearby.html", code=302)

@app.route("/api/nearby")
def api_nearby():
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    tp = (request.args.get("type") or "").strip().lower()
    brand_filter = request.args.get("brand", "")
    limit = int(request.args.get("limit", 10))

    results = []

    # --- 明確分支 ---
    if tp == "police":
        print("🚓 查詢警察局資料中...")
        for it in POLICE_DATA:
            name = it.get("name", "")
            addr = it.get("poi_addr") or it.get("display_addr")
            coords = geocode(addr)
            if not coords:
                continue
            dist = get_distance(lat, lng, coords["lat"], coords["lng"])
            results.append({
                "brand": "警察局",
                "name": name,
                "address": addr,
                "lat": coords["lat"],
                "lng": coords["lng"],
                "distance": round(dist, 2)
            })

    elif tp == "store":
        print(f"🏪 查詢便利商店資料中... (品牌篩選：{brand_filter})")
        match_count = 0
        for it in TAIPEI_STORES:
            # 比對品牌（用 normalize_text 確保一致）
            brand1 = normalize_text(it["brand"])
            brand2 = normalize_text(brand_filter)
            print(f"🔎 比對品牌: {it['brand']} vs {brand_filter}", end=" ")

            if brand_filter and brand_filter != "全部" and brand1 != brand2:
                print("❌")
                continue
            print("✅")

            dist = get_distance(lat, lng, it["lat"], it["lng"])
            if dist > 30:
                continue

            results.append({
                "brand": it["brand"],
                "name": it["name"],
                "address": it["address"],
                "lat": it["lat"],
                "lng": it["lng"],
                "distance": round(dist, 2)
            })
            match_count += 1

        print(f"🧮 篩選後共 {match_count} 筆符合 {brand_filter}")

    else:
        print(f"⚠️ 未知的 type 參數：{tp}")
        return jsonify({"error": "未知的 type 類別，請使用 'store' 或 'police'"})

    # --- 結果排序 + 保底處理 ---
    results.sort(key=lambda x: x["distance"])
    if not results and tp == "store":
        print("⚠️ 找不到符合條件的店，回傳台北市最近10間（保底）")
        fallback = []
        for it in TAIPEI_STORES:
            dist = get_distance(lat, lng, it["lat"], it["lng"])
            it["distance"] = round(dist, 2)
            fallback.append(it)
        fallback.sort(key=lambda x: x["distance"])
        results = fallback[:10]

    return jsonify(results[:limit])




@app.route("/api/brands")
def api_brands():
    from collections import Counter
    cnt = Counter([s["brand"] for s in TAIPEI_STORES])
    return jsonify({"brands": dict(cnt), "total": len(TAIPEI_STORES)})

@app.route("/api/rebuild_stores")
def api_rebuild_stores():
    out = ensure_taipei_stores()
    global TAIPEI_STORES
    TAIPEI_STORES = load_taipei_stores()
    return jsonify({"ok": True, "result": out, "loaded": len(TAIPEI_STORES)})

@app.route("/api/config")
def api_config():
    return jsonify({"GOOGLE_MAPS_API_KEY": GOOGLE_MAPS_API_KEY})

# ---------------------------
# 安全／危險 熱圖 API
# ---------------------------
DATA_LIGHTS = os.path.join(DATA_DIR, "lights.csv")
DATA_ACCIDENTS = os.path.join(DATA_DIR, "accidents.csv")
DATA_CRIME = os.path.join(DATA_DIR, "crime.csv")

_LAT_KEYS = ["lat", "latitude", "y", "緯度"]
_LNG_KEYS = ["lng", "lon", "longitude", "x", "經度"]
_ADDR_KEYS = ["address", "地址", "地點", "位置", "地點名稱"]

def _pick_col(d: dict, keys: list):
    for k in keys:
        if k in d and d[k]: return d[k]
        for kk in d.keys():
            if kk.strip().lower() == k: return d[kk]
    return None

def _to_float(x):
    try: return float(str(x).strip())
    except: return None

def _read_points_from_csv(filepath: str):
    pts = []
    if not os.path.exists(filepath):
        print(f"⚠️ 找不到資料檔：{filepath}")
        return pts

    # --- TWD97 TM2(121E) 轉 WGS84 ---
    def twd97_to_wgs84(x, y):
        import math
        a = 6378137.0
        b = 6356752.314245
        lon0 = 121 * math.pi / 180
        k0 = 0.9999
        dx = 250000
        e = (1 - (b / a) ** 2) ** 0.5
        x -= dx
        M = y / k0
        mu = M / (a * (1 - e ** 2 / 4 - 3 * e ** 4 / 64 - 5 * e ** 6 / 256))
        e1 = (1 - (1 - e ** 2) ** 0.5) / (1 + (1 - e ** 2) ** 0.5)
        J1 = 3 * e1 / 2 - 27 * e1 ** 3 / 32
        J2 = 21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32
        J3 = 151 * e1 ** 3 / 96
        J4 = 1097 * e1 ** 4 / 512
        fp = mu + J1 * math.sin(2 * mu) + J2 * math.sin(4 * mu) + J3 * math.sin(6 * mu) + J4 * math.sin(8 * mu)
        e2 = (e * a / b) ** 2
        C1 = e2 * math.cos(fp) ** 2
        T1 = math.tan(fp) ** 2
        R1 = a * (1 - e ** 2) / ((1 - (e * math.sin(fp)) ** 2) ** 1.5)
        N1 = a / ((1 - (e * math.sin(fp)) ** 2) ** 0.5)
        D = x / (N1 * k0)
        Q1 = N1 * math.tan(fp) / R1
        Q2 = (D ** 2) / 2
        Q3 = (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * e2) * D ** 4 / 24
        Q4 = (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * e2 - 3 * C1 ** 2) * D ** 6 / 720
        lat = fp - Q1 * (Q2 - Q3 + Q4)
        Q5 = D
        Q6 = (1 + 2 * T1 + C1) * D ** 3 / 6
        Q7 = (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * e2 + 24 * T1 ** 2) * D ** 5 / 120
        lon = lon0 + (Q5 - Q6 + Q7) / math.cos(fp)
        return math.degrees(lat), math.degrees(lon)

    import csv
    for enc in ["utf-8-sig", "utf-8", "big5"]:
        try:
            with open(filepath, "r", encoding=enc) as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    # 去除欄位名稱空白
                    row = {k.strip(): v.strip() for k, v in row.items() if k}

                    # ---- 交通事故（座標-X/Y）----
                    if "座標-X" in row and "座標-Y" in row:
                        try:
                            lat = float(row["座標-Y"])
                            lng = float(row["座標-X"])
                            pts.append((lat, lng))
                            continue
                        except:
                            pass

                    # ---- 路燈資料（TWD97X/Y）----
                    if "TWD97X" in row and "TWD97Y" in row:
                        try:
                            x = float(row["TWD97X"])
                            y = float(row["TWD97Y"])
                            lat, lng = twd97_to_wgs84(x, y)
                            pts.append((lat, lng))
                            continue
                        except Exception as e:
                            print(f"⚠️ TWD97 轉換失敗：{e}")
                            continue

                    # ---- 其他格式 ----
                    for k_lat, k_lng in [("lat", "lng"), ("Latitude", "Longitude"), ("緯度", "經度")]:
                        if k_lat in row and k_lng in row:
                            try:
                                lat = float(row[k_lat])
                                lng = float(row[k_lng])
                                pts.append((lat, lng))
                                break
                            except:
                                continue
                break
        except Exception:
            continue

    print(f"📥 讀取 {os.path.basename(filepath)}：{len(pts)} 筆座標")
    return pts





def _grid_key(lat, lng, meters=150):
    lat_deg_per_m = 1.0 / 111_000.0
    lng_deg_per_m = 1.0 / (111_000.0 * max(0.00001, math.cos(math.radians(lat))))
    return (round(lat / (lat_deg_per_m * meters)), round(lng / (lng_deg_per_m * meters)))

def _accumulate(points, base_weight=1.0):
    bucket = defaultdict(float)
    center = {}
    for (lat, lng) in points:
        k = _grid_key(lat, lng)
        bucket[k] += base_weight
        center[k] = (lat, lng)
    maxw = max(bucket.values()) if bucket else 1.0
    out = []
    for k, w in bucket.items():
        c = center[k]
        out.append({"lat": c[0], "lng": c[1], "weight": round(w / maxw, 3)})
    return out

@app.route("/api/heatmap")
def api_heatmap():
    """安全分級熱圖資料：融合事故(危險)與路燈(安全)"""
    limit = int(request.args.get("limit", 1000))

    # --- 讀取三份資料 ---
    accidents = _read_points_from_csv(DATA_ACCIDENTS)
    lights = _read_points_from_csv(DATA_LIGHTS)

    if not accidents and not lights:
        return jsonify([])

    # --- 統合資料 ---
    import random
    danger_points = random.sample(accidents, min(limit, len(accidents)))
    safe_points = random.sample(lights, min(limit, len(lights)))

    # --- 為每個點加入安全指數 ---
    results = []
    for lat, lng in danger_points:
        results.append({"lat": lat, "lng": lng, "safety": -1})  # 紅色：危險
    for lat, lng in safe_points:
        results.append({"lat": lat, "lng": lng, "safety": +1})  # 綠色：安全

    print(f"📥 讀取 accidents.csv：{len(accidents)} 筆座標")
    print(f"📥 讀取 lights.csv：{len(lights)} 筆座標")
    print(f"🔥 輸出紅={len(danger_points)} 綠={len(safe_points)}")

    return jsonify(results)


@app.route("/nearby.html")
def serve_nearby():
    return send_from_directory(PUBLIC_DIR, "nearby.html")

@app.route("/heatmap.html")
def serve_heatmap():
    return send_from_directory(PUBLIC_DIR, "heatmap.html")

# ---------------------------
# 啟動伺服器
# ---------------------------
if __name__ == "__main__":
    print("🚀 啟動 Guardian Light 後端（Render 版）")
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
