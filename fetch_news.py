#!/usr/bin/env python3
"""
Vitta Wire — cloud fetcher.
Pulls banking/economics RSS feeds, writes news.json for the dashboard,
and sends a WhatsApp digest of newly-arrived stories via CallMeBot.
Runs on a schedule from GitHub Actions. No proxy needed (server-side).
"""
import os, re, sys, json, html, datetime, urllib.parse
import feedparser, requests

# --- Sources: edit this list to add/remove feeds ---------------------------
# Each Google News feed reliably surfaces the regulator's news. You can also
# drop in any direct RSS URL (e.g. an official RBI/SEBI feed).
SOURCES = [
    {"name": "RBI",              "scope": "india", "url": "https://news.google.com/rss/search?q=%22Reserve%20Bank%20of%20India%22%20OR%20RBI%20policy&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "SEBI",             "scope": "india", "url": "https://news.google.com/rss/search?q=SEBI%20markets%20regulator&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "NABARD",           "scope": "india", "url": "https://news.google.com/rss/search?q=NABARD%20rural%20agriculture%20finance&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "SIDBI / MSME",     "scope": "india", "url": "https://news.google.com/rss/search?q=SIDBI%20OR%20%22MSME%20credit%22%20India&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Indian Banking",   "scope": "india", "url": "https://news.google.com/rss/search?q=Indian%20banking%20sector%20NPA%20deposits%20credit&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Indian Economy",   "scope": "india", "url": "https://news.google.com/rss/search?q=India%20economy%20inflation%20GDP%20RBI&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "US Fed",           "scope": "world", "url": "https://news.google.com/rss/search?q=Federal%20Reserve%20interest%20rates&hl=en-US&gl=US&ceid=US:en"},
    {"name": "ECB / Europe",     "scope": "world", "url": "https://news.google.com/rss/search?q=European%20Central%20Bank%20OR%20Bank%20of%20England&hl=en&gl=US&ceid=US:en"},
    {"name": "IMF / World Bank", "scope": "world", "url": "https://news.google.com/rss/search?q=IMF%20OR%20%22World%20Bank%22%20global%20economy&hl=en&gl=US&ceid=US:en"},
    {"name": "Global Banking",   "scope": "world", "url": "https://news.google.com/rss/search?q=global%20banking%20regulation%20Basel%20banks&hl=en&gl=US&ceid=US:en"},
]

OUT_FILE   = "news.json"
MAX_ITEMS  = 120   # how many stories to keep in the dashboard
MAX_ALERTS = 5     # max headlines per WhatsApp digest (keeps the message readable)


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def to_ms(entry) -> int:
    tp = entry.get("published_parsed") or entry.get("updated_parsed")
    if tp:
        dt = datetime.datetime(*tp[:6], tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)


def parse_source(src: dict) -> list:
    out = []
    feed = feedparser.parse(src["url"])
    for e in feed.entries:
        title = clean(e.get("title", ""))
        if not title:
            continue
        link = e.get("link", "")
        publisher = ""
        if e.get("source") and isinstance(e.source, dict):
            publisher = e.source.get("title", "")
        if not publisher and " - " in title:           # Google News "Headline - Publisher"
            title, publisher = title.rsplit(" - ", 1)
        desc = clean(e.get("summary", ""))[:220]
        out.append({
            "id": (link or title)[:200],
            "title": title.strip(),
            "link": link,
            "desc": desc,
            "source": src["name"],
            "publisher": (publisher or src["name"]).strip(),
            "scope": src["scope"],
            "ts": to_ms(e),
        })
    return out


def send_whatsapp(new_items: list, total: int) -> None:
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        print("WhatsApp credentials not set — skipping alert.")
        return

    plural = "story" if total == 1 else "stories"
    lines = [f"🔔 Vitta Wire — {total} new banking {plural}", ""]
    for it in new_items:
        flag = "🇮🇳" if it["scope"] == "india" else "🌐"
        lines.append(f"{flag} {it['title']}")
        lines.append(it["link"])
        lines.append("")
    if total > len(new_items):
        lines.append(f"…and {total - len(new_items)} more in the app.")
    msg = "\n".join(lines).strip()

    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": msg, "apikey": apikey}
    )
    try:
        r = requests.get(url, timeout=40)
        print(f"WhatsApp sent — HTTP {r.status_code}")
    except Exception as ex:
        print(f"WhatsApp error: {ex}", file=sys.stderr)


def main():
    collected = []
    for src in SOURCES:
        try:
            items = parse_source(src)
            collected += items
            print(f"{src['name']}: {len(items)} items")
        except Exception as ex:
            print(f"{src['name']}: ERROR {ex}", file=sys.stderr)

    # dedupe + sort newest first + cap
    dedup = {}
    for it in collected:
        dedup.setdefault(it["id"], it)
    items = sorted(dedup.values(), key=lambda x: x["ts"], reverse=True)[:MAX_ITEMS]

    # compare against last run to find genuinely new stories
    prev_ids, first_run = set(), True
    if os.path.exists(OUT_FILE):
        try:
            prev = json.load(open(OUT_FILE, encoding="utf-8"))
            prev_ids = {i["id"] for i in prev.get("items", [])}
            first_run = False
        except Exception:
            pass
    new_items = [i for i in items if i["id"] not in prev_ids]

    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(items)} items · {len(new_items)} new · first_run={first_run}")

    # don't blast the whole feed on the very first run
    if not first_run and new_items:
        send_whatsapp(new_items[:MAX_ALERTS], len(new_items))


if __name__ == "__main__":
    main()
