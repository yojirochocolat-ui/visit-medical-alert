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

st.set_page_config(page_title="訪問医療用停電アラート", layout="wide")

st.title("⚡ 訪問医療用停電アラート (マップ統合デモ版)")
st.caption("四国電力送配電の停電情報と患者リストをリアルタイム照合し、リストと地図で可視化します。")

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
# 2. 香川県全域のダミー患者データ生成
# ---------------------------------------------------------
@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤", 
                  "吉田", "山田", "佐々木", "山口", "松本", "井上", "木村", "林", "斎藤", "清水"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子", "修", "由美子",
                   "健二", "明美", "大輔", "真由美", "拓也", "香織", "直樹", "裕子", "哲也", "恵"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    
    kagawa_spots = [
        ("香川県高松市番町1丁目", 34.3427, 134.0465),
        ("香川県高松市瓦町2丁目", 34.3385, 134.0520),
        ("香川県高松市栗林町1丁目", 34.3295, 134.0470),
        ("香川県高松市木太町", 34.3330, 134.0750),
        ("香川県高松市太田上町", 34.3050, 134.0500),
        ("香川県丸亀市大手町1丁目", 34.2890, 133.7970),
        ("香川県丸亀市綾歌町富熊", 34.2320, 133.8450),
        ("香川県坂出市室町", 34.3160, 133.8560),
        ("香川県善通寺市文京町2丁目", 34.2260, 133.7840),
        ("香川県観音寺市坂本町1丁目", 34.1270, 133.6530),
        ("香川県さぬき市志度", 34.3210, 134.1730),
        ("香川県東かがわ市三本松", 34.2530, 134.3480),
        ("香川県三豊市高瀬町新名", 34.1840, 133.7050),
        ("香川県木田郡三木町氷上", 34.2700, 134.1300),
        ("香川県綾歌郡宇多津町濱五番丁", 34.3080, 133.8150),
        ("香川県綾歌郡綾川町滝宮", 34.2500, 133.9180),
        ("香川県仲多度郡琴平町", 34.1890, 133.8180),
        ("香川県仲多度郡多度津町家中", 34.2710, 133.7530),
        ("香川県小豆郡土庄町", 34.4860, 134.1750),
    ]
    
    device_options = ["なし", "人工呼吸器", "人工透析装置", "ペースメーカー"]
    device_weights = [0.5, 0.2, 0.15, 0.15]
    
    patients = []
    used_names = set()
    random.seed(42)
    
    for i in range(1, 51):
        while True:
            name = f"{random.choice(last_names)} {random.choice(first_names)}"
            if name not in used_names:
                used_names.add(name)
                break
                
        spot_addr, base_lat, base_lon = random.choice(kagawa_spots)
        p_id = f"P{i:03d}"
        addr = f"{spot_addr}{random.randint(1, 99)}番地"
        doc = random.choice(doctors)
        tel = f"090-{random.randint(1000,9999)}-{random.randint(10,99)}XX"
        lat = base_lat + random.uniform(-0.008, 0.008)
        lon = base_lon + random.uniform(-0.008, 0.008)
        
        device = random.choices(device_options, weights=device_weights)[0]
        if device == "なし":
            battery = "ー"
        else:
            battery = random.choices(["○", "ー", "？"], weights=[0.7, 0.1, 0.2])[0]
        
        patients.append({
            "ID": p_id, "患者名": name, "住所": addr, 
            "担当医": doc, "連絡先": tel, "使用装置": device, "バッテリ": battery,
            "備考": "",
            "lat": lat, "lon": lon
        })
    return patients

# ---------------------------------------------------------
# 3. サイドバー設定
# ---------------------------------------------------------
st.sidebar.header("⚙️ 動作設定")
mode = st.sidebar.radio("情報取得モード", ["🧪 仮想シミュレーションモード", "🌐 リアルタイムWeb取得モード"])

st.sidebar.markdown("---")
st.sidebar.header("🖥️ 画面レイアウト設定")
layout_option = st.sidebar.radio(
    "画面の表示スタイル", 
    ["左右に並べて表示 (PC・大画面向け)", "タブで切り替えて表示 (スマホ・省スペース向け)"]
)

uploaded_file = st.sidebar.file_uploader("手元の患者リスト(Excel/CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    df_patients = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
else:
    df_patients = pd.DataFrame(generate_50_kagawa_patients())
    st.sidebar.success("香川県内ダミーデータ（50名分）を使用中")

# セッション状態の初期化
if "sim_areas" not in st.session_state:
    st.session_state.sim_areas = ["高松市番町", "宇多津町"]
if "sim_created_time" not in st.session_state:
    st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

# ---------------------------------------------------------
# 4. 停電データの照合準備 & 優先度ソート
# ---------------------------------------------------------
outage_data = []
created_time_str = ""

if mode == "🧪 仮想シミュレーションモード":
    st.subheader("1. 🧪 停電エリア・シミュレーター")
    
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        sim_input = st.text_input(
            "停電が発生したと想定する地域（香川県内の市町村や町名）を入力", 
            value="高松市番町, 宇多津町"
        )
    with col_btn:
        st.write(" ") # レイアウト調整用余白
        st.write(" ")
        if st.button("▶️ シミュレーション実行", use_container_width=True):
            st.session_state.sim_areas = [a.strip() for a in sim_input.split(",") if a.strip()]
            st.session_state.sim_created_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            st.success("シミュレーションを実行・作成日時を更新しました！")

    for area in st.session_state.sim_areas:
        outage_data.append({"prefecture": "香川県", "city": area, "towns": [area], "raw_towns": area})
    
    created_time_str = st.session_state.sim_created_time
    st.caption(f"現在のテスト対象エリア: **{', '.join(st.session_state.sim_areas)}**")

else:
    st.subheader("1. 🌐 Webリアルタイム停電情報")
    outage_data = fetch_outage_info()
    created_time_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    
    if outage_data:
        outage_df_display = [{"都道府県": item["prefecture"], "市区町村": item["city"], "対象町名": item["raw_towns"]} for item in outage_data]
        st.dataframe(pd.DataFrame(outage_df_display), use_container_width=True)
    else:
        st.warning("現在、四国電力Webサイト上に該当する停電情報はありません。")

def check_outage(address, outage_list):
    for item in outage_list:
        city_clean = re.sub(r".*郡", "", item["city"])
        if city_clean in address:
            return True, item["city"]
        for town in item["towns"]:
            if town and town in address:
                return True, town
    return False, "正常"

results = []
alerts = []

for idx, row in df_patients.iterrows():
    is_outage, area_info = check_outage(str(row["住所"]), outage_data)
    results.append({
        "ID": row.get("ID", f"P{idx+1:03d}"),
        "停電リスク": "⚠️ 停電可能性あり" if is_outage else "🟢 正常",
        "検知エリア": area_info if is_outage else "-",
        "患者名": row["患者名"],
        "使用装置": row.get("使用装置", "なし"),
        "バッテリ": row.get("バッテリ", "ー"),
        "担当医": row.get("担当医", "-"),
        "連絡先": row.get("連絡先", "-"),
        "住所": row["住所"],
        "備考": row.get("備考", ""),
        "lat": row.get("lat", 34.3400),
        "lon": row.get("lon", 134.0450)
    })
    if is_outage:
        alerts.append(row)

df_result = pd.DataFrame(results)

# ソート用のキーを設定
def get_device_order(val):
    return 1 if str(val) == "なし" else 0

def get_battery_order(val):
    v = str(val)
    if v == "ー":
        return 0
    elif v == "？":
        return 1
    elif v == "○":
        return 2
    return 3

df_result["risk_sort"] = df_result["停電リスク"].apply(lambda x: 0 if "⚠️" in x else 1)
df_result["device_sort"] = df_result["使用装置"].apply(get_device_order)
df_result["battery_sort"] = df_result["バッテリ"].apply(get_battery_order)

# 優先度順に並び替え後、ソート用列を削除
df_result = df_result.sort_values(
    by=["risk_sort", "device_sort", "battery_sort"]
).drop(columns=["risk_sort", "device_sort", "battery_sort"])

# ---------------------------------------------------------
# 5. 地図描画関数
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
        
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_level)
    
    for _, row in display_df.iterrows():
        is_alert = "⚠️" in row["停電リスク"]
        color = "red" if is_alert else "green"
        icon_type = "exclamation-triangle" if is_alert else "user"
        
        popup_html = f"""
        <div style='font-size:12px; width:200px;'>
            <b>【{row['停電リスク']}】</b><br>
            <b>氏名:</b> {row['患者名']}<br>
            <b>装置:</b> {row['使用装置']} (バッテリ: {row['バッテリ']})<br>
            <b>担当:</b> {row['担当医']}<br>
            <b>TEL:</b> {row['連絡先']}<br>
            <b>住所:</b> {row['住所']}
        </div>
        """
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=220),
            tooltip=f"{'⚠️[停電]' if is_alert else '🟢'} {row['患者名']} 様（{row['使用装置']} / バッテリ:{row['バッテリ']}）",
            icon=folium.Icon(color=color, icon=icon_type, prefix="fa")
        ).add_to(m)
    return m

# ---------------------------------------------------------
# 6. 日本語対応 PDFレポート & HTMLマップ生成機能
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
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, 
        rightMargin=15, leftMargin=15, topMargin=20, bottomMargin=20
    )
    story = []

    font_name = register_japanese_font()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle', parent=styles['Heading1'], 
        fontName=font_name, fontSize=14, leading=17, spaceAfter=8
    )
    normal_style = ParagraphStyle(
        'NormalStyle', parent=styles['Normal'], 
        fontName=font_name, fontSize=8, leading=11
    )
    cell_style = ParagraphStyle(
        'CellStyle', parent=styles['Normal'], 
        fontName=font_name, fontSize=7, leading=9.5
    )

    story.append(Paragraph("【緊急対応確認】停電可能性患者リスト", title_style))
    story.append(Paragraph(f"<b>作成日時: {created_time} 作成</b> | 対象件数: {len(df_alert_patients)} 名", normal_style))
    story.append(Paragraph("※拡大マップやルート検索はダウンロードした「HTMLマップ」をご活用ください。", normal_style))
    story.append(Spacer(1, 10))

    headers = ["ID", "検知エリア", "患者名", "使用装置", "バッテリ", "担当医", "連絡先", "住所", "備考"]
    table_data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle('HeaderStyle', parent=cell_style, textColor=colors.whitesmoke)) for h in headers]]
    
    for _, row in df_alert_patients.iterrows():
        table_data.append([
            Paragraph(str(row["ID"]), cell_style),
            Paragraph(str(row["検知エリア"]), cell_style),
            Paragraph(str(row["患者名"]), cell_style),
            Paragraph(str(row["使用装置"]), cell_style),
            Paragraph(str(row["バッテリ"]), cell_style),
            Paragraph(str(row["担当医"]), cell_style),
            Paragraph(str(row["連絡先"]), cell_style),
            Paragraph(str(row["住所"]), cell_style),
            Paragraph(str(row.get("備考", "")), cell_style)
        ])

    t = Table(table_data, colWidths=[25, 55, 50, 65, 35, 50, 70, 150, 60])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d9534f')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')])
    ]))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer

def get_html_map_download(m):
    return m._repr_html_()

# ---------------------------------------------------------
# 7. 画面表示エリア
# ---------------------------------------------------------
st.subheader(f"2. 患者照合結果 & マップ可視化 (該当患者: {len(alerts)} / 全 {len(df_patients)} 名)")
st.caption(f"🕒 **リスト作成日時: {created_time_str} 作成**")

df_alert_only = df_result[df_result["停電リスク"].str.contains("⚠️")]

if len(alerts) > 0:
    st.error(f"🚨 停電エリア内に該当する患者が **{len(alerts)} 名** ピックアップされました！")
    
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        pdf_data = create_pdf_report(df_alert_only, created_time_str)
        st.download_button(
            label="📄 1. 停電対象者リスト（PDF）をDL",
            data=pdf_data,
            file_name="停電リスク対象患者リスト.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    with col_dl2:
        m_target_dl = build_map(df_result, target_only=True)
        html_data = get_html_map_download(m_target_dl)
        st.download_button(
            label="🗺️ 2. 停電対象のみ拡大訪問マップ（HTML）をDL",
            data=html_data,
            file_name="停電対象者_拡大訪問マップ.html",
            mime="text/html",
            use_container_width=True
        )
else:
    st.success("現在、停電エリアに該当する患者はいません。")

def highlight_outage(val):
    if "⚠️" in str(val):
        return "background-color: #ffcccc; font-weight: bold; color: #990000;"
    return ""

display_cols = ["ID", "停電リスク", "検知エリア", "患者名", "使用装置", "バッテリ", "担当医", "連絡先", "住所", "備考"]

if layout_option == "左右に並べて表示 (PC・大画面向け)":
    col1, col2 = st.columns([6, 5])
    with col1:
        st.markdown("#### 📋 患者リスト (全体)")
        st.dataframe(df_result[display_cols].style.map(highlight_outage, subset=["停電リスク"]), use_container_width=True, height=500)
    with col2:
        st.markdown("#### 🗺️ 訪問エリアマップ (赤:停電 / 緑:正常)")
        m = build_map(df_result)
        st_folium(m, width="100%", height=500)
else:
    tab1, tab2, tab3 = st.tabs(["📋 リスト表示(全体)", "🗺️ マップ表示(全体)", "⚠️ 停電対象者のみ拡大マップ"])
    with tab1:
        st.dataframe(df_result[display_cols].style.map(highlight_outage, subset=["停電リスク"]), use_container_width=True, height=500)
    with tab2:
        m = build_map(df_result, target_only=False)
        st_folium(m, width="100%", height=500)
    with tab3:
        st.markdown("※緑ピン（正常）を非表示にし、停電対象エリアを自動拡大して表示しています。")
        m_target = build_map(df_result, target_only=True)
        st_folium(m_target, width="100%", height=500)

# ---------------------------------------------------------
# 8. アナウンス通知機能（デモ表示のみ）
# ---------------------------------------------------------
st.subheader("3. 初動用アナウンスメール送信（デモ）")
target_email = st.text_input("送信先医師のメールアドレス", value="doctor@example.com")

if st.button("📧 対象患者のアラート通知を一括送信"):
    if len(alerts) > 0:
        st.write("**【医師へ送信される自動アナウンスプレビュー】**")
        for idx, row in df_alert_only.iterrows():
            st.code(f"""
件名: 【緊急停電アラート】担当患者の地域で停電検知（{row['患者名']} 様）
宛先: {target_email} ({row['担当医']}御中)

{row['担当医']} 先生

{row['患者名']} 様の居住地域（{row['住所']}）にて停電が発生している可能性があります。
作成日時: {created_time_str}
使用装置: {row['使用装置']} (バッテリ: {row['バッテリ']})

有事の初動対応および安否・医療機器の動作確認をお願いいたします。
            """, language="text")
        st.success(f"✅ {len(alerts)} 件の通知メッセージを作成・送信処理（デモ）しました。")
    else:
        st.info("停電対象患者がいないため通知は送信されません。")
