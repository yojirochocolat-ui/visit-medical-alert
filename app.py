<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>訪問エリアマップ</title>
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        #map {
            width: 100%;
            height: 500px;
        }
        body {
            font-family: sans-serif;
        }
    </style>
</head>
<body>

    <h2>🗺️ 訪問エリアマップ <span style="font-size:0.8em; font-weight:normal;">(🔵 現住所 / 🔴 停電未対応 / 🟣 確認済 / 🟢 停電なし)</span></h2>
    
    <div id="map"></div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // 高松シンボルタワーの正確な座標（緯度: 34.3533, 経度: 134.0470）
        const takamatsuLat = 34.3533;
        const takamatsuLng = 134.0470;

        // 地図の初期化: setView([緯度, 経度], ズームレベル)
        // ズームレベル 16〜17 に設定することで、高松駅周辺が詳細に拡大表示されます
        const map = L.map('map').setView([takamatsuLat, takamatsuLng], 16);

        // OpenStreetMapタイルの読み込み
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        // 現住所（拠点）マーカーを追加
        const homeMarker = L.marker([takamatsuLat, takamatsuLng]).addTo(map);

        // ポップアップの設定と自動表示 (.openPopup())
        homeMarker.bindPopup("<b>📍 現住所（拠点）</b><br>場所: 高松シンボルタワー").openPopup();
    </script>
</body>
</html>
