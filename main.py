import asyncio, aiohttp, math, re, sqlite3, random, threading, requests, os
from flask import Flask, render_template_string, request
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter

app = Flask(__name__)

# --- CONFIG ---
TARGET_PAGES = 10000
DB_NAME = "searcli_v2.db"
STOP_WORDS = {"как", "что", "такое", "где", "это", "для", "под", "над", "в", "на", "и", "или", "быть", "с", "по", "ли"}

class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL') # Режим для ускорения работы
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, views INTEGER, content TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS words (word TEXT, doc_id INTEGER, count INTEGER)')
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, img_url TEXT UNIQUE, page_url TEXT, alt TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_w ON words(word)')
        self.conn.commit()

    def add_all(self, url, title, words, text, images):
        cursor = self.conn.cursor()
        try:
            cursor.execute("INSERT OR IGNORE INTO docs (url, title, views, content) VALUES (?, ?, ?, ?)", 
                           (url, title, random.randint(1000, 5000), text))
            row = cursor.execute("SELECT id FROM docs WHERE url=?", (url,)).fetchone()
            if row:
                doc_id = row[0]
                cursor.executemany("INSERT INTO words VALUES (?, ?, ?)", [(w, doc_id, c) for w, c in words.items()])
                for img_url, alt in images:
                    cursor.execute("INSERT OR IGNORE INTO images (img_url, page_url, alt) VALUES (?, ?, ?)", (img_url, url, alt))
            self.conn.commit()
        except: pass

    def search_text(self, query):
        q_low = query.lower().strip()
        q_words = [w for w in re.findall(r'[a-zа-яё0-9]{3,}', q_low) if w not in STOP_WORDS]
        if not q_words: return []
        cursor = self.conn.cursor()
        res = {}
        for word in q_words:
            cursor.execute('SELECT d.id, d.url, d.title, i.count, d.views, d.content FROM words i JOIN docs d ON i.doc_id = d.id WHERE i.word = ?', (word,))
            for d_id, url, title, tf, v, content in cursor.fetchall():
                t_low = (title or "").lower()
                score = (math.log(tf + 1) * 2 + math.log(v + 1))
                if word in t_low: score *= 10.0
                if q_low in t_low: score *= 50.0
                if d_id not in res: res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': (content or "")[:150]}
                else: res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)

    def search_img(self, query):
        cursor = self.conn.cursor()
        cursor.execute("SELECT img_url, alt FROM images WHERE alt LIKE ? LIMIT 30", ('%' + query + '%',))
        return cursor.fetchall()

db = DatabaseManager()

def get_widgets():
    data = {"usd": "78.50", "temp": "0", "idx": "0"}
    try:
        r1 = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=1).json()
        data["usd"] = f"{r1['Valute']['USD']['Value']:.2f}"
        r2 = requests.get("https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.61&current_weather=true", timeout=1).json()
        data["temp"] = f"{int(round(r2['current_weather']['temperature']))}"
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

async def crawler():
    seeds = ["https://ru.wikipedia.org/wiki/Служебная:Random", "https://news.google.com/", "https://www.rbc.ru/"]
    queue, visited = list(seeds), set()
    async with aiohttp.ClientSession(headers={'User-Agent': 'SearcliBot/1.0'}) as session:
        while queue and len(visited) < TARGET_PAGES:
            url = queue.pop(0)
            if url in visited: continue
            try:
                async with session.get(url, timeout=5) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    soup = BeautifulSoup(await r.text(errors='ignore'), 'html.parser')
                    text = soup.get_text()
                    title = (soup.title.string or url).strip()
                    imgs = [(urljoin(url, i['src']), i.get('alt','')) for i in soup.find_all('img', src=True) if len(i.get('alt','')) > 3][:10]
                    db.add_all(url, title, Counter(re.findall(r'[a-zа-яё0-9]{3,}', text.lower())), text, imgs)
                    for a in soup.find_all('a', href=True)[:15]:
                        l = urljoin(url, a['href'])
                        if urlparse(l).netloc and l not in visited: queue.append(l)
            except: continue
            await asyncio.sleep(1) # Даем серверу "подышать"

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Searcli</title>
    <style>
        :root { --bg: #121212; --text: #e8eaed; --primary: #bb86fc; --border: #333; --sub: #9aa0a6; }
        body { font-family: sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
        .logo-box { text-align: center; margin: 40px 0; }
        .logo { font-size: 72px; font-weight: 500; color: var(--primary); text-decoration: none; }
        .widgets { display: flex; gap: 10px; margin-bottom: 30px; }
        .widget { background: #1e1e1e; padding: 15px; border-radius: 15px; text-align: center; border: 1px solid var(--border); min-width: 80px; }
        .search-input { width: 100%; max-width: 600px; padding: 15px 25px; border-radius: 50px; border: 1px solid var(--border); background: #1e1e1e; color: var(--text); font-size: 18px; outline: none; }
        .tabs { margin: 20px 0; display: flex; gap: 20px; }
        .tab { text-decoration: none; color: var(--sub); font-size: 14px; }
        .tab.active { color: var(--primary); font-weight: bold; border-bottom: 2px solid var(--primary); }
        .res-item { width: 100%; margin-bottom: 30px; text-align: left; }
        .res-title { font-size: 20px; color: var(--primary); text-decoration: none; }
        .rating-bar { width: 100px; height: 4px; background: #333; border-radius: 2px; margin-top: 8px; overflow: hidden; }
        .img-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; width: 100%; }
        .img-card { height: 150px; border-radius: 8px; overflow: hidden; background: #333; }
        .img-card img { width: 100%; height: 100%; object-fit: cover; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo-box">
            <a href="/" class="logo">Searcli</a>
            <div style="font-size: 14px; font-weight: 300; opacity: 0.7;">developer by Labretto</div>
        </div>
        {% if not q %}<div class="widgets">
            <div class="widget"><div style="font-weight:bold">{{ w.temp }}°C</div><div style="font-size:10px">ПОГОДА</div></div>
            <div class="widget"><div style="font-weight:bold">{{ w.usd }} ₽</div><div style="font-size:10px">USD/RUB</div></div>
            <div class="widget"><div style="font-weight:bold">{{ w.idx }}</div><div style="font-size:10px">ИНДЕКС</div></div>
        </div>{% endif %}
        <form action="/search" style="width:100%; text-align:center;">
            <input name="q" class="search-input" placeholder="Поиск..." value="{{ q }}" required>
            <input type="hidden" name="t" value="{{ t }}">
        </form>
        {% if q %}<div class="tabs">
            <a href="/search?q={{q}}&t=text" class="tab {% if t!='img' %}active{% endif %}">Все</a>
            <a href="/search?q={{q}}&t=img" class="tab {% if t=='img' %}active{% endif %}">Картинки</a>
        </div>{% endif %}
        <div style="width:100%">
            {% if t == 'img' %}<div class="img-grid">
                {% for i in results %}<div class="img-card"><a href="{{ i[0] }}" target="_blank"><img src="{{ i[0] }}"></a></div>{% endfor %}
            </div>{% else %}
                {% for r in results %}<div class="res-item">
                    <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
                    <div style="font-size:14px; color:var(--sub)">{{ r.snippet }}...</div>
                    <div style="display:flex; align-items:center; gap:10px; font-size:11px; margin-top:5px; color:var(--sub);">
                        <div class="rating-bar"><div style="width:{{ [r.score*2, 100]|min }}%; height:100%; background:#bb86fc"></div></div>
                        Рейтинг: {{ "%.1f"|format(r.score) }}
                    </div>
                </div>{% endfor %}
            {% endif %}
        </div>
        <div style="margin-top: auto; padding: 40px 0; font-size: 10px; font-weight: 300; letter-spacing: 3px; opacity: 0.5;">Searcli 1.0</div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, q="", t="text", results=[], w=get_widgets())

@app.route('/search')
def search():
    q, t = request.args.get('q', ''), request.args.get('t', 'text')
    res = db.search_img(q) if t == 'img' else db.search_text(q)
    return render_template_string(HTML, q=q, t=t, results=res, w=get_widgets())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: asyncio.run(crawler()), daemon=True).start()
    app.run(host='0.0.0.0', port=port)
