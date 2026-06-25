import os, json, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
MAX_ARTICLES = 50
BATCH_SIZE   = 20

NEWS_SOURCES = [
    {"name": "매일경제", "rss": "https://www.mk.co.kr/rss/30000001/"},
    {"name": "연합뉴스", "rss": "https://www.yna.co.kr/rss/economy.xml"},
    {"name": "SBS경제",  "rss": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02"},
    {"name": "전자신문", "rss": "https://rss.etnews.com/Section901.xml"},
]


# ── RSS ──────────────────────────────────────────────────

def _parse_date(entry):
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(KST)
        except Exception:
            pass
    return None


def _fetch_source(source, start, end):
    result = []
    try:
        feed = feedparser.parse(source["rss"])
        for entry in feed.entries:
            pub = _parse_date(entry)
            if pub is None or not (start <= pub <= end):
                continue
            raw = getattr(entry, "description", "") or getattr(entry, "summary", "")
            content = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)[:400]
            result.append({
                "source":   source["name"],
                "title":    entry.title.strip(),
                "link":     entry.link.strip(),
                "pub_date": pub.strftime("%m/%d %H:%M"),
                "_ts":      pub.isoformat(),
                "_content": content,
                "summary":  "",
            })
    except Exception:
        pass
    return result


def fetch_all(hours):
    now   = datetime.now(KST)
    end   = now
    start = now - timedelta(hours=hours)
    articles = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_fetch_source, s, start, end) for s in NEWS_SOURCES]
        for f in as_completed(futures):
            articles.extend(f.result())
    articles.sort(key=lambda x: x["_ts"])
    return articles[:MAX_ARTICLES]


# ── Gemini ───────────────────────────────────────────────

def _gemini_request(prompt):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4096},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{GEMINI_URL}?key={GEMINI_KEY}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _parse_numbered(raw, count):
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            try:
                end = line.index("]")
                idx = int(line[1:end])
                result[idx] = line[end + 1:].strip()
            except ValueError:
                pass
    return [result.get(i + 1, "") for i in range(count)]


def summarize(articles):
    if not articles or not GEMINI_KEY:
        return

    for i in range(0, len(articles), BATCH_SIZE):
        chunk = articles[i:i + BATCH_SIZE]
        lines = "\n\n".join(
            f"[{j+1}] 제목: {a['title']}\n내용: {a['_content'][:250]}"
            for j, a in enumerate(chunk)
        )
        prompt = (
            "아래 경제 뉴스 기사들을 각각 2~3문장으로 요약하세요.\n"
            "반드시 '[번호] 요약문' 형식으로 출력하고, 수치는 그대로 유지하세요.\n\n"
            f"{lines}"
        )
        try:
            raw = _gemini_request(prompt)
            summaries = _parse_numbered(raw, len(chunk))
            for art, summ in zip(chunk, summaries):
                art["summary"] = summ
        except urllib.error.HTTPError as e:
            if e.code == 429:
                for art in chunk:
                    art["summary"] = "(API 한도 초과 — 잠시 후 다시 시도)"
            else:
                for art in chunk:
                    art["summary"] = f"(요약 오류: {e.code})"
        except Exception as e:
            for art in chunk:
                art["summary"] = f"(요약 오류: {e})"


# ── Handler ──────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        hours = int(params.get("hours", ["14"])[0])

        try:
            articles = fetch_all(hours)
            summarize(articles)

            # 내부 전용 필드 제거 후 응답
            out = [{k: v for k, v in a.items() if not k.startswith("_")} for a in articles]

            body = json.dumps(
                {"articles": out, "count": len(out), "has_key": bool(GEMINI_KEY)},
                ensure_ascii=False,
            ).encode("utf-8")
            code = 200
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")
            code = 500

        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        pass
