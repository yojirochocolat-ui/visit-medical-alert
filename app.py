import io
import os
import glob
import re
import random
import urllib.parse
from datetime import datetime, timezone, timedelta
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
# 自動リロード用ライブラリ
from streamlit_autorefresh import st_autorefresh
# PDF生成用ライブラリ
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 日本時間（JST）の定義
JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------
# ページ基本設定 & カスタムCSS
# ---------------------------------------------------------
st.set_page_config(page_title="停電アラート", layout="wide")
st.markdown("""
    <style>
        .block-container {
            padding-top: 0.3rem !important;
            padding-bottom: 1rem !important;
        }
        [data-testid="stSidebarUserContent"] {
            padding-top: 0.3rem !important;
        }
        header {visibility: visible !important;}
        [data-testid="stHeader"] {display: block !important;}
        [data-testid="stAppHeaderActionElements"] { display: none !important; }
        .stAppDeployButton { display: none !important; }
    </style>
""", unsafe_allow_html=True)

# 🔄 5分（300,000ミリ秒）ごとに画面を自動リロード
st_autorefresh(interval=300000, key="data_auto_refresh")
st.title("⚡ 停電アラート")
st.caption("リアルタイムの停電情報と患者リストを照合し、優先度自動トリアージとナビ連携で初動対応を支援します。")

# ---------------------------------------------------------
# セッション状態の初期化
# ---------------------------------------------------------
if "sim_areas" not in st.session_state:
    st.session_state.sim_areas = []
if "sim_created_time" not in st.session_state:
    st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
if "last_fetch_time" not in st.session_state:
    st.session_state.last_fetch_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
if "patient_status" not in st.session_state:
    st.session_state.patient_status = {}
if "patients_data" not in st.session_state:
    st.session_state.patients_data = None
if "filter_unhandled" not in st.session_state:
    st.session_state.filter_unhandled = False
if "selected_patient_id" not in st.session_state:
    st.session_state.selected_patient_id = "指定なし（全体表示）"

# ---------------------------------------------------------
# 住所から緯度・経度を取得する関数 (ジオコーディング)
# ---------------------------------------------------------
@st.cache_data(ttl=86400)
def geocode_address(address):
    if not address or pd.isna(address) or str(address).strip() in ["-", ""]:
        return 34.3400, 134.0450
    if "サンポート" in str(address) or "シンボルタワー" in str(address) or "高松駅" in str(address):
        return 34.3533, 134.0470
    try:
        url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={requests.utils.quote(str(address))}"
        res = requests.get(url, timeout=3).json()
        if res and len(res) > 0:
            lon, lat = res[0]["geometry"]["coordinates"]
            return lat, lon
    except Exception:
        pass
    return 34.3400, 134.0450

# ---------------------------------------------------------
# 1. 四国電力の停電情報を取得する関数
# ---------------------------------------------------------
PREFECTURE_URLS = {
    "香川県": "https://www.yonden.co.jp/nw/teiden-info/kagawa.html",
    "徳島県": "https://www.yonden.co.jp/nw/teiden-info/tokushima.html",
    "愛媛県": "https://www.yonden.co.jp/nw/teiden-info/ehime.html",
    "高知県": "https://www.yonden.co.jp/nw/teiden-info/kochi.html",
}

@st.cache_data(ttl=300)
def fetch_outage_info():
    outage_list = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    for pref_name, url in PREFECTURE_URLS.items():
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text, "html.parser")
            body_text = soup.get_text()
            time_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}時\d{1,2}分\s*現在)", body_text)
            announced_at = time_match.group(1) if time_match else "日時不明"
            no_outage = "停電情報はありません" in body_text
            tables = soup.find_all("table")
            if not no_outage and tables:
                for table in tables:
                    for row in table.find_all("tr"):
                        cols = [ele.text.strip() for ele in row.find_all(["td", "th"])]
                        if not cols or "発生日時" in cols[0]:
                            continue
                        if len(cols) >= 2:
                            city = cols[0]
                            raw_towns = cols[1]
                            towns = [t.strip() for t in re.split(r"[、\s]+", raw_towns) if t.strip()]
                            outage_list.append({
                                "prefecture": pref_name,
                                "city": city,
                                "towns": towns,
                                "raw_towns": raw_towns,
                                "announced_at": announced_at
                            })
        except Exception:
            continue
    return outage_list

bg_realtime_outage_data = fetch_outage_info()
st.session_state.last_fetch_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

# ---------------------------------------------------------
# 2. デモ用データ生成
# ---------------------------------------------------------
@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子", "大輔", "美咲"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    
    kagawa_spots = [
        "香川県高松市栗林町1丁目", "香川県高松市栗林町2丁目", "香川県高松市宮脇町1丁目", "香川県高松市宮脇町2丁目",
        "香川県高松市昭和町1丁目", "香川県高松市茜町", "香川県高松市扇町1丁目", "香川県高松市紫雲町",
        "香川県高松市中新町", "香川県高松市藤塚町1丁目", "香川県高松市天神前", "香川県高松市錦町1丁目",
        "香川県高松市番町1丁目", "香川県高松市番町3丁目", "香川県高松市瓦町1丁目", "香川県高松市塩上町1丁目"
    ]
    
    device_options = ["なし", "人工呼吸器", "人工透析装置", "ペースメーカー"]
    device_weights = [0.5, 0.2, 0.15, 0.15]
    
    patients = []
    random.seed(42)
    
    for i in range(1, 51):
        name = f"{random.choice(last_names)} {random.choice(first_names)}"
        spot_addr = random.choice(kagawa_spots)
        p_id = f"P{i:03d}"
        addr = f"{spot_addr}{random.randint(1, 15)}-{random.randint(1, 10)}"
        doc = random.choice(doctors)
        tel = f"090-{random.randint(1000,9999)}-{random.randint(10,99):02d}XX"
        device = random.choices(device_options, weights=device_weights)[0]
        battery = "ー" if device == "なし" else random.choice(["○", "ー", "？"])
        
        patients.append({
            "ID": p_id, "患者名": name, "住所": addr, 
            "担当医": doc, "連絡先": tel, "使用装置": device, "バッテリ": battery,
            "備考": ""
        })
    return patients

if st.session_state.patients_data is None:
    initial_df = pd.DataFrame(generate_50_kagawa_patients())
    lats, lons = [], []
    random.seed(123)
    for _, r in initial_df.iterrows():
        base_lat, base_lon = geocode_address(r["住所"])
        # ジッター幅を極小（約10〜20m）に抑えて精度向上
        jitter_lat = base_lat + random.uniform(-0.0002, 0.0002)
        jitter_lon = base_lon + random.uniform(-0.0002, 0.0002)
        lats.append(jitter_lat)
        lons.append(jitter_lon)
    initial_df["lat"] = lats
    initial_df["lon"] = lons
    st.session_state.patients_data = initial_df

# ---------------------------------------------------------
# 3. サイドバー設定
# ---------------------------------------------------------
st.sidebar.header("⚙️ 動作設定")
current_location_addr = st.sidebar.text_input("📍 現住所（拠点）", value="高松市サンポート2番1号")
staff1_location_addr = st.sidebar.text_input("🏃 スタッフ1現在地", value="", placeholder="高松市瓦町1丁目")
staff2_location_addr = st.sidebar.text_input("🏃 スタッフ2現在地", value="", placeholder="高松市栗林町2丁目")
mode = st.sidebar.radio("情報取得モード", options=["仮想シミュレーションモード", "リアルタイムWeb取得モード"])

# ---------------------------------------------------------
# 4. 停電データの照合準備 & トリアージ
# ---------------------------------------------------------
outage_data = []
if mode == "仮想シミュレーションモード":
    st.subheader("1. 停電エリア・シミュレーター")
    sim_input = st.text_input("停電が発生したと想定する地域を入力", value=",".join(st.session_state.sim_areas), placeholder="例: 宮脇町, 昭和町1丁目")
    if st.button("▶️ シミュレーション実行"):
        st.session_state.sim_areas = [a.strip() for a in sim_input.split(",") if a.strip()]
        st.rerun()
    for area in st.session_state.sim_areas:
        outage_data.append({"prefecture": "香川県", "city": area, "towns": [area], "raw_towns": area})
else:
    outage_data = bg_realtime_outage_data

def check_outage(address, outage_list):
    if not outage_list: return False, "正常"
    addr_str = str(address)
    for item in outage_list:
        if item["city"] in addr_str: return True, item["city"]
        for town in item["towns"]:
            if town and town in addr_str: return True, town
    return False, "正常"

def calc_triage_level(device, battery):
    d, b = str(device), str(battery)
    if "人工呼吸器" in d: return "Lv.4", 4
    elif "人工透析" in d or "在宅酸素" in d: return "Lv.3", 3
    elif d != "なし": return "Lv.3" if b in ["ー", "？"] else "Lv.2", 2 if b in ["ー", "？"] else 2
    return "Lv.1", 1

results = []
for idx, row in st.session_state.patients_data.iterrows():
    p_id = str(row.get("ID", f"P{idx+1:03d}"))
    is_outage, area_info = check_outage(str(row["住所"]), outage_data)
    triage_label, triage_score = calc_triage_level(row.get("使用装置", "なし"), row.get("バッテリ", "ー"))
    status_info = st.session_state.patient_status.get(p_id, {})
    
    results.append({
        "ID": p_id,
        "対応ステータス": status_info.get("status", "未対応"),
        "更新時刻": status_info.get("updated_at", "-"),
        "停電リスク": status_info.get("override_outage", "⚠️ 停電可能性あり" if is_outage else "🟢 正常"),
        "トリアージ": triage_label,
        "triage_score": triage_score,
        "患者名": row["患者名"],
        "使用装置": row.get("使用装置", "なし"),
        "バッテリ": row.get("バッテリ", "ー"),
        "担当医": row.get("担当医", "-"),
        "連絡先": row.get("連絡先", "-"),
        "住所": row["住所"],
        "lat": float(row.get("lat", 34.3400)),
        "lon": float(row.get("lon", 134.0450))
    })

df_result = pd.DataFrame(results)

# ---------------------------------------------------------
# 5. 地図描画関数 (特定患者へのフォーカス/ジャンプ機能付き)
# ---------------------------------------------------------
def build_map(df, target_only=False, home_address="", focus_patient_id=None):
    if target_only:
        display_df = df[(df["停電リスク"].str.contains("⚠️")) & (df["対応ステータス"] != "安否確認済（安全）")]
    else:
        display_df = df

    # 特定の患者がフォーカス指定されている場合、その座標を中心に設定
    focus_row = display_df[display_df["ID"] == focus_patient_id]
    if not focus_row.empty and focus_patient_id != "指定なし（全体表示）":
        center_lat = focus_row.iloc[0]["lat"]
        center_lon = focus_row.iloc[0]["lon"]
        start_zoom = 18  # ピンポイントにズーム
    else:
        center_lat, center_lon = geocode_address(home_address)
        start_zoom = 15

    m = folium.Map(location=[center_lat, center_lon], zoom_start=start_zoom)
    marker_cluster = MarkerCluster(disableClusteringAtZoom=16).add_to(m)

    # ピンの描画
    for _, row in display_df.iterrows():
        is_alert = "⚠️" in row["停電リスク"]
        color = "gray" if row["対応ステータス"] == "安否確認済（安全）" else ("red" if is_alert else "green")
        
        # 選択された患者には特別なマーカー枠を強調表示
        icon_name = "star" if row["ID"] == focus_patient_id else "user"
        
        popup_html = f"<b>【{row['トリアージ']}】{row['患者名']} 様</b><br>住所: {row['住所']}<br>状態: {row['対応ステータス']}"
        
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{row['ID']}: {row['患者名']} 様",
            icon=folium.Icon(color=color, icon=icon_name, prefix="fa")
        ).add_to(marker_cluster if row["ID"] != focus_patient_id else m) # 選択中のピンはクラスタリングから外して直接描画

    return m

# ---------------------------------------------------------
# 6. メインUI表示エリア
# ---------------------------------------------------------
st.subheader("2. 患者照合結果 & マップ可視化")

# 🎯 ピンジャンプ機能：リストから患者を選択するセレクトボックス
patient_options = ["指定なし（全体表示）"] + [f"{r['ID']} - {r['患者名']} ({r['住所']})" for _, r in df_result.iterrows()]
selected_option = st.selectbox(
    "🎯 地図でピンポイント確認したい患者を選択（選択すると該当位置に自動でジャンプします）:",
    options=patient_options,
    index=0
)

# 選択された患者IDの抽出
selected_id = selected_option.split(" - ")[0] if selected_option != "指定なし（全体表示）" else "指定なし（全体表示）"

col1, col2 = st.columns([6, 5])
with col1:
    st.markdown("#### 📋 患者リスト")
    edited_df = st.data_editor(
        df_result[["ID", "対応ステータス", "停電リスク", "トリアージ", "患者名", "住所"]],
        use_container_width=True,
        height=450,
        hide_index=True
    )

with col2:
    st.markdown("#### 🗺️ 訪問エリアマップ")
    m = build_map(
        df_result, 
        home_address=current_location_addr,
        focus_patient_id=selected_id  # 選択したIDを渡してジャンプ
    )
    st_folium(m, width="100%", height=450, key=f"map_{selected_id}")
