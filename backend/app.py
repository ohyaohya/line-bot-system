# -*- coding: utf-8 -*-
import os, math, csv, json, requests
from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS

# ---------------------------
# 基本設定
# ---------------------------
app = Flask(__name__)
CORS(app)

PUBLIC_DIR = "public"
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
    """全形轉半形 + 去空白"""
    if not t: return ""
    t = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in t)
    return t.replace(" ", "").replace("　", "").lower()

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

    # Step 1: 清理地址
    clean = address.replace("　", "").replace(" ", "")
    for token in ["地下一層", "地下1樓", "地下二樓", "B1", "B2", "B3", "1樓", "2樓", "3樓", "4樓", "5樓", "6樓"]:
        clean = clean.replace(token, "")
    for sep in ["、", "，", ","]:
        if sep in clean:
            clean = clean.split(sep)[0]
    clean = clean.replace("之", "")

    # Step 2: 呼叫 Google API
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

# ---------------------------
# 品牌偵測
# ---------------------------
def detect_brand(company: str) -> str:
    if not company: return "其他"
    c = normalize_text(company.replace("股份有限公司", ""))
    if any(k in c for k in ["統一超商", "7-eleven", "7-11", "7－11", "7_11", "7eleven", "seven"]):
        return "7-ELEVEN"
    if "全家" in c:
        return "全家"
    if "全聯" in c:
        return "全聯"
    if "萊爾富" in c or "hi-life" in c or "hilife" in c:
        return "萊爾富"
    if "來來" in c:
        return "來來"
    return "其他"

# ---------------------------
# 產生台北店舖檔
# ---------------------------
def ensure_taipei_stores():
    if not os.path.exists(STORES_CSV):
        print("⚠️ 找不到 stores.csv，無法重建")
        return {"ok": False}
    rows = []
    with open(STORES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = (row.get("分公司地址") or "").strip()
            name = (row.get("分公司名稱") or row.get("公司名稱") or "").strip()
            company = (row.get("公司名稱") or "").strip()
            if ("台北市" in addr) or ("臺北市" in addr):
                brand = detect_brand(company) or detect_brand(name)
                rows.append({"brand": brand, "name": name, "address": addr})

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

# ---------------------------
# 資料載入
# ---------------------------
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
        r = requests.get(TAIPEI_POLICE_API_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        lst = data.get("result", {}).get("results", [])
        print(f"✅ 警察局資料載入：{len(lst)} 筆")
        return lst
    except Exception as e:
        print(f"⚠️ 載入警察局資料失敗：{e}")
        return []

POLICE_DATA = load_police()
TAIPEI_STORES = load_taipei_stores()

# ---------------------------
# API 路由
# ---------------------------
@app.route("/")
def root_redirect():
    # 首頁自動導向 nearby.html
    return redirect("/nearby.html", code=302)

@app.route("/api/nearby")
def api_nearby():
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    tp = request.args.get("type", "store")
    brand_filter = request.args.get("brand", "")
    limit = int(request.args.get("limit", 10))

    results = []
    if tp == "police":
        for it in POLICE_DATA:
            name = it.get("name", "")
            addr = it.get("poi_addr") or it.get("display_addr")
            coords = geocode(addr)
            if not coords: continue
            dist = get_distance(lat, lng, coords["lat"], coords["lng"])
            results.append({
                "brand": "警察局", "name": name, "address": addr,
                "lat": coords["lat"], "lng": coords["lng"], "distance": round(dist, 2)
            })
    else:
        for it in TAIPEI_STORES:
            if brand_filter and brand_filter != "全部" and it["brand"] != brand_filter:
                continue
            dist = get_distance(lat, lng, it["lat"], it["lng"])
            if dist > 30:
                continue
            results.append({
                "brand": it["brand"], "name": it["name"], "address": it["address"],
                "lat": it["lat"], "lng": it["lng"], "distance": round(dist, 2)
            })
    results.sort(key=lambda x: x["distance"])
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

@app.route("/nearby.html")
def serve_nearby():
    return send_from_directory(PUBLIC_DIR, "nearby.html")

# ---------------------------
# 啟動伺服器
# ---------------------------
if __name__ == "__main__":
    print("🚀 啟動 Guardian Light 後端（Render 版）")
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
