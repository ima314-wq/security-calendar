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

# 【改善】検索キーワードを拡大し、網を広げる
RSS_URL = "https://news.google.com/rss/search?q=%E6%83%85%E5%A0%B1%E6%BC%8F%E6%B4%A9+OR+%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA+OR+%E6%83%85%E5%A0%B1%E6%B5%81%E5%87%BA+OR+%E4%B8%8D%E6%AD%A3%E3%82%A2%E3%82%AF%E3%82%BB%E3%82%B9+OR+%E3%83%A9%E3%83%B3%E3%82%B9%E3%83%A1%E3%82%A6%E3%82%A2%E3%83%8A+OR+%E3%82%B5%E3%82%A4%E3%83%90%E3%83%A9%E6%88%B8%E6%93%8A&hl=ja&gl=JP&ceid=JP:ja"

# 1. RSS取得（AIに判断させるため、直近3日分を幅広く取得）
feed = feedparser.parse(RSS_URL)
candidate_entries = []

for entry in getattr(feed, "entries", []):
    if "published" in entry:
        try:
            pub_date = parsedate_to_datetime(entry.published).astimezone(JST)
            if pub_date >= datetime.now(JST) - timedelta(days=3):
                candidate_entries.append({
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "published": entry.published
                })
        except Exception as e:
            print(f"日付解析エラー: {e}")

print(f"AIに渡す候補記事数: {len(candidate_entries)} 件")

# 2. AIによる判定
api_key = os.environ.get("GEMINI_API_KEY")
client = None
if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except:
        print("APIクライアントの初期化に失敗しました。")

if not candidate_entries or not client:
    verified_incidents = None
else:
    prompt = f"""
    あなたは日本のサイバーセキュリティ監視員です。
    以下に最近のニュース見出し一覧を提供します。この中から、【{today_str}】に関連する情報漏洩事案を抽出してください。

    【抽出対象】
    - 日本国内の企業、自治体、組織で発生した「個人情報の流出」「不正アクセス」「ランサムウェア被害」「機密情報の漏洩」などの事案。
    - 【重要】「〜の疑い」「調査中」という報道であっても、新しい事案として報じられている場合は必ず含めてください。
    - 記事上の日付が {today_str} と完全に一致しなくても、内容的に「今日報じられたニュース」であると判断できるものはすべて抽出してください。
    - 単なる対策セミナー、法改正、海外事例、一般的なセキュリティコラムは除外してください。

    ニュース一覧:
    {json.dumps(candidate_entries, ensure_ascii=False, indent=2)}
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
            print(f"-> AIによる判定に成功しました ({model_name})")
            break
        except Exception as e:
            print(f"モデル {model_name} でエラーが発生しました (無視して続行します): {e}")
            continue

# 3. AIが失敗した場合の「キーワードフォールバック」 (ここが最強の安全装置)
if verified_incidents is None:
    print("AI解析が失敗したため、キーワード判定に切り替えます...")
    verified_incidents = []
    keywords = ["漏洩", "流出", "不正アクセス", "ランサムウェア", "サイバー攻撃", "漏えい"]
    
    # レポート対象日の記事だけを抽出
    for entry in candidate_entries:
        try:
            pub_date = parsedate_to_datetime(entry["published"]).astimezone(JST)
            if pub_date.strftime("%Y-%m-%d") == today_str:
                if any(k in entry["title"] for k in keywords) and ("セミナー" not in entry["title"]):
                    verified_incidents.append({
                        "company": "報道記事参照",
                        "leak_type": "不正アクセス・漏洩の疑い",
                        "scale": "記事参照",
                        "summary": entry["title"],
                        "url": entry["link"]
                    })
        except:
            continue
    print(f"-> キーワード判定により {len(verified_incidents)} 件を抽出しました。")

# 4. data/incidents.json の更新
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
