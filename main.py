import asyncio, aiohttp, math, re, sqlite3, random, threading, requests, os
from flask import Flask, render_template_string, request, jsonify
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter
from datetime import datetime

app = Flask(__name__)

# --- CONFIG ---
TARGET_PAGES = 10000
DB_NAME = "searcli_final_v1.db"

class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, content TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS words (word TEXT, doc_id INTEGER, count INTEGER)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_w ON words(word)')
        self.conn.commit()

    def add_all(self, url, title, words, text):
        cursor = self.conn.cursor()
        try:
            cursor.execute("INSERT OR IGNORE INTO docs (url, title, content) VALUES (?, ?, ?)", (url, title, text))
            doc_id = cursor.execute("SELECT id FROM docs WHERE url=?", (url,)).fetchone()[0]
            cursor.executemany("INSERT INTO words VALUES (?, ?, ?)", [(w, doc_id, c) for w, c in words.items()])
            self.conn.commit()
        except: pass

    def search_text(self, query):
        q_low = query.lower().strip()
        q_words = re.findall(r'[a-zа-яё0-9]+', q_low)
        if not q_words: return []
        cursor = self.conn.cursor()
        res = {}
        for word in q_words:
            cursor.execute('''SELECT d.id, d.url, d.title, i.count, d.content 
                             FROM words i JOIN docs d ON i.doc_id = d.id WHERE i.word = ?''', (word,))
            for d_id, url, title, tf, content in cursor.fetchall():
                score = tf * 10
                if word in (title or "").lower(): score += 100
                if d_id not in res: res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': content[:180]}
                else: res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)[:20]

db = DatabaseManager()

# --- СТАБИЛЬНЫЕ ВИДЖЕТЫ ---
def get_widgets_data():
    data = {"usd": "91.45", "eur": "99.10", "temp": "—", "city": "Интернет", "idx": "0"}
    try:
        # Погода и город через более надежный API
        geo = requests.get("https://ipwho.is/", timeout=3).json()
        if geo.get('success'):
            data["city"] = geo.get('city', 'Москва')
            lat, lon = geo.get('latitude'), geo.get('longitude')
            w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
            data["temp"] = f"{int(round(w_res['current_weather']['temperature']))}"
        
        # Курс валют (запасной источник)
        cur = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=3).json()
        data["usd"] = f"{cur['rates']['RUB']:.2f}"
        data["eur"] = f"{cur['rates']['RUB'] / cur['rates']['EUR']:.2f}"
        
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

# --- ГЛОБАЛЬНЫЙ КРАУЛЕР ---
async def crawler_task():
    seeds = ["https://ru.wikipedia.org/wiki/Брин,_Сергей", "https://news.yandex.ru", "https://habr.com", "https://www.rbc.ru"]
    queue, visited = list(seeds), set()
    async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
        while queue and len(visited) < TARGET_PAGES:
            url = queue.pop(0)
            if url in visited: continue
            try:
                async with session.get(url, timeout=5) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    html = await r.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    title = (soup.title.string or url).strip()
                    text = soup.get_text(separator=' ')
                    db.add_all(url, title, Counter(re.findall(r'[a-zа-яё0-9]+', text.lower())), text)
                    
                    for a in soup.find_all('a', href=True):
                        link = urljoin(url, a['href'])
                        if urlparse(link).netloc and link not in visited:
                            queue.append(link)
                        if len(queue) > 500: break
            except: continue
            await asyncio.sleep(0.1) # Быстрый краулинг

def run_crawler_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(crawler_task())

# --- ИНТЕРФЕЙС LABRETTO ---
HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Searcli</title>
    <style>
        :root { --bg: #0a0a0a; --text: #fff; --primary: #bb86fc; --border: #222; }
        body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; }
        .container { max-width: 700px; margin: 0 auto; }
        .logo { font-size: 60px; font-weight: 200; color: #fff; text-decoration: none; letter-spacing: -2px; display: block; text-align: center; margin-top: 40px;}
        .dev-tag { font-size: 11px; color: #fff; font-weight: 200; letter-spacing: 3px; text-align: center; opacity: 0.7; margin-bottom: 30px;}
        .widgets { display: flex; gap: 10px; margin-bottom: 25px; }
        .w-card { background: #111; padding: 15px; border-radius: 20px; border: 1px solid var(--border); flex: 1; text-align: center; }
        .search-input { width: 100%; padding: 18px 25px; border-radius: 50px; border: 1px solid var(--border); background: #111; color: #fff; font-size: 17px; outline: none; box-sizing: border-box; }
        .res-item { margin-top: 35px; border-bottom: 1px solid #1a1a1a; padding-bottom: 20px; }
        .res-title { color: #8ab4f8; font-size: 20px; text-decoration: none; display: block; margin-bottom: 5px; }
        .rating-box { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
        .rating-bar { height: 4px; background: #222; border-radius: 2px; flex-grow: 1; overflow: hidden; }
        .rating-fill { height: 100%; background: var(--primary); }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="logo">Searcli</a>
        <div class="dev-tag">developer by Labretto</div>

        <div class="widgets">
            <div class="w-card"><b>{{ w.temp }}°C</b><br><small style="color:#888">{{ w.city }}</small></div>
            <div class="w-card"><b>{{ w.usd }}₽</b><br><small style="color:#888">USD</small></div>
            <div class="w-card"><b>{{ w.idx }}</b><br><small style="color:#888">ИНДЕКС</small></div>
        </div>

        <form action="/search">
            <input name="q" class="search-input" placeholder="Найти в глобальной сети..." value="{{ q }}" required>
        </form>

        {% if smart %}<div style="background:#111; padding:20px; border-radius:25px; margin-top:20px; border:1px solid var(--primary)">{{ smart|safe }}</div>{% endif %}

        {% for r in results %}
        <div class="res-item">
            <small style="color:#888">{{ r.url[:60] }}</small>
            <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
            <div style="color:#aaa; font-size:14px">{{ r.snippet }}...</div>
            <div class="rating-box">
                <span style="font-size:10px; color:var(--primary)">RANK</span>
                <div class="rating-bar"><div class="rating-fill" style="width:{{ [r.score/5, 100]|min }}%"></div></div>
            </div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, q="", results=[], w=get_widgets_data(), smart="")

@app.route('/search')
def search():
    q = request.args.get('q', '')
    w = get_widgets_data()
    res = db.search_text(q)
    
    # Смарт-виджет для Википедии
    smart = ""
    if res and "wikipedia.org" in res[0]['url']:
        smart = f"<b>Инсайт из Википедии:</b><br>{res[0]['snippet']}... <br><a href='{res[0]['url']}' style='color:var(--primary)'>Читать полностью</a>"
    
    return render_template_string(HTML, q=q, results=res, w=w, smart=smart)

if __name__ == '__main__':
    threading.Thread(target=run_crawler_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)
