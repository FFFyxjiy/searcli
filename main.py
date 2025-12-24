import asyncio, aiohttp, math, re, sqlite3, random, threading, requests
from flask import Flask, render_template_string, request
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter

app = Flask(__name__)

# --- CONFIG ---
TARGET_PAGES = 15000
DB_NAME = "searcli_final.db"
STOP_WORDS = {"как", "что", "такое", "где", "это", "для", "под", "над", "в", "на", "и", "или", "быть", "с", "по", "ли"}


class DatabaseManager:
    def __init__(self, db_path=DB_NAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute(
            'CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, views INTEGER, content TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS words (word TEXT, doc_id INTEGER, count INTEGER)')
        cursor.execute(
            'CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, img_url TEXT UNIQUE, page_url TEXT, alt TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_w ON words(word)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_img_alt ON images(alt)')
        self.conn.commit()

    def add_all(self, url, title, words, text, images):
        cursor = self.conn.cursor()
        try:
            cursor.execute("INSERT OR IGNORE INTO docs (url, title, views, content) VALUES (?, ?, ?, ?)",
                           (url, title, random.randint(1000, 100000), text))
            row = cursor.execute("SELECT id FROM docs WHERE url=?", (url,)).fetchone()
            if row:
                doc_id = row[0]
                cursor.executemany("INSERT INTO words VALUES (?, ?, ?)", [(w, doc_id, c) for w, c in words.items()])
                for img_url, alt in images:
                    if img_url and len(img_url) < 500:  # Защита от слишком длинных data-urls
                        cursor.execute("INSERT OR IGNORE INTO images (img_url, page_url, alt) VALUES (?, ?, ?)",
                                       (img_url, url, alt))
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def search_text(self, query):
        q_words = [w for w in re.findall(r'[a-zа-яё0-9]{3,}', query.lower()) if w not in STOP_WORDS]
        if not q_words: return []
        cursor = self.conn.cursor()
        res = {}
        for word in q_words:
            cursor.execute('SELECT d.id, d.url, d.title, i.count, d.views, d.content FROM words i JOIN docs d ON i.doc_id = d.id WHERE i.word = ?', (word,))
            for d_id, url, title, tf, v, content in cursor.fetchall():
                # УЛУЧШЕННЫЙ АЛГОРИТМ:
                # Огромный бонус (x10) если слово в заголовке
                title_bonus = 10.0 if word in (title or "").lower() else 1.0
                # Бонус за популярность (просмотры) и плотность слова
                score = (math.log(tf + 1) * 2 + math.log(v + 1)) * title_bonus
                
                if d_id not in res: 
                    res[d_id] = {'url': url, 'title': title or url, 'score': score, 'snippet': (content or "")[:160]}
                else: 
                    res[d_id]['score'] += score
        
        # Сортируем: сначала самые релевантные
        return sorted(res.values(), key=lambda x: x['score'], reverse=True)

    def search_img(self, query):
        if not query: return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT img_url, alt FROM images WHERE alt LIKE ? LIMIT 50", ('%' + query + '%',))
        return cursor.fetchall()


db = DatabaseManager()


def get_widgets():
    data = {"usd": "00.00", "temp": "0"}
    try:
        # Запросы с коротким таймаутом, чтобы не тормозить загрузку страницы
        r1 = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=1.5).json()
        data["usd"] = f"{r1['Valute']['USD']['Value']:.2f}"
        r2 = requests.get("https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.61&current_weather=true",
                          timeout=1.5).json()
        data["temp"] = f"{int(round(r2['current_weather']['temperature']))}°C"
    except Exception:
        pass  # Если API не ответило, просто выведем значения по умолчанию
    return data


async def crawler():
    seeds = ["https://habr.com/ru/", "https://www.rbc.ru/", "https://ru.wikipedia.org/wiki/Заглавная_страница",
             "https://unsplash.com/"]
    q, visited = list(seeds), set()
    async with aiohttp.ClientSession(headers={'User-Agent': 'SearcliBot/2.0 (by Labretto)'}) as session:
        while q and len(visited) < TARGET_PAGES:
            url = q.pop(0)
            if url in visited: continue
            try:
                async with session.get(url, timeout=7) as r:
                    if r.status != 200: continue
                    visited.add(url)
                    html_text = await r.text(errors='ignore')
                    soup = BeautifulSoup(html_text, 'html.parser')

                    # Извлечение картинок с базовой фильтрацией мусора
                    imgs = []
                    for i in soup.find_all('img', src=True):
                        src = urljoin(url, i['src'])
                        alt_t = i.get('alt', '').strip()
                        if len(alt_t) > 3 and src.startswith('http'):
                            imgs.append((src, alt_t))

                    text_content = soup.get_text()
                    title = (soup.title.string or url).strip()
                    word_counts = Counter(re.findall(r'[a-zа-яё0-9]{3,}', text_content.lower()))

                    db.add_all(url, title, word_counts, text_content, imgs)

                    # Лимит на очередь, чтобы не переполнять память
                    if len(q) < 5000:
                        for a in soup.find_all('a', href=True):
                            l = urljoin(url, a['href'])
                            if urlparse(l).netloc and l not in visited:
                                q.append(l)
            except Exception:
                continue
            await asyncio.sleep(0.2)  # Вежливый интервал


# --- UI HTML (ДИЗАЙН НЕ ТРОНУТ) ---
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Searcli</title>
    <style>
        :root { 
            --bg: #ffffff; --text: #202124; --primary: #8e44ad; --border: #dfe1e5; 
            --sub: #70757a; --dev-text: #666; --accent: #27ae60;
        }
        @media (prefers-color-scheme: dark) {
            :root { 
                --bg: #121212; --text: #e8eaed; --primary: #bb86fc; --border: #333; 
                --sub: #9aa0a6; --dev-text: #fff;
            }
        }

        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; overflow-x: hidden; }
        
        .container { 
            width: 100%; max-width: 800px; margin: 0 auto; padding: 15px; 
            box-sizing: border-box; display: flex; flex-direction: column; align-items: center; 
        }

        /* Logo Section */
        .logo-box { text-align: center; margin: 40px 0 20px; width: 100%; }
        .logo { font-size: clamp(48px, 15vw, 72px); font-weight: 500; color: var(--primary); text-decoration: none; display: block; }
        .developer { font-size: 14px; font-weight: 300; margin-top: 5px; opacity: 0.8; }

        /* Widgets Grid - Улучшенная адаптивность */
        .widgets { 
            display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; 
            margin-bottom: 25px; width: 100%; max-width: 500px; 
        }
        .widget { 
            background: var(--card, var(--border)); padding: 12px; 
            border-radius: 15px; text-align: center; opacity: 0.9;
        }
        .widget-val { font-size: 18px; font-weight: bold; }
        .widget-label { font-size: 10px; text-transform: uppercase; margin-top: 4px; }

        /* Search Form */
        .search-form { width: 100%; margin-bottom: 20px; }
        .search-input { 
            width: 100%; padding: 16px 24px; border-radius: 30px; 
            border: 1px solid var(--border); background: var(--bg); 
            color: var(--text); font-size: 16px; outline: none; 
            box-sizing: border-box; -webkit-appearance: none; /* Убирает тени на iOS */
        }

        /* Tabs */
        .tabs { display: flex; gap: 25px; margin-bottom: 20px; border-bottom: 1px solid var(--border); width: 100%; justify-content: center; }
        .tab { text-decoration: none; color: var(--sub); font-size: 15px; padding: 10px 5px; position: relative; }
        .tab.active { color: var(--primary); font-weight: bold; }
        .tab.active::after { content: ''; position: absolute; bottom: -1px; left: 0; width: 100%; height: 2px; background: var(--primary); }

        /* Results */
        .res-item { width: 100%; margin-bottom: 25px; word-wrap: break-word; }
        .res-title { font-size: 18px; color: var(--primary); text-decoration: none; line-height: 1.3; }
        .snippet { font-size: 14px; color: var(--sub); margin-top: 6px; line-height: 1.5; }
        .rating-box { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 12px; }
        .rating-bar { flex: 0 0 80px; height: 5px; background: #ddd; border-radius: 3px; overflow: hidden; }

        /* Images Grid - Резина */
        .img-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); 
            gap: 8px; width: 100%; 
        }
        .img-card { aspect-ratio: 1/1; border-radius: 10px; overflow: hidden; background: #333; }
        .img-card img { width: 100%; height: 100%; object-fit: cover; }

        /* Mobile Optimization */
        @media (max-width: 480px) {
            .container { padding: 10px; }
            .logo-box { margin-top: 20px; }
            .res-title { font-size: 17px; }
            .widgets { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo-box">
            <a href="/" class="logo">Searcli</a>
            <div class="developer" style="color: var(--dev-text); font-size: 14px; font-weight: 300; margin-top: 5px;">developer by Labretto</div>
        </div>
        {% if not q %}<div class="widgets">
            <div class="widget"><div style="font-weight:bold">{{ w.temp }}</div><div style="font-size:10px">ПОГОДА</div></div>
            <div class="widget"><div style="font-weight:bold">{{ w.usd }} ₽</div><div style="font-size:10px">КУРС USD</div></div>
        </div>{% endif %}
        <form action="/search" style="width:100%; text-align:center;">
            <input name="q" class="search-input" placeholder="Поиск..." value="{{ q }}" required>
            <input type="hidden" name="t" value="{{ t }}">
        </form>
        <div class="tabs">
            <a href="/search?q={{q}}&t=text" class="tab {% if t!='img' %}active{% endif %}">Все</a>
            <a href="/search?q={{q}}&t=img" class="tab {% if t=='img' %}active{% endif %}">Картинки</a>
        </div>
        {% if t == 'img' %}<div class="img-grid">
            {% for i in results %}<div class="img-card"><a href="{{ i[0] }}" target="_blank"><img src="{{ i[0] }}" title="{{ i[1] }}" loading="lazy"></a></div>{% endfor %}
        </div>{% else %}<div style="width:100%">
            {% for r in results %}<div class="res-item">
                <a href="{{ r.url }}" class="res-title" target="_blank">{{ r.title }}</a>
                <div style="font-size:14px; color:var(--sub)">{{ r.snippet }}...</div>
                <div style="display:flex; align-items:center; gap:10px; font-size:11px; color:var(--sub); margin-top:5px;">
                    <div class="rating-bar"><div style="width:{{ [r.score*4, 100]|min }}%; height:100%; background:#27ae60"></div></div>
                    Рейтинг: {{ "%.1f"|format(r.score) }}
                </div>
            </div>{% endfor %}
        </div>{% endif %}
    </div>
<div style="text-align: center; margin-top: 50px; padding-bottom: 20px; font-size: 10px; font-weight: 300; color: var(--sub); letter-spacing: 2px; opacity: 0.6;">
        Searcli 1.0
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
    # Запуск краулера в отдельном демоническом потоке
    threading.Thread(target=lambda: asyncio.run(crawler()), daemon=True).start()
    # Запуск Flask. host='0.0.0.0' делает его доступным в локальной сети
    app.run(host='0.0.0.0', port=5000, debug=False)
