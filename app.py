import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import random
import folium
from streamlit_folium import st_folium

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
# 2. 香川県全域（8市5町）のダミー患者データ（緯度経度付き）
# ---------------------------------------------------------
@st.cache_data
def generate_50_kagawa_patients():
    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]
    first_names = ["太郎", "花子", "一郎", "幸子", "健一", "洋子", "誠", "和子", "修", "由美子"]
    doctors = ["佐藤医師", "高橋医師", "鈴木医師", "中村医師"]
    
    # 香川県各地の拠点（住所, 基準緯度, 基準経度）
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
    
    patients = []
    random.seed(42)
    for i in range(1, 51):
        spot_addr, base_lat, base_lon = random.choice(kagawa_spots)
        p_id = f"P{i:03d}"
        name = f"{random.choice(last_names)} {random.choice(first_names)}"
        addr = f"{spot_addr}{random.randint(1, 99)}番地"
        doc = random.choice(doctors)
        tel = f"090-{random.randint(1000,9999)}-{random.randint(1000,9999)}"
        # 少しランダムに座標を散らしてプロットを重ねないようにする
        lat = base_lat + random.uniform(-0.008, 0.008)
        lon = base_lon + random.uniform(-0.008, 0.008)
        
        patients.append({
            "ID": p_id, "患者名": name, "住所": addr, 
            "担当医": doc, "連絡先": tel, "lat": lat, "lon": lon
        })
    return patients

# ---------------------------------------------------------
# 3. サイドバー：モード ＆ 表示レイアウト設定
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

# ---------------------------------------------------------
# 4. 停電データの準備
# ---------------------------------------------------------
outage_data = []

if mode == "🧪 仮想シミュレーションモード":
    st.subheader("1. 🧪 停電エリア・シミュレーター")
    sim_input = st.text_input(
        "停電が発生したと想定する地域（香川県内の市町村や町名）を入力", 
        value="高松市番町, 丸亀市大手町"
    )
    areas = [a.strip() for a in sim_input.split(",") if a.strip()]
    for area in areas:
        outage_data.append({"prefecture": "香川県", "city": area, "towns": [area], "raw_towns": area})
    st.caption(f"現在のテスト対象エリア: **{', '.join(areas)}**")

else:
    st.subheader("1. 🌐 Webリアルタイム停電情報")
    outage_data = fetch_outage_info()
    if outage_data:
        outage_df_display = [{"都道府県": item["prefecture"], "市区町村": item["city"], "対象町名": item["raw_towns"]} for item in outage_data]
        st.dataframe(pd.DataFrame(outage_df_display), use_container_width=True)
    else:
        st.warning("現在、四国電力Webサイト上に該当する停電情報はありません。")

# ---------------------------------------------------------
# 5. 患者照合 & ソート処理
# ---------------------------------------------------------
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
        "住所": row["住所"],
        "担当医": row.get("担当医", "-"),
        "連絡先": row.get("連絡先", "-"),
        "lat": row.get("lat", 34.3400),
        "lon": row.get("lon", 134.0450)
    })
    if is_outage:
        alerts.append((row["患者名"], row["住所"], row.get("担当医", "担当医")))

df_result = pd.DataFrame(results)

# 停電リスク者を最上位に自動ソート
df_result["sort_key"] = df_result["停電リスク"].apply(lambda x: 0 if "⚠️" in x else 1)
df_result = df_result.sort_values("sort_key").drop(columns=["sort_key"])

# ---------------------------------------------------------
# 6. 地図オブジェクト（Folium）の生成関数
# ---------------------------------------------------------
def build_map(df):
    # 香川県中心付近（高松市）を中心位置に設定
    m = folium.Map(location=[34.3000, 133.9500], zoom_start=10)
    
    for _, row in df.iterrows():
        is_alert = "⚠️" in row["停電リスク"]
        color = "red" if is_alert else "green"
        icon_type = "exclamation-triangle" if is_alert else "user"
        
        popup_html = f"""
        <div style='font-size:12px; width:180px;'>
            <b>【{row['停電リスク']}】</b><br>
            <b>氏名:</b> {row['患者名']}<br>
            <b>住所:</b> {row['住所']}<br>
            <b>担当:</b> {row['担当医']}<br>
            <b>TEL:</b> {row['連絡先']}
        </div>
        """
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=200),
            tooltip=f"{'⚠️[停電]' if is_alert else '🟢'} {row['患者名']} 様",
            icon=folium.Icon(color=color, icon=icon_type, prefix="fa")
        ).add_to(m)
    return m

# ---------------------------------------------------------
# 7. 表示エリア（レイアウト切替対応）
# ---------------------------------------------------------
st.subheader(f"2. 患者照合結果 & マップ可視化 (該当患者: {len(alerts)} / 全 {len(df_patients)} 名)")

if len(alerts) > 0:
    st.error(f"🚨 停電エリア内に該当する患者が **{len(alerts)} 名** ピックアップされました！")
else:
    st.success("現在、停電エリアに該当する患者はいません。")

# 表のスタイル設定
def highlight_outage(val):
    if "⚠️" in str(val):
        return "background-color: #ffcccc; font-weight: bold; color: #990000;"
    return ""

display_cols = ["ID", "停電リスク", "検知エリア", "患者名", "住所", "担当医", "連絡先"]

# --- 表示パターン A: 左右並列表示 (PC向け) ---
if layout_option == "左右に並べて表示 (PC・大画面向け)":
    col1, col2 = st.columns([6, 5])
    
    with col1:
        st.markdown("#### 📋 患者リスト (優先ソート済み)")
        st.dataframe(
            df_result[display_cols].style.applymap(highlight_outage, subset=["停電リスク"]),
            use_container_width=True,
            height=500
        )
        
    with col2:
        st.markdown("#### 🗺️ 訪問エリアマップ (赤:停電 / 緑:正常)")
        m = build_map(df_result)
        st_folium(m, width="100%", height=500)

# --- 表示パターン B: タブ切替表示 (スマホ向け) ---
else:
    tab1, tab2 = st.tabs(["📋 リスト表示", "🗺️ マップ表示"])
    
    with tab1:
        st.dataframe(
            df_result[display_cols].style.applymap(highlight_outage, subset=["停電リスク"]),
            use_container_width=True,
            height=500
        )
        
    with tab2:
        m = build_map(df_result)
        st_folium(m, width="100%", height=500)

# ---------------------------------------------------------
# 8. アナウンス通知機能
# ---------------------------------------------------------
st.subheader("3. 初動用アナウンスメール送信（デモ）")
target_email = st.text_input("送信先医師のメールアドレス", value="doctor@example.com")

if st.button("📧 対象患者のアラート通知を一括送信"):
    if len(alerts) > 0:
        st.write("**【医師へ送信される自動アナウンスプレビュー】**")
        for name, addr, doc in alerts:
            st.code(f"""
件名: 【緊急停電アラート】担当患者の地域で停電検知（{name} 様）
宛先: {target_email} ({doc}御中)

{doc} 先生

{name} 様の居住地域（{addr}）にて停電が発生している可能性があります。
有事の初動対応および安否・医療機器（在宅酸素等）の動作確認をお願いいたします。
            """, language="text")
        st.success(f"✅ {len(alerts)} 件の通知メッセージを作成・送信処理（デモ）しました。")
    else:
        st.info("停電対象患者がいないため通知は送信されません。")