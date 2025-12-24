import asyncio, aiohttp, math, re, sqlite3, random, threading, requests, os
from flask import Flask, render_template_string, request, jsonify
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION ---
TARGET_PAGES = 10000
DB_NAME = "searcli_final_v1.db"
# Стоп-слова для очистки запроса
STOP_WORDS = {"как", "что", "такое", "где", "это", "для", "под", "над", "в", "на", "и", "или", "быть", "с", "по", "ли"}

# --- DATABASE MANAGER ---
class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # Режим WAL позволяет одновременно читать (поиск) и писать (краулер)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.execute('PRAGMA cache_size=-2000')
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, views INTEGER, content TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS words (word TEXT, doc_id INTEGER, count INTEGER)')
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, img_url TEXT UNIQUE, page_url TEXT, alt TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_word ON words(word)')
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
                
                # Алгоритм ранжирования Labretto Rank
                score = (tf / (len(c_low)/1000 + 1)) * math.log(v + 1)
                if word in t_low: score *= 20.0
                if q_low in t_low: score *= 100.0
                if q_low in u_low: score *= 50.0 # Навигационный бонус (поиск сайтов по URL)
                
                if d_id not in res:
                    res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': c_low[:180]}
                else:
                    res[d_id]['score'] += score
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)[:25]

    def search_img(self, query):
        cursor = self.conn.cursor()
        cursor.execute("SELECT img_url, alt FROM images WHERE alt LIKE ? LIMIT 40", ('%' + query + '%',))
        return cursor.fetchall()

db = DatabaseManager()

# --- UTILS & WIDGETS ---
def get_widgets_data():
    data = {"usd": "89.20", "eur": "96.40", "temp": "0", "city": "Москва", "idx": "0"}
    try:
        # Геолокация
        geo = requests.get("http://ip-api.com/json/", timeout=1).json()
        if geo.get('status') == 'success':
            data["city"] = geo['city']
            lat, lon = geo['lat'], geo['lon']
        else: lat, lon = 55.75, 37.61
        
        # Погода
        r_w = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=1).json()
        data["temp"] = f"{int(round(r_w['current_weather']['temperature']))}"
        
        # Валюта
        r_c = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=1).json()
        data["usd"] = f"{r_c['Valute']['USD']['Value']:.2f}"
        data["eur"] = f"{r_c['Valute']['EUR']['Value']:.2f}"
        
        # Индекс базы
        c = db.conn.cursor()
        data["idx"] = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    except: pass
    return data

def generate_smart_widget(query, results, w):
    q = query.lower()
    # Виджет времени
    if "время" in q:
        return f'<div class="smart-card"><div class="smart-label">ВРЕМЯ</div><div class="smart-val">{datetime.now().strftime("%H:%M")}</div><div class="smart-sub">{w["city"]}, Россия</div></div>'
    
    # Виджет погоды
    if "погода" in q:
        return f'<div class="smart-card"><div class="smart-label">ПОГОДА</div><div class="smart-val">{w["temp"]}°C</div><div class="smart-sub">{w["city"]} • Обновлено только что</div></div>'

    # Виджет валют
    if any(x in q for x in ["курс", "доллар", "евро", "валюта"]):
        return f'<div class="smart-card"><div class="smart-label">ФИНАНСЫ</div><div style="display:flex;gap:30px;margin-top:10px;"><div><div style="font-size:24px;font-weight:bold;">{w["usd"]} ₽</div><div class="smart-sub">USD/RUB</div></div><div><div style="font-size:24px;font-weight:bold;">{w["eur"]} ₽</div><div class="smart-sub">EUR/RUB</div></div></div></div>'

    # Виджет личности (Википедия)
    if results and "wikipedia.org" in results[0]['url']:
        r = results[0]
        return f'<div class="smart-card" style="border-left:4px solid var(--primary)"><div class="smart-label">ЭНЦИКЛОПЕДИЯ</div><div style="font-size:22px;font-weight:bold;margin:10px 0">{r["title"].split(" — ")[0]}</div><p style="font-size:14px;color:#ccc;line-height:1.6">{r["snippet"]}...</p><a href="{r["url"]}" target="_blank" class="smart-btn">Читать статью</a></div>'
    
    return ""

# --- CRAWLER ---
async def crawler():
    seeds = [
        "https://ru.wikipedia.org/wiki/Список_самых_посещаемых_веб-сайтов",
        "https://top100.rambler.ru/",
        "https://habr.com/ru/all/",
        "https://en.wikipedia.org/wiki/Main_Page"
    ]
    queue, visited = list(seeds), set()
    random.shuffle(queue)
    
    async with aiohttp.ClientSession(headers={'User-Agent': 'SearcliBot/1.0'}) as session:
        while queue and len(visited) < TARGET_PAGES:
            url = queue.pop(0)
            if url in visited or not url.startswith('http'): continue
            try:
                async with session.get(url, timeout=6) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    soup = BeautifulSoup(await r.text(errors='ignore'), 'html.parser')
                    for s in soup(["script", "style", "nav", "footer"]): s.decompose()
                    
                    title = (soup.title.string or url).strip()
                    text = soup.get_text(separator=' ')
                    
                    imgs = []
                    for i in soup.find_all('img', src=True):
                        src = urljoin(url, i['src'])
                        alt = i.get('alt', '').strip()
                        if len(alt) > 5 and src.startswith('http'):
                            imgs.append((src, alt))
                            if len(imgs) >= 6: break

                    db.add_all(url, title, Counter(re.findall(r'[a-zа-яё0-9]{3,}', text.lower())), text, imgs)
                    
                    # Сбор новых ссылок (Приоритет на внешние сайты для масштаба)
                    links_added = 0
                    for a in soup.find_all('a', href=True):
                        link = urljoin(url, a['href'])
                        if urlparse(link).netloc and link not in visited:
                            if urlparse(link).netloc != urlparse(url).netloc:
                                queue.insert(0, link)
                            else:
                                queue.append(link)
                            links_added += 1
                        if links_added > 10: break
            except: continue
            await asyncio.sleep(1.2)

# --- UI TEMPLATE ---
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Searcli</title>
    <style>
        :root { --bg: #0f0f0f; --text: #f1f1f1; --primary: #bb86fc; --border: #2a2a2a; --sub: #a0a0a0; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin: 0; line-height: 1.6; }
        .container { max-width: 750px; margin: 0 auto; padding: 20px; display: flex; flex-direction: column; min-height: 100vh; }
        .logo { font-size: 65px; font-weight: 800; color: var(--primary); text-decoration: none; letter-spacing: -3px; }
        .search-box { position: relative; width: 100%; margin: 25px 0; }
        .search-input { width: 100%; padding: 18px 28px; border-radius: 50px; border: 1px solid var(--border); background: #1a1a1a; color: #fff; font-size: 18px; outline: none; box-sizing: border-box; transition: 0.3s; }
        .search-input:focus { border-color: var(--primary); box-shadow: 0 0 20px rgba(187,134,252,0.15); }
        .suggestions { position: absolute; top: 100%; left: 20px; right: 20px; background: #1a1a1a; border: 1px solid var(--border); border-top: none; border-radius: 0 0 25px 25px; z-index: 100; display: none; }
        .s-item { padding: 12px 25px; cursor: pointer; border-bottom: 1px solid #222; }
        .s-item:hover { background: #222; color: var(--primary); }
        .smart-card { background: #1a1a1a; border: 1px solid var(--border); border-radius: 28px; padding: 30px; margin-bottom: 30px; animation: fadeInUp 0.5s ease; }
        .smart-label { font-size: 10px; color: var(--primary); letter-spacing: 3px; font-weight: 900; }
        .smart-val { font-size: 48px; font-weight: 800; margin: 10px 0; }
        .smart-sub { color: var(--sub); font-size: 13px; }
        .smart-btn { display: inline-block; margin-top: 15px; padding: 12px 25px; background: var(--primary); color: #000; border-radius: 14px; text-decoration: none; font-weight: bold; font-size: 14px; }
        .res-item { margin-bottom: 40px; }
        .res-link { color: var(--sub); font-size: 13px; text-decoration: none; display: block; margin-bottom: 5px; }
        .res-title { color: #8ab4f8; font-size: 22px; text-decoration: none; font-weight: 500; }
        .res-title:hover { text-decoration: underline; }
        .rating-bar { height: 4px; background: #222; border-radius: 2px; width: 100px; margin-top: 10px; overflow: hidden; }
        .widgets { display: flex; gap: 12px; margin-bottom: 30px; }
        .w-card { background: #1a1a1a; padding: 18px; border-radius: 20px; border: 1px solid var(--border); flex: 1; text-align: center; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <center style="margin-top:50px">
            <a href="/" class="logo">Searcli</a>
            <div style="font-size:11px; color:var(--primary); letter-spacing:5px; margin-top:-10px; font-weight:bold;">LABRETTO</div>
        </center>

        {% if not q %}
        <div class="widgets">
            <div class="w-card"><div style="font-size:20px;font-weight:bold;">{{ w.temp }}°C</div><div style="font-size:9px;color:var(--sub);letter-spacing:1px;">{{ w.city|upper }}</div></div>
            <div class="w-card"><div style="font-size:20px;font-weight:bold;">{{ w.usd }}₽</div><div style="font-size:9px;color:var(--sub);letter-spacing:1px;">USD/RUB</div></div>
            <div class="w-card"><div style="font-size:20px;font-weight:bold;">{{ w.idx }}</div><div style="font-size:9px;color:var(--sub);letter-spacing:1px;">СТРАНИЦ</div></div>
        </div>
        {% endif %}

        <div class="search-box">
            <form action="/search"><input id="q" name="q" class="search-input" placeholder="Введите запрос..." autocomplete="off" value="{{ q }}" required></form>
            <div id="s-box" class="suggestions"></div>
        </div>

        <div style="width:100%">
            {{ smart|safe }}
            {% if results %}
                {% for r in results %}
                <div class="res-item">
                    <a href="{{ r.url }}" class="res-link" target="_blank">{{ r.url[:70] }}...</a>
                    <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
                    <div style="color:var(--sub);font-size:15px;margin-top:6px;">{{ r.snippet }}...</div>
                    <div class="rating-bar"><div style="width:{{ [r.score*1.5, 100]|min }}%;height:100%;background:var(--primary)"></div></div>
                </div>
                {% endfor %}
            {% elif q %}
                <center style="margin-top:100px; color:var(--sub);">По вашему запросу ничего не найдено.</center>
            {% endif %}
        </div>
        <div style="margin: auto 0 30px; text-align: center; font-size: 11px; color: var(--sub); letter-spacing: 5px; font-weight: bold;">LABRETTO SEARCLI 1.0</div>
    </div>
    <script>
        const qI = document.getElementById('q'), sB = document.getElementById('s-box');
        qI.oninput = async () => {
            if (qI.value.length < 2) { sB.style.display = 'none'; return; }
            const r = await fetch(`/suggest?p=${encodeURIComponent(qI.value)}`), data = await r.json();
            if (data.length) {
                sB.innerHTML = data.map(w => `<div class="s-item">${w}</div>`).join('');
                sB.style.display = 'block';
                document.querySelectorAll('.s-item').forEach(el => el.onclick = () => { qI.value = el.innerText; qI.closest('form').submit(); });
            } else sB.style.display = 'none';
        };
        document.onclick = (e) => { if(e.target !== qI) sB.style.display = 'none'; };
    </script>
</body>
</html>
"""

# --- ROUTES ---
@app.route('/')
def home():
    return render_template_string(HTML, q="", results=[], w=get_widgets_data(), smart="")

@app.route('/suggest')
def suggest():
    return jsonify(db.get_suggestions(request.args.get('p', '')))

@app.route('/search')
def search():
    q = request.args.get('q', '')
    w_data = get_widgets_data()
    res = db.search_text(q)
    smart = generate_smart_widget(q, res, w_data)
    return render_template_string(HTML, q=q, results=res, w=w_data, smart=smart)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    # Запуск краулера в отдельном потоке
    threading.Thread(target=lambda: asyncio.run(crawler()), daemon=True).start()
    app.run(host='0.0.0.0', port=port)
