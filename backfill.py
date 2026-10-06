import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai
from email.utils import parsedate_to_datetime

# 設定
START_DATE = "2026-10-01"
END_DATE = "2026-10-05"
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA&hl=ja&gl=JP&ceid=JP:ja"

print(f"{START_DATE} から {END_DATE} までのデータを復旧します...")

# 1. RSSから記事を取得
feed = feedparser.parse(RSS_URL)
all_entries = []
for entry in getattr(feed, "entries", [])[:100]:
    # 公開日を datetime オブジェクトに変換して保存
    pub_date = None
    if "published" in entry:
        try:
            pub_date = parsedate_to_datetime(entry.published).astimezone(timezone.utc)
        except:
            pass
            
    all_entries.append({
        "title": getattr(entry, "title", ""),
        "link": getattr(entry, "link", ""),
        "published": pub_date # datetime型
    })

api_key = os.environ.get("GEMINI_API_KEY")
client = None
if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except:
        print("APIクライアントの初期化に失敗しました。")

# 2. AIによる解析を試行
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
{json.dumps(all_entries, ensure_ascii=False, indent=2, default=str)}
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
            print(f"モデル {model_name} でエラーが発生しました: {e}")
            continue

# 3. AIが失敗した場合の「キーワードフォールバック」 (日付判定を追加)
if verified_results is None:
    print("AI解析が失敗したため、日付に基づいたキーワード判定に切り替えます...")
    verified_results = []
    keywords = ["漏洩", "流出", "不正アクセス", "ランサムウェア"]
    
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    
    current = start_dt
    while current <= end_dt:
        date_str = current.strftime("%Y-%m-%d")
        day_incidents = []
        
        for item in all_entries:
            # 記事の公開日があるか、かつその日が現在のループ日と同じかを確認
            if item["published"]:
                article_date_str = item["published"].strftime("%Y-%m-%d")
                if article_date_str == date_str:
                    # 日付が一致した上で、キーワードが含まれているか判定
                    if any(k in item["title"] for k in keywords) and ("セミナー" not in item["title"]):
                        day_incidents.append({
                            "company": "報道記事参照",
                            "leak_type": "不正アクセス・漏洩の疑い",
                            "scale": "記事参照",
                            "summary": item["title"],
                            "url": item["link"]
                        })
        
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

database.update(past_data)

with open(data_file, "w", encoding="utf-8") as f:
    json.dump(database, f, ensure_ascii=False, indent=2)

print(f"正常に {data_file} を更新しました。")
