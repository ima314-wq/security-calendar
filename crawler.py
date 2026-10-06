import os
import json
import time
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai
from google.genai import types

# JST (日本時間) の取得
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)
today_str = now_jst.strftime("%Y-%m-%d")

# 監視対象のRSS (Google ニュース検索: 日本語)
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA&hl=ja&gl=JP&ceid=JP:ja"

feed = feedparser.parse(RSS_URL)
recent_entries = []

# 直近の記事を最大20件ピックアップ
for entry in getattr(feed, "entries", [])[:20]:
    recent_entries.append({
        "title": getattr(entry, "title", ""),
        "link": getattr(entry, "link", ""),
        "published": getattr(entry, "published", "")
    })

print(f"取得した記事数: {len(recent_entries)} 件")

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=api_key)

prompt = f"""
以下は本日収集された日本のニュース見出し一覧です。
【条件】
1. 日本国内の企業・自治体・組織で「実際に個人情報や機密情報の漏洩・流出・不正アクセス被害が発生した/公表された一次事案」のみを抽出してください。
2. 単なるセキュリティ対策セミナーの告知、法改正の解説、海外での事例、一般的なコラムは絶対に除外してください。
3. 該当する漏洩事案が1件もなければ、空配列 [] を返してください。

ニュース一覧:
{json.dumps(recent_entries, ensure_ascii=False, indent=2)}

JSONスキーマに従って出力してください。
"""

schema_config = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema={
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "company": {"type": "STRING", "description": "被害に遭った企業・組織名"},
                "leak_type": {"type": "STRING", "description": "原因 (例: 不正アクセス, 設定ミス, 端末紛失, ランサムウェア)"},
                "scale": {"type": "STRING", "description": "被害件数や規模 (不明なら'調査中')"},
                "summary": {"type": "STRING", "description": "事案の簡潔な1文要約"},
                "url": {"type": "STRING", "description": "ニュース記事のリンクURL"}
            },
            "required": ["company", "leak_type", "summary", "url"]
        }
    }
)

# 試行するモデル候補の優先順位リスト
candidate_models = ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-2.5-pro"]
verified_incidents = None

for model_name in candidate_models:
    print(f"モデル '{model_name}' を試行中...")
    success = False
    
    # 最大3回リトライ（混雑時の503対策）
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=schema_config
            )
            verified_incidents = json.loads(response.text)
            print(f"-> 成功: {len(verified_incidents)} 件のインシデントを検出")
            success = True
            break
        except Exception as e:
            print(f"[{model_name}] 試行 {attempt}/3 でエラー: {e}")
            if attempt < 3:
                wait_sec = attempt * 5  # 5秒、10秒待機
                print(f"{wait_sec}秒待機してリトライします...")
                time.sleep(wait_sec)
    
    if success:
        break

# もしすべてのモデル・リトライで失敗した場合は、簡易キーワード判定にフォールバック（プロセスを落とさない）
if verified_incidents is None:
    print("警告: AIモデルの呼び出しが混雑等によりすべて失敗しました。簡易キーワード判定に切り替えます。")
    verified_incidents = []
    keywords = ["漏洩", "流出", "不正アクセス", "ランサムウェア"]
    for item in recent_entries:
        if any(k in item["title"] for k in keywords) and ("セミナー" not in item["title"]):
            verified_incidents.append({
                "company": "報道記事参照",
                "leak_type": "不正アクセス・漏洩の疑い",
                "scale": "記事参照",
                "summary": item["title"],
                "url": item["link"]
            })

# data/incidents.json の更新
data_file = "data/incidents.json"
os.makedirs(os.path.dirname(data_file), exist_ok=True)

if os.path.exists(data_file):
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            database = json.load(f)
    except Exception:
        database = {}
else:
    database = {}

if verified_incidents:
    database[today_str] = {
        "status": "bad",
        "incidents": verified_incidents
    }
else:
    database[today_str] = {
        "status": "clean",
        "incidents": []
    }

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"{today_str} のデータを正常に保存・更新しました。")
