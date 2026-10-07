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
    """
    実行タイミングに合わせて、レポート対象の日付を決定する。
    深夜0時〜1時の間に実行された場合は、前日の日付を返す。
    """
    now = datetime.now(JST)
    # 深夜 1:00 までなら、前日のまとめとして処理する
    if now.hour < 1:
        target_date = now - timedelta(days=1)
    else:
        target_date = now
    return target_date.strftime("%Y-%m-%d")

today_str = get_report_date()
print(f"レポート対象日: {today_str}")

# 監視対象のRSS
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA&hl=ja&gl=JP&ceid=JP:ja"

# 1. RSS取得と【日付フィルタリング】
feed = feedparser.parse(RSS_URL)
filtered_entries = []

for entry in getattr(feed, "entries", []):
    # 公開日を解析
    if "published" in entry:
        try:
            pub_date = parsedate_to_datetime(entry.published).astimezone(JST)
            pub_date_str = pub_date.strftime("%Y-%m-%d")
            
            # 【重要】レポート対象日と同じ日の記事だけを抽出
            if pub_date_str == today_str:
                filtered_entries.append({
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "published": entry.published
                })
        except Exception as e:
            print(f"日付解析エラー: {e}")

print(f"{today_str} に公開された記事数: {len(filtered_entries)} 件")

# 2. AIによる判定
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=api_key)

# 記事が1件もない場合は、即座に clean として保存
if not filtered_entries:
    verified_incidents = []
else:
    prompt = f"""
    以下は {today_str} に公開された日本のニュース見出し一覧です。
    【条件】
    1. 日本国内の企業・自治体・組織で「実際に個人情報や機密情報の漏洩・流出・不正アクセス被害が発生した/公表された一次事案」のみを抽出してください。
    2. 単なるセキュリティ対策セミナーの告知、法改正の解説、海外での事例、一般的なコラムは絶対に除外してください。
    3. 該当する漏洩事案が1件もなければ、空配列 [] を返してください。

    ニュース一覧:
    {json.dumps(filtered_entries, ensure_ascii=False, indent=2)}
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

    # AIが失敗した場合は空配列にする（または簡易判定をここに入れる）
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

print(f"{today_str} のデータを正常に保存しました。")
