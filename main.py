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
STOP_WORDS = {"как", "что", "такое", "где", "это", "для", "под", "над", "в", "на", "и", "или", "быть", "с", "по", "ли"}

class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, views INTEGER, content TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS words (word TEXT, doc_id INTEGER, count INTEGER)')
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, img_url TEXT UNIQUE, page_url TEXT, alt TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_w ON words(word)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_doc ON words(doc_id)')
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
        except: self.conn.rollback()

    def get_suggestions(self, prefix):
        if len(prefix) < 2: return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT word FROM words WHERE word LIKE ? LIMIT 5", (prefix.lower() + '%',))
        return [r[0] for r in cursor.fetchall()]

    def search_text(self, query):
        q_low = query.lower().strip()
        q_words = [w for w in re.findall(r'[a-zа-яё0-9]{3,}', q_low) if w not in STOP_WORDS]
        if not q_words: return []
        cursor = self.conn.cursor()
        res = {}
        for word in q_words:
            cursor.execute('''
                SELECT d.id, d.url, d.title, i.count, d.views, d.content 
                FROM words i JOIN docs d ON i.doc_id = d.id WHERE i.word = ?''', (word,))
            for d_id, url, title, tf, v, content in cursor.fetchall():
                t_low, u_low, c_low = (title or "").lower(), url.lower(), (content or "").lower()
                score = (tf / (len(c_low)/1000 + 1)) * math.log(v + 1)
                if word in t_low: score *= 20.0
                if q_low in t_low: score *= 100.0
                if q_low in u_low: score *= 50.0
                if d_id not in res: res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': c_low[:160]}
                else: res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)[:25]

    def search_img(self, query):
        cursor = self.conn.cursor()
        cursor.execute("SELECT img_url, alt FROM images WHERE alt LIKE ? LIMIT 40", ('%' + query + '%',))
        return cursor.fetchall()

db = DatabaseManager()

def get_widgets_data():
    data = {"usd": "89.20", "temp": "0", "idx": "0"}
    try:
        r1 = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=1).json()
        data["usd"] = f"{r1['Valute']['USD']['Value']:.2f}"
        r2 = requests.get("https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.61&current_weather=true", timeout=1).json()
        data["temp"] = f"{int(round(r2['current_weather']['temperature']))}"
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

def generate_smart_widget(query, results, w_data):
    q = query.lower()
    if "время" in q:
        return f'<div class="smart-card"><div class="smart-label">ВРЕМЯ</div><div class="smart-val">{datetime.now().strftime("%H:%M")}</div><div class="smart-sub">Москва, Россия</div></div>'
    if any(x in q for x in ["курс", "доллар", "валюта"]):
        return f'<div class="smart-card"><div class="smart-label">КУРС ЦБ</div><div class="smart-val">$ {w_data["usd"]} ₽</div><div class="smart-sub">Обновлено сегодня</div></div>'
    if results and "wikipedia.org" in results[0]['url']:
        r = results[0]
        return f'<div class="smart-card" style="border-left:4px solid var(--primary)"><div class="smart-label">WIKIPEDIA</div><div style="font-size:20px;font-weight:bold;margin:10px 0">{r["title"]}</div><p style="font-size:14px;color:#ccc">{r["snippet"]}...</p><a href="{r["url"]}" class="smart-btn">Читать статью</a></div>'
    return ""

async def crawler():
    seeds = ["https://ru.wikipedia.org/wiki/Список_самых_посещаемых_веб-сайтов", "https://top100.rambler.ru/", "https://arxiv.org/"]
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
                    for s in soup(["script", "style"]): s.decompose()
                    t, txt = (soup.title.string or url).strip(), soup.get_text(separator=' ')
                    imgs = [(urljoin(url, i['src']), i.get('alt','')) for i in soup.find_all('img', src=True) if len(i.get('alt','')) > 5][:8]
                    db.add_all(url, t, Counter(re.findall(r'[a-zа-яё0-9]{3,}', txt.lower())), txt, imgs)
                    for a in soup.find_all('a', href=True):
                        l = urljoin(url, a['href'])
                        if urlparse(l).netloc and l not in visited:
                            if urlparse(l).netloc != urlparse(url).netloc: queue.insert(0, l)
                            else: queue.append(l)
                        if len(queue) > 100: break
            except: continue
            await asyncio.sleep(1.5)

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Searcli</title>
    <style>
        :root { --bg: #0f0f0f; --text: #f1f1f1; --primary: #bb86fc; --border: #2a2a2a; --sub: #a0a0a0; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin: 0; }
        .container { max-width: 750px; margin: 0 auto; padding: 20px; min-height: 100vh; display: flex; flex-direction: column; }
        .logo { font-size: 60px; font-weight: 700; color: var(--primary); text-decoration: none; letter-spacing: -2px; }
        .search-box { width: 100%; position: relative; margin: 20px 0; }
        .search-input { width: 100%; padding: 16px 25px; border-radius: 30px; border: 1px solid var(--border); background: #1a1a1a; color: #fff; font-size: 17px; outline: none; box-sizing: border-box; transition: 0.3s; }
        .search-input:focus { border-color: var(--primary); box-shadow: 0 0 15px rgba(187,134,252,0.1); }
        .suggestions { position: absolute; top: 100%; left: 15px; right: 15px; background: #1a1a1a; border: 1px solid var(--border); border-top: none; border-radius: 0 0 20px 20px; z-index: 100; display: none; }
        .s-item { padding: 12px 20px; cursor: pointer; }
        .s-item:hover { background: #252525; color: var(--primary); }
        .smart-card { background: #1a1a1a; border: 1px solid var(--border); border-radius: 24px; padding: 25px; margin-bottom: 30px; animation: slideUp 0.4s ease; }
        .smart-label { font-size: 11px; color: var(--primary); letter-spacing: 2px; font-weight: bold; }
        .smart-val { font-size: 42px; font-weight: bold; margin: 10px 0; }
        .smart-sub { color: var(--sub); font-size: 13px; }
        .smart-btn { display: inline-block; margin-top: 15px; padding: 10px 20px; background: var(--primary); color: #000; border-radius: 12px; text-decoration: none; font-weight: bold; font-size: 13px; }
        .res-item { margin-bottom: 35px; }
        .res-link { color: var(--sub); font-size: 12px; text-decoration: none; display: block; margin-bottom: 4px; }
        .res-title { color: #8ab4f8; font-size: 20px; text-decoration: none; }
        .res-title:hover { text-decoration: underline; }
        .rating { height: 4px; background: #333; border-radius: 2px; width: 80px; margin-top: 8px; overflow: hidden; }
        .widgets { display: flex; gap: 10px; margin: 20px 0; }
        .w-card { background: #1a1a1a; padding: 15px; border-radius: 18px; border: 1px solid var(--border); flex: 1; text-align: center; }
        @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <center style="margin-top:40px">
            <a href="/" class="logo">Searcli</a>
            <div style="font-size: 12px; opacity: 0.5; letter-spacing: 1px;">BY LABRETTO</div>
        </center>

        {% if not q %}
        <div class="widgets">
            <div class="w-card"><div style="font-size:18px">{{ w.temp }}°C</div><div style="font-size:9px;color:var(--sub)">МОСКВА</div></div>
            <div class="w-card"><div style="font-size:18px">{{ w.usd }}₽</div><div style="font-size:9px;color:var(--sub)">USD</div></div>
            <div class="w-card"><div style="font-size:18px">{{ w.idx }}</div><div style="font-size:9px;color:var(--sub)">СТРАНИЦ</div></div>
        </div>
        {% endif %}

        <div class="search-box">
            <form action="/search"><input id="q" name="q" class="search-input" placeholder="Поиск в интернете..." autocomplete="off" value="{{ q }}" required></form>
            <div id="s-box" class="suggestions"></div>
        </div>

        <div style="width:100%">
            {{ smart|safe }}
            {% if t == 'img' %}
                <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(160px,1fr));gap:15px">
                    {% for i in results %}<div style="height:160px;background:#222;border-radius:15px;overflow:hidden"><img src="{{ i[0] }}" style="width:100%;height:100%;object-fit:cover" loading="lazy"></div>{% endfor %}
                </div>
            {% else %}
                {% for r in results %}
                    <div class="res-item">
                        <a href="{{ r.url }}" class="res-link">{{ r.url[:60] }}</a>
                        <a href="{{ r.url }}" class="res-title">{{ r.title }}</a>
                        <div style="color:var(--sub);font-size:14px;margin-top:5px">{{ r.snippet }}...</div>
                        <div class="rating"><div style="width:{{ [r.score*2, 100]|min }}%;height:100%;background:var(--primary)"></div></div>
                    </div>
                {% endfor %}
            {% endif %}
        </div>
        <div style="margin: auto 0 20px; text-align: center; font-size: 10px; color: var(--sub); letter-spacing: 4px;">SEARCLI 1.0</div>
    </div>
    <script>
        const inp = document.getElementById('q'), box = document.getElementById('s-box');
        inp.oninput = async () => {
            if (inp.value.length < 2) { box.style.display = 'none'; return; }
            const r = await fetch(`/suggest?p=${encodeURIComponent(inp.value)}`), words = await r.json();
            if (words.length) {
                box.innerHTML = words.map(w => `<div class="s-item">${w}</div>`).join('');
                box.style.display = 'block';
                document.querySelectorAll('.s-item').forEach(i => i.onclick = () => { inp.value = i.innerText; inp.closest('form').submit(); });
            } else box.style.display = 'none';
        };
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, q="", t="text", results=[], w=get_widgets_data(), smart="")

@app.route('/suggest')
def suggest():
    return jsonify(db.get_suggestions(request.args.get('p', '')))

@app.route('/search')
def search():
    q, t = request.args.get('q', ''), request.args.get('t', 'text')
    w_data = get_widgets_data()
    res = db.search_img(q) if t == 'img' else db.search_text(q)
    smart = generate_smart_widget(q, res, w_data) if t != 'img' else ""
    return render_template_string(HTML, q=q, t=t, results=res, w=w_data, smart=smart)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: asyncio.run(crawler()), daemon=True).start()
    app.run(host='0.0.0.0', port=port)
