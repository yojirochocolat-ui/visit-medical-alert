import streamlit as st

import pandas as pd

import numpy as np

import random

import time

import urllib.parse

import smtplib

from email.mime.text import MIMEText

from email.mime.multipart import MIMEMultipart



# ---------------------------------------------------------

# ページ設定

# ---------------------------------------------------------

st.set_page_config(

    page_title="災害時患者対応支援システム",

    page_icon="🏥",

    layout="wide"

)



# ---------------------------------------------------------

# 1. ユーティリティ関数 & モックデータ定義

# ---------------------------------------------------------



def geocode_address(address):

    """

    簡易ジオコーディング（デモ用）

    香川県内の代表的な座標をベースに数値を返します。

    """

    if "高松" in address:

        return 34.3427, 134.0465

    elif "丸亀" in address:

        return 34.2897, 133.7971

    elif "坂出" in address:

        return 34.3163, 133.8583

    elif "善通寺" in address:

        return 34.2259, 133.7853

    elif "観音寺" in address:

        return 34.1283, 133.6521

    else:

        return 34.3400, 134.0400



def generate_50_kagawa_patients():

    """初期デフォルト患者データ（香川県50名）の生成"""

    cities = ["高松市", "丸亀市", "坂出市", "善通寺市", "観音寺市"]

    towns = ["中央町", "本町", "緑ヶ丘", "桜町", "富士見町", "朝日町", "港町", "昭和町"]

    doctors = ["佐藤 医師", "鈴木 医師", "高橋 医師", "田中 医師", "伊藤 医師"]

    devices = ["人工呼吸器", "ペーサー", "酸素濃縮器", "なし"]

    batteries = ["あり", "なし", "一部動作"]

    

    first_names = ["太郎", "花子", "一郎", "順子", "健太", "美咲", "修", "洋子", "大介", "恵子"]

    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]



    data = []

    random.seed(42)  # 再現性のためのシード

    for i in range(1, 51):

        p_id = f"A-{i}"

        name = f"{random.choice(last_names)} {random.choice(first_names)}"

        address = f"香川県{random.choice(cities)}{random.choice(towns)}{random.randint(1, 20)}番地{random.randint(1, 10)}号"

        doc = random.choice(doctors)

        tel = f"090-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"

        dev = random.choice(devices)

        bat = random.choice(batteries) if dev != "なし" else "なし"

        notes = "特記事項なし" if i % 3 != 0 else "要優先確認"



        data.append({

            "ID": p_id,

            "患者名": name,

            "住所": address,

            "担当医": doc,

            "連絡先": tel,

            "使用装置": dev,

            "バッテリ": bat,

            "備考": notes

        })

    return data



@st.cache_data

def load_template_file():

    """患者リストのフォーマット用テンプレートExcelを生成"""

    df_template = pd.DataFrame([{

        "ID": "例: A-101",

        "患者名": "山田 太郎",

        "住所": "香川県高松市番町1丁目1番1号",

        "担当医": "佐藤 医師",

        "連絡先": "090-0000-0000",

        "使用装置": "人工呼吸器",

        "バッテリ": "あり",

        "備考": "エレベーター停止時は階段使用"

    }])

    # バイトデータ変換

    import io

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:

        df_template.to_excel(writer, index=False, sheet_name='Template')

    return output.getvalue(), "patient_list_template.xlsx"



def send_actual_email(smtp_server, smtp_port, sender_email, sender_password, recipients, subject, body_text):

    """SMTPサーバー経由で実際にメールを一括送信する関数"""

    try:

        msg = MIMEMultipart()

        msg['From'] = sender_email

        msg['To'] = ", ".join(recipients)

        msg['Subject'] = subject

        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))



        server = smtplib.SMTP(smtp_server, smtp_port)

        server.starttls()

        server.login(sender_email, sender_password)

        server.send_message(msg)

        server.quit()

        return True, "メールが正常に一括送信されました！"

    except Exception as e:

        return False, f"メール送信に失敗しました: {str(e)}"





# ---------------------------------------------------------

# 2. セッション状態の初期化

# ---------------------------------------------------------

if "patients_data" not in st.session_state:

    init_data = pd.DataFrame(generate_50_kagawa_patients())

    lats, lons = [], []

    random.seed(123)

    for _, r in init_data.iterrows():

        base_lat, base_lon = geocode_address(r["住所"])

        jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

        jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

        lats.append(jitter_lat)

        lons.append(jitter_lon)

    init_data["lat"] = lats

    init_data["lon"] = lons

    st.session_state.patients_data = init_data



if "patient_status" not in st.session_state:

    st.session_state.patient_status = {}





# ---------------------------------------------------------

# 3. サイドバー設定 & データ読み込み

# ---------------------------------------------------------

st.sidebar.header("⚙️ 動作設定")



# 【強力更新】新しいキーを設定して既存キャッシュを完全に上書き

current_location_addr = st.sidebar.text_input(

    "📍 現住所（拠点・現在地）", 

    value="高松市サンポート2番1号",

    key="input_current_location_v3",

    help="マップ上に現在地/拠点ピンとして表示され、ナビ起動時の出発地としても使用されます"

)



mode = st.sidebar.radio(

    "情報取得モード", 

    options=["仮想シミュレーションモード", "リアルタイムWeb取得モード"],

    key="input_fetch_mode_v3"

)



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

    key="input_update_mode_v3",

    help="【現リストに追加】: 重複する名前がある場合は上書き追加します。\n【現リストと入れ替え】: 前のデータを全て削除し、添付ファイルのみでリストを新規作成します。"

)



uploaded_file = st.sidebar.file_uploader("手元の患者リスト(Excel/CSV)を選択", type=["xlsx", "csv"])



# ボタンを横並びに配置（1:1の比率）

col_btn1, col_btn2 = st.sidebar.columns([1, 1])



with col_btn1:

    btn_update = st.button("データ更新", type="primary", use_container_width=True)



with col_btn2:

    btn_reset = st.button("🔄 初期データに戻す", type="secondary", use_container_width=True)



# ---------------------------------------------------------

# データ更新ボタンが押された時の処理

# ---------------------------------------------------------

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

                lats, lons = [], []

                random.seed(999)

                for _, r in new_df.iterrows():

                    base_lat, base_lon = geocode_address(r["住所"])

                    jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

                    jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

                    lats.append(jitter_lat)

                    lons.append(jitter_lon)

                new_df["lat"] = lats

                new_df["lon"] = lons



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

                st.rerun()

        except Exception as e:

            st.sidebar.error(f"ファイルの読み込みに失敗しました: {e}")

    else:

        st.sidebar.warning("ファイルを選択してから「データ更新」を押してください。")



# ---------------------------------------------------------

# 初期データに戻すボタンが押された時の処理

# ---------------------------------------------------------

if btn_reset:

    with st.spinner("デフォルトの初期患者リスト（50名）にリセット中..."):

        initial_df = pd.DataFrame(generate_50_kagawa_patients())

        lats, lons = [], []

        random.seed(123)

        for _, r in initial_df.iterrows():

            base_lat, base_lon = geocode_address(r["住所"])

            jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

            jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

            lats.append(jitter_lat)

            lons.append(jitter_lon)

        initial_df["lat"] = lats

        initial_df["lon"] = lons

        

        # セッション状態および対応ステータスを初期化

        st.session_state.patients_data = initial_df

        st.session_state.patient_status = {}

        st.sidebar.info("🔄 初期デフォルトの患者リストにリセットしました！")

        st.rerun()



st.sidebar.caption(f"現在登録されている総患者数: **{len(st.session_state.patients_data)} 名**")





# ---------------------------------------------------------

# 4. メインコンテンツ表示

# ---------------------------------------------------------

st.title("🏥 災害時患者対応支援システム")

st.markdown("停電リスク情報や地図情報を一元管理し、初動対応を迅速化します。")



# --- 1. 概要サマリー表示 ---

df = st.session_state.patients_data.copy()



# 対応ステータスの適用

status_list = [st.session_state.patient_status.get(pid, "未対応") for pid in df["ID"]]

df["対応ステータス"] = status_list



# ダミーの停電リスク・トリアージ算出（デモ用）

risks, triages = [], []

for i, row in df.iterrows():

    if i % 7 == 0:

        risks.append("⚠️ 停電可能性あり")

        triages.append("Lv.1")

    elif i % 4 == 0:

        risks.append("🟡 注意")

        triages.append("Lv.2")

    else:

        risks.append("🟢 正常")

        triages.append("Lv.3" if i % 2 == 0 else "Lv.4")



df["停電リスク"] = risks

df["トリアージ"] = triages



st.header("1. 患者状況一覧")

st.dataframe(

    df[["ID", "対応ステータス", "停電リスク", "トリアージ", "患者名", "住所", "担当医", "連絡先", "使用装置", "バッテリ", "備考"]],

    use_container_width=True,

    height=300

)



st.markdown("---")



# --- 2. 評価・マップ機能 ---

st.header("2. 拠点・患者位置マップ")



# マップデータ構築

map_df = df[["lat", "lon", "患者名", "停電リスク"]].copy()



# 現在地/拠点ピンの追加

base_lat, base_lon = geocode_address(current_location_addr)

base_row = pd.DataFrame([{

    "lat": base_lat,

    "lon": base_lon,

    "患者名": f"📍 拠点（{current_location_addr}）",

    "停電リスク": "拠点"

}])

map_df = pd.concat([base_row, map_df], ignore_index=True)



st.map(map_df, latitude="lat", longitude="lon", size=20)



st.markdown("---")



# ---------------------------------------------------------

# 3. 初動用アナウンスメール送信（実送信対応＆3箇所設定）

# ---------------------------------------------------------

st.header("3. 初動用アナウンスメール送信")



# 送信元SMTP設定（折りたたみアコーディオン）

with st.expander("⚙️ メール送信元設定（SMTP認証情報）", expanded=False):

    st.caption("※Gmailを使用する場合は「アプリパスワード」を生成して入力してください。")

    col_smtp1, col_smtp2 = st.columns(2)

    with col_smtp1:

        smtp_server = st.text_input("SMTPサーバー", value="smtp.gmail.com")

        sender_email = st.text_input("送信元メールアドレス", value="", placeholder="example@gmail.com")

    with col_smtp2:

        smtp_port = st.number_input("SMTPポート", value=587)

        sender_password = st.text_input("送信元パスワード（アプリパスワード）", type="password")



st.markdown("##### 📩 送信先メールアドレス設定（最大3箇所）")

col_mail1, col_mail2, col_mail3 = st.columns(3)



with col_mail1:

    email_1 = st.text_input("送信先 1（医師/担当者）", value="", placeholder="doctor1@example.com")

with col_mail2:

    email_2 = st.text_input("送信先 2（本部/管理者）", value="", placeholder="admin@example.com")

with col_mail3:

    email_3 = st.text_input("送信先 3（予備/共有）", value="", placeholder="info@example.com")



# 一括送信ボタン

if st.button("📧 対象患者のアラート通知を一括送信", type="primary", use_container_width=True):

    # 入力された宛先から空文字を除外してリスト化

    recipients = [e.strip() for e in [email_1, email_2, email_3] if e.strip()]



    # エラーチェック

    if not sender_email or not sender_password:

        st.error("「⚙️ メール送信元設定」を展開して、送信元メールアドレスとパスワードを入力してください。")

    elif not recipients:

        st.warning("送信先メールアドレスを少なくとも1箇所入力してください。")

    else:

        # 送信本文の生成

        total_count = len(df)

        unhandled_count = len(df[df["対応ステータス"] == "未対応"])

        alert_count = len(df[df["停電リスク"] == "⚠️ 停電可能性あり"])



        mail_body = f"""【緊急アナウンス】患者アラート通知



関係者各位



災害等に伴う患者監視アラートの更新通知です。



■ 現在の状況サマリー

・拠点位置: {current_location_addr}

・登録総患者数: {total_count} 名

・未対応患者数: {unhandled_count} 名

・停電リスク注意患者数: {alert_count} 名



詳細は災害対策支援システムのダッシュボードをご確認ください。

※本メールはシステムより自動送信されています。

"""



        mail_subject = f"【緊急通知】災害対策患者アラート（対象患者数: {total_count}名）"



        # メール送信実行

        with st.spinner("メールを一括送信中..."):

            success, msg = send_actual_email(

                smtp_server=smtp_server,

                smtp_port=int(smtp_port),

                sender_email=sender_email,

                sender_password=sender_password,

                recipients=recipients,

                subject=mail_subject,

                body_text=mail_body

            )



            if success:

                st.success(f"{msg}\n送信先: {', '.join(recipients)}")

            else:

                st.error(msg)

import streamlit as st

import pandas as pd

import numpy as np

import random

import time

import urllib.parse

import smtplib

from email.mime.text import MIMEText

from email.mime.multipart import MIMEMultipart



# ---------------------------------------------------------

# ページ設定

# ---------------------------------------------------------

st.set_page_config(

    page_title="災害時患者対応支援システム",

    page_icon="🏥",

    layout="wide"

)



# ---------------------------------------------------------

# 1. ユーティリティ関数 & モックデータ定義

# ---------------------------------------------------------



def geocode_address(address):

    """

    簡易ジオコーディング（デモ用）

    香川県内の代表的な座標をベースに数値を返します。

    """

    if "高松" in address:

        return 34.3427, 134.0465

    elif "丸亀" in address:

        return 34.2897, 133.7971

    elif "坂出" in address:

        return 34.3163, 133.8583

    elif "善通寺" in address:

        return 34.2259, 133.7853

    elif "観音寺" in address:

        return 34.1283, 133.6521

    else:

        return 34.3400, 134.0400



def generate_50_kagawa_patients():

    """初期デフォルト患者データ（香川県50名）の生成"""

    cities = ["高松市", "丸亀市", "坂出市", "善通寺市", "観音寺市"]

    towns = ["中央町", "本町", "緑ヶ丘", "桜町", "富士見町", "朝日町", "港町", "昭和町"]

    doctors = ["佐藤 医師", "鈴木 医師", "高橋 医師", "田中 医師", "伊藤 医師"]

    devices = ["人工呼吸器", "ペーサー", "酸素濃縮器", "なし"]

    batteries = ["あり", "なし", "一部動作"]

    

    first_names = ["太郎", "花子", "一郎", "順子", "健太", "美咲", "修", "洋子", "大介", "恵子"]

    last_names = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]



    data = []

    random.seed(42)  # 再現性のためのシード

    for i in range(1, 51):

        p_id = f"A-{i}"

        name = f"{random.choice(last_names)} {random.choice(first_names)}"

        address = f"香川県{random.choice(cities)}{random.choice(towns)}{random.randint(1, 20)}番地{random.randint(1, 10)}号"

        doc = random.choice(doctors)

        tel = f"090-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"

        dev = random.choice(devices)

        bat = random.choice(batteries) if dev != "なし" else "なし"

        notes = "特記事項なし" if i % 3 != 0 else "要優先確認"



        data.append({

            "ID": p_id,

            "患者名": name,

            "住所": address,

            "担当医": doc,

            "連絡先": tel,

            "使用装置": dev,

            "バッテリ": bat,

            "備考": notes

        })

    return data



@st.cache_data

def load_template_file():

    """患者リストのフォーマット用テンプレートExcelを生成"""

    df_template = pd.DataFrame([{

        "ID": "例: A-101",

        "患者名": "山田 太郎",

        "住所": "香川県高松市番町1丁目1番1号",

        "担当医": "佐藤 医師",

        "連絡先": "090-0000-0000",

        "使用装置": "人工呼吸器",

        "バッテリ": "あり",

        "備考": "エレベーター停止時は階段使用"

    }])

    # バイトデータ変換

    import io

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:

        df_template.to_excel(writer, index=False, sheet_name='Template')

    return output.getvalue(), "patient_list_template.xlsx"



def send_actual_email(smtp_server, smtp_port, sender_email, sender_password, recipients, subject, body_text):

    """SMTPサーバー経由で実際にメールを一括送信する関数"""

    try:

        msg = MIMEMultipart()

        msg['From'] = sender_email

        msg['To'] = ", ".join(recipients)

        msg['Subject'] = subject

        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))



        server = smtplib.SMTP(smtp_server, smtp_port)

        server.starttls()

        server.login(sender_email, sender_password)

        server.send_message(msg)

        server.quit()

        return True, "メールが正常に一括送信されました！"

    except Exception as e:

        return False, f"メール送信に失敗しました: {str(e)}"





# ---------------------------------------------------------

# 2. セッション状態の初期化

# ---------------------------------------------------------

if "patients_data" not in st.session_state:

    init_data = pd.DataFrame(generate_50_kagawa_patients())

    lats, lons = [], []

    random.seed(123)

    for _, r in init_data.iterrows():

        base_lat, base_lon = geocode_address(r["住所"])

        jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

        jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

        lats.append(jitter_lat)

        lons.append(jitter_lon)

    init_data["lat"] = lats

    init_data["lon"] = lons

    st.session_state.patients_data = init_data



if "patient_status" not in st.session_state:

    st.session_state.patient_status = {}





# ---------------------------------------------------------

# 3. サイドバー設定 & データ読み込み

# ---------------------------------------------------------

st.sidebar.header("⚙️ 動作設定")



# 【強力更新】新しいキーを設定して既存キャッシュを完全に上書き

current_location_addr = st.sidebar.text_input(

    "📍 現住所（拠点・現在地）", 

    value="高松市サンポート2番1号",

    key="input_current_location_v3",

    help="マップ上に現在地/拠点ピンとして表示され、ナビ起動時の出発地としても使用されます"

)



mode = st.sidebar.radio(

    "情報取得モード", 

    options=["仮想シミュレーションモード", "リアルタイムWeb取得モード"],

    key="input_fetch_mode_v3"

)



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

    key="input_update_mode_v3",

    help="【現リストに追加】: 重複する名前がある場合は上書き追加します。\n【現リストと入れ替え】: 前のデータを全て削除し、添付ファイルのみでリストを新規作成します。"

)



uploaded_file = st.sidebar.file_uploader("手元の患者リスト(Excel/CSV)を選択", type=["xlsx", "csv"])



# ボタンを横並びに配置（1:1の比率）

col_btn1, col_btn2 = st.sidebar.columns([1, 1])



with col_btn1:

    btn_update = st.button("データ更新", type="primary", use_container_width=True)



with col_btn2:

    btn_reset = st.button("🔄 初期データに戻す", type="secondary", use_container_width=True)



# ---------------------------------------------------------

# データ更新ボタンが押された時の処理

# ---------------------------------------------------------

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

                lats, lons = [], []

                random.seed(999)

                for _, r in new_df.iterrows():

                    base_lat, base_lon = geocode_address(r["住所"])

                    jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

                    jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

                    lats.append(jitter_lat)

                    lons.append(jitter_lon)

                new_df["lat"] = lats

                new_df["lon"] = lons



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

                st.rerun()

        except Exception as e:

            st.sidebar.error(f"ファイルの読み込みに失敗しました: {e}")

    else:

        st.sidebar.warning("ファイルを選択してから「データ更新」を押してください。")



# ---------------------------------------------------------

# 初期データに戻すボタンが押された時の処理

# ---------------------------------------------------------

if btn_reset:

    with st.spinner("デフォルトの初期患者リスト（50名）にリセット中..."):

        initial_df = pd.DataFrame(generate_50_kagawa_patients())

        lats, lons = [], []

        random.seed(123)

        for _, r in initial_df.iterrows():

            base_lat, base_lon = geocode_address(r["住所"])

            jitter_lat = base_lat + random.uniform(-0.0012, 0.0012)

            jitter_lon = base_lon + random.uniform(-0.0012, 0.0012)

            lats.append(jitter_lat)

            lons.append(jitter_lon)

        initial_df["lat"] = lats

        initial_df["lon"] = lons

        

        # セッション状態および対応ステータスを初期化

        st.session_state.patients_data = initial_df

        st.session_state.patient_status = {}

        st.sidebar.info("🔄 初期デフォルトの患者リストにリセットしました！")

        st.rerun()



st.sidebar.caption(f"現在登録されている総患者数: **{len(st.session_state.patients_data)} 名**")





# ---------------------------------------------------------

# 4. メインコンテンツ表示

# ---------------------------------------------------------

st.title("🏥 災害時患者対応支援システム")

st.markdown("停電リスク情報や地図情報を一元管理し、初動対応を迅速化します。")



# --- 1. 概要サマリー表示 ---

df = st.session_state.patients_data.copy()



# 対応ステータスの適用

status_list = [st.session_state.patient_status.get(pid, "未対応") for pid in df["ID"]]

df["対応ステータス"] = status_list



# ダミーの停電リスク・トリアージ算出（デモ用）

risks, triages = [], []

for i, row in df.iterrows():

    if i % 7 == 0:

        risks.append("⚠️ 停電可能性あり")

        triages.append("Lv.1")

    elif i % 4 == 0:

        risks.append("🟡 注意")

        triages.append("Lv.2")

    else:

        risks.append("🟢 正常")

        triages.append("Lv.3" if i % 2 == 0 else "Lv.4")



df["停電リスク"] = risks

df["トリアージ"] = triages



st.header("1. 患者状況一覧")

st.dataframe(

    df[["ID", "対応ステータス", "停電リスク", "トリアージ", "患者名", "住所", "担当医", "連絡先", "使用装置", "バッテリ", "備考"]],

    use_container_width=True,

    height=300

)



st.markdown("---")



# --- 2. 評価・マップ機能 ---

st.header("2. 拠点・患者位置マップ")



# マップデータ構築

map_df = df[["lat", "lon", "患者名", "停電リスク"]].copy()



# 現在地/拠点ピンの追加

base_lat, base_lon = geocode_address(current_location_addr)

base_row = pd.DataFrame([{

    "lat": base_lat,

    "lon": base_lon,

    "患者名": f"📍 拠点（{current_location_addr}）",

    "停電リスク": "拠点"

}])

map_df = pd.concat([base_row, map_df], ignore_index=True)



st.map(map_df, latitude="lat", longitude="lon", size=20)



st.markdown("---")



# ---------------------------------------------------------

# 3. 初動用アナウンスメール送信（実送信対応＆3箇所設定）

# ---------------------------------------------------------

st.header("3. 初動用アナウンスメール送信")



# 送信元SMTP設定（折りたたみアコーディオン）

with st.expander("⚙️ メール送信元設定（SMTP認証情報）", expanded=False):

    st.caption("※Gmailを使用する場合は「アプリパスワード」を生成して入力してください。")

    col_smtp1, col_smtp2 = st.columns(2)

    with col_smtp1:

        smtp_server = st.text_input("SMTPサーバー", value="smtp.gmail.com")

        sender_email = st.text_input("送信元メールアドレス", value="", placeholder="example@gmail.com")

    with col_smtp2:

        smtp_port = st.number_input("SMTPポート", value=587)

        sender_password = st.text_input("送信元パスワード（アプリパスワード）", type="password")



st.markdown("##### 📩 送信先メールアドレス設定（最大3箇所）")

col_mail1, col_mail2, col_mail3 = st.columns(3)



with col_mail1:

    email_1 = st.text_input("送信先 1（医師/担当者）", value="", placeholder="doctor1@example.com")

with col_mail2:

    email_2 = st.text_input("送信先 2（本部/管理者）", value="", placeholder="admin@example.com")

with col_mail3:

    email_3 = st.text_input("送信先 3（予備/共有）", value="", placeholder="info@example.com")



# 一括送信ボタン

if st.button("📧 対象患者のアラート通知を一括送信", type="primary", use_container_width=True):

    # 入力された宛先から空文字を除外してリスト化

    recipients = [e.strip() for e in [email_1, email_2, email_3] if e.strip()]



    # エラーチェック

    if not sender_email or not sender_password:

        st.error("「⚙️ メール送信元設定」を展開して、送信元メールアドレスとパスワードを入力してください。")

    elif not recipients:

        st.warning("送信先メールアドレスを少なくとも1箇所入力してください。")

    else:

        # 送信本文の生成

        total_count = len(df)

        unhandled_count = len(df[df["対応ステータス"] == "未対応"])

        alert_count = len(df[df["停電リスク"] == "⚠️ 停電可能性あり"])



        mail_body = f"""【緊急アナウンス】患者アラート通知



関係者各位



災害等に伴う患者監視アラートの更新通知です。



■ 現在の状況サマリー

・拠点位置: {current_location_addr}

・登録総患者数: {total_count} 名

・未対応患者数: {unhandled_count} 名

・停電リスク注意患者数: {alert_count} 名



詳細は災害対策支援システムのダッシュボードをご確認ください。

※本メールはシステムより自動送信されています。

"""



        mail_subject = f"【緊急通知】災害対策患者アラート（対象患者数: {total_count}名）"



        # メール送信実行

        with st.spinner("メールを一括送信中..."):

            success, msg = send_actual_email(

                smtp_server=smtp_server,

                smtp_port=int(smtp_port),

                sender_email=sender_email,

                sender_password=sender_password,

                recipients=recipients,

                subject=mail_subject,

                body_text=mail_body

            )



            if success:

                st.success(f"{msg}\n送信先: {', '.join(recipients)}")

            else:

                st.error(msg) 

