import asyncio, aiohttp, math, re, sqlite3, random, threading, requests, os
from flask import Flask, render_template_string, request, jsonify
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter
from datetime import datetime

app = Flask(__name__)

# --- CONFIG ---
TARGET_PAGES = 15000
DB_NAME = "searcli_final_v1.db"

# Список интересных фактов для главного экрана
FACTS = [
    "Первый домен в истории — symbolics.com — был зарегистрирован 15 марта 1985 года.",
    "Около 40% всего трафика в интернете генерируют боты, а не люди.",
    "Google изначально назывался BackRub.",
    "Первое видео на YouTube было загружено 23 апреля 2005 года его сооснователем Джаведом Каримом.",
    "Символ '@' использовался еще в эпоху Возрождения для обозначения веса."
]

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
            row = cursor.execute("SELECT id FROM docs WHERE url=?", (url,)).fetchone()
            if row:
                doc_id = row[0]
                cursor.executemany("INSERT INTO words VALUES (?, ?, ?)", [(w, doc_id, c) for w, c in words.items()])
                self.conn.commit()
        except: pass

    def get_suggestions(self, prefix):
        if len(prefix) < 2: return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT word FROM words WHERE word LIKE ? LIMIT 5", (prefix.lower() + '%',))
        return [r[0] for r in cursor.fetchall()]

    def search_text(self, query):
        q_low = query.lower().strip()
        q_words = re.findall(r'[a-zа-яё0-9]+', q_low)
        if not q_words: return []
        cursor = self.conn.cursor()
        res = {}
        for word in q_words:
            cursor.execute('SELECT d.id, d.url, d.title, i.count, d.content FROM words i JOIN docs d ON i.doc_id = d.id WHERE i.word = ?', (word,))
            for d_id, url, title, tf, content in cursor.fetchall():
                score = tf * 10
                if word in (title or "").lower(): score += 200
                if d_id not in res: res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': content[:180]}
                else: res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)[:25]

db = DatabaseManager()

def get_widgets_data():
    data = {"usd": "91.5", "eur": "99.2", "temp": "—", "city": "Москва", "idx": "0"}
    try:
        geo = requests.get("https://ipwho.is/", timeout=4).json()
        if geo.get('success'):
            data["city"] = geo.get('city', 'Москва')
            lat, lon = geo.get('latitude'), geo.get('longitude')
            w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
            data["temp"] = f"{int(round(w_res['current_weather']['temperature']))}"
        cur = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=3).json()
        data["usd"] = f"{cur['rates']['RUB']:.2f}"
        data["eur"] = f"{cur['rates']['RUB'] / cur['rates']['EUR']:.2f}"
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

async def crawler_task():
    seeds = [
        "https://www.google.com/search?q=news+tech+science+wiki", # Хитрость для зацепа
        "https://dmoz-odp.org/",        # Огромный каталог ссылок на весь мир
        "https://top100.rambler.ru/",   # Весь рунет
        "https://www.reddit.com/r/all/", # Весь англоязычный интернет
        "https://habr.com/ru/all/",
        "https://en.wikipedia.org/wiki/Special:Random" # Прыжок в случайную точку мира
    ]
    queue, visited = list(seeds), set()
    async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
        while queue and len(visited) < TARGET_PAGES:
            url = queue.pop(0)
            if url in visited: continue
            try:
                async with session.get(url, timeout=5) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    soup = BeautifulSoup(await r.text(errors='ignore'), 'html.parser')
                    for s in soup(["script", "style"]): s.decompose()
                    t, txt = (soup.title.string or url).strip(), soup.get_text(separator=' ')
                    db.add_all(url, t, Counter(re.findall(r'[a-zа-яё0-9]+', txt.lower())), txt)
                    for a in soup.find_all('a', href=True):
                        link = urljoin(url, a['href'])
                        if urlparse(link).netloc and link not in visited: queue.append(link)
                        if len(queue) > 1000: break
            except: continue
            await asyncio.sleep(0.1)
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Searcli</title>
    <style>
        :root { --bg: #0a0a0a; --text: #fff; --primary: #bb86fc; --border: #222; --card: #111; }
        body { font-family: 'Inter', -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 15px; overflow-x: hidden; }
        .container { max-width: 700px; margin: 0 auto; width: 100%; box-sizing: border-box; }
        .logo { font-size: clamp(40px, 10vw, 60px); font-weight: 200; color: #fff; text-decoration: none; letter-spacing: -2px; display: block; text-align: center; margin-top: 30px;}
        .dev-tag { font-size: 10px; color: #fff; font-weight: 200; letter-spacing: 3px; text-align: center; opacity: 0.6; margin-bottom: 25px;}
        .widgets { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 20px; }
        .w-card { background: var(--card); padding: 12px 5px; border-radius: 18px; border: 1px solid var(--border); text-align: center; font-size: 14px; }
        .search-box { position: relative; margin-bottom: 20px; }
        .search-input { width: 100%; padding: 16px 22px; border-radius: 50px; border: 1px solid var(--border); background: var(--card); color: #fff; font-size: 16px; outline: none; box-sizing: border-box; }
        .search-input:focus { border-color: var(--primary); }
        .suggestions { position: absolute; top: 100%; left: 15px; right: 15px; background: #111; border: 1px solid var(--border); border-radius: 0 0 20px 20px; z-index: 1000; display: none; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
        .s-item { padding: 12px 20px; cursor: pointer; border-bottom: 1px solid #1a1a1a; font-size: 15px; }
        .s-item:hover { background: #1a1a1a; color: var(--primary); }
        .smart-widget { background: var(--card); border: 1px solid var(--primary); border-radius: 20px; padding: 20px; margin: 20px 0; }
        .res-item { margin-top: 30px; border-bottom: 1px solid #1a1a1a; padding-bottom: 20px; }
        .res-title { color: #8ab4f8; font-size: 18px; text-decoration: none; display: block; word-wrap: break-word; }
        .rating-box { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
        .rating-bar { height: 3px; background: #222; border-radius: 2px; flex-grow: 1; overflow: hidden; }
        .rating-fill { height: 100%; background: var(--primary); }
        .fact-card { background: linear-gradient(145deg, #111, #0a0a0a); border: 1px dashed #333; padding: 20px; border-radius: 20px; margin-top: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <center>
            <a href="/" class="logo">Searcli</a>
            <div class="dev-tag">developer by Labretto</div>
        </center>

        <div class="widgets">
            <div class="w-card"><b>{{ w.temp }}°C</b><br><small style="color:#888; font-size:9px;">{{ w.city }}</small></div>
            <div class="w-card"><b>{{ w.usd }}₽</b><br><small style="color:#888; font-size:9px;">USD</small></div>
            <div class="w-card"><b>{{ w.idx }}</b><br><small style="color:#888; font-size:9px;">ИНДЕКС</small></div>
        </div>

        <div class="search-box">
            <form action="/search"><input id="q" name="q" class="search-input" placeholder="Поиск" value="{{ q }}" required autocomplete="off"></form>
            <div id="s-box" class="suggestions"></div>
        </div>

        {% if not q %}
        <div class="fact-card">
            <span style="font-size:10px; color:var(--primary); letter-spacing:2px; font-weight:bold;">ИНТЕРЕСНЫЙ ФАКТ</span>
            <p style="font-size:14px; color:#ccc; line-height:1.5; margin-top:10px;">{{ fact }}</p>
        </div>
        {% endif %}

        {% if smart_html %}{{ smart_html|safe }}{% endif %}

        {% for r in results %}
        <div class="res-item">
            <small style="color:#888; font-size:11px; display:block; margin-bottom:4px;">{{ r.url[:50] }}...</small>
            <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
            <div style="color:#aaa; font-size:14px; margin-top:6px;">{{ r.snippet }}...</div>
            <div class="rating-box"><div class="rating-bar"><div class="rating-fill" style="width:{{ [r.score/6, 100]|min }}%"></div></div></div>
        </div>
        {% endfor %}
    </div>

    <script>
        const qI = document.getElementById('q'), sB = document.getElementById('s-box');
        qI.oninput = async () => {
            if (qI.value.length < 2) { sB.style.display = 'none'; return; }
            const r = await fetch(`/suggest?p=${encodeURIComponent(qI.value)}`), data = await r.json();
            if (data.length) {
                sB.innerHTML = data.map(w => `<div class="s-item">${w}</div>`).join('');
                sB.style.display = 'block';
                document.querySelectorAll('.s-item').forEach(el => {
                    el.onclick = () => { qI.value = el.innerText; qI.closest('form').submit(); };
                });
            } else sB.style.display = 'none';
        };
        document.onclick = (e) => { if (e.target !== qI) sB.style.display = 'none'; };
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, q="", results=[], w=get_widgets_data(), smart_html="", fact=random.choice(FACTS))

@app.route('/suggest')
def suggest():
    return jsonify(db.get_suggestions(request.args.get('p', '')))

@app.route('/search')
def search():
    q = request.args.get('q', '').lower()
    w = get_widgets_data()
    res = db.search_text(q)
    smart = ""
    if "погода" in q:
        smart = f'<div class="smart-widget"><small style="color:var(--primary)">ПОГОДА</small><div style="font-size:35px; font-weight:bold;">{w["temp"]}°C</div><div>{w["city"]}</div></div>'
    elif any(x in q for x in ["курс", "доллар"]):
        smart = f'<div class="smart-widget"><small style="color:var(--primary)">КУРС</small><div style="font-size:25px; font-weight:bold;">{w["usd"]} ₽ за 1$</div></div>'
    return render_template_string(HTML, q=q, results=res, w=w, smart_html=smart, fact="")

if __name__ == '__main__':
    threading.Thread(target=lambda: asyncio.run(crawler_task()), daemon=True).start()
    app.run(host='0.0.0.0', port=10000)
