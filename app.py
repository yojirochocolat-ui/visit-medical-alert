import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import random
import folium
from streamlit_folium import st_folium
import io
import os
import glob
import urllib.parse
from datetime import datetime, timezone, timedelta

# 日本時間（JST）の定義
JST = timezone(timedelta(hours=9))

# PDF生成用ライブラリ
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

st.set_page_config(page_title="停電アラート", layout="wide", initial_sidebar_state="expanded")

# ---------------------------------------------------------
# カスタムCSS（デザイン洗練・スタイル定義）
# ---------------------------------------------------------
custom_css = """
<style>
    /* 全体フォント・背景調整 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
    }
    
    /* メインタイトルの洗練 */
    .main-header {
        font-size: 2.0rem;
        font-weight: 700;
        color: #0F172A;
        letter-spacing: -0.02em;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    
    /* カード風スタイリング */
    .metric-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 1.25rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        text-align: center;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0F172A;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #64748B;
        margin-top: 0.2rem;
    }
    .metric-danger {
        color: #DC2626;
    }
    
    /* トリアージバッジスタイル */
    .badge-lv4 {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-lv3 {
        background-color: #FEF3C7;
        color: #92400E;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-lv2 {
        background-color: #E0F2FE;
        color: #075985;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-lv1 {
        background-color: #F1F5F9;
        color: #475569;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 500;
        font-size: 0.85rem;
    }
    
    /* セクション見出しデザイン */
    .section-title {
        font-size: 1.15rem;
        font-weight: 600;
        color: #1E293B;
        margin-top: 1.5rem;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    /* サイドバーのカスタマイズ */
    [data-testid="stSidebar"] {
        background-color: #F8FAFC;
        border-right: 1px solid #E2E8F0;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# ヘッダー領域
st.markdown('<div class="main-header">⚡ 停電アラート</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">リアルタイムの停電情報と患者リストを照合し、優先度自動トリアージとナビ連携で初動対応を支援します。</div>', unsafe_allow_html=True)

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

# ---------------------------------------------------------
# 住所から緯度・経度を取得する関数 (ジオコーディング)
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 1. 四国電力の停電情報を取得する関数
# ---------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_outage_info():
    url = "https://www.yonden.co.jp/nw/teiden-info/history07.html" 
    try:
        response = requests.get(url, timeout=10)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")
        
        outage_list = []
        for tr in soup.find_all("tr"):
            pre_elem = tr.find("th", class_="pre")
            city_elem = tr.find("td", class_="city")
            town_elem = tr.find("td", class_="town")
            
            if city_elem and town_elem:
                pre = pre_elem.text.strip() if pre_elem else ""
                city = city_elem.text.strip()
                towns_raw = town_elem.text.strip()
                towns = [t.strip() for t in re.split(r"[、\s]+", towns_raw) if t.strip()]
                outage_list.append({"prefecture": pre, "city": city, "towns": towns, "raw_towns": towns_raw})
        return outage_list
    except Exception:
        return []

# ---------------------------------------------------------
# 2. デモ用データ・添付ファイルDL関数
# ---------------------------------------------------------
@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    
    kagawa_spots = [
        "香川県高松市番町1丁目", "香川県高松市瓦町2丁目", "香川県高松市栗林町1丁目",
        "香川県丸亀市大手町1丁目", "香川県綾歌郡宇多津町濱五番丁"
    ]
    
    device_options = ["なし", "人工呼吸器", "人工透析装置", "ペースメーカー"]
    device_weights = [0.5, 0.2, 0.15, 0.15]
    
    patients = []
    random.seed(42)
    
    for i in range(1, 51):
        name = f"{random.choice(last_names)} {random.choice(first_names)}"
        spot_addr = random.choice(kagawa_spots)
        p_id = f"P{i:03d}"
        addr = f"{spot_addr}{random.randint(1, 99)}番地"
        doc = random.choice(doctors)
        tel = f"090-{random.randint(1000,9999)}-{random.randint(10,99)}0"
        
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
    else:
        sample_data = [{
            "ID": "P001", "患者名": "山田 太郎", "住所": "香川県高松市番町1丁目1番地",
            "担当医": "佐藤医師", "連絡先": "090-1234-5678", "使用装置": "人工呼吸器",
            "バッテリ": "○", "備考": "要緊急確認"
        }]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame(sample_data).to_excel(writer, index=False, sheet_name='患者リスト')
        output.seek(0)
        return output.getvalue(), "患者リスト_登録フォーマット.xlsx"

# 初期データのロード
if st.session_state.patients_data is None:
    initial_df = pd.DataFrame(generate_50_kagawa_patients())
    lats, lons = [], []
    for _, r in initial_df.iterrows():
        lat, lon = geocode_address(r["住所"])
        lats.append(lat)
        lons.append(lon)
    initial_df["lat"] = lats
    initial_df["lon"] = lons
    st.session_state.patients_data = initial_df

# ---------------------------------------------------------
# 3. サイドバー設定 & データ読み込み
# ---------------------------------------------------------
st.sidebar.markdown("### ⚙️ 動作モード")
mode = st.sidebar.radio("情報取得モード", ["🧪 仮想シミュレーション", "🌐 リアルタイムWeb取得"], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📂 データ統合管理")

template_bytes, template_filename = load_template_file()
st.sidebar.download_button(
    label="📥 登録フォーマット(Excel)DL",
    data=template_bytes,
    file_name=template_filename,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True
)

uploaded_file = st.sidebar.file_uploader("患者リスト(Excel/CSV)を統合取り込み", type=["xlsx", "csv"])

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
        
        with st.spinner("位置情報を解析・統合中..."):
            lats, lons = [], []
            for _, r in new_df.iterrows():
                lat, lon = geocode_address(r["住所"])
                lats.append(lat)
                lons.append(lon)
            new_df["lat"] = lats
            new_df["lon"] = lons

            current_df = st.session_state.patients_data.set_index("ID")
            new_df = new_df.set_index("ID")
            updated_df = new_df.combine_first(current_df).reset_index()
            st.session_state.patients_data = updated_df
            
        st.sidebar.success("データ統合完了（重複IDは更新されました）")
    except Exception as e:
        st.sidebar.error(f"ファイル取込エラー: {e}")

st.sidebar.caption(f"登録患者数: **{len(st.session_state.patients_data)} 名**")

# ---------------------------------------------------------
# 4. 停電データの照合準備 & 優先度(トリアージ)ソート
# ---------------------------------------------------------
outage_data = []
created_time_str = ""

if mode == "🧪 仮想シミュレーション":
    st.markdown('<div class="section-title">1. 🧪 停電シミュレーター</div>', unsafe_allow_html=True)
    
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        sim_input = st.text_input(
            "想定停電地域（市町村・町名）", 
            value="高松市番町, 宇多津町",
            label_visibility="collapsed"
        )
    with col_btn:
        if st.button("▶️ シミュレーション実行", use_container_width=True):
            st.session_state.sim_areas = [a.strip() for a in sim_input.split(",") if a.strip()]
            st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.success("指定地域で照合を更新しました")

    for area in st.session_state.sim_areas:
        outage_data.append({"prefecture": "香川県", "city": area, "towns": [area], "raw_towns": area})
    
    created_time_str = st.session_state.sim_created_time

else:
    st.markdown('<div class="section-title">1. 🌐 Webリアルタイム停電情報</div>', unsafe_allow_html=True)
    outage_data = fetch_outage_info()
    created_time_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    
    if outage_data:
        outage_df_display = [{"都道府県": item["prefecture"], "市区町村": item["city"], "対象町名": item["raw_towns"]} for item in outage_data]
        st.dataframe(pd.DataFrame(outage_df_display), use_container_width=True, height=150)
    else:
        st.info("現在Web上に該当する停電情報はありません。")

def check_outage(address, outage_list):
    for item in outage_list:
        city_clean = re.sub(r".*郡", "", item["city"])
        if city_clean in str(address):
            return True, item["city"]
        for town in item["towns"]:
            if town and town in str(address):
                return True, town
    return False, "正常"

def calc_triage_level(device, battery):
    d = str(device)
    b = str(battery)
    if "人工呼吸器" in d:
        return "Lv.4 (最優先)", 4
    elif "人工透析" in d or "在宅酸素" in d:
        return "Lv.3 (高リスク)", 3
    elif d != "なし":
        if b in ["ー", "？"]:
            return "Lv.3 (高リスク)", 3
        return "Lv.2 (中リスク)", 2
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

# 優先度ソート (停電エリア > トリアージLv順)
df_result["risk_sort"] = df_result["停電リスク"].apply(lambda x: 0 if "⚠️" in x else 1)
df_result = df_result.sort_values(
    by=["risk_sort", "triage_score"], ascending=[True, False]
).drop(columns=["risk_sort"])

df_alert_only = df_result[df_result["停電リスク"].str.contains("⚠️")]
lv4_cnt = len(df_alert_only[df_alert_only["トリアージ"].str.contains("Lv.4")])
unhandled_cnt = len(df_alert_only[df_alert_only["対応ステータス"] == "未対応"])

# ---------------------------------------------------------
# メトリクスダッシュボード表示
# ---------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{len(st.session_state.patients_data)}</div><div class="metric-label">総登録患者数</div></div>', unsafe_allow_html=True)
with m2:
    st.markdown(f'<div class="metric-card"><div class="metric-value metric-danger">{len(alerts)}</div><div class="metric-label">⚠️ 停電対象患者</div></div>', unsafe_allow_html=True)
with m3:
    st.markdown(f'<div class="metric-card"><div class="metric-value metric-danger">{lv4_cnt}</div><div class="metric-label">🚨 最優先 (Lv.4)</div></div>', unsafe_allow_html=True)
with m4:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{unhandled_cnt}</div><div class="metric-label">📋 未対応件数</div></div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 地図描画関数
# ---------------------------------------------------------
def build_map(df, target_only=False):
    if target_only:
        display_df = df[df["停電リスク"].str.contains("⚠️")]
    else:
        display_df = df

    if not display_df.empty:
        center_lat = display_df["lat"].mean()
        center_lon = display_df["lon"].mean()
        zoom_level = 14 if target_only else 11
    else:
        center_lat, center_lon = 34.3000, 133.9500
        zoom_level = 11
        
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_level, tiles="CartoDB positron")
    
    for _, row in display_df.iterrows():
        is_alert = "⚠️" in row["停電リスク"]
        color = "red" if is_alert else "gray"
        icon_type = "exclamation-triangle" if is_alert else "user"
        
        nav_url = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(str(row['住所']))}"
        tel_clean = str(row['連絡先']).replace("-", "")
        
        popup_html = f"""
        <div style='font-family:sans-serif; font-size:12px; width:220px; line-height:1.6;'>
            <div style='font-weight:700; color:#DC2626; font-size:13px;'>【{row['トリアージ']}】</div>
            <b>氏名:</b> {row['患者名']}<br>
            <b>ステータス:</b> {row['対応ステータス']}<br>
            <b>使用装置:</b> {row['使用装置']} (バッテリ:{row['バッテリ']})<br>
            <b>住所:</b> {row['住所']}<br>
            <div style='margin-top:8px; display:flex; gap:5px;'>
                <a href='tel:{tel_clean}' target='_blank' style='background:#2563EB; color:white; padding:4px 8px; text-decoration:none; border-radius:4px; font-size:11px; font-weight:600;'>📞 TEL発信</a>
                <a href='{nav_url}' target='_blank' style='background:#059669; color:white; padding:4px 8px; text-decoration:none; border-radius:4px; font-size:11px; font-weight:600;'>🗺️ ナビ起動</a>
            </div>
        </div>
        """
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=240),
            tooltip=f"{row['トリアージ']} | {row['患者名']} 様",
            icon=folium.Icon(color=color, icon=icon_type, prefix="fa")
        ).add_to(m)
    return m

# ---------------------------------------------------------
# PDFレポート生成
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
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName=font_name, fontSize=14, leading=17, spaceAfter=8)
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName=font_name, fontSize=8, leading=11)
    cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'], fontName=font_name, fontSize=7, leading=9.5)

    story.append(Paragraph("【緊急対応確認】停電可能性患者リスト (トリアージ別)", title_style))
    story.append(Paragraph(f"<b>作成日時: {created_time} 作成</b> | 対象件数: {len(df_alert_patients)} 名", normal_style))
    story.append(Spacer(1, 10))

    headers = ["ID", "トリアージ", "患者名", "状態", "使用装置", "バッテリ", "担当医", "連絡先", "住所"]
    table_data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle('HeaderStyle', parent=cell_style, textColor=colors.whitesmoke)) for h in headers]]
    
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
            Paragraph(str(row["住所"]), cell_style)
        ])

    t = Table(table_data, colWidths=[25, 65, 45, 45, 65, 30, 45, 65, 145])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')])
    ]))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------------------------------------------------
# 5. 照合結果 & マップ可視化
# ---------------------------------------------------------
st.markdown('<div class="section-title">2. 📍 状況可視化 & フォローリスト</div>', unsafe_allow_html=True)

if len(alerts) > 0:
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        pdf_data = create_pdf_report(df_alert_only, created_time_str)
        st.download_button(
            label="📄 停電対象者リスト（PDF）を出力",
            data=pdf_data,
            file_name="停電リスク対象患者リスト.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    with col_dl2:
        m_target_dl = build_map(df_result, target_only=True)
        html_data = m_target_dl._repr_html_()
        st.download_button(
            label="🗺️ 訪問対象マップ（HTML）を出力",
            data=html_data,
            file_name="停電対象者_拡大訪問マップ.html",
            mime="text/html",
            use_container_width=True
        )

# 絞り込みフィルター
only_unhandled = st.checkbox("🔍 未対応の患者のみを表示", value=False)
display_target_df = df_result[df_result["対応ステータス"] == "未対応"] if only_unhandled else df_result

def highlight_row(val):
    if "Lv.4" in str(val):
        return "background-color: #FEE2E2; font-weight: bold; color: #991B1B;"
    elif "Lv.3" in str(val):
        return "background-color: #FEF3C7; font-weight: bold; color: #92400E;"
    return ""

display_cols = ["ID", "対応ステータス", "トリアージ", "停電リスク", "患者名", "使用装置", "バッテリ", "担当医", "連絡先", "住所"]

tab_list, tab_map = st.tabs(["📋 リスト一覧", "🗺️ マップ可視化"])
with tab_list:
    st.dataframe(
        display_target_df[display_cols].style.map(highlight_row, subset=["トリアージ"]),
        use_container_width=True, height=400
    )
with tab_map:
    m = build_map(display_target_df)
    st_folium(m, width="100%", height=450)

# ---------------------------------------------------------
# 6. 対応ステータス更新パネル
# ---------------------------------------------------------
st.markdown('<div class="section-title">3. 📝 現場ステータス更新</div>', unsafe_allow_html=True)

if len(df_alert_only) > 0:
    col_p, col_s, col_b = st.columns([3, 2, 1])
    with col_p:
        target_p = st.selectbox(
            "対象患者を選択", 
            options=df_alert_only["ID"] + " : " + df_alert_only["患者名"] + " (" + df_alert_only["トリアージ"] + ")"
        )
        selected_p_id = target_p.split(" : ")[0]
    with col_s:
        new_status = st.selectbox("ステータス", ["未対応", "連絡中", "安否確認済（安全）", "緊急訪問中"], key=f"select_{selected_p_id}")
    with col_b:
        st.write(" ")
        st.write(" ")
        if st.button("💾 更新", use_container_width=True):
            st.session_state.patient_status[selected_p_id] = {
                "status": new_status,
                "updated_at": datetime.now(JST).strftime("%H:%M")
            }
            st.success(f"更新完了")
            st.rerun()
else:
    st.info("対応更新が必要な停電対象患者はいません。")

# ---------------------------------------------------------
# 7. アナウンス通知デモ
# ---------------------------------------------------------
st.markdown('<div class="section-title">4. 📧 一括アラート通知（デモ）</div>', unsafe_allow_html=True)
col_mail, col_send = st.columns([3, 1])
with col_mail:
    target_email = st.text_input("送信先メールアドレス", value="doctor@example.com", label_visibility="collapsed")
with col_send:
    if st.button("📧 アラート一括送信", use_container_width=True):
        if len(alerts) > 0:
            st.success(f"✅ {len(alerts)} 件の通知メッセージを送信処理しました。")
        else:
            st.info("送信対象の患者はいません。")
