"""note記事ドラフトメーカー notedraft（note arbeit2025 専用）

note アカウント「arbeit2025」（技術・個人開発系）の執筆・進捗管理ツール。
外部 AI API は使わず、テンプレートの組み立てで記事案と下書きを作る。
下書きは推敲の土台であり、そのまま公開するものではない。
あわせて、公開済み記事の登録と、スキ数などの数値の手入力による記録・
履歴管理、投稿ペースのダッシュボードで進捗を管理する。note の公式 API は
使わず、数値は自動取得しない（本人が手入力する）。
単一ファイル Flask + SQLite。
"""
import hashlib
import io
import os
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, redirect, render_template_string, request, send_file, url_for, abort

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
DB_PATH = os.environ.get("NOTEDRAFT_DB", os.path.join(os.path.dirname(__file__), "notedraft.db"))

DEFAULT_ACCOUNT = "arbeit2025"

GENRE_PRESETS = ["個人開発・ツール制作", "学習・研究メモ", "Bot・自動化の運用記録", "技術メモ・その他"]
TONES = ["カジュアル", "丁寧", "熱量高め"]
LENGTHS = ["短め", "標準", "長め"]  # 見出し数: 短め=2, 標準=3, 長め=3(各節長め)
DRAFT_STATUSES = ["案", "下書き", "公開済み"]
HASHTAG_PRESETS = ["個人開発", "Python", "Flask", "GitHub", "自動化", "学習記録", "Render", "Bot開発", "SQLite", "note更新"]

TITLE_PATTERNS = [
    "{kw}を作ってみた",
    "{kw}で詰まったところと、どう解決したか",
    "{kw}の記録：{angle}",
    "{kw}をはじめて分かったこと",
    "{kw}運用メモ：{angle}",
    "小さく始める{kw}",
]

ANGLES = {
    "個人開発・ツール制作": ["小さく作って公開するまで", "設計で迷った点と決め方", "使っている技術の話"],
    "学習・研究メモ": ["調べたことの整理", "先行研究から学んだこと", "自分の言葉でのまとめ"],
    "Bot・自動化の運用記録": ["運用ルールと上限の設計", "止める仕組みの話", "しばらく動かしてみた結果"],
    "技術メモ・その他": ["環境構築のメモ", "エラーの切り分け方", "次に試したいこと"],
}
DEFAULT_ANGLES = ["やってみた記録", "詰まった点のメモ", "次に試したいこと"]

SECTION_HEADINGS = ["きっかけ", "やったこと", "詰まったところ", "分かったこと", "次にやること"]

SECTION_BODIES = {
    "きっかけ": [
        "そもそものきっかけは、{kw} にまつわる小さな不便や疑問でした。",
        "大げさな動機ではなく、「これがあったら楽になりそう」というところから始まっています。",
    ],
    "やったこと": [
        "今回は {kw} について、まず動くものを小さく作る（試す）ところから始めました。",
        "途中で仕様を欲張りすぎないよう、最初に決めた範囲の中で進めています。",
    ],
    "詰まったところ": [
        "{kw} で一番詰まったのは、想定と実際の動きが違ったところでした。",
        "エラーの切り分けをしながら、原因を一つずつ潰していきました。",
    ],
    "分かったこと": [
        "やってみて分かったのは、{kw} は思っていたよりシンプルに始められる、ということです。",
        "同時に、続けていくための仕組み（記録や上限づくり）の大事さも感じました。",
    ],
    "次にやること": [
        "次は {kw} まわりで残っている課題に手をつける予定です。",
        "進展があれば、また運用記録として書きます。",
    ],
}

INTRO_BY_TONE = {
    "カジュアル": "今回は {kw} の話です。ゆるく書いていきます。",
    "丁寧": "今回は {kw} について書きます。本記事では、経緯と結果を整理して記録します。",
    "熱量高め": "今回は {kw} の話をさせてください。これが本当に面白いんです。",
}

CLOSING_BY_TONE = {
    "カジュアル": "そんな感じで、引き続きゆるく続けていきます。",
    "丁寧": "以上、{kw} の記録でした。最後までお読みいただきありがとうございました。",
    "熱量高め": "{kw}、まだまだ続けます。次回の記録もお楽しみに。",
}

WARNING = "注意：Studylog の未公開の発明要素・特許関連の秘密情報は入力しないでください。下書きはテンプレート生成です。公開前に必ず本人が推敲してください。"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '下書き',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            published_date TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT '',
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            views INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS article_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            checked_date TEXT NOT NULL,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            views INTEGER
        );
        """
    )
    # 既存 DB の drafts に status 列が無い場合は追加する（移行ガード）
    cols = [r[1] for r in db.execute("PRAGMA table_info(drafts)").fetchall()]
    if "status" not in cols:
        db.execute("ALTER TABLE drafts ADD COLUMN status TEXT NOT NULL DEFAULT '下書き'")
    db.commit()
    db.close()


def get_setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def now_str():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def today_str():
    return datetime.now(JST).strftime("%Y-%m-%d")


def parse_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_opt_int(value):
    if value is None or str(value).strip() == "":
        return None
    return parse_int(value, None)


def article_diff(db, article):
    """直近の履歴とその前回の履歴との差分（前回が無ければ各項目 None）。"""
    rows = db.execute(
        "SELECT * FROM article_stats WHERE article_id = ? ORDER BY id DESC LIMIT 2",
        (article["id"],),
    ).fetchall()
    diff = {"likes": None, "comments": None, "views": None}
    if len(rows) < 2:
        return diff
    cur, prev = rows[0], rows[1]
    diff["likes"] = cur["likes"] - prev["likes"]
    diff["comments"] = cur["comments"] - prev["comments"]
    if cur["views"] is not None and prev["views"] is not None:
        diff["views"] = cur["views"] - prev["views"]
    return diff


def fmt_diff(v):
    if v is None:
        return "―"
    return f"+{v}" if v > 0 else str(v)


def generate_ideas(keyword, genre):
    angles = ANGLES.get(genre, DEFAULT_ANGLES)
    seed = int(hashlib.md5(f"{keyword}|{genre}".encode("utf-8")).hexdigest(), 16)
    ideas = []
    for i in range(3):
        pattern = TITLE_PATTERNS[(seed + i) % len(TITLE_PATTERNS)]
        angle = angles[(seed + i) % len(angles)]
        ideas.append({"title": pattern.format(kw=keyword, angle=angle), "angle": angle})
    return ideas


def build_draft(title, keyword, genre, tone, length, hashtags, closing_text):
    n_sections = 2 if length == "短め" else 3
    headings = SECTION_HEADINGS[:n_sections] if length != "長め" else ["きっかけ", "やったこと", "分かったこと"]
    lines = [f"# {title}", ""]
    lines.append(INTRO_BY_TONE.get(tone, INTRO_BY_TONE["カジュアル"]).format(kw=keyword))
    if genre:
        lines.append(f"（ジャンル：{genre}）")
    lines.append("")
    for h in headings:
        lines.append(f"## {h}")
        bodies = SECTION_BODIES[h]
        reps = 2 if length == "長め" else 1
        for b in bodies[:reps]:
            lines.append(b.format(kw=keyword))
        lines.append("")
    lines.append("## まとめ")
    lines.append(CLOSING_BY_TONE.get(tone, "").format(kw=keyword))
    lines.append("")
    if hashtags:
        lines.append(" ".join(f"#{t}" for t in hashtags))
    if closing_text:
        lines.append("")
        lines.append(closing_text)
    return "\n".join(lines)


INDEX = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>note記事ドラフトメーカー（note {{ account }} 専用）</title>
<style>body{font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em}
.warn{background:#fff3cd;border:1px solid #d9b84a;padding:.8em;border-radius:6px}
pre{background:#f4f4f4;padding:1em;white-space:pre-wrap}textarea{width:100%}</style>
</head><body>
<nav><a href="{{ url_for('index') }}">記事案・下書き</a>｜<a href="{{ url_for('articles') }}">公開記事</a>｜<a href="{{ url_for('dashboard') }}">ダッシュボード</a></nav>
<h1>note記事ドラフトメーカー</h1>
<p><strong>note {{ account }} 専用</strong>の執筆支援ツールです。記事案と下書きをテンプレートで組み立てます（外部 AI は使いません）。</p>
<p class="warn">{{ warning }}</p>

<h2>記事案を作る</h2>
<form method="post" action="{{ url_for('ideas') }}">
  <label>テーマ／キーワード <input type="text" name="keyword" required placeholder="例：自作ツールの死活監視"></label><br>
  <label>ジャンル
    <select name="genre">{% for gname in genres %}<option>{{ gname }}</option>{% endfor %}</select></label>
  <label>自由入力ジャンル <input type="text" name="genre_free" placeholder="任意（入力が優先されます）"></label><br>
  <label>トーン
    <select name="tone">{% for t in tones %}<option>{{ t }}</option>{% endfor %}</select></label>
  <label>長さ
    <select name="length">{% for l in lengths %}<option>{{ l }}</option>{% endfor %}</select></label>
  <h3>ハッシュタグ候補</h3>
  {% for tag in hashtags %}
  <label><input type="checkbox" name="tags" value="{{ tag }}"> #{{ tag }}</label>
  {% endfor %}
  <br><button type="submit">記事案を3件作る</button>
</form>

{% if ideas_list %}
<h2>記事案（3件）</h2>
{% for idea in ideas_list %}
<div>
  <strong>{{ idea.title }}</strong>（切り口：{{ idea.angle }}）
  <form method="post" action="{{ url_for('draft') }}" style="display:inline">
    <input type="hidden" name="title" value="{{ idea.title }}">
    <input type="hidden" name="keyword" value="{{ form.keyword }}">
    <input type="hidden" name="genre" value="{{ form.genre }}">
    <input type="hidden" name="tone" value="{{ form.tone }}">
    <input type="hidden" name="length" value="{{ form.length }}">
    {% for tag in form.tags %}<input type="hidden" name="tags" value="{{ tag }}">{% endfor %}
    <button type="submit">この案で下書きを作る</button>
  </form>
</div>
{% endfor %}
<h3>タイトルを直接入力して下書きを作る</h3>
<form method="post" action="{{ url_for('draft') }}">
  <input type="text" name="title" size="50" placeholder="タイトルを直接入力">
  <input type="hidden" name="keyword" value="{{ form.keyword }}">
  <input type="hidden" name="genre" value="{{ form.genre }}">
  <input type="hidden" name="tone" value="{{ form.tone }}">
  <input type="hidden" name="length" value="{{ form.length }}">
  {% for tag in form.tags %}<input type="hidden" name="tags" value="{{ tag }}">{% endfor %}
  <button type="submit">下書きを作る</button>
</form>
{% endif %}

{% if draft_body %}
<h2>下書き（{{ draft_body|length }}文字）</h2>
<pre id="draft">{{ draft_body }}</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('draft').innerText)">コピー</button>
<form method="post" action="{{ url_for('save_draft') }}" style="display:inline">
  <input type="hidden" name="title" value="{{ draft_title }}">
  <input type="hidden" name="genre" value="{{ form.genre }}">
  <textarea name="body" hidden>{{ draft_body }}</textarea>
  <button type="submit">この下書きを保存する</button>
</form>
{% endif %}

<h2>保存した下書き</h2>
<ul>
{% for d in drafts %}
<li>{{ d.created_at }} <a href="{{ url_for('view_draft', did=d.id) }}">{{ d.title }}</a>（{{ d.genre }}）［状態：{{ d.status }}］
  <a href="{{ url_for('download_draft', did=d.id) }}">Markdown</a>
  <form method="post" action="{{ url_for('draft_status', did=d.id) }}" style="display:inline">
    <select name="status">{% for s in statuses %}<option {% if s == d.status %}selected{% endif %}>{{ s }}</option>{% endfor %}</select>
    <button>状態を変更</button>
  </form>
  {% if d.status != '公開済み' %}
  <form method="post" action="{{ url_for('draft_publish', did=d.id) }}" style="display:inline"><button>公開済みにする</button></form>
  {% endif %}
  <form method="post" action="{{ url_for('delete_draft', did=d.id) }}" style="display:inline"><button>削除</button></form></li>
{% else %}<li>保存した下書きはありません</li>{% endfor %}
</ul>

<h2>設定</h2>
<form method="post" action="{{ url_for('save_settings') }}">
  <label>対象アカウント名 <input type="text" name="account" value="{{ account }}"></label><br>
  <label>締めの定型文（署名など、下書き末尾に自動で入ります）<br>
  <textarea name="closing" rows="2">{{ closing }}</textarea></label><br>
  <button type="submit">設定を保存</button>
</form>
</body></html>"""

VIEW = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ d.title }} - notedraft</title>
<style>body{font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em}
pre{background:#f4f4f4;padding:1em;white-space:pre-wrap}</style>
</head><body>
<nav><a href="{{ url_for('index') }}">記事案・下書き</a>｜<a href="{{ url_for('articles') }}">公開記事</a>｜<a href="{{ url_for('dashboard') }}">ダッシュボード</a></nav>
<h1>{{ d.title }}</h1>
<p>{{ d.created_at }}／ジャンル：{{ d.genre }}／状態：{{ d.status }}／{{ d.body|length }}文字</p>
<pre id="draft">{{ d.body }}</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('draft').innerText)">コピー</button>
<a href="{{ url_for('download_draft', did=d.id) }}">Markdown でダウンロード</a>
<p><a href="{{ url_for('index') }}">戻る</a></p>
</body></html>"""


ARTICLES = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>公開記事の管理 - notedraft</title>
<style>body{font-family:sans-serif;max-width:860px;margin:2em auto;padding:0 1em}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3em .5em}
input[type=number]{width:6em}</style>
</head><body>
<nav><a href="{{ url_for('index') }}">記事案・下書き</a>｜<a href="{{ url_for('articles') }}">公開記事</a>｜<a href="{{ url_for('dashboard') }}">ダッシュボード</a></nav>
<h1>公開記事の管理（note {{ account }}）</h1>
<p>公開済み記事を登録して、スキ数・コメント数・ビュー数を手入力で記録します。数値は note から自動取得しません（公式 API は使っていません）。「数値を更新」を押すと現在の数値が上書きされ、確認履歴にも残ります。</p>

<h2>記事を登録</h2>
<form method="post" action="{{ url_for('add_article') }}">
  <label>タイトル <input type="text" name="title" required size="50"></label><br>
  <label>URL <input type="url" name="url" size="50" placeholder="https://note.com/arbeit2025/n/..."></label><br>
  <label>公開日 <input type="date" name="published_date" required></label><br>
  <label>ジャンル
    <select name="genre">{% for gname in genres %}<option>{{ gname }}</option>{% endfor %}</select></label>
  <label>自由入力ジャンル <input type="text" name="genre_free" placeholder="任意（入力が優先されます）"></label><br>
  <label>スキ数 <input type="number" name="likes" value="0" min="0"></label>
  <label>コメント数 <input type="number" name="comments" value="0" min="0"></label>
  <label>ビュー数 <input type="number" name="views" min="0" placeholder="任意"></label><br>
  <button type="submit">登録する</button>
</form>

<h2>記事一覧（{{ articles|length }}件）</h2>
<table>
<tr><th>公開日</th><th>タイトル</th><th>ジャンル</th><th>スキ</th><th>コメント</th><th>ビュー</th><th>前回比</th><th>操作</th></tr>
{% for a in articles %}
<tr>
  <td>{{ a.published_date }}</td>
  <td>{% if a.url %}<a href="{{ a.url }}">{{ a.title }}</a>{% else %}{{ a.title }}{% endif %}</td>
  <td>{{ a.genre }}</td>
  <td>{{ a.likes }}</td>
  <td>{{ a.comments }}</td>
  <td>{{ a.views if a.views is not none else '―' }}</td>
  <td>スキ {{ a.diff.likes }}／コメント {{ a.diff.comments }}／ビュー {{ a.diff.views }}</td>
  <td>
    <a href="{{ url_for('edit_article', aid=a.id) }}">編集</a>
    <form method="post" action="{{ url_for('delete_article', aid=a.id) }}" style="display:inline" onsubmit="return confirm('この記事を削除しますか？')"><button>削除</button></form>
  </td>
</tr>
<tr>
  <td colspan="8">
    <form method="post" action="{{ url_for('update_stats', aid=a.id) }}">
      数値を更新：スキ <input type="number" name="likes" value="{{ a.likes }}" min="0">
      コメント <input type="number" name="comments" value="{{ a.comments }}" min="0">
      ビュー <input type="number" name="views" value="{{ a.views if a.views is not none else '' }}" min="0">
      <button type="submit">数値を更新</button>
      <span>最終更新：{{ a.updated_at }}</span>
    </form>
  </td>
</tr>
{% else %}
<tr><td colspan="8">登録された記事はまだありません</td></tr>
{% endfor %}
</table>
</body></html>"""

ARTICLE_EDIT = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>記事の編集 - notedraft</title>
<style>body{font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em}</style>
</head><body>
<nav><a href="{{ url_for('index') }}">記事案・下書き</a>｜<a href="{{ url_for('articles') }}">公開記事</a>｜<a href="{{ url_for('dashboard') }}">ダッシュボード</a></nav>
<h1>記事の編集</h1>
<form method="post" action="{{ url_for('edit_article', aid=a.id) }}">
  <label>タイトル <input type="text" name="title" required size="50" value="{{ a.title }}"></label><br>
  <label>URL <input type="url" name="url" size="50" value="{{ a.url }}"></label><br>
  <label>公開日 <input type="date" name="published_date" required value="{{ a.published_date }}"></label><br>
  <label>ジャンル <input type="text" name="genre" value="{{ a.genre }}"></label><br>
  <button type="submit">保存する</button>
</form>

<h2>数値の履歴</h2>
<ul>
{% for s in stats %}
<li>{{ s.checked_date }}：スキ {{ s.likes }}／コメント {{ s.comments }}／ビュー {{ s.views if s.views is not none else '―' }}</li>
{% else %}<li>履歴はまだありません</li>{% endfor %}
</ul>
<p><a href="{{ url_for('articles') }}">記事一覧に戻る</a></p>
</body></html>"""

DASHBOARD = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ダッシュボード - notedraft</title>
<style>body{font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3em .5em}</style>
</head><body>
<nav><a href="{{ url_for('index') }}">記事案・下書き</a>｜<a href="{{ url_for('articles') }}">公開記事</a>｜<a href="{{ url_for('dashboard') }}">ダッシュボード</a></nav>
<h1>進捗ダッシュボード（note {{ account }}）</h1>
<ul>
  <li>総記事数：{{ total }}件</li>
  <li>今月（{{ month }}）の投稿数：{{ month_count }}件</li>
  <li>最終投稿日：{{ last_date if last_date else '―' }}{% if days_since is not none %}（{{ days_since }}日前）{% endif %}</li>
  <li>平均投稿間隔：{% if avg_interval is not none %}{{ avg_interval }}日{% else %}―（記事2件以上で計算します）{% endif %}</li>
</ul>
<h2>ジャンル別本数</h2>
<table>
<tr><th>ジャンル</th><th>本数</th></tr>
{% for row in genre_counts %}
<tr><td>{{ row['genre'] if row['genre'] else '（未設定）' }}</td><td>{{ row['c'] }}件</td></tr>
{% else %}<tr><td colspan="2">登録された記事はまだありません</td></tr>{% endfor %}
</table>

<h2>下書きのパイプライン</h2>
<table>
<tr><th>状態</th><th>件数</th></tr>
{% for s in statuses %}
<tr><td>{{ s }}</td><td>{{ pipeline[s] }}件</td></tr>
{% endfor %}
</table>
</body></html>"""


def base_context(db):
    return {
        "account": get_setting(db, "account", DEFAULT_ACCOUNT),
        "closing": get_setting(db, "closing", ""),
        "genres": GENRE_PRESETS,
        "tones": TONES,
        "lengths": LENGTHS,
        "hashtags": HASHTAG_PRESETS,
        "statuses": DRAFT_STATUSES,
        "warning": WARNING,
        "ideas_list": None,
        "draft_body": None,
        "draft_title": "",
        "form": {},
        "drafts": db.execute("SELECT * FROM drafts ORDER BY id DESC").fetchall(),
    }


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    db = get_db()
    return render_template_string(INDEX, **base_context(db))


@app.route("/ideas", methods=["POST"])
def ideas():
    db = get_db()
    keyword = (request.form.get("keyword") or "").strip()
    genre = (request.form.get("genre_free") or "").strip() or request.form.get("genre", GENRE_PRESETS[0])
    form = {
        "keyword": keyword,
        "genre": genre,
        "tone": request.form.get("tone", TONES[0]),
        "length": request.form.get("length", LENGTHS[1]),
        "tags": request.form.getlist("tags"),
    }
    ctx = base_context(db)
    ctx["ideas_list"] = generate_ideas(keyword, genre) if keyword else []
    ctx["form"] = form
    return render_template_string(INDEX, **ctx)


@app.route("/draft", methods=["POST"])
def draft():
    db = get_db()
    keyword = (request.form.get("keyword") or "").strip()
    title = (request.form.get("title") or "").strip()
    if not title:
        title = f"{keyword}の記録"
    form = {
        "keyword": keyword,
        "genre": request.form.get("genre", ""),
        "tone": request.form.get("tone", TONES[0]),
        "length": request.form.get("length", LENGTHS[1]),
        "tags": request.form.getlist("tags"),
    }
    body = build_draft(
        title, keyword, form["genre"], form["tone"], form["length"],
        form["tags"], get_setting(db, "closing", ""),
    )
    ctx = base_context(db)
    ctx["draft_body"] = body
    ctx["draft_title"] = title
    ctx["form"] = form
    return render_template_string(INDEX, **ctx)


@app.route("/drafts", methods=["POST"])
def save_draft():
    title = (request.form.get("title") or "").strip()
    body = request.form.get("body", "")
    if title and body:
        db = get_db()
        db.execute(
            "INSERT INTO drafts(title, body, genre, status, created_at) VALUES(?, ?, ?, ?, ?)",
            (title, body, (request.form.get("genre") or "").strip(), "下書き",
             datetime.now(JST).strftime("%Y-%m-%d %H:%M")),
        )
        db.commit()
    return redirect(url_for("index"))


@app.route("/drafts/<int:did>")
def view_draft(did):
    db = get_db()
    row = db.execute("SELECT * FROM drafts WHERE id = ?", (did,)).fetchone()
    if row is None:
        abort(404)
    return render_template_string(VIEW, d=row)


@app.route("/drafts/<int:did>/download")
def download_draft(did):
    db = get_db()
    row = db.execute("SELECT * FROM drafts WHERE id = ?", (did,)).fetchone()
    if row is None:
        abort(404)
    buf = io.BytesIO(row["body"].encode("utf-8"))
    return send_file(buf, mimetype="text/markdown", download_name=f"draft_{did}.md", as_attachment=True)


@app.route("/drafts/<int:did>/delete", methods=["POST"])
def delete_draft(did):
    db = get_db()
    db.execute("DELETE FROM drafts WHERE id = ?", (did,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/drafts/<int:did>/status", methods=["POST"])
def draft_status(did):
    status = request.form.get("status", "下書き")
    if status not in DRAFT_STATUSES:
        status = "下書き"
    db = get_db()
    db.execute("UPDATE drafts SET status = ? WHERE id = ?", (status, did))
    db.commit()
    return redirect(url_for("index"))


@app.route("/drafts/<int:did>/publish", methods=["POST"])
def draft_publish(did):
    db = get_db()
    row = db.execute("SELECT * FROM drafts WHERE id = ?", (did,)).fetchone()
    if row is None:
        abort(404)
    now = now_str()
    db.execute(
        "INSERT INTO articles(title, url, published_date, genre, likes, comments, views, created_at, updated_at)"
        " VALUES(?, '', ?, ?, 0, 0, NULL, ?, ?)",
        (row["title"], today_str(), row["genre"], now, now),
    )
    db.execute("UPDATE drafts SET status = '公開済み' WHERE id = ?", (did,))
    db.commit()
    return redirect(url_for("articles"))


@app.route("/articles")
def articles():
    db = get_db()
    rows = db.execute("SELECT * FROM articles ORDER BY published_date DESC, id DESC").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["diff"] = {k: fmt_diff(v) for k, v in article_diff(db, row).items()}
        items.append(item)
    return render_template_string(
        ARTICLES,
        account=get_setting(db, "account", DEFAULT_ACCOUNT),
        genres=GENRE_PRESETS,
        articles=items,
    )


@app.route("/articles", methods=["POST"])
def add_article():
    title = (request.form.get("title") or "").strip()
    published = (request.form.get("published_date") or "").strip()
    if title and published:
        genre = (request.form.get("genre_free") or "").strip() or request.form.get("genre", "")
        now = now_str()
        db = get_db()
        db.execute(
            "INSERT INTO articles(title, url, published_date, genre, likes, comments, views, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, (request.form.get("url") or "").strip(), published, genre,
             parse_int(request.form.get("likes")), parse_int(request.form.get("comments")),
             parse_opt_int(request.form.get("views")), now, now),
        )
        db.commit()
    return redirect(url_for("articles"))


@app.route("/articles/<int:aid>/edit", methods=["GET", "POST"])
def edit_article(aid):
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        abort(404)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        published = (request.form.get("published_date") or "").strip()
        if title and published:
            db.execute(
                "UPDATE articles SET title = ?, url = ?, published_date = ?, genre = ?, updated_at = ? WHERE id = ?",
                (title, (request.form.get("url") or "").strip(), published,
                 (request.form.get("genre") or "").strip(), now_str(), aid),
            )
            db.commit()
        return redirect(url_for("articles"))
    stats = db.execute(
        "SELECT * FROM article_stats WHERE article_id = ? ORDER BY id DESC", (aid,)
    ).fetchall()
    return render_template_string(ARTICLE_EDIT, a=row, stats=stats)


@app.route("/articles/<int:aid>/delete", methods=["POST"])
def delete_article(aid):
    db = get_db()
    db.execute("DELETE FROM article_stats WHERE article_id = ?", (aid,))
    db.execute("DELETE FROM articles WHERE id = ?", (aid,))
    db.commit()
    return redirect(url_for("articles"))


@app.route("/articles/<int:aid>/stats", methods=["POST"])
def update_stats(aid):
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        abort(404)
    likes = parse_int(request.form.get("likes"))
    comments = parse_int(request.form.get("comments"))
    views = parse_opt_int(request.form.get("views"))
    db.execute(
        "UPDATE articles SET likes = ?, comments = ?, views = ?, updated_at = ? WHERE id = ?",
        (likes, comments, views, now_str(), aid),
    )
    db.execute(
        "INSERT INTO article_stats(article_id, checked_date, likes, comments, views) VALUES(?, ?, ?, ?, ?)",
        (aid, today_str(), likes, comments, views),
    )
    db.commit()
    return redirect(url_for("articles"))


@app.route("/dashboard")
def dashboard():
    db = get_db()
    today = datetime.now(JST).date()
    rows = db.execute("SELECT * FROM articles ORDER BY published_date ASC, id ASC").fetchall()
    total = len(rows)
    month_prefix = today.strftime("%Y-%m")
    month_count = sum(1 for r in rows if r["published_date"].startswith(month_prefix))
    last_date = rows[-1]["published_date"] if rows else None
    days_since = None
    avg_interval = None
    if last_date:
        try:
            days_since = (today - date.fromisoformat(last_date)).days
        except ValueError:
            days_since = None
    if total >= 2:
        try:
            first = date.fromisoformat(rows[0]["published_date"])
            last = date.fromisoformat(rows[-1]["published_date"])
            avg_interval = round((last - first).days / (total - 1), 1)
        except ValueError:
            avg_interval = None
    genre_counts = db.execute(
        "SELECT genre, COUNT(*) AS c FROM articles GROUP BY genre ORDER BY c DESC, genre"
    ).fetchall()
    pipeline = {s: 0 for s in DRAFT_STATUSES}
    for r in db.execute("SELECT status, COUNT(*) AS c FROM drafts GROUP BY status").fetchall():
        if r["status"] in pipeline:
            pipeline[r["status"]] = r["c"]
    return render_template_string(
        DASHBOARD,
        account=get_setting(db, "account", DEFAULT_ACCOUNT),
        total=total,
        month=today.strftime("%Y年%m月"),
        month_count=month_count,
        last_date=last_date,
        days_since=days_since,
        avg_interval=avg_interval,
        genre_counts=genre_counts,
        statuses=DRAFT_STATUSES,
        pipeline=pipeline,
    )


@app.route("/settings", methods=["POST"])
def save_settings():
    db = get_db()
    account = (request.form.get("account") or "").strip() or DEFAULT_ACCOUNT
    closing = request.form.get("closing", "")
    for key, value in (("account", account), ("closing", closing)):
        db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    db.commit()
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
