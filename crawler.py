import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import feedparser
from google import genai
from google.genai import types
from email.utils import parsedate_to_datetime

# JST (日本時間) の設定
JST = timezone(timedelta(hours=9))

def get_report_date():
    now = datetime.now(JST)
    if now.hour < 1:
        target_date = now - timedelta(days=1)
    else:
        target_date = now
    return target_date.strftime("%Y-%m-%d")

today_str = get_report_date()
print(f"レポート対象日: {today_str}")

# 【改善1】検索キーワードを大幅に拡大
# 「情報漏洩」「個人情報流出」「不正アクセス」「ランサムウェア」「サイバー攻撃」で検索
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA+OR+%E4%B8%8D%E6%AD%A3%E3%82%A2%E3%82%AF%E3%82%BB%E3%82%B9+OR+%E3%83%A9%E3%83%B3%E3%82%B9%E3%83%A1%E3%82%A6%E3%82%A2%E3%83%8A+OR+%E3%82%B5%E3%82%A4%E3%83%90%E3%83%A9%E6%88%B8%E6%93%8A&hl=ja&gl=JP&ceid=JP:ja"

# 1. RSS取得
feed = feedparser.parse(RSS_URL)
all_entries = []

for entry in getattr(feed, "entries", []):
    # 【改善2】日付フィルタを緩和
    # AIに判定させるため、直近3日分くらいはすべて候補に入れる
    if "published" in entry:
        try:
            pub_date = parsedate_to_datetime(entry.published).astimezone(JST)
            # レポート対象日から3日前までの記事をすべて取得
            if pub_date >= datetime.now(JST) - timedelta(days=3):
                all_entries.append({
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "published": entry.published
                })
        except Exception as e:
            print(f"日付解析エラー: {e}")

print(f"AIに渡す候補記事数: {len(all_entries)} 件")

# 2. AIによる判定
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=api_key)

if not all_entries:
    verified_incidents = []
else:
    # 【改善3】プロンプトを「包括的」に変更
    prompt = f"""
    以下は最近の日本のニュース見出し一覧です。
    この中から、【{today_str}】に発生、または公表された日本の企業・自治体・組織による情報漏洩事案を抽出してください。

    【抽出条件】
    - 「個人情報の流出」「不正アクセスによるデータ取得」「ランサムウェア被害」「機密情報の漏洩」などが含まれるもの。
    - 「〜の疑い」や「調査中」という段階の報道でも、一次的な事案であれば含めてください。
    - 単なる対策セミナー、法改正、海外事例、一般的なセキュリティ啓発コラムは除外してください。
    - {today_str} 以外の日の記事は無視してください。

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
                    "company": {"type": "STRING", "description": "被害に遭った企業・組織名"},
                    "leak_type": {"type": "STRING", "description": "原因 (例: 不正アクセス, ランサムウェア, 設定ミス)"},
                    "scale": {"type": "STRING", "description": "被害規模 (不明なら'調査中')"},
                    "summary": {"type": "STRING", "description": "事案の簡潔な要約"},
                    "url": {"type": "STRING", "description": "記事URL"}
                },
                "required": ["company", "leak_type", "summary", "url"]
            }
        }
    )

    candidate_models = ["gemini-1.5-flash", "gemini-1.5-pro"]
    verified_incidents = None

    for model_name in candidate_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=schema_config
            )
            verified_incidents = json.loads(response.text)
            break
        except Exception as e:
            print(f"モデル {model_name} でエラー: {e}")
            continue

    if verified_incidents is None:
        verified_incidents = []

# 3. data/incidents.json の更新
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
