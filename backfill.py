import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai
from email.utils import parsedate_to_datetime

# 設定
START_DATE = "2026-10-07"
END_DATE = "2026-10-09"
# 【改善】検索キーワードを拡大
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA+OR+%E4%B8%8D%E6%AD%A3%E3%82%A2%E3%82%AF%E3%82%BB%E3%82%B9+OR+%E3%83%A9%E3%83%B3%E3%82%B9%E3%83%A1%E3%82%A6%E3%82%A2%E3%83%8A+OR+%E3%82%B5%E3%82%A4%E3%83%90%E3%83%A9%E6%88%B8%E6%93%8A&hl=ja&gl=JP&ceid=JP:ja"

print(f"{START_DATE} から {END_DATE} までのデータを高精度モードで復旧します...")

# 1. RSSから記事を取得
feed = feedparser.parse(RSS_URL)
all_entries = []
for entry in getattr(feed, "entries", []):
    if "published" in entry:
        try:
            pub_date = parsedate_to_datetime(entry.published).astimezone(timezone.utc)
            if pub_date >= datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(days=2):
                all_entries.append({
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "published": entry.published
                })
        except Exception as e:
            print(f"日付解析エラー: {e}")

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=api_key)

# 2. Geminiに「テキストとして」JSONを依頼 (response_schemaを削除)
prompt = f"""
以下は最近の日本のニュース見出し一覧です。
この中から、【{START_DATE} から {END_DATE}】の期間に発生、または公表された日本の企業・自治体・組織による情報漏洩事案を抽出してください。

【抽出条件】
- 「個人情報の流出」「不正アクセスによるデータ取得」「ランサムウェア被害」「機密情報の漏洩」などが含まれるもの。
- 「〜の疑い」や「調査中」という段階の報道でも、一次的な事案であれば含めてください。
- 単なる対策セミナー、法改正、海外事例、一般的なセキュリティ啓発コラムは除外してください。

【出力形式】
- 出力は必ず純粋な JSON 配列形式のみとしてください。
- Markdownのコードブロック (```json ... ```) は絶対に付けないでください。
- 形式: [ {{ "date": "YYYY-MM-DD", "status": "bad", "incidents": [ {{ "company": "...", "leak_type": "...", "scale": "...", "summary": "...", "url": "..." }} ] }} ]
- 該当事案がない日も、date と status: "clean" を含めてください。

ニュース一覧:
{json.dumps(all_entries, ensure_ascii=False, indent=2)}
"""

candidate_models = ["gemini-1.5-flash", "gemini-1.5-pro"]
verified_results = None

for model_name in candidate_models:
    try:
        print(f"モデル '{model_name}' で解析を試行中...")
        # response_schema を使わずに呼び出す
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        text = response.text.strip()
        # AIが ```json ... ``` を付けてしまった場合のクリーニング
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\n?|```$', '', text, flags=re.MULTILINE).strip()
        
        verified_results = json.loads(text)
        print(f"-> 成功: {model_name} でデータを抽出しました。")
        break
    except Exception as e:
        print(f"モデル {model_name} でエラーが発生しました: {e}")
        continue

if verified_results is None:
    print("すべてのモデルでエラーが発生しました。")
    exit(1)

# 3. data/incidents.json にマージして保存
data_file = "data/incidents.json"
if os.path.exists(data_file):
    with open(data_file, "r", encoding="utf-8") as f:
        database = json.load(f)
else:
    database = {}

for item in verified_results:
    date_key = item["date"]
    database[date_key] = {
        "status": item["status"],
        "incidents": item["incidents"]
    }

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"正常に {data_file} を更新しました。カレンダーを確認してください！")
