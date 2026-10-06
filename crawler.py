import os
import json
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
for entry in feed.entries[:20]:
    recent_entries.append({
        "title": entry.title,
        "link": entry.link,
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

response = client.models.generate_content(
    model="gemini-1.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
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
)

verified_incidents = json.loads(response.text)
print(f"判定された漏洩インシデント: {len(verified_incidents)} 件")

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

print(f"{today_str} のデータを保存しました。")
