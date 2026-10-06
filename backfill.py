import os
import json
import time
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai
from google.genai import types

# 設定
START_DATE = "2026-10-01"
END_DATE = "2026-10-05"  # 昨日の分まで
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA&hl=ja&gl=JP&ceid=JP:ja"

print(f"{START_DATE} から {END_DATE} までのデータを復旧します...")

# 1. RSSから多めに記事を取得
feed = feedparser.parse(RSS_URL)
all_entries = []
for entry in getattr(feed, "entries", [])[:100]:
    all_entries.append({
        "title": getattr(entry, "title", ""),
        "link": getattr(entry, "link", ""),
        "published": getattr(entry, "published", "")
    })

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=api_key)

# 2. Geminiに「リスト形式」で抽出させる
prompt = f"""
以下はニュース記事の一覧です。
【条件】
1. {START_DATE} から {END_DATE} までの期間に、日本国内の企業・自治体・組織で「実際に個人情報や機密情報の漏洩・流出・不正アクセス被害が発生した/公表された一次事案」のみを抽出してください。
2. 結果は「日ごとのオブジェクト」を要素に持つ配列（リスト）形式で返してください。
3. 該当する事案がない日も、日付を a `date` フィールドに入れ、status: "clean" として含めてください。
4. 単なるセミナー告知、海外事例、一般コラムは除外してください。

ニュース一覧:
{json.dumps(all_entries, ensure_ascii=False, indent=2)}
"""

schema_config = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema={
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "date": {"type": "STRING", "description": "YYYY-MM-DD形式の日付"},
                "status": {"type": "STRING", "enum": ["bad", "clean"]},
                "incidents": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "company": {"type": "STRING"},
                            "leak_type": {"type": "STRING"},
                            "scale": {"type": "STRING"},
                            "summary": {"type": "STRING"},
                            "url": {"type": "STRING"}
                        },
                        "required": ["company", "leak_type", "summary", "url"]
                    }
                }
            },
            "required": ["date", "status", "incidents"]
        }
    }
)

# crawler.py と同様に、複数のモデルを試行する
candidate_models = ["gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro"]
verified_results = None

for model_name in candidate_models:
    try:
        print(f"モデル '{model_name}' で解析を試行中...")
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=schema_config
        )
        verified_results = json.loads(response.text)
        print(f"-> 成功: {model_name} でデータを抽出しました。")
        break
    except Exception as e:
        print(f"モデル {model_name} でエラーが発生しました: {e}")
        continue

if verified_results is None:
    print("すべての試行モデルでエラーが発生しました。処理を中断します。")
    exit(1)

# リスト形式から辞書形式に変換
past_data = {}
for item in verified_results:
    date_key = item["date"]
    past_data[date_key] = {
        "status": item["status"],
        "incidents": item["incidents"]
    }

# 3. data/incidents.json にマージして保存
data_file = "data/incidents.json"
if os.path.exists(data_file):
    with open(data_file, "r", encoding="utf-8") as f:
        database = json.load(f)
else:
    database = {}

database.update(past_data)

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"正常に {data_file} を更新しました。カレンダーを確認してください！")
