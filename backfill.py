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
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA+OR+%E4%B8%8D%E6%AD%A3%E3%82%A2%E3%82%AF%E3%82%BB%E3%82%B9+OR+%E3%83%A9%E3%83%B3%E3%82%B9%E3%83%A1%E3%82%A6%E3%82%A2%E3%83%8A+OR+%E3%82%B5%E3%82%A4%E3%83%90%E3%83%A9%E6%88%B8%E6%93%8A&hl=ja&gl=JP&ceid=JP:ja"

print(f"{START_DATE} から {END_DATE} までのデータを復旧します...")

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
client = None
if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except:
        print("APIクライアントの初期化に失敗しました。")

# 2. AIによる解析を試行 (失敗しても絶対に止まらない)
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

if client:
    for model_name in candidate_models:
        try:
            print(f"AIモデル '{model_name}' で解析を試行中...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = re.sub(r'^```(?:json)?\n?|```$', '', text, flags=re.MULTILINE).strip()
            verified_results = json.loads(text)
            print(f"-> 成功: AIによる精査が完了しました。")
            break
        except Exception as e:
            print(f"モデル {model_name} でエラーが発生しました (無視して続行します): {e}")
            continue

# 3. AIが失敗した場合の「日付別キーワード判定」 (ここが安全装置)
if verified_results is None:
    print("AI解析がすべて失敗したため、日付ベースのキーワード判定に切り替えます...")
    verified_results = []
    keywords = ["漏洩", "流出", "不正アクセス", "ランサムウェア", "サイバー攻撃"]
    
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    
    current = start_dt
    while current <= end_dt:
        date_str = current.strftime("%Y-%m-%d")
        day_incidents = []
        
        for item in all_entries:
            if item["published"]:
                try:
                    # 公開日を解析して日付が一致するか確認
                    pub_dt = parsedate_to_datetime(item["published"]).astimezone(timezone.utc)
                    if pub_dt.strftime("%Y-%m-%d") == date_str:
                        if any(k in item["title"] for k in keywords) and ("セミナー" not in item["title"]):
                            day_incidents.append({
                                "company": "報道記事参照",
                                "leak_type": "不正アクセス・漏洩の疑い",
                                "scale": "記事参照",
                                "summary": item["title"],
                                "url": item["link"]
                            })
                except:
                    continue
        
        verified_results.append({
            "date": date_str,
            "status": "bad" if day_incidents else "clean",
            "incidents": day_incidents
        })
        current += timedelta(days=1)

# 4. データ変換と保存
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

for date_key, value in past_data.items():
    database[date_key] = value

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"正常に {data_file} を更新しました。")
