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
from streamlit_autorefresh import st_autorefresh

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
            padding-top: 2rem !important; /* 少し余白を広げて見切れを防ぐ */
            padding-bottom: 1rem !important;
        }
        [data-testid="stSidebarUserContent"] {
            padding-top: 0.3rem !important;
        }
        header {visibility: visible !important;}
        [data-testid="stHeader"] {display: block !important;}
        [data-testid="stAppHeaderActionElements"] {display: none !important; }
        .stAppDeployButton {display: none !important;}
        div[data-testid="stDataFrame"] div[role="gridcell"] {
            white-space: normal !important;
            line-height: 1.25rem !important;
        }
        /* タイトルの見切れ防止と余白確保 */
        h1 {
            overflow: visible !important;
            line-height: 1.3 !important;
            padding-top: 0.5rem !important;
            margin-bottom: 0.5rem !important;
        }
    </style>
""", unsafe_allow_html=True)

st.title("💡 停電アラート")
st.caption("リアルタイムの停電情報と患者リストを照合し、優先度自動トリアージとナビ連携で初動対応を支援します。")

# ---------------------------------------------------------
# セッション状態の初期化
# ---------------------------------------------------------
def init_session_state():
    defaults = {
        "sim_areas": [],
        "sim_created_time": datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
        "last_fetch_time": "未取得",
        "patient_status": {},
        "patients_data": None,
        "layout_option": "左右並べ（PC・大画面向け）",
        "realtime_outage_data": [],
        "auto_refresh_enabled": False,
        "auto_filtered_once": False, # 自動絞り込み判定フラグ
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# ---------------------------------------------------------
# 住所から緯度・経度を取得する関数（ジオコーディング）
# ---------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def geocode_address(address):
    if not address or pd.isna(address) or str(address).strip() in ["-", ""]:
        return 34.3400, 134.0450
    address_str = str(address)
    if "サンポート" in address_str or "シンボルタワー" in address_str or "高松駅" in address_str:
        return 34.3533, 134.0470
    try:
        url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={requests.utils.quote(address_str)}"
        res = requests.get(url, timeout=3).json()
        if res and len(res) > 0:
            lon, lat = res[0]["geometry"]["coordinates"]
            return lat, lon
    except Exception:
        pass
    return 34.3400, 134.0450


def ensure_lat_lon(df, seed=999):
    """lat/lon列があれば再ジオコーディングせず、不足行だけ補完する。"""
    df = df.copy()
    if "lat" not in df.columns:
        df["lat"] = pd.NA
    if "lon" not in df.columns:
        df["lon"] = pd.NA

    random.seed(seed)
    for idx, row in df.iterrows():
        lat_val = row.get("lat")
        lon_val = row.get("lon")
        if pd.notna(lat_val) and pd.notna(lon_val):
            continue
        base_lat, base_lon = geocode_address(row.get("住所", ""))
        df.at[idx, "lat"] = base_lat + random.uniform(-0.00015, 0.00015)
        df.at[idx, "lon"] = base_lon + random.uniform(-0.00015, 0.00015)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce").fillna(34.3400)
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce").fillna(134.0450)
    return df

# ---------------------------------------------------------
# 1. 四国電力の停電情報を取得する関数
# ---------------------------------------------------------
PREFECTURE_URLS = {
    "香川県": "https://www.yonden.co.jp/nw/teiden-info/kagawa.html",
    "徳島県": "https://www.yonden.co.jp/nw/teiden-info/tokushima.html",
    "愛媛県": "https://www.yonden.co.jp/nw/teiden-info/ehime.html",
    "高知県": "https://www.yonden.co.jp/nw/teiden-info/kochi.html",
}


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def split_towns(raw_towns):
    raw = normalize_text(raw_towns)
    if not raw or raw == "-":
        return []
    return [t.strip() for t in re.split(r"[、,\s]+", raw) if t.strip()]


def detect_column_map(headers):
    col_map = {"city": None, "town": None, "reason": None, "status": None}
    for i, h in enumerate(headers):
        text = normalize_text(h)
        if any(k in text for k in ["市区町村", "市町村", "市町", "地区"]):
            col_map["city"] = i
        elif any(k in text for k in ["町名", "対象町名", "地域", "地区名"]):
            col_map["town"] = i
        elif any(k in text for k in ["停電理由", "理由", "原因"]):
            col_map["reason"] = i
        elif any(k in text for k in ["対応状況", "状況", "復旧", "作業"]):
            col_map["status"] = i
    return col_map


def parse_outage_table(table, pref_name, announced_at):
    records = []
    rows = table.find_all("tr")
    if not rows:
        return records

    header_cols = [normalize_text(ele.get_text(" ", strip=True)) for ele in rows[0].find_all(["th", "td"])]
    col_map = detect_column_map(header_cols)

    for row in rows[1:] if any(header_cols) else rows:
        cols = [normalize_text(ele.get_text(" ", strip=True)) for ele in row.find_all(["td", "th"])]
        cols = [c for c in cols if c != ""]
        if not cols:
            continue
        joined = " ".join(cols)
        if "発生日時" in joined or "停電情報はありません" in joined:
            continue

        city = "-"
        town = "-"
        reason = "-"
        status = "-"

        if col_map["city"] is not None and col_map["city"] < len(cols):
            city = cols[col_map["city"]]
        if col_map["town"] is not None and col_map["town"] < len(cols):
            town = cols[col_map["town"]]
        if col_map["reason"] is not None and col_map["reason"] < len(cols):
            reason = cols[col_map["reason"]]
        if col_map["status"] is not None and col_map["status"] < len(cols):
            status = cols[col_map["status"]]

        if city == "-" or town == "-":
            if len(cols) >= 6:
                city = cols[1]
                town = cols[2]
                reason = cols[4]
                status = cols[5]
            elif len(cols) >= 5:
                city = cols[0]
                town = cols[1]
                reason = cols[3]
                status = cols[4]
            elif len(cols) >= 4:
                city = cols[0]
                town = cols[1]
                reason = cols[2]
                status = cols[3]
            elif len(cols) >= 2:
                city = cols[0]
                town = cols[1]

        if city == pref_name and town not in ["-", ""]:
            match = re.match(r"(.+?[市町村])\s*(.*)", town)
            if match:
                city = match.group(1)
                town = match.group(2).strip() or town

        towns = split_towns(town)
        if city not in ["-", ""] and town not in ["-", ""]:
            records.append({
                "prefecture": pref_name,
                "city": city,
                "town": town,
                "towns": towns,
                "raw_towns": town,
                "reason": reason,
                "status": status,
                "announced_at": announced_at,
            })
    return records


@st.cache_data(ttl=300, show_spinner=False)
def fetch_outage_info():
    outage_list = []
    errors = []
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
            body_text = soup.get_text(" ", strip=True)

            time_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}時\d{1,2}分\s*現在)", body_text)
            announced_at = time_match.group(1) if time_match else "日時不明"
            no_outage = "停電情報はありません" in body_text

            if no_outage:
                continue

            tables = soup.find_all("table")
            for table in tables:
                records = parse_outage_table(table, pref_name, announced_at)
                outage_list.extend(records)
        except Exception as e:
            errors.append(f"{pref_name}: {e}")
            continue

    return outage_list, errors

# ---------------------------------------------------------
# 2. デモ用データ・添付ファイルDL関数
# ---------------------------------------------------------
@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤", "吉田", "山田", "松本", "井上", "木村"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子", "大輔", "美咲", "直樹", "裕子"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    kagawa_spots = [
        "香川県高松市栗林町1丁目", "香川県高松市栗林町2丁目", "香川県高松市宮脇町1丁目", "香川県高松市宮脇町2丁目",
        "香川県高松市昭和町1丁目", "香川県高松市茜町", "香川県高松市扇町1丁目", "香川県高松市紫雲町",
        "香川県高松市中新町", "香川県高松市藤塚町1丁目", "香川県高松市天神前", "香川県高松市錦町1丁目",
        "香川県高松市番町1丁目", "香川県高松市番町3丁目", "香川県高松市瓦町1丁目", "香川県高松市塩上町1丁目",
        "香川県高松市観光通1丁目", "香川県高松市木太町", "香川県高松市伏石町", "香川県高松市太田上町",
        "香川県高松市多肥上町", "香川県高松市多肥下町", "香川県高松市今里町1丁目", "香川県高松市松縄町",
        "香川県高松市林町", "香川県高松市三条町", "香川県高松市一宮町", "香川県高松市香西本町",
        "香川県高松市香西南町", "香川県高松市屋島西町", "香川県高松市屋島中町", "香川県高松市春日町",
        "香川県高松市川島東町", "香川県高松市福岡町1丁目", "香川県高松市福岡町3丁目", "香川県高松市古馬場町",
        "香川県高松市兵庫町", "香川県高松市片原町", "香川県高松市丸亀町", "香川県高松市南新町",
        "香川県高松市田町", "香川県高松市花園町1丁目", "香川県高松市築地町", "香川県高松市塩江町安原上",
        "香川県高松市国分寺町新居", "香川県高松市牟礼町牟礼", "香川県高松市庵治町", "香川県高松市香川町川東上",
        "香川県高松市鬼無町佐藤", "香川県高松市檀紙町"
    ]
    device_options = ["なし", "人工呼吸器", "人工透析装置", "ペースメーカー"]
    device_weights = [0.5, 0.2, 0.15, 0.15]
    patients = []
    random.seed(42)
    spots_shuffled = kagawa_spots.copy()
    random.shuffle(spots_shuffled)
    for i in range(1, 51):
        name = f"{random.choice(last_names)} {random.choice(first_names)}"
        spot_addr = spots_shuffled[i - 1]
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


def load_template_file():
    xlsx_files = glob.glob("*.xlsx")
    target_file = None
    for f in xlsx_files:
        if not f.startswith("~$"):
            target_file = f
            break
    if target_file and os.path.exists(target_file):
        with open(target_file, "rb") as f:
            return f.read(), os.path.basename(target_file)
    sample_data = [{
        "ID": "P001", "患者名": "山田 太郎", "住所": "香川県高松市宮脇町1丁目1-1",
        "担当医": "佐藤医師", "連絡先": "090-1234-56XX", "使用装置": "人工呼吸器",
        "バッテリ": "○", "備考": "要緊急確認"
    }]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(sample_data).to_excel(writer, index=False, sheet_name="患者リスト")
    output.seek(0)
    return output.getvalue(), "患者リスト_登録フォーマット.xlsx"


if st.session_state.patients_data is None:
    initial_df = pd.DataFrame(generate_50_kagawa_patients())
    st.session_state.patients_data = ensure_lat_lon(initial_df, seed=123)

# ---------------------------------------------------------
# 3. サイドバー設定 & データ読み込み
# ---------------------------------------------------------
st.sidebar.header("⚙️ 動作設定")
mode = st.sidebar.radio(
    "情報取得モード",
    options=["仮想シミュレーションモード", "リアルタイムWeb取得モード"],
    key="input_fetch_mode_v3"
)

auto_refresh_enabled = st.sidebar.toggle(
    "5分ごとの自動更新",
    value=False,
    key="auto_refresh_enabled",
    help="ONにした場合のみ5分ごとに画面を自動更新します。通常はOFFで、手動更新を推奨します。"
)
if auto_refresh_enabled:
    st_autorefresh(interval=300000, key="data_auto_refresh")

current_location_addr = st.sidebar.text_input(
    "📍 現住所（拠点・現在地）",
    value="高松市サンポート2番1号",
    key="input_current_location_v3",
    help="マップ上に拠点ピン(青)として表示され、ナビ起動時の標準出発地として使用されます"
)

st.sidebar.markdown("---")
st.sidebar.subheader("🏃 スタッフ現在地設定")
col_s1_text, col_s1_toggle = st.sidebar.columns([3, 1])
with col_s1_text:
    staff1_location_addr = st.text_input(
        "スタッフ1の現在地",
        value="",
        placeholder="例: 高松市瓦町1丁目",
        key="input_staff1_location_v1",
        help="マップ上にスタッフ1のピン(橙)として表示されます"
    )
with col_s1_toggle:
    st.write(" ")
    staff1_show_pin = st.toggle("表示", value=True, key="toggle_staff1_pin")

col_s2_text, col_s2_toggle = st.sidebar.columns([3, 1])
with col_s2_text:
    staff2_location_addr = st.text_input(
        "スタッフ2の現在地",
        value="",
        placeholder="例: 高松市栗林町2丁目",
        key="input_staff2_location_v1",
        help="マップ上にスタッフ2のピン(紫)として表示されます"
    )
with col_s2_toggle:
    st.write(" ")
    staff2_show_pin = st.toggle("表示", value=True, key="toggle_staff2_pin")

st.sidebar.markdown("---")
st.sidebar.header("📂 データ追加・更新設定")
template_bytes, template_filename = load_template_file()
st.sidebar.download_button(
    label="📥 患者リスト登録フォーマット(Excel)をDL",
    data=template_bytes,
    file_name=template_filename,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True
)
update_mode = st.sidebar.radio(
    "取り込み方法を選択",
    options=["現リストに追加", "現リストと入れ替え"],
    index=0,
    key="input_update_mode_v3"
)
uploaded_file = st.sidebar.file_uploader("手元の患者リスト(Excel/CSV)を選択", type=["xlsx", "csv"])
col_btn1, col_btn2 = st.sidebar.columns([1, 1])
with col_btn1:
    btn_update = st.button("データ更新", type="primary", use_container_width=True)
with col_btn2:
    btn_reset = st.button("🔄 初期データに戻す", type="secondary", use_container_width=True)

if btn_update:
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith(".csv"):
                new_df = pd.read_csv(uploaded_file)
            else:
                new_df = pd.read_excel(uploaded_file)
            target_cols = ["ID", "患者名", "住所", "担当医", "連絡先", "使用装置", "バッテリ", "備考"]
            for col in target_cols:
                if col not in new_df.columns:
                    new_df[col] = "-"
            new_df = new_df.fillna("-")
            with st.spinner("位置情報を計算してデータを更新中..."):
                new_df = ensure_lat_lon(new_df, seed=999)
                if update_mode == "現リストと入れ替え":
                    st.session_state.patients_data = new_df
                    st.session_state.patient_status = {}
                    st.sidebar.success("前のデータを削除し、新規リストに入れ替えました！")
                else:
                    current_df = st.session_state.patients_data
                    combined_df = pd.concat([current_df, new_df], ignore_index=True)
                    updated_df = combined_df.drop_duplicates(subset=["患者名"], keep="last").reset_index(drop=True)
                    st.session_state.patients_data = updated_df
                    st.sidebar.success("現リストにデータを追加・上書き更新しました！")
                st.session_state.auto_filtered_once = False # 再判定のためリセット
                st.rerun()
        except Exception as e:
            st.sidebar.error(f"ファイルの読み込みに失敗しました: {e}")
    else:
        st.sidebar.warning("ファイルを選択してから「データ更新」を押してください。")

if btn_reset:
    with st.spinner("デフォルトの初期患者リスト（50名）にリセット中..."):
        initial_df = pd.DataFrame(generate_50_kagawa_patients())
        st.session_state.patients_data = ensure_lat_lon(initial_df, seed=123)
        st.session_state.patient_status = {}
        st.session_state.auto_filtered_once = False
        st.sidebar.info("🔄 初期デフォルトの患者リストにリセットしました！")
        st.rerun()

st.sidebar.caption(f"現在登録されている総患者数: **{len(st.session_state.patients_data)} 名**")

# ---------------------------------------------------------
# 4. 停電データの照合準備 & 優先度(トリアージ)ソート
# ---------------------------------------------------------
outage_data = []
created_time_str = ""

if mode == "仮想シミュレーションモード":
    st.subheader("1. 停電エリア・シミュレーター")
    col_input, col_btn1, col_btn2, _ = st.columns([4, 2, 1.5, 4.5])
    with col_input:
        sim_input = st.text_input(
            "停電が発生したと想定する地域（市町村や町名）を入力",
            value=",".join(st.session_state.sim_areas),
            placeholder="例: 宮脇町, 木太町, 栗林町1丁目"
        )
    with col_btn1:
        st.write(" ")
        st.write(" ")
        if st.button("▶️ シミュレーション実行", use_container_width=True):
            st.session_state.sim_areas = [a.strip() for a in sim_input.split(",") if a.strip()]
            st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.session_state.auto_filtered_once = False # シミュレーション実行時に自動絞り込みを再適用
            st.success("シミュレーションを実行・作成日時を更新しました！")
    with col_btn2:
        st.write(" ")
        st.write(" ")
        if st.button("🔄 リセット", use_container_width=True):
            st.session_state.sim_areas = []
            st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.session_state.auto_filtered_once = False
            st.rerun()

    for area in st.session_state.sim_areas:
        outage_data.append({
            "prefecture": "香川県",
            "city": area,
            "town": area,
            "towns": [area],
            "raw_towns": area,
            "reason": "シミュレーション",
            "status": "仮想停電エリアとして設定",
            "announced_at": st.session_state.sim_created_time,
        })
    created_time_str = st.session_state.sim_created_time
    if st.session_state.sim_areas:
        st.caption(f"現在のテスト対象エリア: **{', '.join(st.session_state.sim_areas)}** (作成日時: {created_time_str})")
    else:
        st.caption(f"現在のテスト対象エリア: **指定なし（全員正常）** (作成日時: {created_time_str})")
else:
    col_rt_title, col_rt_btn, _ = st.columns([4, 1.5, 4.5])
    with col_rt_title:
        st.subheader("1. Webリアルタイム停電情報（四国版）")
    with col_rt_btn:
        if st.button("🔄 最新情報に更新", help="四国電力の最新データを手動取得します", use_container_width=True):
            fetch_outage_info.clear()
            with st.spinner("最新の停電情報を取得中..."):
                fetched_data, fetch_errors = fetch_outage_info()
            st.session_state.realtime_outage_data = fetched_data
            st.session_state.last_fetch_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.session_state.auto_filtered_once = False
            if fetch_errors:
                st.warning("一部県の取得に失敗しました: " + " / ".join(fetch_errors))
            st.rerun()

    if st.session_state.last_fetch_time == "未取得" or auto_refresh_enabled:
        with st.spinner("停電情報を取得中..."):
            fetched_data, fetch_errors = fetch_outage_info()
        st.session_state.realtime_outage_data = fetched_data
        st.session_state.last_fetch_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        st.session_state.auto_filtered_once = False
        if fetch_errors:
            st.warning("一部県の取得に失敗しました: " + " / ".join(fetch_errors))

    outage_data = st.session_state.realtime_outage_data
    created_time_str = st.session_state.last_fetch_time

    if outage_data:
        outage_df_display = pd.DataFrame([
            {
                "都道府県": item.get("prefecture", "-"),
                "市区町村": item.get("city", "-"),
                "対象町名": item.get("town", item.get("raw_towns", "-")),
                "停電理由": item.get("reason", "-"),
                "対応状況": item.get("status", "-"),
                "サイト発表日時": item.get("announced_at", "-"),
            }
            for item in outage_data
        ])
        st.dataframe(
            outage_df_display,
            use_container_width=True,
            hide_index=True,
            height=min(260, 58 + len(outage_df_display) * 58),
            column_config={
                "都道府県": st.column_config.TextColumn("都道府県", width="small"),
                "市区町村": st.column_config.TextColumn("市区町村", width="small"),
                "対象町名": st.column_config.TextColumn("対象町名", width="small"),
                "停電理由": st.column_config.TextColumn("停電理由", width="small"),
                "対応状況": st.column_config.TextColumn("対応状況", width="medium"),
                "サイト発表日時": st.column_config.TextColumn("サイト発表日時", width="small"),
            }
        )
    else:
        st.success(f"現在（{created_time_str} 取得）、四国版のWebサイト上に該当する停電情報はありません。")


def check_outage(address, outage_list):
    if not outage_list:
        return False, "正常"
    addr_str = str(address)
    for item in outage_list:
        city = str(item.get("city", ""))
        if city and city != "-" and city in addr_str:
            return True, city
        for town in item.get("towns", []):
            if town and town != "-" and town in addr_str:
                return True, town
        raw_town = str(item.get("town", item.get("raw_towns", "")))
        if raw_town and raw_town != "-" and raw_town in addr_str:
            return True, raw_town
    return False, "正常"


def calc_triage_level(device, battery):
    d = str(device)
    b = str(battery)
    if "人工呼吸器" in d:
        return "Lv.4", 4
    elif "人工透析" in d or "在宅酸素" in d:
        return "Lv.3", 3
    elif d != "なし":
        if b in ["ー", "？"]:
            return "Lv.3", 3
        return "Lv.2", 2
    return "Lv.1", 1

results = []
for idx, row in st.session_state.patients_data.iterrows():
    p_id = str(row.get("ID", f"P{idx+1:03d}"))
    is_outage, area_info = check_outage(str(row["住所"]), outage_data)
    triage_label, triage_score = calc_triage_level(row.get("使用装置", "なし"), row.get("バッテリ", "ー"))
    status_info = st.session_state.patient_status.get(p_id, {})
    current_status = status_info.get("status", "未対応")
    updated_at = status_info.get("updated_at", "-")
    if "override_outage" in status_info:
        current_outage_str = status_info["override_outage"]
    else:
        current_outage_str = "⚠️ 停電可能性あり" if is_outage else "🟢 正常"
    results.append({
        "ID": p_id,
        "対応ステータス": current_status,
        "更新時刻": updated_at,
        "停電リスク": current_outage_str,
        "トリアージ": triage_label,
        "triage_score": triage_score,
        "検知エリア": area_info if "⚠️" in current_outage_str else "-",
        "患者名": row["患者名"],
        "使用装置": row.get("使用装置", "なし"),
        "バッテリ": row.get("バッテリ", "ー"),
        "担当医": row.get("担当医", "-"),
        "連絡先": row.get("連絡先", "-"),
        "住所": row["住所"],
        "備考": row.get("備考", ""),
        "lat": float(row.get("lat", 34.3400)),
        "lon": float(row.get("lon", 134.0450)),
    })

df_result = pd.DataFrame(results)
df_result["risk_sort"] = df_result["停電リスク"].apply(lambda x: 0 if "⚠️" in x else 1)
df_result = df_result.sort_values(by=["risk_sort", "triage_score"], ascending=[True, False]).drop(columns=["risk_sort"])
df_alert_all = df_result[df_result["停電リスク"].str.contains("⚠️")]
df_visit_target = df_alert_all[df_alert_all["対応ステータス"] != "安否確認済（安全）"]

# ★ 停電対象者がいて、まだ自動チェックが適用されていない場合は自動でONにする
if len(df_visit_target) > 0 and not st.session_state.get("auto_filtered_once", False):
    st.session_state["filter_unhandled"] = True
    st.session_state["auto_filtered_once"] = True

# ---------------------------------------------------------
# 5. 地図描画関数
# ---------------------------------------------------------
def build_map(df, target_only=False, home_address="", staff1_address="", staff2_address="", selected_patient_id=None, show_staff1=True, show_staff2=True):
    if target_only:
        display_df = df[(df["停電リスク"].str.contains("⚠️")) & (df["対応ステータス"] != "安否確認済（安全）")]
    else:
        display_df = df

    home_lat, home_lon = geocode_address(home_address)
    target_patient = None
    if selected_patient_id and selected_patient_id != "選択なし（全体表示）":
        matched = df[df["ID"] == selected_patient_id]
        if not matched.empty:
            target_patient = matched.iloc[0]

    if target_patient is not None:
        m = folium.Map(location=[target_patient["lat"], target_patient["lon"]], zoom_start=18)
    else:
        m = folium.Map(location=[home_lat, home_lon], zoom_start=15)

    marker_cluster = MarkerCluster(disableClusteringAtZoom=16).add_to(m)
    bounds_points = []

    if home_address and home_address.strip() != "":
        home_popup = f"""
        <div style='font-size:12px; width:180px;'>
            <b style='color:blue;'>📍 現住所（拠点）</b><br>
            <b>場所:</b> {home_address}
        </div>
        """
        folium.Marker(
            location=[home_lat, home_lon],
            popup=folium.Popup(home_popup, max_width=200),
            tooltip=f"📍 現住所 ({home_address})",
            icon=folium.Icon(color="blue", icon="home", prefix="fa")
        ).add_to(m)
        bounds_points.append([home_lat, home_lon])

    if show_staff1 and staff1_address and staff1_address.strip() != "":
        s1_lat, s1_lon = geocode_address(staff1_address)
        s1_popup = f"""
        <div style='font-size:12px; width:180px;'>
            <b style='color:orange;'>🏃 スタッフ1</b><br>
            <b>現在地:</b> {staff1_address}
        </div>
        """
        folium.Marker(
            location=[s1_lat, s1_lon],
            popup=folium.Popup(s1_popup, max_width=200),
            tooltip=f"🏃 スタッフ1 ({staff1_address})",
            icon=folium.Icon(color="orange", icon="user", prefix="fa")
        ).add_to(m)
        bounds_points.append([s1_lat, s1_lon])

    if show_staff2 and staff2_address and staff2_address.strip() != "":
        s2_lat, s2_lon = geocode_address(staff2_address)
        s2_popup = f"""
        <div style='font-size:12px; width:180px;'>
            <b style='color:purple;'>🏃 スタッフ2</b><br>
            <b>現在地:</b> {staff2_address}
        </div>
        """
        folium.Marker(
            location=[s2_lat, s2_lon],
            popup=folium.Popup(s2_popup, max_width=200),
            tooltip=f"🏃 スタッフ2 ({staff2_address})",
            icon=folium.Icon(color="purple", icon="user", prefix="fa")
        ).add_to(m)
        bounds_points.append([s2_lat, s2_lon])

    for _, row in display_df.iterrows():
        is_alert = "⚠️" in row["停電リスク"]
        if row["対応ステータス"] == "安否確認済（安全）":
            color = "gray"
            icon_type = "check-circle"
            status_badge = "<span style='color:gray;'>⚪ 確認済</span>"
        elif is_alert:
            color = "red"
            icon_type = "exclamation-triangle"
            status_badge = "<span style='color:red;'>🔴 停電未対応</span>"
        else:
            color = "green"
            icon_type = "user"
            status_badge = "<span style='color:green;'>🟢 停電なし</span>"

        encoded_origin = urllib.parse.quote(str(home_address))
        encoded_dest = urllib.parse.quote(str(row["住所"]))
        nav_url = f"https://www.google.com/maps/dir/?api=1&origin={encoded_origin}&destination={encoded_dest}"
        tel_clean = str(row["連絡先"]).replace("-", "").replace("X", "").replace("x", "")
        popup_html = f"""
        <div style='font-size:12px; width:230px; line-height:1.6;'>
            <b style='color:red;'>【{row['トリアージ']}】</b><br>
            <b>氏名:</b> {row['患者名']} ({status_badge})<br>
            <b>状態:</b> {row['対応ステータス']}<br>
            <b>装置:</b> {row['使用装置']} (バッテリ:{row['バッテリ']})<br>
            <b>住所:</b> {row['住所']}<br><hr style='margin:5px 0;'>
            <a href='tel:{tel_clean}' target='_blank' style='background:#0275d8; color:white; padding:3px 8px; text-decoration:none; border-radius:3px; font-size:11px;'>📞 TEL発信</a>
            <a href='{nav_url}' target='_blank' style='background:#5cb85c; color:white; padding:3px 8px; text-decoration:none; border-radius:3px; font-size:11px; margin-left:5px;'>🗺️ ナビ起動</a>
        </div>
        """
        marker = folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{row['トリアージ']} | {row['患者名']} 様 ({row['対応ステータス']})",
            icon=folium.Icon(color=color, icon=icon_type, prefix="fa")
        )
        if target_patient is not None and row["ID"] == target_patient["ID"]:
            marker.add_to(m)
        else:
            marker.add_to(marker_cluster)
        bounds_points.append([row["lat"], row["lon"]])

    if target_patient is None:
        if len(bounds_points) > 1:
            min_lat = min(p[0] for p in bounds_points)
            max_lat = max(p[0] for p in bounds_points)
            min_lon = min(p[1] for p in bounds_points)
            max_lon = max(p[1] for p in bounds_points)
            m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=(30, 30), max_zoom=16)
        elif len(bounds_points) == 1:
            m.location = bounds_points[0]
            m.zoom_start = 16
    return m

# ---------------------------------------------------------
# 6. レポート生成処理（PDF）
# ---------------------------------------------------------
def register_japanese_font():
    font_name = "IPAGothic"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        ttf_url = "https://raw.githubusercontent.com/google/fonts/main/ofl/sawarabigothic/SawarabiGothic-Regular.ttf"
        font_path = "SawarabiGothic-Regular.ttf"
        if not os.path.exists(font_path):
            res = requests.get(ttf_url, timeout=10)
            with open(font_path, "wb") as f:
                f.write(res.content)
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    return font_name


def create_pdf_report(df_alert_patients, created_time):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15, leftMargin=15, topMargin=20, bottomMargin=20)
    story = []
    font_name = register_japanese_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleStyle", parent=styles["Heading1"], fontName=font_name, fontSize=14, leading=17, spaceAfter=8)
    normal_style = ParagraphStyle("NormalStyle", parent=styles["Normal"], fontName=font_name, fontSize=8, leading=11)
    cell_style = ParagraphStyle("CellStyle", parent=styles["Normal"], fontName=font_name, fontSize=7, leading=9.5)

    story.append(Paragraph("【要訪問対象】停電エリア要対応患者リスト", title_style))
    story.append(Paragraph(f"<b>作成日時: {created_time} 作成</b> | 訪問対象件数: {len(df_alert_patients)} 名（安否確認済除外）", normal_style))
    story.append(Spacer(1, 10))

    headers = ["ID", "トリアージ", "患者名", "状態", "使用装置", "バッテリ", "担当医", "連絡先", "住所"]
    table_data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle("HeaderStyle", parent=cell_style, textColor=colors.whitesmoke)) for h in headers]]

    for _, row in df_alert_patients.iterrows():
        table_data.append([
            Paragraph(str(row["ID"]), cell_style),
            Paragraph(str(row["トリアージ"]), cell_style),
            Paragraph(str(row["患者名"]), cell_style),
            Paragraph(str(row["対応ステータス"]), cell_style),
            Paragraph(str(row["使用装置"]), cell_style),
            Paragraph(str(row["バッテリ"]), cell_style),
            Paragraph(str(row["担当医"]), cell_style),
            Paragraph(str(row["連絡先"]), cell_style),
            Paragraph(str(row["住所"]), cell_style),
        ])

    t = Table(table_data, colWidths=[25, 45, 45, 45, 65, 30, 45, 65, 165])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a6b82")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f9")]),
    ]))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------------------------------------------------
# 7. 画面表示エリア
# ---------------------------------------------------------
col_title, col_radio = st.columns([5, 5])
with col_title:
    st.subheader("2. 患者照合結果 & マップ可視化")
with col_radio:
    layout_option = st.radio(
        "表示スタイル",
        ["左右並べ（PC・大画面向け）", "タブ切替（スマホ、省スペース向け）"],
        horizontal=True,
        label_visibility="collapsed",
        key="layout_option_radio"
    )

st.caption(f"🕒 **データ取得・リスト作成日時: {created_time_str}**")

if len(df_alert_all) > 0:
    lv4_cnt = len(df_visit_target[df_visit_target["トリアージ"] == "Lv.4"])
    confirmed_cnt = len(df_alert_all[df_alert_all["対応ステータス"] == "安否確認済（安全）"])
    st.error(f"🚨 停電エリア内に該当する患者が **{len(df_alert_all)} 名** ピックアップされました！（うち【要訪問 Lv.4】: **{lv4_cnt} 名** / 安否確認済み: **{confirmed_cnt} 名**）")

    col_dl1, col_dl2, col_dl3 = st.columns([1, 1, 1])
    with col_dl1:
        pdf_data = create_pdf_report(df_visit_target, created_time_str)
        st.download_button(
            label=f"📄 訪問対象者リスト（PDF: {len(df_visit_target)}名）をDL",
            data=pdf_data,
            file_name="訪問対象者_停電リスク患者リスト.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    with col_dl2:
        m_target_dl = build_map(
            df_result,
            target_only=True,
            home_address=current_location_addr,
            staff1_address=staff1_location_addr,
            staff2_address=staff2_location_addr,
            show_staff1=staff1_show_pin,
            show_staff2=staff2_show_pin
        )
        html_data = m_target_dl._repr_html_()
        st.download_button(
            label=f"🗺️ 訪問対象のみ拡大マップ（HTML: {len(df_visit_target)}名）をDL",
            data=html_data,
            file_name="訪問対象者_拡大マップ.html",
            mime="text/html",
            use_container_width=True
        )
    with col_dl3:
        if len(df_visit_target) > 0:
            target_for_route = df_visit_target.sort_values(by="triage_score", ascending=False)
            addresses = target_for_route["住所"].tolist()
            origin = current_location_addr
            if len(addresses) > 1:
                destination = addresses[-1]
                waypoints = "|".join(addresses[:-1])
                multi_nav_url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(origin)}&destination={urllib.parse.quote(destination)}&waypoints={urllib.parse.quote(waypoints)}"
            elif len(addresses) == 1:
                multi_nav_url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(origin)}&destination={urllib.parse.quote(addresses[0])}"
            else:
                multi_nav_url = ""
            if multi_nav_url:
                st.markdown(f'<a href="{multi_nav_url}" target="_blank" style="display:block; text-align:center; background:#5b7994; color:white; padding:8px 12px; text-decoration:none; border-radius:4px; font-weight:bold; font-size:14px; box-sizing:border-box;">🚗 巡回ルート検索（高優先順・{len(addresses)}名）</a>', unsafe_allow_html=True)
                st.caption("※Googleマップナビが別タブで起動します")
else:
    st.success("現在、停電エリアに該当する患者はいません。（全員正常）")

filter_col1, filter_col2 = st.columns([3, 3])
with filter_col1:
    only_unhandled = st.checkbox("🔍 停電可能性あり ＆ 未対応の患者のみに絞り込む", key="filter_unhandled")

if only_unhandled:
    display_target_df = df_result[(df_result["停電リスク"].str.contains("⚠️")) & (df_result["対応ステータス"] == "未対応")]
else:
    display_target_df = df_result.copy()

patient_options = ["選択なし（全体表示）"] + [
    f"{r['ID']} | {r['患者名']} 様 ({r['トリアージ']} - {r['住所']})"
    for _, r in display_target_df.iterrows()
]
with filter_col2:
    selected_option = st.selectbox(
        "🔍 特定患者にズーム（地図自動ジャンプ）",
        options=patient_options,
        index=0,
        help="選択した患者の位置へ地図が拡大（ズームレベル18）してピンポイント移動します"
    )

selected_patient_id = None
if selected_option != "選択なし（全体表示）":
    selected_patient_id = selected_option.split(" | ")[0]

display_cols = ["ID", "対応ステータス", "停電リスク", "トリアージ", "患者名", "使用装置", "バッテリ", "担当医", "連絡先", "住所"]
column_config = {
    "対応ステータス": st.column_config.SelectboxColumn("対応ステータス", options=["未対応", "連絡中", "安否確認済（安全）", "緊急訪問中"], required=True),
    "停電リスク": st.column_config.SelectboxColumn("停電リスク", options=["⚠️ 停電可能性あり", "🟢 正常"], required=True),
    "ID": st.column_config.TextColumn("ID", disabled=True),
    "トリアージ": st.column_config.TextColumn("トリアージ", disabled=True),
    "患者名": st.column_config.TextColumn("患者名", disabled=True),
    "使用装置": st.column_config.TextColumn("使用装置", disabled=True),
    "バッテリ": st.column_config.TextColumn("バッテリ", disabled=True),
    "担当医": st.column_config.TextColumn("担当医", disabled=True),
    "連絡先": st.column_config.TextColumn("連絡先", disabled=True),
    "住所": st.column_config.TextColumn("住所", disabled=True),
}

list_title_html = "#### 📋 患者リスト <span style='font-size:12px; color:gray; font-weight:normal;'>（対応ステータス・停電リスクは直接編集可）</span>"
map_legend_title = "#### 🗺️ 訪問エリアマップ <span style='font-size:13px; font-weight:normal;'>(🔵 拠点 / 🟠 スタッフ1 / 🟣 スタッフ2 / 🔴 停電未対応 / ⚪ 確認済 / 🟢 停電なし)</span>"

if layout_option == "左右並べ（PC・大画面向け）":
    col1, col2 = st.columns([6, 5])
    with col1:
        st.markdown(list_title_html, unsafe_allow_html=True)
        edited_df = st.data_editor(
            display_target_df[display_cols],
            column_config=column_config,
            use_container_width=True,
            height=450,
            hide_index=True,
            key="table_editor"
        )
    with col2:
        st.markdown(map_legend_title, unsafe_allow_html=True)
        m = build_map(
            display_target_df,
            home_address=current_location_addr,
            staff1_address=staff1_location_addr,
            staff2_address=staff2_location_addr,
            selected_patient_id=selected_patient_id,
            show_staff1=staff1_show_pin,
            show_staff2=staff2_show_pin
        )
        st_folium(m, width="100%", height=450, key="map_pc")
else:
    tab1, tab2, tab3 = st.tabs(["📋 リスト表示", "🗺️ マップ表示(全体)", "⚠️ 訪問対象者のみ拡大マップ"])
    with tab1:
        st.markdown(list_title_html, unsafe_allow_html=True)
        edited_df = st.data_editor(
            display_target_df[display_cols],
            column_config=column_config,
            use_container_width=True,
            height=450,
            hide_index=True,
            key="table_editor_tab"
        )
    with tab2:
        st.markdown(map_legend_title, unsafe_allow_html=True)
        m = build_map(
            display_target_df,
            target_only=False,
            home_address=current_location_addr,
            staff1_address=staff1_location_addr,
            staff2_address=staff2_location_addr,
            selected_patient_id=selected_patient_id,
            show_staff1=staff1_show_pin,
            show_staff2=staff2_show_pin
        )
        st_folium(m, width="100%", height=450, key="map_tab_all")
    with tab3:
        st.markdown(map_legend_title, unsafe_allow_html=True)
        m_target = build_map(
            display_target_df,
            target_only=True,
            home_address=current_location_addr,
            staff1_address=staff1_location_addr,
            staff2_address=staff2_location_addr,
            selected_patient_id=selected_patient_id,
            show_staff1=staff1_show_pin,
            show_staff2=staff2_show_pin
        )
        st_folium(m_target, width="100%", height=450, key="map_tab_target")

editor_key = "table_editor" if layout_option == "左右並べ（PC・大画面向け）" else "table_editor_tab"
if editor_key in st.session_state and st.session_state[editor_key].get("edited_rows"):
    edited_rows = st.session_state[editor_key]["edited_rows"]
    updated_flag = False
    for row_idx, changes in edited_rows.items():
        p_id = display_target_df.iloc[row_idx]["ID"]
        if p_id not in st.session_state.patient_status:
            st.session_state.patient_status[p_id] = {}
        if "対応ステータス" in changes:
            st.session_state.patient_status[p_id]["status"] = changes["対応ステータス"]
            st.session_state.patient_status[p_id]["updated_at"] = datetime.now(JST).strftime("%H:%M")
            updated_flag = True
        if "停電リスク" in changes:
            st.session_state.patient_status[p_id]["override_outage"] = changes["停電リスク"]
            updated_flag = True
    if updated_flag:
        st.rerun()

# ---------------------------------------------------------
# 8. アナウンス通知機能（デモ）
# ---------------------------------------------------------
st.markdown("---")
st.subheader("3. 初動用アナウンスメール送信（デモ）")
target_email = st.text_input("送信先医師のメールアドレス", value="doctor@example.com")
if st.button("📧 対象患者のアラート通知を一括送信"):
    if len(df_visit_target) > 0:
        st.write("**【医師へ送信される自動アナウンスプレビュー】**")
        for idx, row in df_visit_target.iterrows():
            st.code(f"""
件名: 【緊急停電アラート】担当患者の地域で停電検知（{row['患者名']} 様 / トリアージ: {row['トリアージ']}）
宛先: {target_email} ({row['担当医']}御中)

{row['担当医']} 先生

{row['患者名']} 様の居住地域（{row['住所']}）にて停電が発生している可能性があります。

・トリアージ緊急度: {row['トリアージ']}
・使用装置: {row['使用装置']} (バッテリ: {row['バッテリ']})
・作成日時: {created_time_str}

有事の初動対応および安否・医療機器の動作確認をお願いいたします。
            """, language="text")
        st.success(f"✅ {len(df_visit_target)} 件の通知メッセージを作成・送信処理（デモ）しました。")
    else:
        st.info("訪問対象（未確認）の停電患者がいないため通知は送信されません。")
