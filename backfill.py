import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai

# 設定
START_DATE = "2026-10-01"
END_DATE = "2026-10-05"
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA&hl=ja&gl=JP&ceid=JP:ja"

print(f"{START_DATE} から {END_DATE} までのデータを復旧します...")

# 1. RSSから記事を取得
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

# 2. プロンプトでJSON形式を厳格に指定 (スキーマ機能を使わない)
prompt = f"""
以下はニュース記事の一覧です。
【抽出条件】
- {START_DATE} から {END_DATE} までの期間に、日本国内の企業・自治体・組織で「実際に個人情報や機密情報の漏洩・流出・不正アクセス被害が発生した/公表された一次事案」のみを抽出してください。
- 該当する事案がない日も、日付を a `date` フィールドに入れ、status: "clean" として含めてください。
- 単なるセミナー告知、海外事例、一般コラムは絶対に除外してください。

【出力形式】
- 出力は必ず純粋な JSON 配列形式のみとしてください。
- Markdownのコードブロック (```json ... ```) は絶対に付けないでください。
- 形式: [ {{ "date": "YYYY-MM-DD", "status": "bad", "incidents": [ {{ "company": "...", "leak_type": "...", "scale": "...", "summary": "...", "url": "..." }} ] }} ]

ニュース一覧:
{json.dumps(all_entries, ensure_ascii=False, indent=2)}
"""

candidate_models = ["gemini-1.5-flash", "gemini-1.5-pro"]
verified_results = None

for model_name in candidate_models:
    try:
        print(f"モデル '{model_name}' で解析を試行中...")
        # schema_config を使わず、単純なテキスト生成として呼び出す
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        
        text = response.text.strip()
        # 万が一AIが ```json ... ``` を付けてしまった場合のクリーニング処理
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\n?|```$', '', text, flags=re.MULTILINE).strip()
        
        verified_results = json.loads(text)
        print(f"-> 成功: {model_name} でデータを抽出しました。")
        break
    except Exception as e:
        print(f"モデル {model_name} でエラーが発生しました: {e}")
        continue

if verified_results is None:
    print("すべての試行モデルでエラーが発生しました。")
    exit(1)

# 3. データ変換と保存
past_data = {}
for item in verified_results:
    if "date" in item:
        date_key = item["date"]
        past_data[date_key] = {
            "status": item.get("status", "clean"),
            "incidents": item.get("incidents", [])
        }

data_file = "data/incidents.json"
if os.path.exists(data_file):
    with open(data_file, "r", encoding="utf-8") as f:
        database = json.load(f)
else:
    database = {}

database.update(past_data)

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"正常に {data_file} を更新しました。")
