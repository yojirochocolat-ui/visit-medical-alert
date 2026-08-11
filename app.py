import math
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import urllib.parse
import random

from bs4 import BeautifulSoup
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

JST = timezone(timedelta(hours=9))

st.set_page_config(
    page_title="停電アラート & 訪問ルート最適化",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------
# カスタムCSS
# ---------------------------------------------------------
custom_css = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
    }
    
    .main-header {
        font-size: 2.0rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.95rem;
        opacity: 0.85;
        margin-bottom: 1.5rem;
    }
    
    .metric-card {
        background-color: var(--background-secondary-color, rgba(128, 128, 128, 0.12));
        border: 1px solid var(--border-color, rgba(128, 128, 128, 0.25));
        border-radius: 12px;
        padding: 1.25rem;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
    }
    .metric-label {
        font-size: 0.85rem;
        opacity: 0.85;
        margin-top: 0.2rem;
    }
    .metric-danger {
        color: #EF4444;
    }
    
    .section-title {
        font-size: 1.15rem;
        font-weight: 600;
        margin-top: 1.5rem;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .route-item {
        padding: 8px 12px;
        margin-bottom: 6px;
        border-radius: 6px;
        background-color: var(--background-secondary-color, rgba(128, 128, 128, 0.08));
        border-left: 4px solid #2563EB;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

st.markdown(
    '<div class="main-header">⚡ 停電アラート & 訪問ルート最適化</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="sub-header">リアルタイムの停電情報と患者リストを照合し、優先度トリアージ・巡回ルート提案・返信対応付き自動メール通知を行います。</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# セッション状態の初期化
# ---------------------------------------------------------
if "sim_areas" not in st.session_state:
    st.session_state.sim_areas = ["高松市番町", "宇多津町"]
if "sim_created_time" not in st.session_state:
    st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
if "patient_status" not in st.session_state:
    st.session_state.patient_status = {}
if "patients_data" not in st.session_state:
    st.session_state.patients_data = None
if "doctor_address" not in st.session_state:
    st.session_state.doctor_address = "香川県高松市サンポート"
if "show_route" not in st.session_state:
    st.session_state.show_route = False

def send_email_alert(to_email, reply_to_email, subject, body, smtp_config):
    if not smtp_config.get("server") or not smtp_config.get("sender_email") or not smtp_config.get("password"):
        return False, "SMTP設定（サーバー・送信元メール・パスワード）が不完全です。"
    
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_config['sender_email']
        msg['To'] = to_email
        msg['Subject'] = subject
        
        if reply_to_email and reply_to_email.strip():
            msg['Reply-To'] = reply_to_email.strip()
        else:
            msg['Reply-To'] = smtp_config['sender_email']
            
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        server = smtplib.SMTP(smtp_config['server'], int(smtp_config.get('port', 587)), timeout=10)
        server.starttls()
        server.login(smtp_config['sender_email'], smtp_config['password'])
        server.send_message(msg)
        server.quit()
        return True, "送信成功"
    except Exception as e:
        return False, f"送信失敗: {str(e)}"

@st.cache_data(ttl=86400)
def geocode_address(address):
    if not address or pd.isna(address) or str(address).strip() == "-":
        return 34.3400, 134.0450
    try:
        url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={requests.utils.quote(str(address))}"
        res = requests.get(url, timeout=3).json()
        if res and len(res) > 0:
            lon, lat = res[0]["geometry"]["coordinates"]
            return lat, lon
    except Exception:
        pass
    return 34.3400, 134.0450

def calc_distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@st.cache_data(ttl=300)
def fetch_outage_info():
    url = "https://www.yonden.co.jp/nw/teiden-info/history07.html"
    try:
        response = requests.get(url, timeout=10)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        
        outage_list = []
        for tr in soup.find_all('tr'):
            pre_elem = tr.find('th', class_='pre')
            city_elem = tr.find('td', class_='city')
            town_elem = tr.find('td', class_='town')
            
            if city_elem and town_elem:
                pre = pre_elem.text.strip() if pre_elem else ""
                city = city_elem.text.strip()
                towns_raw = town_elem.text.strip()
                towns = [t.strip() for t in re.split(r'[、\s]+', towns_raw) if t.strip()]
                outage_list.append({
                    "prefecture": pre,
                    "city": city,
                    "towns": towns,
                    "raw_towns": towns_raw
                })
        return outage_list
    except Exception:
        return []

@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    
    kagawa_spots = [
        "香川県高松市番町1丁目",
        "香川県高松市瓦町2丁目",
        "香川県高松市栗林町1丁目",
        "香川県高松市古馬場町",
        "香川県高松市鍛冶屋町",
        "香川県高松市紺屋町",
        "香川県高松市丸亀町",
        "香川県綾歌郡宇多津町濱五番丁",
    ]
    device_options = ["なし", "人工呼吸器", "人工透析装置", "ペースメーカー"]
    device_weights = [0.5, 0.2, 0.15, 0.15]
    
    patients = []
    random.seed(42)
    for i in range(1, 51):
        name = f"{random.choice(last_names)} {random.choice(first_names)}"
        spot_addr = random.choice(kagawa_spots)
        p_id = f"P{i:03d}"
        addr = f"{spot_addr}{random.randint(1, 20)}番地"
        doc = random.choice(doctors)
        tel = f"090-{random.randint(1000,9999)}-{random.randint(10,99)}0"
        
        device = random.choices(device_options, weights=device_weights)[0]
        battery = "ー" if device == "なし" else random.choice(["○", "ー", "？"])
        
        patients.append({
            "ID": p_id,
            "患者名": name,
            "住所": addr,
            "担当医": doc,
            "連絡先": tel,
            "使用装置": device,
            "バッテリ": battery,
            "備考": ""
        })
    return patients

if st.session_state.patients_data is None:
    initial_df = pd.DataFrame(generate_50_kagawa_patients())
    lats, lons = [], []
    random.seed(101)
    for _, r in initial_df.iterrows():
        lat, lon = geocode_address(r["住所"])
        lat += (random.random() - 0.5) * 0.003
        lon += (random.random() - 0.5) * 0.003
        lats.append(lat)
        lons.append(lon)
    initial_df["lat"] = lats
    initial_df["lon"] = lons
    st.session_state.patients_data = initial_df

# ---------------------------------------------------------
# サイドバー
# ---------------------------------------------------------
st.sidebar.markdown("### 🚗 医師・訪問チーム設定")
doctor_loc_input = st.sidebar.text_input("拠点・現在地住所", value=st.session_state.doctor_address)
if doctor_loc_input != st.session_state.doctor_address:
    st.session_state.doctor_address = doctor_loc_input

doc_lat, doc_lon = geocode_address(st.session_state.doctor_address)

st.sidebar.markdown("---")
st.sidebar.markdown("### 📧 メール送信・返信先設定")

default_smtp = st.secrets.get("smtp", {})
smtp_server = st.sidebar.text_input("SMTPサーバー", value=default_smtp.get("server", "smtp.gmail.com"))
smtp_port = st.sidebar.number_input("SMTPポート", value=int(default_smtp.get("port", 587)))
sender_email = st.sidebar.text_input("送信元メール (システム)", value=default_smtp.get("sender_email", ""))
sender_password = st.sidebar.text_input("パスワード", value=default_smtp.get("password", ""), type="password")
reply_to_email = st.sidebar.text_input("✉️ 返信先メールアドレス (任意)", value=default_smtp.get("reply_to", sender_email))

smtp_config = {
    "server": smtp_server,
    "port": smtp_port,
    "sender_email": sender_email,
    "password": sender_password
}

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ 動作モード")
mode = st.sidebar.radio("情報取得モード", ["🧪 仮想シミュレーション", "🌐 リアルタイムWeb取得"], label_visibility="collapsed")

# ---------------------------------------------------------
# 照合処理 & トリアージ
# ---------------------------------------------------------
outage_data = []
if mode == "🧪 仮想シミュレーション":
    st.markdown('<div class="section-title">1. 🧪 停電シミュレーター</div>', unsafe_allow_html=True)
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        sim_input = st.text_input("想定停電地域", value="高松市番町, 宇多津町", label_visibility="collapsed")
    with col_btn:
        if st.button("▶️ シミュレーション実行", use_container_width=True):
            st.session_state.sim_areas = [a.strip() for a in sim_input.split(",") if a.strip()]
            st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.session_state.show_route = False
            st.success("指定地域で照合を更新しました")
            
    for area in st.session_state.sim_areas:
        outage_data.append({"prefecture": "香川県", "city": area, "towns": [area], "raw_towns": area})
    created_time_str = st.session_state.sim_created_time
else:
    st.markdown('<div class="section-title">1. 🌐 Webリアルタイム停電情報</div>', unsafe_allow_html=True)
    outage_data = fetch_outage_info()
    created_time_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

def check_outage(address, outage_list):
    for item in outage_list:
        city_clean = re.sub(r'.*郡', '', item["city"])
        if city_clean in str(address):
            return True, item["city"]
        for town in item["towns"]:
            if town and town in str(address):
                return True, town
    return False, "正常"

def calc_triage_level(device, battery):
    d, b = str(device), str(battery)
    if "人工呼吸器" in d:
        return "Lv.4 (最優先)", 4
    elif "人工透析" in d or "在宅酸素" in d:
        return "Lv.3 (高リスク)", 3
    elif d != "なし":
        return ("Lv.3 (高リスク)", 3) if b in ["ー", "？"] else ("Lv.2 (中リスク)", 2)
    return "Lv.1 (要確認)", 1

results = []
alerts = []

for idx, row in st.session_state.patients_data.iterrows():
    p_id = str(row.get("ID", f"P{idx+1:03d}"))
    is_outage, area_info = check_outage(str(row["住所"]), outage_data)
    triage_label, triage_score = calc_triage_level(row.get("使用装置", "なし"), row.get("バッテリ", "ー"))
    status_info = st.session_state.patient_status.get(p_id, {"status": "未対応", "updated_at": "-"})
    
    results.append({
        "ID": p_id,
        "対応ステータス": status_info["status"],
        "更新時刻": status_info["updated_at"],
        "停電リスク": "⚠️ 停電可能性あり" if is_outage else "🟢 正常",
        "トリアージ": triage_label,
        "triage_score": triage_score,
        "検知エリア": area_info if is_outage else "-",
        "患者名": row["患者名"],
        "使用装置": row.get("使用装置", "なし"),
        "バッテリ": row.get("バッテリ", "ー"),
        "担当医": row.get("担当医", "-"),
        "連絡先": row.get("連絡先", "-"),
        "住所": row["住所"],
        "備考": row.get("備考", ""),
        "lat": float(row.get("lat", 34.3400)),
        "lon": float(row.get("lon", 134.0450))
    })
    if is_outage:
        alerts.append(row)

df_result = pd.DataFrame(results)

df_visit_needed = df_result[
    (df_result["停電リスク"].str.contains("⚠️")) & 
    (df_result["対応ステータス"].isin(["未対応", "緊急訪問中"]))
]

# ---------------------------------------------------------
# 最適ルート計算関数
# ---------------------------------------------------------
def calculate_optimal_route(start_lat, start_lon, target_df):
    unvisited = target_df.copy().to_dict('records')
    route = []
    curr_lat, curr_lon = start_lat, start_lon
    
    while unvisited:
        unvisited.sort(key=lambda x: (
            -x["triage_score"],
            calc_distance_km(curr_lat, curr_lon, x["lat"], x["lon"])
        ))
        next_spot = unvisited.pop(0)
        dist = calc_distance_km(curr_lat, curr_lon, next_spot["lat"], next_spot["lon"])
        next_spot["visit_order"] = len(route) + 1
        next_spot["distance_from_prev_km"] = round(dist, 2)
        route.append(next_spot)
        curr_lat, curr_lon = next_spot["lat"], next_spot["lon"]
        
    return pd.DataFrame(route)

df_route = pd.DataFrame()
if not df_visit_needed.empty:
    df_route = calculate_optimal_route(doc_lat, doc_lon, df_visit_needed)

# ---------------------------------------------------------
# メトリクス表示
# ---------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{len(st.session_state.patients_data)}</div><div class="metric-label">総登録患者数</div></div>', unsafe_allow_html=True)
with m2:
    st.markdown(f'<div class="metric-card"><div class="metric-value metric-danger">{len(alerts)}</div><div class="metric-label">⚠️ 停電対象患者</div></div>', unsafe_allow_html=True)
with m3:
    st.markdown(f'<div class="metric-card"><div class="metric-value metric-danger">{len(df_visit_needed)}</div><div class="metric-label">🚗 要訪問・未対応</div></div>', unsafe_allow_html=True)
with m4:
    total_km = df_route["distance_from_prev_km"].sum() if not df_route.empty else 0
    val_str = f"{round(total_km, 1)} km" if st.session_state.show_route else "ー"
    st.markdown(f'<div class="metric-card"><div class="metric-value">{val_str}</div><div class="metric-label">🗺️ 巡回ルート長</div></div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# リスト表示
# ---------------------------------------------------------
st.markdown('<div class="section-title">2. 📋 登録患者・停電照合リスト（全件）</div>', unsafe_allow_html=True)

display_cols = ["ID", "対応ステータス", "停電リスク", "トリアージ", "患者名", "使用装置", "バッテリ", "住所", "担当医", "連絡先", "更新時刻"]
st.dataframe(df_result[display_cols], use_container_width=True, height=280)

# ---------------------------------------------------------
# 3. 🗺️ マップ ＆ 巡回ルート提案
# ---------------------------------------------------------
st.markdown('<div class="section-title">3. 🗺️ 訪問対象マップ & 巡回ルート</div>', unsafe_allow_html=True)

col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    if st.button("🗺️ 巡回ルートを計算・表示", type="primary"):
        st.session_state.show_route = True

if not df_visit_needed.empty:
    col_map, col_info = st.columns([3, 2])
    with col_map:
        m = folium.Map(location=[doc_lat, doc_lon], zoom_start=13, tiles="OpenStreetMap")
        
        # 拠点マーカー
        folium.Marker(
            location=[doc_lat, doc_lon],
            popup=f"<b>🏁 出発拠点</b><br>{st.session_state.doctor_address}",
            tooltip="🏁 出発拠点（現在地）",
            icon=folium.Icon(color="black", icon="home", prefix="fa")
        ).add_to(m)

        # モードA: ボタンを押す前（対象患者の位置に赤ピンのみ表示）
        if not st.session_state.show_route:
            for _, row in df_visit_needed.iterrows():
                pt = [float(row["lat"]), float(row["lon"])]
                folium.Marker(
                    location=pt,
                    popup=f"<b>{row['患者名']} 様</b><br>{row['トリアージ']}<br>{row['住所']}",
                    tooltip=f"⚠️ {row['患者名']} 様",
                    icon=folium.Icon(color="red", icon="user", prefix="fa")
                ).add_to(m)
            st_folium(m, width="100%", height=480)

        # モードB: ボタンを押した後（順序付きピン＆巡回ルート線描画）
        else:
            route_points = [[doc_lat, doc_lon]]
            for _, row in df_route.iterrows():
                order = int(row["visit_order"])
                pt = [float(row["lat"]), float(row["lon"])]
                route_points.append(pt)
                
                # 数字付きのカスタムマーカー
                icon_html = f"""
                <div style="
                    background-color: #DC2626;
                    color: #FFFFFF;
                    border-radius: 50%;
                    width: 28px;
                    height: 28px;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    font-weight: bold;
                    font-size: 14px;
                    border: 2px solid #FFFFFF;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.5);
                ">{order}</div>
                """
                
                folium.Marker(
                    location=pt,
                    popup=f"<b>#{order} {row['患者名']} 様</b><br>{row['トリアージ']}<br>{row['住所']}",
                    tooltip=f"#{order} {row['患者名']} 様",
                    icon=folium.DivIcon(
                        html=icon_html,
                        icon_size=(28, 28),
                        icon_anchor=(14, 14)
                    )
                ).add_to(m)
            
            # 各ルートを青い線で重畳描画
            folium.PolyLine(
                route_points,
                color="#2563EB",
                weight=6,
                opacity=0.85
            ).add_to(m)
            
            st_folium(m, width="100%", height=480)
            
    with col_info:
        if not st.session_state.show_route:
            st.info("💡 「巡回ルートを計算・表示」ボタンを押すと、トリアージと距離を考慮した最適な訪問順序と巡回ルートが描画されます。")
            st.markdown("**【訪問対象患者】**")
            for _, r in df_visit_needed.iterrows():
                st.markdown(f"- ⚠️ **{r['患者名']} 様** ({r['トリアージ']}) - {r['住所']}")
        else:
            st.markdown(f"**📍 出発地:** `{st.session_state.doctor_address}`")
            st.markdown(f"**🚗 巡回件数:** `{len(df_route)} 件` | **総距離:** `{round(df_route['distance_from_prev_km'].sum(), 1)} km`")
            
            waypoints = "|".join([urllib.parse.quote(r["住所"]) for _, r in df_route.iterrows()])
            gmap_multi_url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(st.session_state.doctor_address)}&destination={urllib.parse.quote(df_route.iloc[-1]['住所'])}&waypoints={waypoints}"
            
            st.markdown(f"""
            <a href="{gmap_multi_url}" target="_blank" style="display:inline-block; background-color:#2563EB; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:600; font-size:13px; margin-bottom:12px;">
                📱 Googleマップで全巡回ルートを開く
            </a>
            """, unsafe_allow_html=True)
            
            st.markdown("**【訪問順序リスト】**")
            for _, r in df_route.iterrows():
                st.markdown(f"""
                <div class="route-item">
                    <b>#{r['visit_order']} {r['患者名']} 様</b> ({r['トリアージ']})<br>
                    📍 {r['住所']} <span style="opacity:0.8; font-size:0.85rem;">(前地点より {r['distance_from_prev_km']}km)</span>
                </div>
                """, unsafe_allow_html=True)
else:
    st.info("現在、訪問が必要な未対応の停電対象患者はいません。")

# ---------------------------------------------------------
# 4. メール機能
# ---------------------------------------------------------
st.markdown('<div class="section-title">4. 📧 緊急メールアラート送信（返信機能付き）</div>', unsafe_allow_html=True)

effective_reply_to = reply_to_email.strip() if reply_to_email.strip() else sender_email

col_m_input, col_m_btn = st.columns([3, 1])
with col_m_input:
    target_email = st.text_input("送信先メールアドレス（現場スタッフ・担当医）", value="Yojirochocolat@mail.com")
    st.caption(f"ℹ️ 受信者が返信すると **`{effective_reply_to}`** 宛に届きます。")

with col_m_btn:
    st.write(" ")
    st.write(" ")
    send_trigger = st.button("🚨 アラートメール送信", use_container_width=True)

if send_trigger:
    if not df_route.empty:
        waypoints = "|".join([urllib.parse.quote(r["住所"]) for _, r in df_route.iterrows()])
        gmap_multi_url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(st.session_state.doctor_address)}&destination={urllib.parse.quote(df_route.iloc[-1]['住所'])}&waypoints={waypoints}"
        
        subject = f"【緊急アラート】停電発生による訪問対応依頼（対象 {len(df_route)} 名）"
        body = "担当各位\n\nリアルタイム停電アラートシステムより自動通知します。\n"
        body += "以下の地域で停電が発生（または可能性）しており、緊急訪問が必要な患者様が抽出されました。\n\n"
        body += f"■ 出発拠点: {st.session_state.doctor_address}\n"
        body += f"■ 対象件数: {len(df_route)} 名\n"
        body += f"■ 推定巡回距離: {round(df_route['distance_from_prev_km'].sum(), 1)} km\n\n"
        body += "--------------------------------------------------\n"
        body += "【推奨巡回ルート順序】\n"
        
        for _, r in df_route.iterrows():
            body += f"\n#{r['visit_order']} {r['患者名']} 様 ({r['トリアージ']})\n"
            body += f"  ・使用装置: {r['使用装置']} (バッテリ:{r['バッテリ']})\n"
            body += f"  ・住所: {r['住所']}\n"
            body += f"  ・連絡先: {r['連絡先']}\n"
            body += f"  ・前地点からの距離: {r['distance_from_prev_km']} km\n"
            
        body += "\n--------------------------------------------------\n"
        body += f"▼ Googleマップ一括ナビ用URL:\n{gmap_multi_url}\n\n"
        body += f"※現場からの状況報告やお問い合わせは、本メールに直接返信（{effective_reply_to} 宛）してください。\n"
        body += "※本メールは停電アラートシステムより自動送信されています。"
        
        with st.spinner("メール送信中..."):
            success, msg = send_email_alert(target_email, effective_reply_to, subject, body, smtp_config)
            if success:
                st.success(f"✅ {target_email} 宛に緊急アラートメールを送信しました。\n(返信先: {effective_reply_to})")
            else:
                st.error(f"❌ 送信エラー: {msg}")
    else:
        st.info("現在、送信対象となる未対応の停電患者はいません。")

# ---------------------------------------------------------
# 5. ステータス更新
# ---------------------------------------------------------
st.markdown('<div class="section-title">5. 📝 現場ステータス更新</div>', unsafe_allow_html=True)

if len(df_visit_needed) > 0:
    col_p, col_s, col_b = st.columns([3, 2, 1])
    with col_p:
        target_p = st.selectbox(
            "対象患者を選択",
            options=(df_visit_needed["ID"] + " : " + df_visit_needed["患者名"] + " (" + df_visit_needed["トリアージ"] + ")")
        )
        selected_p_id = target_p.split(" : ")[0]
    with col_s:
        new_status = st.selectbox("ステータス", ["未対応", "緊急訪問中", "安否確認済（安全）", "連絡中"], key=f"select_{selected_p_id}")
    with col_b:
        st.write(" ")
        st.write(" ")
        if st.button("💾 更新", use_container_width=True):
            st.session_state.patient_status[selected_p_id] = {
                "status": new_status,
                "updated_at": datetime.now(JST).strftime("%H:%M")
            }
            st.success("ステータスを更新しました")
            st.rerun()
else:
    st.info("現在、対応更新が必要な停電対象患者はいません。")
