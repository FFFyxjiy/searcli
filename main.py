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

class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL') # Позволяет искать во время записи
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
                if word in (title or "").lower(): score += 200 # Бонус за заголовок
                if d_id not in res: res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': content[:200]}
                else: res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)[:30]

db = DatabaseManager()

# --- СТАБИЛЬНЫЕ ДАННЫЕ ---
def get_widgets_data():
    data = {"usd": "91.50", "eur": "99.20", "temp": "—", "city": "Москва", "idx": "0"}
    try:
        # 1. Город и погода (ipwho.is + open-meteo)
        geo = requests.get("https://ipwho.is/", timeout=4).json()
        if geo.get('success'):
            data["city"] = geo.get('city', 'Москва')
            lat, lon = geo.get('latitude'), geo.get('longitude')
            w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
            data["temp"] = f"{int(round(w_res['current_weather']['temperature']))}"
        
        # 2. Актуальная валюта
        cur = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=3).json()
        data["usd"] = f"{cur['rates']['RUB']:.2f}"
        data["eur"] = f"{cur['rates']['RUB'] / cur['rates']['EUR']:.2f}"
        
        # 3. Индекс базы
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

# --- ГЛОБАЛЬНЫЙ КРАУЛЕР ---
async def crawler_task():
    # Начальные точки для глобального интернета
    seeds = [
        "https://ru.wikipedia.org/wiki/Брин,_Сергей", 
        "https://ria.ru", 
        "https://habr.com/ru/all/",
        "https://www.rbc.ru",
        "https://lenta.ru"
    ]
    queue, visited = list(seeds), set()
    async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0 (SearcliBot/1.0)'}) as session:
        while queue and len(visited) < TARGET_PAGES:
            url = queue.pop(0)
            if url in visited: continue
            try:
                async with session.get(url, timeout=5) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    soup = BeautifulSoup(await r.text(errors='ignore'), 'html.parser')
                    for s in soup(["script", "style", "nav", "footer"]): s.decompose() # Очистка
                    
                    t, txt = (soup.title.string or url).strip(), soup.get_text(separator=' ')
                    db.add_all(url, t, Counter(re.findall(r'[a-zа-яё0-9]+', txt.lower())), txt)
                    
                    # Собираем ссылки на любые другие сайты (Global Web)
                    for a in soup.find_all('a', href=True):
                        link = urljoin(url, a['href'])
                        if urlparse(link).netloc and link not in visited:
                            queue.append(link)
                        if len(queue) > 1500: break # Ограничение очереди для стабильности
            except: continue
            await asyncio.sleep(0.1)

def run_crawler_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(crawler_task())

# --- ИНТЕРФЕЙС LABRETTO ---
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Searcli</title>
    <style>
        :root { --bg: #0a0a0a; --text: #fff; --primary: #bb86fc; --border: #222; --card: #111; }
        body { font-family: 'Inter', -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; }
        .container { max-width: 700px; margin: 0 auto; }
        .logo { font-size: 60px; font-weight: 200; color: #fff; text-decoration: none; letter-spacing: -2px; display: block; text-align: center; margin-top: 50px;}
        .dev-tag { font-size: 11px; color: #fff; font-weight: 200; letter-spacing: 3px; text-align: center; opacity: 0.6; margin-bottom: 35px;}
        .widgets { display: flex; gap: 10px; margin-bottom: 25px; }
        .w-card { background: var(--card); padding: 15px; border-radius: 20px; border: 1px solid var(--border); flex: 1; text-align: center; }
        .search-box { position: relative; margin-bottom: 20px; }
        .search-input { width: 100%; padding: 18px 28px; border-radius: 50px; border: 1px solid var(--border); background: var(--card); color: #fff; font-size: 17px; outline: none; box-sizing: border-box; transition: 0.3s; }
        .search-input:focus { border-color: var(--primary); box-shadow: 0 0 15px rgba(187,134,252,0.1); }
        .smart-widget { background: var(--card); border: 1px solid var(--primary); border-radius: 25px; padding: 25px; margin: 25px 0; animation: slideUp 0.5s ease; }
        .smart-label { font-size: 10px; color: var(--primary); letter-spacing: 2px; font-weight: 900; margin-bottom: 12px; display: block; }
        .res-item { margin-top: 40px; border-bottom: 1px solid #1a1a1a; padding-bottom: 25px; }
        .res-link { color: #888; font-size: 12px; text-decoration: none; display: block; margin-bottom: 5px; }
        .res-title { color: #8ab4f8; font-size: 21px; text-decoration: none; display: block; }
        .res-title:hover { text-decoration: underline; }
        .rating-box { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
        .rating-bar { height: 4px; background: #222; border-radius: 2px; flex-grow: 1; overflow: hidden; }
        .rating-fill { height: 100%; background: var(--primary); }
        @keyframes slideUp { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <center>
            <a href="/" class="logo">Searcli</a>
            <div class="dev-tag">developer by Labretto</div>
        </center>

        <div class="widgets">
            <div class="w-card"><b>{{ w.temp }}°C</b><br><small style="color:#888">{{ w.city }}</small></div>
            <div class="w-card"><b>{{ w.usd }}₽</b><br><small style="color:#888">USD</small></div>
            <div class="w-card"><b>{{ w.idx }}</b><br><small style="color:#888">ИНДЕКС</small></div>
        </div>

        <form action="/search" class="search-box">
            <input name="q" class="search-input" placeholder="Поиск в Labretto..." value="{{ q }}" required autocomplete="off">
        </form>

        {% if smart_html %}{{ smart_html|safe }}{% endif %}

        {% for r in results %}
        <div class="res-item">
            <a href="{{ r.url }}" class="res-link" target="_blank">{{ r.url[:70] }}</a>
            <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
            <div style="color:#aaa; font-size:15px; margin-top:8px; line-height:1.5;">{{ r.snippet }}...</div>
            <div class="rating-box">
                <span style="font-size:10px; color:var(--primary); font-weight:bold;">RANK</span>
                <div class="rating-bar"><div class="rating-fill" style="width:{{ [r.score/8, 100]|min }}%"></div></div>
            </div>
        </div>
        {% endfor %}

        {% if q and not results %}
            <center style="margin-top:50px; color:#555;">По вашему запросу ничего не найдено.</center>
        {% endif %}

        <div style="margin: 80px 0 40px; text-align: center; font-size: 10px; color: #444; letter-spacing: 5px;">LABRETTO SEARCLI 1.0</div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, q="", results=[], w=get_widgets_data(), smart_html="")

@app.route('/search')
def search():
    q = request.args.get('q', '').lower()
    w = get_widgets_data()
    res = db.search_text(q)
    smart_html = ""

    # Смарт-виджеты (Как в Google)
    if "погода" in q:
        smart_html = f'<div class="smart-widget"><span class="smart-label">ПОГОДА СЕЙЧАС</span><div style="font-size:45px; font-weight:bold;">{w["temp"]}°C</div><div style="color:#888">{w["city"]} • Данные обновлены</div></div>'
    elif any(x in q for x in ["курс", "доллар", "евро", "валюта"]):
        smart_html = f'<div class="smart-widget"><span class="smart-label">ФИНАНСОВЫЙ ИНДИКАТОР</span><div style="display:flex; gap:40px;"><div><div style="font-size:32px; font-weight:bold;">{w["usd"]} ₽</div><div style="color:#888">1 USD</div></div><div><div style="font-size:32px; font-weight:bold;">{w["eur"]} ₽</div><div style="color:#888">1 EUR</div></div></div></div>'
    elif res and "wikipedia.org" in res[0]['url']:
        # Если первый результат — википедия, делаем красивый блок
        smart_html = f'<div class="smart-widget"><span class="smart-label">ЭНЦИКЛОПЕДИЧЕСКАЯ СПРАВКА</span><div style="font-size:20px; font-weight:bold; margin-bottom:10px;">{res[0]["title"].split(" — ")[0]}</div><div style="color:#ccc; font-size:15px; line-height:1.6;">{res[0]["snippet"]}...</div><a href="{res[0]["url"]}" target="_blank" style="color:var(--primary); display:block; margin-top:12px; text-decoration:none; font-weight:bold;">→ Читать статью в Wikipedia</a></div>'

    return render_template_string(HTML, q=q, results=res, w=w, smart_html=smart_html)

if __name__ == '__main__':
    # Запуск краулера в фоне
    threading.Thread(target=run_crawler_thread, daemon=True).start()
    # Запуск сервера
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
