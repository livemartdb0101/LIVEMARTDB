# event_editor_tk.py — イベント編集専用ツール（標準ライブラリのみ）
# 機能：
# - イベント 新規/編集/削除（date/title/sub_title/venue プルダウン）
# - セトリ編集（曲プルダウン、挿入/上下/削除、section/version、seqは常に1..N）
# - セトリ削除時は performer(同 event_id, seq) も同時削除
# - Publish JSON：同フォルダに export_json.py があれば実行（任意）

import os, sys, sqlite3, subprocess, webbrowser, socket, time, tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox
# 画像表示（WEBP）は Pillow があれば表示、無ければ何もしない
try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "eventdata.db")
# DB_PATH = r".\dizzy.db"
ROOT_DIR = r"."
EXPORT_PY = os.path.join(ROOT_DIR, "export_json.py")


# ====== DB 初期化・整合性チェック（A案：GUI起動前に1回だけ） ======

ERROR_LOG_PATH = os.path.join(BASE_DIR, "error.log")

# def _log_error(msg: str, exc: Exception | None = None):
#     """エラー時のみ error.log に追記。普段はログを出さない方針。"""
#     try:
#         with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
#             ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#             f.write(f"[{ts}] {msg}\n")
#             if exc:
#                 f.write(f"  {type(exc).__name__}: {exc}\n")
#     except Exception:
#         # ログ書き込みでさらに失敗しても黙殺（ユーザー体験優先）
#         pass

def _exec_script(con: sqlite3.Connection, sql: str):
    cur = con.cursor()
    cur.executescript(sql)
    con.commit()

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def _index_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,))
    return cur.fetchone() is not None

def _view_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,))
    return cur.fetchone() is not None

def _get_user_version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version;").fetchone()[0] or 0

def _set_user_version(con: sqlite3.Connection, ver: int):
    con.execute(f"PRAGMA user_version = {ver};")
    con.commit()

def _create_initial_schema(con: sqlite3.Connection):
    """
    新規DB向け：テーブル/インデックス/ビューを一括作成 + user_version=1
    ※ 既存DBでも IF NOT EXISTS により二重作成はされません。
    """
    schema_sql = r"""
    PRAGMA foreign_keys = ON;

    -- ====== TABLES ======
    CREATE TABLE IF NOT EXISTS people(
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      birthday TEXT, joined_on TEXT, left_on TEXT, note TEXT,
      x TEXT, instagram TEXT, threads TEXT, facebook TEXT, youtube TEXT, tiktok TEXT
    );

    CREATE TABLE IF NOT EXISTS venues(
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      url TEXT, note TEXT
    );

    CREATE TABLE IF NOT EXISTS songs(
      id INTEGER PRIMARY KEY,
      title TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY,
      date TEXT NOT NULL,
      title TEXT NOT NULL,
      sub_title TEXT,
      venue_id INTEGER,
      form TEXT, era_id INTEGER REFERENCES era(id), tour_id INTEGER REFERENCES tour(id),
      FOREIGN KEY (venue_id) REFERENCES venues(id)
    );

    CREATE TABLE IF NOT EXISTS setlist(
      event_id INTEGER NOT NULL,
      seq INTEGER NOT NULL,
      song_id INTEGER NOT NULL,
      section TEXT,
      version TEXT,
      note TEXT,
      PRIMARY KEY (event_id, seq),
      FOREIGN KEY (event_id) REFERENCES events(id),
      FOREIGN KEY (song_id)  REFERENCES songs(id)
    );

    CREATE TABLE IF NOT EXISTS lineup(
      event_id INTEGER NOT NULL,
      member_id INTEGER NOT NULL,
      role TEXT NOT NULL,
      position TEXT, is_guest INTEGER NOT NULL DEFAULT 0, ord INTEGER,
      PRIMARY KEY (event_id, member_id, role),
      FOREIGN KEY (event_id) REFERENCES events(id),
      FOREIGN KEY (member_id) REFERENCES people(id)
    );

    CREATE TABLE IF NOT EXISTS performer(
      event_id INTEGER NOT NULL,
      seq INTEGER NOT NULL,
      member_id INTEGER NOT NULL,
      role TEXT NOT NULL,
      position TEXT, is_guest INTEGER NOT NULL DEFAULT 0, ord INTEGER,
      PRIMARY KEY (event_id, seq, member_id, role),
      FOREIGN KEY (event_id, seq) REFERENCES setlist(event_id, seq),
      FOREIGN KEY (member_id) REFERENCES people(id)
    );

    CREATE TABLE IF NOT EXISTS roles (
      id INTEGER PRIMARY KEY,
      role TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS era (
      id       INTEGER PRIMARY KEY,
      name     TEXT    NOT NULL,
      start_on TEXT    NOT NULL,
      end_on   TEXT,
      memo     TEXT
    );

    CREATE TABLE IF NOT EXISTS tour (
      id       INTEGER PRIMARY KEY,
      name     TEXT NOT NULL,
      start_on TEXT,
      end_on   TEXT,
      memo     TEXT
    );

    CREATE TABLE IF NOT EXISTS acts (
      id   INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      url TEXT, x TEXT, instagram TEXT, threads TEXT, facebook TEXT, youtube TEXT, tiktok TEXT
    );

    CREATE TABLE IF NOT EXISTS bandsevent(
      event_id INTEGER NOT NULL,
      seq INTEGER NOT NULL,
      act_id INTEGER NOT NULL,
      PRIMARY KEY(event_id, seq),
      FOREIGN KEY(event_id) REFERENCES events(id),
      FOREIGN KEY(act_id) REFERENCES acts(id)
    );

    -- ====== INDEXES ======
    CREATE INDEX IF NOT EXISTS idx_events_date            ON events(date);
    CREATE INDEX IF NOT EXISTS idx_events_venue           ON events(venue_id);
    CREATE INDEX IF NOT EXISTS idx_setlist_song           ON setlist(song_id);
    CREATE INDEX IF NOT EXISTS idx_performer_event_seq    ON performer(event_id, seq);
    CREATE INDEX IF NOT EXISTS idx_lineup_event           ON lineup(event_id);         -- 既存互換
    CREATE INDEX IF NOT EXISTS idx_lineup_event_ord       ON lineup(event_id, ord);    -- 重要：ord順の安定/高速化

    -- ====== VIEWS (常に最新へ再定義) ======
    DROP VIEW IF EXISTS v_events;
    CREATE VIEW v_events AS
      SELECT e.id, e.date, e.title, e.sub_title, v.name AS venue
      FROM events e LEFT JOIN venues v ON v.id = e.venue_id
    /* v_events(id,date,title,sub_title,venue) */;

    DROP VIEW IF EXISTS v_setlist;
    CREATE VIEW v_setlist AS
      SELECT es.event_id, es.seq, es.section, s.title AS song_title, es.version
      FROM setlist es JOIN songs s ON s.id = es.song_id
    /* v_setlist(event_id,seq,section,song_title,version) */;

    DROP VIEW IF EXISTS v_event_members;
    CREATE VIEW v_event_members AS
      SELECT l.event_id, NULL AS seq, p.name AS person, l.role, l.position
      FROM lineup l JOIN people p ON p.id = l.member_id
      UNION ALL
      SELECT pf.event_id, pf.seq, p.name, pf.role, pf.position
      FROM performer pf JOIN people p ON p.id = pf.member_id
    /* v_event_members(event_id,seq,person,role,position) */;

    DROP VIEW IF EXISTS v_bandsevent;
    CREATE VIEW v_bandsevent AS
      SELECT be.event_id,
             be.seq,
             a.name AS act_name
      FROM bandsevent AS be
      JOIN acts AS a ON a.id = be.act_id
    /* v_bandsevent(event_id,seq,act_name) */;
    """
    _exec_script(con, schema_sql)

    # 初期 user_version（将来の変更に備えて）
    if _get_user_version(con) == 0:
        _set_user_version(con, 1)

def _ensure_minimum_objects(con: sqlite3.Connection):
    """
    既存DB向け：不足オブジェクトの補完（非破壊）
    - 必須テーブルが無ければ作成
    - 必須インデックスが無ければ作成
    - ビューは常に最新再定義（列互換のため）
    """
    con.execute("PRAGMA foreign_keys = ON;")

    # 主要テーブルの最低限チェック（events が無い＝空ファイル事故の代表）
    if not _table_exists(con, "events"):
        _create_initial_schema(con)
        return

    # 不足の INDEX を補完（必要最低限）
    # lineup の ord 用インデックスが無ければ付与
    if not _index_exists(con, "idx_lineup_event_ord"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_lineup_event_ord ON lineup(event_id, ord);")
        con.commit()

    # 既存互換の event_id 単体インデックスが無い場合は補完（任意）
    if not _index_exists(con, "idx_lineup_event"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_lineup_event ON lineup(event_id);")
        con.commit()

    # 他の基本インデックスも念のため（存在しない環境への補完）
    if not _index_exists(con, "idx_events_date"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);")
    if not _index_exists(con, "idx_events_venue"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_venue ON events(venue_id);")
    if not _index_exists(con, "idx_setlist_song"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_setlist_song ON setlist(song_id);")
    if not _index_exists(con, "idx_performer_event_seq"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_performer_event_seq ON performer(event_id, seq);")
    con.commit()

    # ビューは常に最新へ再定義（IF EXISTS → 再作成）
    _exec_script(con, r"""
    DROP VIEW IF EXISTS v_events;
    CREATE VIEW v_events AS
      SELECT e.id, e.date, e.title, e.sub_title, v.name AS venue
      FROM events e LEFT JOIN venues v ON v.id = e.venue_id
    /* v_events(id,date,title,sub_title,venue) */;

    DROP VIEW IF EXISTS v_setlist;
    CREATE VIEW v_setlist AS
      SELECT es.event_id, es.seq, es.section, s.title AS song_title, es.version
      FROM setlist es JOIN songs s ON s.id = es.song_id
    /* v_setlist(event_id,seq,section,song_title,version) */;

    DROP VIEW IF EXISTS v_event_members;
    CREATE VIEW v_event_members AS
      SELECT l.event_id, NULL AS seq, p.name AS person, l.role, l.position
      FROM lineup l JOIN people p ON p.id = l.member_id
      UNION ALL
      SELECT pf.event_id, pf.seq, p.name, pf.role, pf.position
      FROM performer pf JOIN people p ON p.id = pf.member_id
    /* v_event_members(event_id,seq,person,role,position) */;

    DROP VIEW IF EXISTS v_bandsevent;
    CREATE VIEW v_bandsevent AS
      SELECT be.event_id,
             be.seq,
             a.name AS act_name
      FROM bandsevent AS be
      JOIN acts AS a ON a.id = be.act_id
    /* v_bandsevent(event_id,seq,act_name) */;
    """)

    # user_version が 0（未設定）の古いDBは 1 に上げておく
    if _get_user_version(con) == 0:
        _set_user_version(con, 1)

def ensure_db():
    """
    起動前に1回だけ呼ぶ。
    - ファイルが無ければ新規作成（初期スキーマ投入）
    - あれば不足補完（非破壊）
    - 失敗時のみ error.log に記録し、ユーザーには最小限の通知
    """
    try:
        # 接続（ファイルが無ければここで空DBファイルが作成される）
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON;")

            # events テーブル有無で “新規 or 既存” をゆるく判定
            if not _table_exists(con, "events"):
                # 新規 or 空ファイル事故 → 初期スキーマ一括
                _create_initial_schema(con)
            else:
                # 既存DB → 不足補完
                _ensure_minimum_objects(con)

    except Exception as e:
        # _log_error("DB初期化/整合性チェック中にエラーが発生しました。", e)
        # 必要最小限の通知（GUI前なので messagebox ではなく print でもOK）
        print("Error: データベースの初期化に失敗しました。error.log を確認してください。", file=sys.stderr)
        # ここで再スローしても良いが、起動継続させない方が安全
        raise



# ---------- DB ユーティリティ ----------
# ---------- 追加：DBユーティリティの改善 ----------
def db_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # 外部キー制約を毎接続で有効化
    con.execute("PRAGMA foreign_keys = ON;")
    return con

def qall(sql, args=()):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]

def qone(sql, args=()):
    rows = qall(sql, args)
    return rows[0] if rows else None

# def exec1(sql, args=()):
#     with db_conn() as con:
#         cur = con.cursor()
#         cur.execute(sql, args)
#         con.commit()

def exec1(sql, args=()):
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(sql, args)
        con.commit()

        if sql.strip().upper().startswith(("UPDATE", "DELETE")):
            if cur.rowcount == 0:
                raise RuntimeError(f"更新失敗検知: {sql}")


def resequence(event_id: int):
    rows = qall("SELECT seq FROM setlist WHERE event_id=? ORDER BY seq", (event_id,))
    for i, r in enumerate(rows, start=1):
        exec1("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (i, event_id, r["seq"]))



HTTP_PORT = 8000
HTTP_PROC = None  # サーバープロセス（起動済みなら保持）

def _is_port_open(host: str, port: int) -> bool:
    """localhost:port が開いているか簡易チェック"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0

def start_http_server_once():
    """
    site/ をルートに簡易HTTPサーバを1回だけ起動。
    既に起動済み or ポート占有時は何もしない。
    """
    global HTTP_PROC
    html_dir = os.path.join(BASE_DIR, "site")
    # index_path = os.path.join(html_dir, "index.html")

    # # html/index.html が無ければ、フォールバックとして site/index.html を試す（任意）
    # if not os.path.exists(index_path):
    #     alt = os.path.join(BASE_DIR, "site", "index.html")
    #     if os.path.exists(alt):
    #         html_dir = os.path.dirname(alt)
    #         index_path = alt

    # 既にポートが開いていればそれを使う
    if _is_port_open("127.0.0.1", HTTP_PORT):
        return

    # サーバ未起動ならバックグラウンド起動
    try:
        # Windows・macOS・Linux どれでも動く（Python同一環境で起動）
        HTTP_PROC = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(HTTP_PORT)],
            cwd=html_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        # サーバ起動待ち（最大1秒くらいのリトライ）
        for _ in range(10):
            if _is_port_open("127.0.0.1", HTTP_PORT):
                break
            time.sleep(0.1)
    except Exception as e:
        messagebox.showerror("HTTPサーバ起動エラー", f"簡易サーバを起動できませんでした。\n{e}")
        return

def open_web_preview():
    """
    ブラウザで index.html を開く（簡易HTTPサーバ経由）。
    """
    start_http_server_once()
    url = f"http://127.0.0.1:{HTTP_PORT}/index.html"
    try:
        webbrowser.open(url, new=2)  # new=2: 可能なら新しいタブで
    except Exception as e:
        messagebox.showerror("ブラウザ起動エラー", f"ブラウザを開けませんでした。\n{e}")

def _on_close():
    global HTTP_PROC
    try:
        if HTTP_PROC and HTTP_PROC.poll() is None:
            HTTP_PROC.terminate()
            # すぐ閉じないことがあるので、少し待ってダメなら kill でもOK
            try:
                HTTP_PROC.wait(timeout=1.0)
            except Exception:
                HTTP_PROC.kill()
    except Exception:
        pass
    root.destroy()




# # ---------- Tk アプリ ----------
class App(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)


        if not os.path.isfile(DB_PATH):
            messagebox.showerror("エラー", f"DBが見つかりません。\n{DB_PATH}")
            self.destroy()
            return

        # キャッシュ（会場・曲）
        self.venues = qall("SELECT id, name FROM venues ORDER BY name COLLATE NOCASE")
        self.songs  = qall("SELECT id, title FROM songs ORDER BY title COLLATE NOCASE")
        self.people = qall("SELECT id, name FROM people ORDER BY name COLLATE NOCASE")
        self.people_names = [p["name"] for p in self.people]
        self.people_name_to_id = {p["name"]: p["id"] for p in self.people}

        self.roles = qall("SELECT id, role FROM roles ORDER BY id")
        self.role_names = [r["role"] for r in self.roles]

        self.song_titles = [s["title"] for s in self.songs]
        self.song_title_to_id = {s["title"]: s["id"] for s in self.songs}
        self.venue_names = [v["name"] for v in self.venues]
        self.venue_name_to_id = {v["name"]: v["id"] for v in self.venues}
        self.event_id = None  # 選択中イベント

        # Era / Tour の読み込み
        self.eras = qall("SELECT id, name FROM era ORDER BY start_on")
        self.era_names = [e["name"] for e in self.eras]
        self.era_name_to_id = {e["name"]: e["id"] for e in self.eras}

        self.tours = qall("SELECT id, name FROM tour ORDER BY name COLLATE NOCASE")
        self.tour_names = [t["name"] for t in self.tours]
        self.tour_name_to_id = {t["name"]: t["id"] for t in self.tours}


        self._build_ui()
        self.refresh_events()

    def _build_ui(self):
        # 上段：左右にイベント一覧 / イベント編集
        top = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=3)

        # 左：イベント一覧
        left = ttk.Frame(top)
        top.add(left)
        # 右：イベント編集
        right = ttk.Frame(top)
        top.add(right)

        def update_ratio(event=None):
            w = top.winfo_width()
            left = int(w * 0.55)   # 左 40%
            top.sashpos(0, left)

        top.bind("<Configure>", update_ratio)


        # --- 左ペイン（イベント編集＋イベント一覧） ---
        # 上：イベント編集
        rf = ttk.LabelFrame(left, text="イベント編集")
        rf.pack(fill=tk.X, expand=False, padx=4, pady=2)

        frm = ttk.Frame(rf); frm.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(frm, text="日付(YYYY-MM-DD):").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="タイトル:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="サブタイトル:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="会場:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="Era:").grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="Tour/Series:").grid(row=2, column=2, sticky="w", padx=4, pady=2)
        ttk.Label(frm, text="形態:").grid(row=3, column=2, sticky="w", padx=4, pady=2)

        self.date_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.venue_var = tk.StringVar()
        self.form_var = tk.StringVar()
        self.era_var = tk.StringVar()
        self.tour_var = tk.StringVar()

        ttk.Entry(frm, textvariable=self.date_var, width=14).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.title_var, width=25).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.sub_var, width=25).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        self.venue_cb = ttk.Combobox(frm, textvariable=self.venue_var, values=self.venue_names, width=20)
        self.venue_cb.grid(row=3, column=1, sticky="w", padx=4, pady=2)
        self.era_cb = ttk.Combobox(frm, textvariable=self.era_var, values=self.era_names, width=12, state="readonly")
        self.era_cb.grid(row=1, column=3, sticky="w", padx=4, pady=2)
        self.tour_cb = ttk.Combobox(frm, textvariable=self.tour_var, values=self.tour_names, width=12, state="readonly")
        self.tour_cb.grid(row=2, column=3, sticky="w", padx=4, pady=2)

        self.form_cb = ttk.Combobox(
            frm,
            textvariable=self.form_var,
            values=("", "BAND", "IDOL"),   # 空（=NULL）も選べるように先頭に空文字
            width=12,
            state="readonly"
        )
        self.form_cb.grid(row=3, column=3, sticky="w", padx=4, pady=2)

        btn_fr = ttk.Frame(rf); btn_fr.pack(fill=tk.X, padx=8, pady=(0,6))
        ttk.Button(btn_fr, text="保存（新規/更新）", command=self.save_event).pack(side=tk.LEFT)
        ttk.Button(btn_fr, text="セトリをリロード", command=self.load_setlist).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_fr, text="Web参照", command=open_web_preview).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_fr, text="Publish JSON", command=self.publish_json).pack(side=tk.RIGHT)

        # 下：イベント一覧
        lf = ttk.LabelFrame(left, text="イベント一覧")
        lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        search_fr = ttk.Frame(lf); search_fr.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(search_fr, text="検索:").pack(side=tk.LEFT)
        self.q_var = tk.StringVar()
        q_entry = ttk.Entry(search_fr, textvariable=self.q_var, width=20)
        q_entry.pack(side=tk.LEFT, padx=6)
        ttk.Button(search_fr, text="検索", command=self.refresh_events).pack(side=tk.LEFT)
        ttk.Button(search_fr, text="新規", command=self.new_event).pack(side=tk.LEFT, padx=(12,0))
        ttk.Button(search_fr, text="削除", command=self.delete_event).pack(side=tk.LEFT, padx=6)
        # ttk.Button(search_fr, text="Web参照", width=12, command=open_web_preview).pack(side=tk.RIGHT, padx=4)
        # ttk.Button(search_fr, text="Publish JSON", width=14, command=self.publish_json).pack(side=tk.RIGHT)

        self.events_tv = ttk.Treeview(lf, columns=("id","date","title","venue","form"), show="headings", height=12)
        self.events_tv.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        self.events_tv.heading("id",  text="id")
        self.events_tv.heading("date",  text="日付")
        self.events_tv.heading("title", text="タイトル")
        self.events_tv.heading("venue", text="会場")
        self.events_tv.heading("form", text="形態")
        self.events_tv.column("id",  width=5, anchor="w")
        self.events_tv.column("date",  width=40, anchor="w")
        self.events_tv.column("title", width=150, anchor="w")
        self.events_tv.column("venue", width=100, anchor="w")
        self.events_tv.column("form", width=30, anchor="w")
        self.events_tv.bind("<<TreeviewSelect>>", self.on_event_select)


        # # --- 右ペイン（イベント編集）

        # --- 出演者（lineup）
        lf = ttk.LabelFrame(right, text="出演者（イベント全体：lineup）")
        lf.pack(fill=tk.BOTH, expand=False, padx=4, pady=2)

        # 一覧
        self.lineup_tv = ttk.Treeview(
            lf,
            columns=("name","role"),   # ★ pos/guest を削除
            show="headings",
            height=4
        )
        self.lineup_tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6,0))
        for c, t, w in [
            ("name","名前",80),
            ("role","役割",220),
        ]:
            self.lineup_tv.heading(c, text=t)
            self.lineup_tv.column(c, width=w, anchor="w")
        
        self.lineup_tv.bind("<<TreeviewSelect>>", self.on_lineup_select)

        # 追加フォーム
        ln_fr = ttk.Frame(lf); ln_fr.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(ln_fr, text="メンバー:").grid(row=0, column=0, sticky="e")
        self.ln_mem_cb = ttk.Combobox(ln_fr, values=self.people_names, width=20)
        self.ln_mem_cb.grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(ln_fr, text="役割:").grid(row=0, column=2, sticky="e")
        # ★ Entry → Combobox（roles テーブルから）
        self.ln_role_cb = ttk.Combobox(ln_fr, values=self.role_names, width=15, state="readonly")
        self.ln_role_cb.grid(row=0, column=3, sticky="w", padx=4)

        # # 追加フォーム
        btn_ln = ttk.Frame(lf); btn_ln.pack(fill=tk.X, padx=6, pady=(0,6))
        ttk.Button(btn_ln, text="追加", command=self.add_lineup).pack(side=tk.LEFT)
        ttk.Button(btn_ln, text="削除", command=self.del_lineup).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_ln, text="更新",  command=self.update_lineup).pack(side=tk.LEFT, padx=6)

        # ▼ 並べ替えボタン（ここを追加）
        ttk.Button(btn_ln, text="▲上へ", command=lambda: self.move_lineup("up")).pack(side=tk.LEFT, padx=(12,0))
        ttk.Button(btn_ln, text="▼下へ", command=lambda: self.move_lineup("down")).pack(side=tk.LEFT, padx=6)

#        ttk.Button(btn_ln, text="現役メンバー追加", command=self.add_active_to_lineup)\
#        .pack(side=tk.LEFT, padx=12)


        # --- 対バン（bandsevent） ---
        bf = ttk.LabelFrame(right, text="対バン（bandsevent）")
        bf.pack(fill=tk.BOTH, expand=False, padx=4, pady=2)

        # 一覧
        self.band_tv = ttk.Treeview(
            bf,
            columns=("seq","act_name"),
            show="headings",
            height=4
        )
        self.band_tv.heading("seq", text="#")
        self.band_tv.heading("act_name", text="バンド名")
        self.band_tv.column("seq", width=10, anchor="w")
        self.band_tv.column("act_name", width=260, anchor="w")
        self.band_tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6,0))

        # 追加フォーム
        band_fr = ttk.Frame(bf)
        band_fr.pack(fill=tk.X, padx=6, pady=2)

        ttk.Label(band_fr, text="バンド名:").grid(row=0, column=0, sticky="e")
        self.band_cb = ttk.Combobox(
            band_fr,
            # values=[r["name"] for r in qall("SELECT DISTINCT name FROM bandsevent ORDER BY name COLLATE NOCASE")],
            values=[r["name"] for r in qall("SELECT name FROM acts ORDER BY name COLLATE NOCASE")],
            width=20
        )
        self.band_cb.grid(row=0, column=1, sticky="w", padx=4)

        # ボタン
        btn_bf = ttk.Frame(bf)
        btn_bf.pack(fill=tk.X, padx=6, pady=(0,6))

        ttk.Button(btn_bf, text="追加", command=self.add_band).pack(side=tk.LEFT)
        ttk.Button(btn_bf, text="削除", command=self.del_band).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_bf, text="▲上へ", command=lambda: self.move_band("up")).pack(side=tk.LEFT, padx=(12,0))
        ttk.Button(btn_bf, text="▼下へ", command=lambda: self.move_band("down")).pack(side=tk.LEFT, padx=6)




        # # セトリ
        # # --- ここから：セトリ UI（正しい順序） ---
        # sf = ttk.LabelFrame(right, text="セトリ編集（seqは常に1..N）")
        # sf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        # # 1) Treeview を“先に”作る
        # self.setlist_tv = ttk.Treeview(
        #     sf, columns=("seq","title","section","version"),
        #     show="headings", height=4
        # )
        # self.setlist_tv.heading("seq",     text="#")
        # self.setlist_tv.heading("title",   text="曲名")
        # self.setlist_tv.heading("section", text="セクション")
        # self.setlist_tv.heading("version", text="バージョン")
        # self.setlist_tv.column("seq",     width=10,  anchor="w")
        # self.setlist_tv.column("title",   width=150, anchor="w")
        # self.setlist_tv.column("section", width=50, anchor="w")
        # self.setlist_tv.column("version", width=50, anchor="w")

        # # 2) それから pack とバインド
        # self.setlist_tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)
        # self.setlist_tv.bind("<<TreeviewSelect>>", self.on_setlist_select)
        # self.setlist_tv.bind("<Double-1>", self.on_setlist_dblclick)  # ダブルクリックで演奏詳細を開く
        # # --- ここまで ---

        # self.setlist_tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)
        # for col, text, w in [("seq","#",60),("title","曲名",280),("section","セクション",120),("version","バージョン",160)]:
        #     self.setlist_tv.heading(col, text=text)
        #     self.setlist_tv.column(col, width=w, anchor="w")
        # self.setlist_tv.bind("<<TreeviewSelect>>", self.on_setlist_select)

        # --- セトリ UI ---
        sf = ttk.LabelFrame(right, text="セトリ編集（seqは常に1..N）")
        sf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        # 1) Treeview 作成（器を作る）
        self.setlist_tv = ttk.Treeview(
            sf, columns=("seq","title","section","version"),
            show="headings", height=4
        )

        # 2) 見た目の設定（★反映されていた「下の方」の数値を採用★）
        columns_info = [
            ("seq", "#", 60),
            ("title", "曲名", 280),
            ("section", "セクション", 120),
            ("version", "バージョン", 160)
        ]
        for col, text, w in columns_info:
            self.setlist_tv.heading(col, text=text)
            self.setlist_tv.column(col, width=w, anchor="w")

        # 3) 画面への配置と動作の設定
        self.setlist_tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)
        self.setlist_tv.bind("<<TreeviewSelect>>", self.on_setlist_select)
        self.setlist_tv.bind("<Double-1>", self.on_setlist_dblclick)


        # 追加・編集 UI
        add_fr = ttk.Frame(sf); add_fr.pack(fill=tk.X, padx=6, pady=(0,6))

        ttk.Label(add_fr, text="曲:").grid(row=1, column=0, sticky="w", pady=(6,0))
        self.song_cb = ttk.Combobox(add_fr, values=self.song_titles, width=20)
        self.song_cb.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6,0))
        ttk.Label(add_fr, text="Section:").grid(row=1, column=4, sticky="e", pady=(6,0))
        self.section_cb = ttk.Combobox(add_fr, values=["","main","encore","MC","SE","Video"], width=6)
        self.section_cb.grid(row=1, column=5, sticky="w", pady=(6,0), padx=(4,0))
        ttk.Label(add_fr, text="Version:").grid(row=1, column=6, sticky="e", pady=(6,0))
        self.version_var = tk.StringVar()
        ttk.Entry(add_fr, textvariable=self.version_var, width=10).grid(row=1, column=7, sticky="w", pady=(6,0), padx=(4,0))

        btn2_fr = ttk.Frame(sf); btn2_fr.pack(fill=tk.X, padx=6, pady=(4,6))
        ttk.Button(btn2_fr, text="追加", command=self.add_row).pack(side=tk.LEFT)
        ttk.Button(btn2_fr, text="削除", command=self.delete_row).pack(side=tk.LEFT, padx=6)
        # ★ 追加（ここから）
        ttk.Button(btn2_fr, text="▲上へ", command=lambda: self.move_setlist("up")).pack(side=tk.LEFT, padx=(12,0))
        ttk.Button(btn2_fr, text="▼下へ", command=lambda: self.move_setlist("down")).pack(side=tk.LEFT, padx=6)
        # ★ 追加（ここまで）
        ttk.Button(btn2_fr, text="更新", command=self.update_selected_row).pack(side=tk.LEFT, padx=12)

        # ステータス
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, foreground="#666").pack(anchor="w", padx=10, pady=(0,8))

    # ----- イベント一覧 -----
    def refresh_events(self):
        q = (self.q_var.get() or "").strip()

        if q:
            rows = qall("""
                SELECT e.id, e.date, e.title, v.name AS venue, e.form
                FROM events e
                LEFT JOIN venues v ON v.id = e.venue_id
                WHERE
                    e.title LIKE ?
                    OR e.date LIKE ?
                    OR v.name LIKE ?
                    OR e.form LIKE ?
                ORDER BY e.date DESC, e.id DESC
            """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))

        else:
            rows = qall("""
                SELECT e.id, e.date, e.title, v.name AS venue, e.form
                FROM events e
                LEFT JOIN venues v ON v.id = e.venue_id
                ORDER BY e.date DESC, e.id DESC
            """)
        # クリア
        self.events_tv.delete(*self.events_tv.get_children())

        # ここがポイント：values を (date, title, venue, form) の順に
        for r in rows:
            id  = r.get("id")  or ""
            date  = r.get("date")  or ""
            title = r.get("title") or ""
            venue = r.get("venue") or ""
            form  = r.get("form")  or ""   # 空＝NULL は "" で表示
            self.events_tv.insert(
                "", "end",
                iid=str(r["id"]),  # ← これが on_event_select の self.event_id になる
                values=(id, date, title, venue, form)
            )

    def on_event_select(self, _evt):
        sel = self.events_tv.selection()
        if not sel: return
        self.event_id = int(sel[0])
        ev = qone("SELECT * FROM events WHERE id=?", (self.event_id,))
        self.date_var.set(ev.get("date") or "")
        self.title_var.set(ev.get("title") or "")
        self.sub_var.set(ev.get("sub_title") or "")
        # venue_id -> name
        venue_name = qone("SELECT name FROM venues WHERE id=?", (ev.get("venue_id"),))
        self.venue_var.set(venue_name["name"] if venue_name else "")
        # Era
        era_id = ev.get("era_id")
        if era_id:
            name = qone("SELECT name FROM era WHERE id=?", (era_id,))
            self.era_var.set(name["name"] if name else "")
        else:
            self.era_var.set("")

        # Tour
        tour_id = ev.get("tour_id")
        if tour_id:
            name = qone("SELECT name FROM tour WHERE id=?", (tour_id,))
            self.tour_var.set(name["name"] if name else "")
        else:
            self.tour_var.set("")

        self.form_var.set(ev.get("form") or "")

        self.load_setlist()
        self.status.set(f"Event {self.event_id} loaded")
        self.load_lineup()
        self.load_band()


    def new_event(self):
        # 新規（未保存）モードへ
        self.event_id = None
        self.status.set("新規モード")

        # 1) 入力欄クリア
        self.date_var.set("")
        self.title_var.set("")
        self.sub_var.set("")
        self.venue_var.set("")
        self.form_var.set("")
        # 使っているなら Era / Tour も空に
        if hasattr(self, "era_var"):  self.era_var.set("")
        if hasattr(self, "tour_var"): self.tour_var.set("")

        # 2) 子テーブル（一覧）クリア
        if hasattr(self, "lineup_tv"):
            self.lineup_tv.delete(*self.lineup_tv.get_children())
        if hasattr(self, "band_tv"):
            self.band_tv.delete(*self.band_tv.get_children())
        if hasattr(self, "setlist_tv"):
            self.setlist_tv.delete(*self.setlist_tv.get_children())

        # 3) 追加フォーム側の入力もクリア（あれば）
        if hasattr(self, "band_var"):    self.band_var.set("")
        if hasattr(self, "ln_mem_var"):  self.ln_mem_var.set("")
        if hasattr(self, "ln_role_var"): self.ln_role_var.set("")
        if hasattr(self, "song_var"):    self.song_var.set("")
        if hasattr(self, "version_var"): self.version_var.set("")
        if hasattr(self, "at_seq_var"):  self.at_seq_var.set("")

        # 4) ★ ここが“末尾挿入”ポイント：イベント一覧の選択/フォーカス解除
        #    （見た目も「新規」であることを明確にする）
        if hasattr(self, "events_tv"):
            try:
                sel = self.events_tv.selection()
                if sel:
                    self.events_tv.selection_remove(sel)
                self.events_tv.focus("")          # フォーカス解除
                # self.events_tv.selection_set(())  # ← 完全に空にする別表記（任意）
            except Exception:
                # 万一 TV が未初期化でも落ちないように
                pass



    def save_event(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("入力不足", "タイトルは必須です")
            return

        date = self.date_var.get().strip() or None
        sub  = self.sub_var.get().strip() or None

        # venue: 名前→id
        vname = (self.venue_var.get() or "").strip()
        venue_id = self.venue_name_to_id.get(vname) if vname else None
        if vname and venue_id is None:
            messagebox.showwarning("未登録の会場", f"会場 '{vname}' はマスタにありません。")
            return

        # era/tour: 名前→id（両分岐で使うので先に取得）
        era_name  = (self.era_var.get()  or "").strip() if hasattr(self, "era_var")  else ""
        tour_name = (self.tour_var.get() or "").strip() if hasattr(self, "tour_var") else ""

        # これらの辞書が無い/未構築でも落ちないよう getattr で取得
        era_map  = getattr(self, "era_name_to_id",  {}) or {}
        tour_map = getattr(self, "tour_name_to_id", {}) or {}

        era_id  = era_map.get(era_name)   if era_name  else None
        tour_id = tour_map.get(tour_name) if tour_name else None

        if era_name and era_id is None:
            messagebox.showwarning("未登録の期", f"Era '{era_name}' はマスタにありません。")
            return
        if tour_name and tour_id is None:
            messagebox.showwarning("未登録のツアー", f"Tour '{tour_name}' はマスタにありません。")
            return

        # form: 空なら NULL、そうでなければ "BAND"/"IDOL"
        val_form = (self.form_var.get() or "").strip()
        db_form  = None if val_form == "" else val_form

        if self.event_id is None:
            # --- INSERT ---
            exec1("""
                INSERT INTO events(date, title, sub_title, venue_id, era_id, tour_id, form)
                VALUES(?, ?, ?, ?, ?, ?, ?)
            """, (date, title, sub, venue_id, era_id, tour_id, db_form))

            row = qone("SELECT id FROM events ORDER BY id DESC LIMIT 1")
            self.event_id = row["id"]

            self.refresh_events()
            self.events_tv.selection_set(str(self.event_id))
            self.events_tv.see(str(self.event_id))
            self.status.set(f"イベント作成: id={self.event_id}")

        else:
            # --- UPDATE ---
            exec1("""
                UPDATE events
                SET date=?, title=?, sub_title=?, venue_id=?, era_id=?, tour_id=?, form=?
                WHERE id=?
            """, (date, title, sub, venue_id, era_id, tour_id, db_form, self.event_id))

            self.refresh_events()
            self.events_tv.selection_set(str(self.event_id))
            self.events_tv.see(str(self.event_id))
            self.status.set("イベント更新")


    def delete_event(self):
        sel = self.events_tv.selection()
        if not sel:
            messagebox.showinfo("削除", "イベントを選択してください")
            return
        eid = int(sel[0])
        if not messagebox.askyesno("確認", f"イベント id={eid} を削除します。関連データ（setlist/performer/lineup/bandsevent）も削除されます。"):
            return
        exec1("DELETE FROM performer WHERE event_id=?", (eid,))
        exec1("DELETE FROM lineup WHERE event_id=?", (eid,))
        exec1("DELETE FROM bandsevent WHERE event_id=?", (eid,))
        exec1("DELETE FROM setlist WHERE event_id=?", (eid,))
        exec1("DELETE FROM events WHERE id=?", (eid,))
        self.event_id = None
        self.refresh_events()
        self.setlist_tv.delete(*self.setlist_tv.get_children())
        self.status.set(f"削除しました: id={eid}")

    # ----- セトリ -----
    def load_setlist(self):
        self.setlist_tv.delete(*self.setlist_tv.get_children())
        if not self.event_id: return
        rows = qall("""
          SELECT es.seq, s.title AS song_title, es.section, COALESCE(es.version,'') AS version
          FROM setlist es JOIN songs s ON s.id=es.song_id
          WHERE es.event_id=? ORDER BY es.seq
        """, (self.event_id,))
        for r in rows:
            self.setlist_tv.insert("", tk.END, iid=str(r["seq"]),
                                   values=(r["seq"], r["song_title"], r["section"] or "", r["version"] or ""))



    # def on_setlist_select(self, event=None):
    #     tv = self.setlist_tv
    #     sel = tv.selection()
    #     if not sel:
    #         return

    #     iid = sel[0]
    #     vals = tv.item(iid, "values")  # values = ("seq","title","section","version")
    #     seq = vals[0] if vals else ""

    #     # ここは「挿入UI撤去後でも落ちない」ようにガード
    #     if hasattr(self, "at_seq_var") and self.at_seq_var is not None:
    #         try:
    #             self.at_seq_var.set(str(seq))
    #         except Exception:
    #             pass

    #     section = vals[2] if len(vals) > 2 else ""
    #     version = vals[3] if len(vals) > 3 else ""
    #     self.section_cb.set(section)
    #     self.version_var.set(version)

    def on_setlist_select(self, event=None):
        """
        セトリの選択行が変わったときに、編集欄（Section/Version等）へ値を反映する。
        - Treeview values: ("seq", "title", "section", "version")
        - 挿入位置UI（at_seq_var）が無くても落ちない
        - 不正な行/空選択は安全に無視
        """
        tv = getattr(self, "setlist_tv", None)
        if tv is None:
            return

        sel = tv.selection()
        if not sel:
            return

        iid = sel[0]
        vals = tv.item(iid, "values") or ()
        # 安全に値を取り出す（不足時は空文字）
        seq_str   = vals[0] if len(vals) > 0 else ""
        title     = vals[1] if len(vals) > 1 else ""
        section   = vals[2] if len(vals) > 2 else ""
        version   = vals[3] if len(vals) > 3 else ""

        # seq は一応整数化の試み（失敗しても続行）
        try:
            seq = int(seq_str)
        except Exception:
            seq = None

        # 旧UIの残骸があっても落ちないように：存在するときだけセット
        if hasattr(self, "at_seq_var") and self.at_seq_var is not None:
            try:
                self.at_seq_var.set(seq_str if seq_str is not None else "")
            except Exception:
                pass

        # 編集欄へ反映
        # section Combobox
        if hasattr(self, "section_cb"):
            try:
                self.section_cb.set(section or "")
            except Exception:
                pass

        # version Entry(StringVar)
        if hasattr(self, "version_var"):
            try:
                self.version_var.set(version or "")
            except Exception:
                pass

        # もしタイトルもどこかへ表示/編集したい場合（任意）
        # if hasattr(self, "song_title_var"):
        #     try:
        #         self.song_title_var.set(title or "")
        #     except Exception:
        #         pass

        # ステータス等の表示（任意）
        if hasattr(self, "status") and seq is not None:
            try:
                self.status.set(f"選択: #{seq} {title}")
            except Exception:
                pass

    def move_setlist(self, direction: str) -> None:
        """
        セトリ（setlist_tv）の選択行を上下に移動する。

        - setlist の (event_id, seq) は performer から参照されているため、
        seq を隣と“安全に”スワップする（負の一時値を介して入れ替え）。
        - performer 側の seq も同一手順でスワップし、整合を保つ。
        - すべてを 1 接続・1 トランザクションで行い、foreign key の検査は
        COMMIT 時まで遅延（PRAGMA defer_foreign_keys=ON）させる。
        - UI は load_setlist() で再描画後、新しい位置（隣の seq）を再選択する。
        """
        # --- 前提チェック ---
        if not getattr(self, "event_id", None):
            self.status.set("先にイベントを保存してください。")
            return

        tv = self.setlist_tv
        sel = tv.selection()
        if not sel:
            self.status.set("セトリの行を選択してください。")
            return

        # 選択行の現在 seq を取得
        iid = sel[0]
        vals = tv.item(iid, "values")  # columns=("seq","title","section","version")
        try:
            cur_seq = int(vals[0])
        except Exception:
            self.status.set("内部エラー：seq が取得できません。")
            return

        # 上下端判定
        total = len(tv.get_children())
        if direction == "up":
            if cur_seq <= 1:
                return
            other_seq = cur_seq - 1
        elif direction == "down":
            if cur_seq >= total:
                return
            other_seq = cur_seq + 1
        else:
            # 想定外（"up"/"down" 以外）
            return

        ev_id = self.event_id

        # --- 1接続・1トランザクションでスワップ（FK遅延） ---
        con = None
        try:
            con = db_conn()  # あなたの実装：sqlite3.connect(...) + FK ON
            cur = con.cursor()

            # 外部キーON ＋ コミット時までFK検査を遅延
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.execute("PRAGMA defer_foreign_keys=ON;")
            cur.execute("BEGIN;")

            # setlist の seq を負の一時値経由でスワップ
            #   cur_seq -> -cur_seq
            cur.execute("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (-cur_seq, ev_id, cur_seq))
            #   other_seq -> cur_seq
            cur.execute("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (cur_seq, ev_id, other_seq))
            #   -cur_seq -> other_seq
            cur.execute("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (other_seq, ev_id, -cur_seq))

            # performer の seq を同様にスワップ
            cur.execute("UPDATE performer SET seq=? WHERE event_id=? AND seq=?", (-cur_seq, ev_id, cur_seq))
            cur.execute("UPDATE performer SET seq=? WHERE event_id=? AND seq=?", (cur_seq, ev_id, other_seq))
            cur.execute("UPDATE performer SET seq=? WHERE event_id=? AND seq=?", (other_seq, ev_id, -cur_seq))

            con.commit()

        except Exception as e:
            # どこかでエラー → ロールバック
            if con:
                try:
                    con.rollback()
                except Exception:
                    pass
            self.status.set(f"並べ替えに失敗: {e}")
            return
        finally:
            if con:
                try:
                    con.close()
                except Exception:
                    pass

        # --- UI 再描画 & 新しい位置を再選択 ---
        if hasattr(self, "load_setlist"):
            self.load_setlist()

        # 見た目上は選択行が隣へ移動したので、other_seq を選ぶ
        for iid2 in tv.get_children():
            v2 = tv.item(iid2, "values")
            if str(v2[0]) == str(other_seq):
                tv.selection_set(iid2)
                tv.focus(iid2)
                tv.see(iid2)
                break

        self.status.set(f"セトリを移動しました（#{cur_seq} ⇄ #{other_seq}）")


    def resequence_setlist(self) -> None:
        """setlist の seq を 1..N に詰め直し、performer.seq も追従させる。"""
        if not getattr(self, "event_id", None):
            return
        ev_id = self.event_id

        con = None
        try:
            con = db_conn()
            cur = con.cursor()
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.execute("PRAGMA defer_foreign_keys=ON;")
            cur.execute("BEGIN;")

            rows = cur.execute(
                "SELECT seq FROM setlist WHERE event_id=? ORDER BY seq",
                (ev_id,)
            ).fetchall()
            old_seqs = [r[0] for r in rows]
            mapping = {old: new for new, old in enumerate(old_seqs, start=1)}

            # setlist を負の一時値へ退避
            for old, new in mapping.items():
                if old != new:
                    cur.execute("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (-new, ev_id, old))
            # performer も負の一時値へ
            for old, new in mapping.items():
                if old != new:
                    cur.execute("UPDATE performer SET seq=? WHERE event_id=? AND seq=?", (-new, ev_id, old))
            # 正の新番号へ
            for old, new in mapping.items():
                if old != new:
                    cur.execute("UPDATE setlist SET seq=? WHERE event_id=? AND seq=?", (new, ev_id, -new))
                    cur.execute("UPDATE performer SET seq=? WHERE event_id=? AND seq=?", (new, ev_id, -new))

            con.commit()
        except Exception:
            if con:
                try: con.rollback()
                except Exception: pass
            raise
        finally:
            if con:
                try: con.close()
                except Exception: pass


    def add_row(self):
        if not getattr(self, "event_id", None):
            self.status.set("先にイベントを保存してください。")
            return

        # 入力取得
        song_title = (self.song_cb.get() or "").strip()
        if not song_title:
            self.status.set("曲を選択してください。")
            return

        section = (self.section_cb.get() or "").strip() or None
        version = (self.version_var.get() or "").strip() or None

        # 曲名→ID
        song_id = self.song_title_to_id.get(song_title)
        if song_id is None:
            self.status.set(f"未登録の曲です: {song_title}")
            return

        # 末尾の次の seq を取得（1..N の N+1 に追加）
        row = qone("SELECT COALESCE(MAX(seq), 0) AS maxseq FROM setlist WHERE event_id=?", (self.event_id,))
        next_seq = int(row["maxseq"]) + 1

        # INSERT（末尾）
        exec1("""
            INSERT INTO setlist(event_id, seq, song_id, section, version)
            VALUES (?, ?, ?, ?, ?)
        """, (self.event_id, next_seq, song_id, section, version))

        # 再読み込み & 末尾行を選択
        if hasattr(self, "load_setlist"):
            self.load_setlist()

            # 末尾選択（seq=next_seq の行）
            tv = self.setlist_tv
            for iid in tv.get_children():
                vals = tv.item(iid, "values")
                if str(vals[0]) == str(next_seq):
                    tv.selection_set(iid)
                    tv.focus(iid)
                    tv.see(iid)
                    break

        self.status.set(f"セトリに追加しました（#{next_seq}: {song_title}）")

    def move_row(self, direction):
        sel = self.setlist_tv.selection()
        if not sel:
            messagebox.showinfo("並べ替え", "行を選択してください")
            return
        seq = int(sel[0])
        if direction == "up":
            other = qone("SELECT seq FROM setlist WHERE event_id=? AND seq<? ORDER BY seq DESC LIMIT 1",
                         (self.event_id, seq))
        else:
            other = qone("SELECT seq FROM setlist WHERE event_id=? AND seq>? ORDER BY seq ASC LIMIT 1",
                         (self.event_id, seq))
        if not other: return
        oseq = other["seq"]
        exec1("""UPDATE setlist
                 SET seq = CASE WHEN seq=? THEN ? WHEN seq=? THEN ? END
                 WHERE event_id=? AND seq IN (?,?)""", (seq, oseq, oseq, seq, self.event_id, seq, oseq))
        resequence(self.event_id)
        self.load_setlist()
        self.setlist_tv.selection_set(str(oseq))
        self.setlist_tv.see(str(oseq))
        self.status.set("並べ替えました")

    def delete_row(self):
        """セトリの選択行を削除し、performer も同 seq を削除。
        その後、setlist/performer の seq を 1..N に詰め直し、UI を再描画する。
        """
        # イベント未保存ガード
        if not getattr(self, "event_id", None):
            messagebox.showinfo("削除", "先にイベントを保存してください。")
            return

        tv = self.setlist_tv
        sel = tv.selection()
        if not sel:
            messagebox.showinfo("削除", "行を選択してください")
            return

        # Treeview の values=("seq","title","section","version")
        iid = sel[0]
        vals = tv.item(iid, "values")
        try:
            seq = int(vals[0])  # ← ここが重要：iid ではなく values[0] が seq
        except Exception:
            messagebox.showwarning("削除", "内部エラー：seq が取得できません。")
            return

        # 確認ダイアログ
        if not messagebox.askyesno("確認", f"seq={seq} を削除します（同 seq の performer も削除）。続行しますか？"):
            return

        # performer → setlist の順で削除（FK順序）
        exec1("DELETE FROM performer WHERE event_id=? AND seq=?", (self.event_id, seq))
        exec1("DELETE FROM setlist   WHERE event_id=? AND seq=?", (self.event_id, seq))

        # 欠番が発生するので、連番詰め（FK整合のため一括トランザクションで行う版）
        try:
            self.resequence_setlist()
        except Exception as e:
            # ここで例外なら再描画だけして状況確認
            self.status.set(f"resequence 失敗: {e}")

        # 再描画
        self.load_setlist()

        # できれば「次の行」を再選択（UX向上）
        # 1) まず同じ seq を探す（削除で詰めたので、元 seq+1 が今の seq になってる）
        target_seq = seq  # 削除した位置に詰め直された行
        for iid2 in tv.get_children():
            v2 = tv.item(iid2, "values")
            if str(v2[0]) == str(target_seq):
                tv.selection_set(iid2)
                tv.focus(iid2)
                tv.see(iid2)
                break

        self.status.set(f"削除: seq={seq}")

    def move_lineup(self, direction):
        sel = self.lineup_tv.selection()
        if not sel:
            messagebox.showinfo("並べ替え", "出演者を選択してください")
            return

        iid = sel[0]  # "member_id::role"
        try:
            mid_str, role = iid.split("::", 1)
            mid = int(mid_str)
        except:
            messagebox.showerror("並べ替え", "内部キーが不正です")
            return

        # 並び（ord）を取得（NULLあれば先に採番）
        has_null = qall("SELECT 1 FROM lineup WHERE event_id=? AND ord IS NULL LIMIT 1", (self.event_id,))
        if has_null:
            self.resequence_lineup(self.event_id)

        rows = qall("""
        SELECT p.id AS member_id, l.role, COALESCE(l.ord,999999) AS ord
        FROM lineup l
        JOIN people p ON p.id = l.member_id
        WHERE l.event_id = ?
        ORDER BY l.ord, p.id
        """, (self.event_id,))


        idx = next((i for i, r in enumerate(rows)
                    if r["member_id"] == mid and r["role"] == role), None)
        if idx is None:
            return

        # 移動先
        if direction == "up":
            if idx == 0: return
            swap_idx = idx - 1
        else:
            if idx == len(rows) - 1: return
            swap_idx = idx + 1

        a = rows[idx]
        b = rows[swap_idx]

        # ord を入れ替え
        exec1("UPDATE lineup SET ord=? WHERE event_id=? AND member_id=? AND role=?",
            (b["ord"], self.event_id, a["member_id"], a["role"]))
        exec1("UPDATE lineup SET ord=? WHERE event_id=? AND member_id=? AND role=?",
            (a["ord"], self.event_id, b["member_id"], b["role"]))

        self.load_lineup()
        new_iid = f"{a['member_id']}::{a['role']}"
        self.lineup_tv.selection_set(new_iid)
        self.lineup_tv.see(new_iid)

    def reload_event_dropdowns(self):
        """Eventタブ内の Combobox をキャッシュ内容で更新する"""
        def _reload(combo, values):
            combo.configure(values=values)
        def _set_values(cb, values):
            cb.configure(values=values or [""])


        # 出演者フォーム
        _reload(self.ln_mem_cb,  self.people_names)
        _reload(self.ln_role_cb, self.role_names)

        # 対バン
        # _reload(self.band_cb, self.act_names)

        # 会場 / Era / Tour
        _reload(self.venue_cb, self.venue_names)
        _reload(self.era_cb,   self.era_names)
        _reload(self.tour_cb,  self.tour_names)

        # 曲（セトリ追加時）
        _reload(self.song_cb,  self.song_titles)

        # ★ acts はキャッシュが無い → ここだけ直クエリにする
        acts = qall("SELECT name FROM acts ORDER BY name COLLATE NOCASE")
        _set_values(self.band_cb, [r["name"] for r in acts])



    def update_selected_row(self):
        sel = self.setlist_tv.selection()
        if not sel:
            messagebox.showinfo("更新", "行を選択してください")
            return
        seq = int(sel[0])
        section = self.section_cb.get().strip() or None
        version = self.version_var.get().strip() or None
        exec1("UPDATE setlist SET section=?, version=? WHERE event_id=? AND seq=?",
              (section, version, self.event_id, seq))
        self.load_setlist()
        self.setlist_tv.selection_set(str(seq))
        self.setlist_tv.see(str(seq))
        self.status.set("更新しました")

    def on_setlist_dblclick(self, _evt):
        sel = self.setlist_tv.selection()
        if not sel: return
        seq = int(sel[0])
        self.open_seq_editor(seq)

    # ----- Publish -----
    def publish_json(self):
        if not os.path.isfile(EXPORT_PY):
            messagebox.showwarning("Publish", f"export_json.py が見つかりません。\n{EXPORT_PY}")
            return
        try:
            proc = subprocess.run([sys.executable, EXPORT_PY], cwd=ROOT_DIR,
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                messagebox.showinfo("Publish", "JSONを書き出しました（/site/data）。")
            else:
                messagebox.showerror("Publish", f"失敗しました。\n{proc.stderr}")
        except Exception as e:
            messagebox.showerror("Publish", str(e))


    def open_seq_editor(self, seq: int):
        """(event_id, seq) の演奏詳細（section/version/note & performer）を編集する小ウィンドウ"""
        if not self.event_id:
            return

        # 対象演奏の現在値（song_id も取得）
        row = qone("""
        SELECT s.id AS song_id, s.title AS song_title,
                es.section, COALESCE(es.version,'') AS version,
                (SELECT 1 FROM pragma_table_info('setlist') WHERE name='note') AS has_note
        FROM setlist es JOIN songs s ON s.id=es.song_id
        WHERE es.event_id=? AND es.seq=?
        """, (self.event_id, seq))
        if not row:
            messagebox.showwarning("演奏詳細", "対象のセットリスト行が見つかりません")
            return
        song_id = row["song_id"]

        # Toplevel
        win = tk.Toplevel(self)
        win.title(f"演奏詳細  event={self.event_id}  #={seq}  {row['song_title']}")
        win.geometry("920x540")
        win.grab_set()

        # ───────────────── 上：曲の属性（この日のこの演奏） ─────────────────
        frm = ttk.LabelFrame(win, text="曲の属性（この日のこの演奏）")
        frm.pack(fill=tk.X, padx=10, pady=3)

        ttk.Label(frm, text=f"曲：{row['song_title']}").grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=2)

        ttk.Label(frm, text="Section:").grid(row=1, column=0, sticky="e", padx=6, pady=2)
        sec_var = tk.StringVar(value=row.get("section") or "")
        sec_cb  = ttk.Combobox(frm, textvariable=sec_var, values=["","main","encore","MC","SE","Video"], width=12)
        sec_cb.grid(row=1, column=1, sticky="w")

        ttk.Label(frm, text="Version:").grid(row=1, column=2, sticky="e", padx=6)
        ver_var = tk.StringVar(value=row.get("version") or "")
        ttk.Entry(frm, textvariable=ver_var, width=24).grid(row=1, column=3, sticky="w")

        # 任意：note（列があれば使う）
        note_present = bool(row.get("has_note"))
        ttk.Label(frm, text="Note:").grid(row=2, column=0, sticky="e", padx=6)
        note_var = tk.StringVar()
        if note_present:
            r2 = qone("SELECT COALESCE(note,'') AS note FROM setlist WHERE event_id=? AND seq=?",
                    (self.event_id, seq))
            note_var.set((r2 or {}).get("note",""))
        ttk.Entry(frm, textvariable=note_var, width=60).grid(row=2, column=1, columnspan=3, sticky="w")

        def save_set_attrs():
            section = sec_var.get().strip() or None
            version = ver_var.get().strip() or None
            if note_present:
                try:
                    exec1("UPDATE setlist SET section=?, version=?, note=? WHERE event_id=? AND seq=?",
                        (section, version, note_var.get().strip() or None, self.event_id, seq))
                except sqlite3.OperationalError:
                    exec1("UPDATE setlist SET section=?, version=? WHERE event_id=? AND seq=?",
                        (section, version, self.event_id, seq))
            else:
                exec1("UPDATE setlist SET section=?, version=? WHERE event_id=? AND seq=?",
                    (section, version, self.event_id, seq))
            self.load_setlist()  # 親のセトリ一覧も更新
            messagebox.showinfo("保存", "演奏の属性を保存しました。")

        ttk.Button(frm, text="保存", command=save_set_attrs).grid(row=3, column=3, sticky="e", pady=(6,4))


        # ───────────────── 下：performer（この演奏の出演） ─────────────────
        pf = ttk.LabelFrame(win, text=f"この演奏の出演（performer） #={seq}")
        pf.pack(fill=tk.BOTH, expand=True, padx=10, pady=3)

        # 1) 一覧（Treeview）
        cols = ("member","role")
        tv = ttk.Treeview(pf, columns=cols, show="headings", height=10)
        tv.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)

        tv.heading("member", text="名前")
        tv.column("member", width=80, anchor="w")

        tv.heading("role", text="役割")
        tv.column("role", width=80, anchor="w")

        def load_performers():
            tv.delete(*tv.get_children())
            rows = qall("""
                SELECT p.id AS member_id,
                       p.name AS member,
                       sl.role,
                       COALESCE(sl.ord, 999999) AS ord
                FROM performer sl
                JOIN people p ON p.id = sl.member_id
                WHERE sl.event_id = ? AND sl.seq = ?
                ORDER BY sl.ord, p.id
            """, (self.event_id, seq))

            for r in rows:
                iid = f"{r['member_id']}::{r['role']}"
                tv.insert("", tk.END, iid=iid, values=(r["member"], r["role"]))

        load_performers()

        # 2) 追加フォーム
        ctl = ttk.Frame(pf)
        ctl.pack(fill=tk.X, padx=6, pady=(0,6))

        ttk.Label(ctl, text="メンバー:").grid(row=0, column=0, sticky="e")
        mem_cb = ttk.Combobox(ctl, values=self.people_names, width=24)
        mem_cb.grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(ctl, text="役割:").grid(row=0, column=2, sticky="e")
        role_cb = ttk.Combobox(ctl, values=self.role_names, width=18, state="readonly")
        role_cb.grid(row=0, column=3, sticky="w", padx=4)

        # 選択行をフォームに反映
        def on_perf_select(_evt):
            sel = tv.selection()
            if not sel:
                return
            iid = sel[0]
            try:
                mid_str, role = iid.split("::", 1)
                mid = int(mid_str)
            except:
                return

            row = qone("""
                SELECT p.name, sl.role
                FROM performer sl
                JOIN people p ON p.id = sl.member_id
                WHERE sl.event_id=? AND sl.seq=? AND sl.member_id=? AND sl.role=?
            """, (self.event_id, seq, mid, role))

            if row:
                mem_cb.set(row["name"])
                role_cb.set(row["role"])

            # 更新用に保持
            nonlocal_edit_mid[0] = mid
            nonlocal_edit_old_role[0] = role

        # nonlocal 用の箱
        nonlocal_edit_mid = [None]
        nonlocal_edit_old_role = [""]

        tv.bind("<<TreeviewSelect>>", on_perf_select)

        # # 追加
        # def add_perf():
        #     name = (mem_cb.get() or "").strip()
        #     role = (role_cb.get() or "").strip()

        #     if not name or name not in self.people_name_to_id:
        #         messagebox.showwarning("入力不足", "メンバーをプルダウンから選んでください")
        #         return
        #     if not role:
        #         messagebox.showwarning("入力不足", "役割を選択してください")
        #         return

        #     mid = self.people_name_to_id[name]

        #     # ord の末尾を取得
        #     row = qone("""
        #         SELECT COALESCE(MAX(ord),0) AS mx
        #         FROM performer
        #         WHERE event_id=? AND seq=?
        #     """, (self.event_id, seq))
        #     new_ord = (row["mx"] if row else 0) + 1

        #     exec1("""
        #         INSERT OR REPLACE INTO performer(event_id, seq, member_id, role, ord)
        #         VALUES (?, ?, ?, ?, ?)
        #     """, (self.event_id, seq, mid, role, new_ord))

        #     load_performers()



        # --- NEW add_perf() 安定版 ---------------------------
        def add_perf():
            name = (mem_cb.get() or "").strip()
            role = (role_cb.get() or "").strip()

            if not name or name not in self.people_name_to_id:
                messagebox.showwarning("入力不足", "メンバーをプルダウンから選んでください")
                return
            if not role:
                messagebox.showwarning("入力不足", "役割を選択してください")
                return

            mid = self.people_name_to_id[name]

            row = qone("""
                SELECT COALESCE(MAX(ord),0) AS mx
                FROM performer
                WHERE event_id=? AND seq=?
            """, (self.event_id, seq))
            new_ord = (row["mx"] if row else 0) + 1

            exec1("""
                INSERT OR IGNORE INTO performer(event_id, seq, member_id, role, ord)
                VALUES (?, ?, ?, ?, ?)
            """, (self.event_id, seq, mid, role, new_ord))

            exec1("""
                UPDATE performer
                SET ord=?
                WHERE event_id=? AND seq=? AND member_id=? AND role=?
            """, (new_ord, self.event_id, seq, mid, role))

            load_performers()
        # -----------------------------------------------------






        # 更新
        def update_perf():
            mid = nonlocal_edit_mid[0]
            old_role = nonlocal_edit_old_role[0]

            if mid is None:
                messagebox.showinfo("更新", "更新する行を選択してください")
                return

            new_role = (role_cb.get() or "").strip()
            if not new_role:
                messagebox.showwarning("更新", "役割を選択してください")
                return

            exec1("""
                UPDATE performer
                SET role=?
                WHERE event_id=? AND seq=? AND member_id=? AND role=?
            """, (new_role, self.event_id, seq, mid, old_role))

            load_performers()

        # 削除
        def del_perf():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("削除", "行を選択してください")
                return
            if not messagebox.askyesno("削除", "選択した出演者を削除しますか？"):
                return

            for iid in sel:
                try:
                    mid_str, role = iid.split("::", 1)
                    mid = int(mid_str)
                except:
                    continue

                exec1("""
                    DELETE FROM performer
                    WHERE event_id=? AND seq=? AND member_id=? AND role=?
                """, (self.event_id, seq, mid, role))

            load_performers()

        # # 並べ替え
        # def move_perf(direction):
        #     sel = tv.selection()
        #     if not sel:
        #         messagebox.showinfo("並べ替え", "行を選択してください")
        #         return

        #     iid = sel[0]
        #     try:
        #         mid_str, role = iid.split("::", 1)
        #         mid = int(mid_str)
        #     except:
        #         return

        #     rows = qall("""
        #         SELECT p.id AS member_id, sl.role, COALESCE(sl.ord,999999) AS ord
        #         FROM performer sl
        #         JOIN people p ON p.id=sl.member_id
        #         WHERE sl.event_id=? AND sl.seq=?
        #         ORDER BY sl.ord, p.id
        #     """, (self.event_id, seq))

        #     idx = next((i for i, r in enumerate(rows)
        #                 if r["member_id"] == mid and r["role"] == role), None)
        #     if idx is None:
        #         return

        #     if direction == "up":
        #         if idx == 0:
        #             return
        #         swap_idx = idx - 1
        #     else:
        #         if idx == len(rows) - 1:
        #             return
        #         swap_idx = idx + 1

        #     a = rows[idx]
        #     b = rows[swap_idx]

        #     exec1("""
        #         UPDATE performer SET ord=?
        #         WHERE event_id=? AND seq=? AND member_id=? AND role=?
        #     """, (b["ord"], self.event_id, seq, a["member_id"], a["role"]))

        #     exec1("""
        #         UPDATE performer SET ord=?
        #         WHERE event_id=? AND seq=? AND member_id=? AND role=?
        #     """, (a["ord"], self.event_id, seq, b["member_id"], b["role"]))

        #     load_performers()
        #     tv.selection_set(iid)
        #     tv.see(iid)


        # --- NEW move_perf() 安定版 --------------------------
        def move_perf(direction):
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("並べ替え", "行を選択してください")
                return

            iid = sel[0]
            try:
                mid_str, role = iid.split("::", 1)
                mid = int(mid_str)
            except:
                return

            rows = qall("""
                SELECT p.id AS member_id, sl.role, COALESCE(sl.ord,999999) AS ord
                FROM performer sl
                JOIN people p ON p.id=sl.member_id
                WHERE sl.event_id=? AND sl.seq=?
                ORDER BY sl.ord, p.id
            """, (self.event_id, seq))

            idx = next((i for i, r in enumerate(rows)
                        if r["member_id"] == mid and r["role"] == role), None)
            if idx is None:
                return

            if direction == "up":
                if idx == 0:
                    return
                swap_idx = idx - 1
            else:
                if idx == len(rows) - 1:
                    return
                swap_idx = idx + 1

            a = rows[idx]
            b = rows[swap_idx]

            # ★ 一時退避を使う安全swap
            exec1("""
                UPDATE performer SET ord=-1
                WHERE event_id=? AND seq=? AND member_id=? AND role=?
            """, (self.event_id, seq, a["member_id"], a["role"]))

            exec1("""
                UPDATE performer SET ord=?
                WHERE event_id=? AND seq=? AND member_id=? AND role=?
            """, (a["ord"], self.event_id, seq, b["member_id"], b["role"]))

            exec1("""
                UPDATE performer SET ord=?
                WHERE event_id=? AND seq=? AND member_id=? AND role=?
            """, (b["ord"], self.event_id, seq, a["member_id"], a["role"]))

            load_performers()
            tv.selection_set(iid)
            tv.see(iid)
        # -----------------------------------------------------



        # lineup → performer へ適用
        def apply_lineup_to_seq():
            rows = qall("""
                SELECT member_id, role, COALESCE(ord,999999) AS ord
                FROM lineup
                WHERE event_id=?
                ORDER BY ord, member_id
            """, (self.event_id,))

            if not rows:
                messagebox.showinfo("適用", "lineup が空です")
                return

            exec1("DELETE FROM performer WHERE event_id=? AND seq=?", (self.event_id, seq))

            for i, r in enumerate(rows, start=1):
                exec1("""
                    INSERT INTO performer(event_id, seq, member_id, role, ord)
                    VALUES (?, ?, ?, ?, ?)
                """, (self.event_id, seq, r["member_id"], r["role"], i))

            load_performers()
            # messagebox.showinfo("適用", "lineup の並び順どおりに適用しました。")

        # ボタン群
        ttk.Button(ctl, text="追加", command=add_perf).grid(row=0, column=7, sticky="w", padx=(8,0))
        ttk.Button(ctl, text="更新", command=update_perf).grid(row=0, column=8, sticky="w", padx=(8,0))
        ttk.Button(ctl, text="削除", command=del_perf).grid(row=0, column=9, sticky="w", padx=(8,0))

        ttk.Button(ctl, text="▲ 上へ", command=lambda: move_perf("up")).grid(row=1, column=7, sticky="w", pady=(6,0))
        ttk.Button(ctl, text="▼ 下へ", command=lambda: move_perf("down")).grid(row=1, column=8, sticky="w", pady=(6,0), padx=(8,0))

        ttk.Button(ctl, text="lineup → この演奏へ適用", command=apply_lineup_to_seq)\
            .grid(row=1, column=1, sticky="w", pady=(6,0))


    def load_lineup(self):
        self.lineup_tv.delete(*self.lineup_tv.get_children())
        if not self.event_id:
            return

        has_null = qall("SELECT 1 FROM lineup WHERE event_id=? AND ord IS NULL LIMIT 1", (self.event_id,))
        if has_null:
            self.resequence_lineup(self.event_id)

        rows = qall("""
        SELECT p.id AS member_id, p.name, l.role, COALESCE(l.ord,999999) AS ord
        FROM lineup l
        JOIN people p ON p.id = l.member_id
        WHERE l.event_id = ?
        ORDER BY l.ord, p.id
        """, (self.event_id,))
        for r in rows:
            iid = f"{r['member_id']}::{r['role'] or ''}"
            self.lineup_tv.insert(
                "",
                tk.END,
                iid=iid,
                values=(r["name"], r["role"] or "")
            )


    def add_lineup(self):
        if not self.event_id:
            messagebox.showinfo("lineup", "先にイベントを保存してください"); return

        name = (self.ln_mem_cb.get() or "").strip()
        role = (self.ln_role_cb.get() or "").strip()   # ★ Combobox から取得

        mid = self.people_name_to_id.get(name)
        if not mid:
            messagebox.showwarning("lineup", "メンバーをプルダウンから選んでください"); return
        if not role:
            messagebox.showwarning("lineup", "役割を選択してください"); return

        has_null = qall("SELECT 1 FROM lineup WHERE event_id=? AND ord IS NULL LIMIT 1", (self.event_id,))
        if has_null:
            self.resequence_lineup(self.event_id)
        row = qone("SELECT COALESCE(MAX(ord),0) AS mx FROM lineup WHERE event_id=?", (self.event_id,))
        new_ord = (row["mx"] if row else 0) + 1

        exec1("""
            INSERT OR REPLACE INTO lineup(event_id, member_id, role, ord)
            VALUES(?,?,?,?)
        """, (self.event_id, mid, role, new_ord))

        self.load_lineup()

    def update_lineup(self):
        if not self.event_id:
            return
        if not hasattr(self, "_edit_mid"):
            messagebox.showinfo("更新", "更新する行を選択してください")
            return

        new_role = self.ln_role_cb.get().strip()
        if not new_role:
            messagebox.showwarning("更新", "役割を選択してください")
            return

        exec1("""
            UPDATE lineup
            SET role = ?
            WHERE event_id=? AND member_id=? AND role=?
        """, (new_role, self.event_id, self._edit_mid, self._edit_old_role))

        self.load_lineup()


    def del_lineup(self):
        sel = self.lineup_tv.selection()
        if not sel: return
        if not messagebox.askyesno("削除", "選択した出演者を削除しますか？"): return
        for iid in sel:
            try:
                mid_str, role = iid.split("::", 1); mid = int(mid_str)
            except: continue
            exec1("DELETE FROM lineup WHERE event_id=? AND member_id=? AND role=?",
                (self.event_id, mid, role))
        # 削除後に採番してきれいに詰める
        self.resequence_lineup(self.event_id)
        self.load_lineup()

    def add_active_to_lineup(self):
        if not self.event_id:
            messagebox.showinfo("lineup", "先にイベントを保存してください"); return
        actives = qall("SELECT id FROM people WHERE left_on IS NULL OR TRIM(left_on)=''")
        if not actives:
            messagebox.showinfo("lineup", "現役メンバー（left_on 空）が見つかりません"); return

        for a in actives:
            # 役割は空で仮登録（重複は無視）
            exec1("""
                INSERT OR IGNORE INTO lineup(event_id, member_id, role, ord)
                VALUES(?,?,?,NULL)
            """, (self.event_id, a["id"], ""))

        self.resequence_lineup(self.event_id)
        self.load_lineup()

    def on_lineup_select(self, _evt):
        sel = self.lineup_tv.selection()
        if not sel:
            return

        iid = sel[0]  # "member_id::role"
        try:
            mid_str, role = iid.split("::", 1)
            mid = int(mid_str)
        except:
            return

        row = qone("""
            SELECT p.name, l.role
            FROM lineup l JOIN people p ON p.id=l.member_id
            WHERE l.event_id=? AND l.member_id=? AND l.role=?
        """, (self.event_id, mid, role))

        if row:
            self.ln_mem_cb.set(row["name"])
            self.ln_role_cb.set(row["role"])

        # 更新用に保持
        self._edit_mid = mid
        self._edit_old_role = role


    def resequence_lineup(self, event_id: int):
        """
        lineup の ord が NULL を含む場合、現在の表示順（ord→名前）で 1..N を振り直す。
        """
        rows = qall("""
        SELECT p.id AS member_id, l.role
        FROM lineup l JOIN people p ON p.id = l.member_id
        WHERE l.event_id = ?
        ORDER BY COALESCE(l.ord, 999999), p.id
        """, (event_id,))
        for i, r in enumerate(rows, start=1):
            exec1("UPDATE lineup SET ord=? WHERE event_id=? AND member_id=? AND role=?",
                (i, event_id, r["member_id"], r["role"]))


    # ----- 対バン（bandsevent） -----
    def load_band(self):
        self.band_tv.delete(*self.band_tv.get_children())
        if not self.event_id:
            return

        rows = qall("""
            SELECT b.seq, a.name AS act_name
            FROM bandsevent AS b
            JOIN acts AS a ON a.id = b.act_id
            WHERE b.event_id=?
            ORDER BY b.seq
        """, (self.event_id,))

        for r in rows:
            self.band_tv.insert(
                "",
                tk.END,
                iid=str(r["seq"]),
                values=(r["seq"], r["act_name"])
            )


    def add_band(self):
        if not self.event_id:
            messagebox.showinfo("追加", "先にイベントを保存してください")
            return

        act_name = self.band_cb.get().strip()
        if not act_name:
            messagebox.showwarning("入力不足", "バンド名を入力してください")
            return

        # acts から act_id を取得
        row = qone("SELECT id FROM acts WHERE name=?", (act_name,))
        if not row:
            messagebox.showwarning("エラー", f"acts に存在しないバンド名です: {act_name}")
            return

        act_id = row["id"]

        # 次の seq を決める
        mx = qone("SELECT COALESCE(MAX(seq),0) AS mx FROM bandsevent WHERE event_id=?", (self.event_id,))
        next_seq = (mx["mx"] or 0) + 1

        # bandsevent に act_id を保存
        exec1(
            "INSERT INTO bandsevent(event_id, seq, act_id) VALUES(?,?,?)",
            (self.event_id, next_seq, act_id)
        )

        # Combobox の候補更新（acts から取るので本来不要だが一応）
        if act_name not in self.band_cb["values"]:
            vals = list(self.band_cb["values"])
            vals.append(act_name)
            vals = sorted(vals, key=lambda x: x.lower())
            self.band_cb["values"] = vals

        self.load_band()
        self.status.set(f"対バン追加: {act_name}")


    # def del_band(self):
    #     sel = self.band_tv.selection()
    #     if not sel:
    #         messagebox.showinfo("削除", "対バンを選択してください")
    #         return
    #     seq = int(sel[0])

    #     exec1("DELETE FROM bandsevent WHERE event_id=? AND seq=?", (self.event_id, seq))

    #     # seq を詰める
    #     rows = qall("SELECT seq FROM bandsevent WHERE event_id=? ORDER BY seq", (self.event_id,))
    #     for i, r in enumerate(rows, start=1):
    #         exec1("UPDATE bandsevent SET seq=? WHERE event_id=? AND seq=?", (i, self.event_id, r["seq"]))

    #     self.load_band()
    #     self.status.set("対バン削除")

    # def del_band(self):
    #     sel = self.band_tv.focus()
    #     if not sel:
    #         return

    #     seq = int(sel)

    #     if not messagebox.askyesno("確認", "このバンドを削除しますか？"):
    #         return

    #     with tx():
    #         # 対象削除
    #         exec1(
    #             "DELETE FROM bandsevent WHERE event_id=? AND seq=?",
    #             (self.event_id, seq)
    #         )

    #         # seqを負数へ一時退避（←事故防止テク 👑）
    #         exec1(
    #             "UPDATE bandsevent SET seq = -seq WHERE event_id=?",
    #             (self.event_id,)
    #         )

    #         # 正しい順番で再採番
    #         rows = qall(
    #             "SELECT seq FROM bandsevent WHERE event_id=? ORDER BY seq DESC",
    #             (self.event_id,)
    #         )

    #         for new_seq, (old_seq,) in enumerate(rows, start=1):
    #             exec1(
    #                 "UPDATE bandsevent SET seq=? WHERE event_id=? AND seq=?",
    #                 (new_seq, self.event_id, old_seq)
    #             )

    #     self.load_band()


    def del_band(self):
        sel = self.band_tv.focus()
        if not sel:
            return

        seq = int(sel)

        if not messagebox.askyesno("確認", "このバンドを削除しますか？"):
            return

        with db_conn() as con:
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")

            # 対象削除
            cur.execute(
                "DELETE FROM bandsevent WHERE event_id=? AND seq=?",
                (self.event_id, seq)
            )

            # seq を一旦マイナスへ退避
            cur.execute(
                "UPDATE bandsevent SET seq = -seq WHERE event_id=?",
                (self.event_id,)
            )

            # 再採番
            rows = cur.execute(
                "SELECT seq FROM bandsevent WHERE event_id=? ORDER BY seq DESC",
                (self.event_id,)
            ).fetchall()

            for new_seq, row in enumerate(rows, start=1):
                old_seq = row[0]
                cur.execute(
                    "UPDATE bandsevent SET seq=? WHERE event_id=? AND seq=?",
                    (new_seq, self.event_id, old_seq)
                )

            con.commit()

        self.load_band()
        self.status.set("対バン削除")





    def move_band(self, direction):
        sel = self.band_tv.selection()
        if not sel:
            messagebox.showinfo("並べ替え", "対バンを選択してください")
            return

        seq = int(sel[0])  # 現在の行の seq（Treeview の item id = seq 前提）

        # となりの seq を取得
        if direction == "up":
            other = qone(
                "SELECT seq FROM bandsevent WHERE event_id=? AND seq<? ORDER BY seq DESC LIMIT 1",
                (self.event_id, seq)
            )
        else:
            other = qone(
                "SELECT seq FROM bandsevent WHERE event_id=? AND seq>? ORDER BY seq ASC LIMIT 1",
                (self.event_id, seq)
            )

        if not other:
            return  # 端（先頭/末尾）は何もしない

        oseq = int(other["seq"])

        # --- ここから：重複を作らない安全な入替え（2ステップ）---
        # アイデア：一旦 2行の seq を「負の値」にして衝突を避ける → その後 正に戻す
        #   1) cur(seq=a) -> -b, cur(seq=b) -> -a
        #   2) その event_id で負の seq を全部 正に戻す（-x -> x）
        with db_conn() as con:
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")

            # step1: 2行を負の値へ（この時点で他の行と重複しない）
            cur.execute(
                """
                UPDATE bandsevent
                SET seq = CASE
                            WHEN seq = ? THEN -?
                            WHEN seq = ? THEN -?
                            ELSE seq
                            END
                WHERE event_id = ?
                AND seq IN (?, ?)
                """,
                (seq, oseq, oseq, seq, self.event_id, seq, oseq)
            )

            # step2: 負の seq をすべて正に戻す（= 実質 a<->b の入れ替え完了）
            cur.execute(
                """
                UPDATE bandsevent
                SET seq = -seq
                WHERE event_id = ?
                AND seq < 0
                """,
                (self.event_id,)
            )

            con.commit()

        # 表示更新＆フォーカス
        self.load_band()
        self.band_tv.selection_set(str(oseq))
        self.band_tv.see(str(oseq))
        self.status.set("対バン並べ替え")




# ---------- 追加：共通CRUDエディタ ----------
class MasterEditor(ttk.Frame):
    """
    汎用マスター編集UI。
    - table: テーブル名（例: "people"）
    - pk:    主キー列名（例: "id"）
    - fields: [{"name":"name","label":"Name","notnull":True,"width":24, "unique":False}, ...]
    - order_by: ソート列（例: "name COLLATE NOCASE"）
    """
    def __init__(self, parent, table, pk, fields, order_by=None, on_changed=None, on_selected=None):
        super().__init__(parent)
        self.table = table
        self.pk = pk
        self.fields = fields
        self.order_by = order_by or fields[0]["name"]
        self.on_changed = on_changed  # 保存/削除後に呼ばれる（Eventタブのキャッシュ更新など）
        self.on_selected = on_selected  # ← 追加

        # 左：検索＋一覧、右：入力フォーム
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)

        self.columnconfigure(1, weight=1)  

        self.rowconfigure(1, weight=1)

        # 検索
        sfrm = ttk.Frame(self)
        sfrm.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=2)
        ttk.Label(sfrm, text="Search:").pack(side="left")
        self.var_search = tk.StringVar()
        ent = ttk.Entry(sfrm, textvariable=self.var_search, width=40)
        ent.pack(side="left", padx=6)
        ent.bind("<Return>", lambda e: self.reload())
        ttk.Button(sfrm, text="Find", command=self.reload).pack(side="left", padx=4)
        ttk.Button(sfrm, text="Reload", command=self.reload).pack(side="left", padx=4)

        # 一覧
        # cols = [self.pk] + [f["name"] for f in self.fields]
        # cols = [self.pk] + [f["name"] for f in self.fields if f.get("name") != "preview"]
        # 一覧に出す列を限定する
        if self.table == "people":
            cols = [self.pk, "name", "birthday", "joined_on", "left_on"]
        elif self.table == "acts":
            cols = [self.pk, "name"]
        else:
            cols = [self.pk] + [f["name"] for f in self.fields if f.get("name") != "preview"]

        self.tv = ttk.Treeview(self, columns=cols, show="headings", height=18)
        for c in cols:
            txt = next((f["label"] for f in self.fields if f["name"] == c), c.capitalize())
            self.tv.heading(c, text=txt)
            self.tv.column(c, width=150 if c != "note" else 240, anchor="w")
        self.tv.grid(row=1, column=0, sticky="nsew", padx=(6,3), pady=2)
        self.tv.bind("<<TreeviewSelect>>", self._on_select)

        ysb = ttk.Scrollbar(self, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscroll=ysb.set)
        ysb.grid(row=1, column=0, sticky="nse", padx=(0,6), pady=2)

        # # 入力フォーム
        # form = ttk.Frame(self)
        # form.grid(row=1, column=1, sticky="nsew", padx=(3,6), pady=2)
        # form.columnconfigure(1, weight=1)

        # self.var_pk = tk.StringVar()
        # ttk.Label(form, text=f"{self.pk} (auto)").grid(row=0, column=0, sticky="w")
        # ttk.Entry(form, textvariable=self.var_pk, state="readonly", width=6).grid(row=0, column=1, sticky="w", pady=2)

        # self.entries = {}
        # # ★ 画像参照保持（PhotoImage の GC 対策）
        # self._img_ref = None

        # for i, f in enumerate(self.fields, start=1):
        #     ttk.Label(form, text=f.get("label", f["name"])).grid(row=i, column=0, sticky="w")
        #     width = f.get("width", 30)

        #     # ★ 追加：preview 行だけは Label を置く（編集不可の表示領域）
        #     if f.get("name") == "preview":
        #         lbl = ttk.Label(form, text="No image")
        #         lbl.grid(row=i, column=1, sticky="w", pady=2)
        #         # entries には他と同じ辞書キーで「Label」を入れておく（後で参照するため）
        #         self.entries[f["name"]] = lbl
        #         continue

        #     if f.get("multiline"):
        #         txt = tk.Text(form, height=f.get("height", 4))
        #         txt.grid(row=i, column=1, sticky="nsew", pady=2)
        #         form.rowconfigure(i, weight=1)
        #         self.entries[f["name"]] = txt
        #     else:
        #         v = tk.StringVar()
        #         ent = ttk.Entry(form, textvariable=v, width=width)
        #         ent.grid(row=i, column=1, sticky="ew", pady=2)
        #         self.entries[f["name"]] = v

        # 入力フォーム
        form = ttk.Frame(self)
        form.grid(row=1, column=1, sticky="nsew", padx=(3,6), pady=2)

        form.rowconfigure(0, weight=1)     # 👈 これ超重要
        form.columnconfigure(0, weight=1)  # 👈 これもセット


        # 👇 追加：フォーム本体（伸びる領域）
        form_body = ttk.Frame(form)
        form_body.grid(row=0, column=0, sticky="nsew")
        form_body.columnconfigure(1, weight=1)

        self.var_pk = tk.StringVar()
        ttk.Label(form_body, text=f"{self.pk} (auto)").grid(row=0, column=0, sticky="w")
        ttk.Entry(form_body, textvariable=self.var_pk, state="readonly", width=6).grid(row=0, column=1, sticky="w", pady=2)

        self.entries = {}
        self._img_ref = None  # PhotoImage GC対策

        for i, f in enumerate(self.fields, start=1):
            ttk.Label(form_body, text=f.get("label", f["name"])).grid(row=i, column=0, sticky="w")
            width = f.get("width", 30)

            if f.get("name") == "preview":
                lbl = ttk.Label(form_body, text="No image")
                lbl.grid(row=i, column=1, sticky="w", pady=2)
                self.entries[f["name"]] = lbl
                continue

            if f.get("multiline"):
                txt = tk.Text(form_body, height=f.get("height", 4))
                txt.grid(row=i, column=1, sticky="nsew", pady=2)
                form_body.rowconfigure(i, weight=1)
                self.entries[f["name"]] = txt
            else:
                v = tk.StringVar()
                ent = ttk.Entry(form_body, textvariable=v, width=width)
                ent.grid(row=i, column=1, sticky="ew", pady=2)
                self.entries[f["name"]] = v



        # # ボタン
        # btnf = ttk.Frame(form)
        # btnf.grid(row=len(self.fields)+1, column=0, columnspan=2, sticky="ew", pady=(8,0))
        # ttk.Button(btnf, text="New", command=self.clear).pack(side="left", padx=4)
        # ttk.Button(btnf, text="Save", command=self.save).pack(side="left", padx=4)
        # ttk.Button(btnf, text="Delete", command=self.delete).pack(side="left", padx=4)

        # # ---- フォーム（入力エリア）
        # form_body = ttk.Frame(form)
        # form_body.grid(row=0, column=0, sticky="nsew")
        # form_body.columnconfigure(1, weight=1)

        # ---- ボタン固定エリア
        btnf = ttk.Frame(form)
        btnf.grid(row=999, column=0, sticky="ew", pady=(8,0))

        ttk.Button(btnf, text="New", command=self.clear).pack(side="left", padx=4)
        ttk.Button(btnf, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(btnf, text="Delete", command=self.delete).pack(side="left", padx=4)

        self.reload()


    def _db_fields(self):
        """DB に保存するフィールド（画面専用 preview を除外）"""
        return [f for f in self.fields if f.get("name") != "preview"]

    def _from_db(self, v):
        """DB値→UI表示用文字列。NULL(None) は空文字で見せる"""
        return "" if v is None else str(v)

    def _to_db(self, field_def, ui_value):
        """
        UI文字列→DB値。
        - notnull では空欄は save() 前の検証で弾くのでここではそのまま返す
        - nullable では空文字なら None にして DB に NULL を入れる
        """
        if ui_value is None:
            ui_value = ""  # UIレイヤでは None を扱わないが保険
        if not field_def.get("notnull") and str(ui_value).strip() == "":
            return None
        return ui_value


    def _collect_values(self):
        data = {}
        for f in self._db_fields():  # preview は対象外
            name = f["name"]
            w = self.entries.get(name)

            if w is None:
                ui_val = ""  # 画面に無い＝空欄扱い
            elif f.get("multiline"):
                ui_val = w.get("1.0", "end").rstrip("\n")
            else:
                ui_val = w.get()  # Entry/Combobox

            # 必須チェック（空白文字だけも不可）
            if f.get("notnull") and (str(ui_val).strip() == ""):
                raise ValueError(f"'{f.get('label', name)}' は必須です。")

            # 空欄→DBでは NULL（nullable のみ）
            data[name] = self._to_db(f, ui_val)

        return data

    def clear(self):
        self.var_pk.set("")
        for f in self.fields:
            name = f["name"]  # ← 先に name を定義する

            if name == "preview":
                # 画像ラベルを初期化
                self.entries[name].config(text="No image", image="")
                self._img_ref = None
                continue

            if f.get("multiline"):
                self.entries[name].delete("1.0", "end")
            else:
                self.entries[name].set("")
        self.tv.selection_remove(self.tv.selection())



    def _on_select(self, _):
        sel = self.tv.selection()
        if not sel:
            return

        item = self.tv.item(sel[0], option="values")  # [pk, col1, col2, ... (すべて文字列)]
        self.var_pk.set(item[0])

        db_fields = self._db_fields()
        expected = 1 + len(db_fields)
        # 開発時の検知（本番はログにするなどに変更可）
        if len(item) != expected:
            # print(f"[WARN] Treeview item length {len(item)} != expected {expected}")
            pass

        # UI は文字列で統一（reloadで _from_db されているため None は来ない）
        for i, f in enumerate(db_fields, start=1):
            name = f["name"]
            val = item[i] if i < len(item) else ""
            if f.get("multiline"):
                w = self.entries[name]
                w.delete("1.0", "end")
                w.insert("1.0", val)
            else:
                self.entries[name].set(val)

        # on_selected へ渡す値も UI表現（空欄は ""）で統一
        if callable(self.on_selected):
            record = {self.pk: item[0]}
            for i, f in enumerate(db_fields, start=1):
                record[f["name"]] = item[i] if i < len(item) else ""
            self.on_selected(record)

        # --- 画像プレビューは従来通り（必要部分だけ残す） ---
        lbl = self.entries.get("preview")
        if lbl is not None:
            rec_id = item[0]
            from os.path import join, isfile
            folder = next((fld.get("folder") for fld in self.fields
                        if fld.get("name") == "preview" and fld.get("folder")), self.table)
            img_path = join(BASE_DIR, "site", "image", f"{folder}_{rec_id}.webp")

            if not HAVE_PIL:
                lbl.config(text="Pillow not installed (WEBP disabled)", image="")
                self._img_ref = None
            elif not isfile(img_path):
                lbl.config(text=f"No image ({folder}_{rec_id}.webp not found)", image="")
                self._img_ref = None
            else:
                im = Image.open(img_path)
                im.thumbnail((320, 320))
                tkimg = ImageTk.PhotoImage(im)
                lbl.config(image=tkimg, text="")
                self._img_ref = tkimg



    def reload(self):
        self.tv.delete(*self.tv.get_children())
        kw = self.var_search.get().strip()
        db_fields = self._db_fields()
        cols = [self.pk] + [f["name"] for f in db_fields]

        if kw:
            # NULL は LIKE にヒットしないので COALESCE で空文字に
            where = " OR ".join([f"COALESCE({c}, '') LIKE ?" for c in cols if c != self.pk])
            args = tuple([f"%{kw}%"] * where.count("?"))
            sql = f"SELECT {', '.join(cols)} FROM {self.table} WHERE {where} ORDER BY {self.order_by}"
            rows = qall(sql, args)
        else:
            sql = f"SELECT {', '.join(cols)} FROM {self.table} ORDER BY {self.order_by}"
            rows = qall(sql)

        for r in rows:
            display_vals = [self._from_db(r.get(c)) for c in cols]  # None→"" にして表示
            self.tv.insert("", "end", values=display_vals)

    def save(self):
        try:
            data = self._collect_values()
        except ValueError as e:
            messagebox.showwarning("入力エラー", str(e))
            return

        pk_val = self.var_pk.get().strip()
        # names = [f["name"] for f in self.fields]
        names = [f["name"] for f in self.fields if f.get("name") != "preview"]
        if pk_val:
            # UPDATE
            sets = ", ".join([f"{n}=?" for n in names])
            args = [data[n] for n in names] + [pk_val]
            sql = f"UPDATE {self.table} SET {sets} WHERE {self.pk}=?"
        else:
            # INSERT
            cols = ", ".join(names)
            qms = ", ".join(["?"] * len(names))
            args = [data[n] for n in names]
            sql = f"INSERT INTO {self.table} ({cols}) VALUES ({qms})"

        try:
            with db_conn() as con:
                con.execute(sql, args)
                con.commit()
            self.reload()
            # 再選択（更新時は同じID、挿入時は最大IDを選ぶ簡易実装）
            if not pk_val:
                row = qone(f"SELECT MAX({self.pk}) AS id FROM {self.table}")
                if row and row["id"] is not None:
                    self.var_pk.set(str(row["id"]))
            if callable(self.on_changed):
                self.on_changed(self.table)
        except sqlite3.IntegrityError as e:
            messagebox.showerror("保存エラー", f"一意制約または外部キー制約エラーの可能性があります。\n\n{e}")
            return

    def delete(self):
        pk_val = self.var_pk.get().strip()
        if not pk_val:
            messagebox.showinfo("削除", "削除対象が選ばれていません。")
            return
        if not messagebox.askyesno("削除確認", "選択中のレコードを削除します。よろしいですか？"):
            return
        try:
            with db_conn() as con:
                con.execute(f"DELETE FROM {self.table} WHERE {self.pk}=?", (pk_val,))
                con.commit()
            self.clear()
            self.reload()
            if callable(self.on_changed):
                self.on_changed(self.table)
        except sqlite3.IntegrityError as e:
            messagebox.showerror("削除エラー", f"外部キー制約により削除できません。\n（他テーブルから参照されています）\n\n{e}")

# ---------- 追加：Eventタブと連動するキャッシュ更新フック ----------
def reload_event_caches(app_instance: "App"):
    """Eventタブが使う会場・曲・人・役割などのキャッシュを更新"""
    if not isinstance(app_instance, App):
        return
    app_instance.venues = qall("SELECT id, name FROM venues ORDER BY name COLLATE NOCASE")
    app_instance.songs  = qall("SELECT id, title FROM songs ORDER BY title COLLATE NOCASE")
    app_instance.people = qall("SELECT id, name FROM people ORDER BY name COLLATE NOCASE")
    app_instance.roles  = qall("SELECT id, role FROM roles  ORDER BY id")
    app_instance.people_names = [p["name"] for p in app_instance.people]
    app_instance.people_name_to_id = {p["name"]: p["id"] for p in app_instance.people}
    app_instance.song_titles = [s["title"] for s in app_instance.songs]
    app_instance.song_title_to_id = {s["title"]: s["id"] for s in app_instance.songs}
    app_instance.venue_names = [v["name"] for v in app_instance.venues]
    app_instance.venue_name_to_id = {v["name"]: v["id"] for v in app_instance.venues}
    app_instance.role_names = [r["role"] for r in app_instance.roles]

    # --- 追加：タブ切り替え時に Event タブなら全部リロード ---
    def _on_tab_changed(e):
        nb = e.widget
        current = nb.tab(nb.select(), "text")
        if current == "Event":
            # 1) DB → キャッシュ更新
            reload_event_caches(app)
            # 2) キャッシュ → Eventタブのプルダウン反映
            app.reload_event_dropdowns()


# if __name__ == "__main__":
#     root = tk.Tk()
#     root.title("Event Editor (SQLite / tkinter)")
#     root.geometry("1350x900")

if __name__ == "__main__":
    # ここで “無ければ作る／不足があれば補う”
    ensure_db()

    root = tk.Tk()
    root.title("Event Editor (SQLite / tkinter)")
    root.geometry("1280x800")
    root.protocol("WM_DELETE_WINDOW", _on_close)
    # app = App(root)
    # app.pack(fill="both", expand=True)
    # root.mainloop()

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    # Event タブ（既存の App をここに入れる）
    frame_event = ttk.Frame(notebook)
    notebook.add(frame_event, text="Event")

    app = App(frame_event)
    app.pack(fill="both", expand=True)

    # 以降：マスター編集タブを追加
    # 変更検知で Event タブのキャッシュを更新
    def _on_master_changed(_table):
        reload_event_caches(app)

    # People（左：CRUD、右：WEBPプレビュー）
    frame_people = ttk.Frame(notebook)
    notebook.add(frame_people, text="People")
    frame_people.columnconfigure(0, weight=0)
    frame_people.columnconfigure(1, weight=1)
    frame_people.rowconfigure(0, weight=1)



    # 左側：MasterEditor（行選択時に _people_on_selected を呼ぶ）
    left_wrap = ttk.Frame(frame_people)
    left_wrap.grid(row=0, column=0, sticky="nsew", padx=(8,6), pady=3)
    MasterEditor(
        left_wrap,
        table="people",
        pk="id",
        fields=[
            {"name":"name",      "label":"Name", "notnull":True, "width":10},
            {"name":"birthday",  "label":"生誕月日", "width":15},
            {"name":"joined_on", "label":"加入年月日", "width":15},
            {"name":"left_on",   "label":"卒業年月日", "width":15},
            # {"name":"note",      "label":"Note", "width":60},

            # --- ここからSNS（あるものだけ入れてOK。空なら保存してもそのまま空） ---
            {"name":"x",         "label":"X (https://x.com/...)",                 "width":40},
            {"name":"instagram", "label":"Instagram (https://instagram.com/...)", "width":40},
            {"name":"threads",   "label":"Threads (https://www.threads.net/@...)", "width":40},
            {"name":"facebook",  "label":"Facebook (https://facebook.com/...)",    "width":40},
            {"name":"youtube",   "label":"YouTube (https://youtube.com/@...)",     "width":40},
            {"name":"tiktok",    "label":"TikTok (https://www.tiktok.com/@...)",   "width":40},

            # 画像プレビュー（画面用フィールド：DBには保存しない）
            {"name":"preview",   "label":"Preview"},
        ],
        order_by="name COLLATE NOCASE",
        on_changed=_on_master_changed
        # on_selected=_people_on_selected  # ← 追加
    ).pack(fill="both", expand=True)



    # Act
    frame_act = ttk.Frame(notebook)
    notebook.add(frame_act, text="Act")
    MasterEditor(
        frame_act,
        table="acts",
        pk="id",
        fields=[
            {"name":"name", "label":"Act Name", "notnull":True, "width":30},
            {"name":"url", "label":"URL", "width":40},

            # SNS（people と同じ構成）
            {"name":"x",         "label":"X (https://x.com/...)",                 "width":40},
            {"name":"instagram", "label":"Instagram (https://instagram.com/...)", "width":40},
            {"name":"threads",   "label":"Threads (https://www.threads.net/@...)", "width":40},
            {"name":"facebook",  "label":"Facebook (https://facebook.com/...)",    "width":40},
            {"name":"youtube",   "label":"YouTube (https://youtube.com/@...)",     "width":40},
            {"name":"tiktok",    "label":"TikTok (https://www.tiktok.com/@...)",   "width":40},

            # preview（DB には保存しない）
            {"name":"preview", "label":"Preview", "folder":"act"},
        ],
        order_by="name COLLATE NOCASE",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # Venue
    frame_venue = ttk.Frame(notebook)
    notebook.add(frame_venue, text="Venue")
    MasterEditor(
        frame_venue,
        table="venues",
        pk="id",
        fields=[
            {"name":"name", "label":"Venue Name", "notnull":True, "width":30},
            {"name":"url", "label":"URL", "width":36},
            {"name":"note", "label":"Note", "width":60},
            {"name":"preview", "label":"Preview", "folder":"venue"},
        ],
        order_by="name COLLATE NOCASE",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # Song
    frame_song = ttk.Frame(notebook)
    notebook.add(frame_song, text="Song")
    MasterEditor(
        frame_song,
        table="songs",
        pk="id",
        fields=[
            {"name":"title", "label":"Title", "notnull":True, "width":40},
            {"name":"preview", "label":"Preview", "folder":"song"},
            ],
        order_by="title COLLATE NOCASE",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # Roles（役割マスター）
    frame_roles = ttk.Frame(notebook)
    notebook.add(frame_roles, text="Roles")
    MasterEditor(
        frame_roles,
        table="roles",
        pk="id",
        fields=[{"name":"role", "label":"Role", "notnull":True, "width":30}],
        order_by="id",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # Era（期）
    frame_era = ttk.Frame(notebook)
    notebook.add(frame_era, text="Era")
    MasterEditor(
        frame_era,
        table="era",
        pk="id",
        fields=[
            {"name":"name", "label":"Era Name", "notnull":True, "width":28},
            {"name":"start_on", "label":"Start (YYYY-MM-DD)", "width":18},
            {"name":"end_on", "label":"End (YYYY-MM-DD)", "width":18},
            {"name":"memo", "label":"Memo", "width":60},
            {"name":"preview", "label":"Preview", "folder":"era"},
        ],
        order_by="start_on",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # Tour
    frame_tour = ttk.Frame(notebook)
    notebook.add(frame_tour, text="Tour")
    MasterEditor(
        frame_tour,
        table="tour",
        pk="id",
        fields=[
            {"name":"name", "label":"Tour Name", "notnull":True, "width":28},
            {"name":"start_on", "label":"Start (YYYY-MM-DD)", "width":18},
            {"name":"end_on", "label":"End (YYYY-MM-DD)", "width":18},
            {"name":"memo", "label":"Memo", "width":60},
            {"name":"preview", "label":"Preview", "folder":"tour"},
        ],
        order_by="start_on",
        on_changed=_on_master_changed
    ).pack(fill="both", expand=True)

    # # タブ切替時にもイベント側キャッシュを念のため更新
    # notebook.bind("<<NotebookTabChanged>>", lambda e: reload_event_caches(app))

    # Event タブに戻ったときだけキャッシュ→UI更新を行う
    def _on_tab_changed(e):
        nb = e.widget
        text = nb.tab(nb.select(), "text")
        if text == "Event":
            reload_event_caches(app)     # DB → メモリ
            app.reload_event_dropdowns() # メモリ → Combobox（UI反映）

    notebook.bind("<<NotebookTabChanged>>", _on_tab_changed)

    root.mainloop()