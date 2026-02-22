import sqlite3, json
from pathlib import Path
from collections import defaultdict

# ============================================
#  基本設定
# ============================================
ROOT = Path(__file__).resolve().parent
DB   = ROOT / "eventdata.db"
OUT  = ROOT / "site" / "data"
OUT.mkdir(parents=True, exist_ok=True)
OUT_IMAGE = ROOT / "site" / "image"
OUT_IMAGE.mkdir(parents=True, exist_ok=True)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def qall(cur, sql, args=()):
    cur.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def qone(cur, sql, args=()):
    cur.execute(sql, args)
    row = cur.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))

# ============================================
#  DB 読み込み
# ============================================
with sqlite3.connect(DB) as conn:
    cur = conn.cursor()

    # ----------------------------------------
    # イベント一覧（検索用）
    # ----------------------------------------
    events = qall(cur, """
        SELECT e.id, e.date, e.title, e.sub_title,
               e.venue_id,
               COALESCE(v.name,'') AS venue,
               e.era_id,
               COALESCE(er.name,'') AS era,
               e.tour_id,
               COALESCE(t.name,'') AS tour,
               COALESCE(e.form,'') AS form
        FROM events e
        LEFT JOIN venues v ON v.id = e.venue_id
        LEFT JOIN era er    ON er.id = e.era_id
        LEFT JOIN tour t    ON t.id = e.tour_id
        ORDER BY e.date
    """)
    write_json(OUT / "events.json", events)

    # ----------------------------------------
    # 曲一覧（検索用）
    # ----------------------------------------
    songs = qall(cur, "SELECT id, title FROM songs ORDER BY id")
    write_json(OUT / "songs.json", songs)

    # ----------------------------------------
    # メンバー一覧（検索用）
    # ----------------------------------------

    people = qall(cur, """
        SELECT id, name,
            COALESCE(birthday,'')  AS birthday,
            COALESCE(joined_on,'') AS joined_on,
            COALESCE(left_on,'')   AS left_on,
            COALESCE(x,'')           AS x,
            COALESCE(instagram,'')   AS instagram,
            COALESCE(threads,'')     AS threads,
            COALESCE(facebook,'')    AS facebook,
            COALESCE(youtube,'')     AS youtube,
            COALESCE(tiktok,'')      AS tiktok
        FROM people
        ORDER BY id
    """)
    write_json(OUT / "people.json", people)


    # ----------------------------------------
    # 会場一覧（検索用）
    # ----------------------------------------
    venues = qall(cur, "SELECT id, name, COALESCE(url,'') AS url FROM venues ORDER BY id")
    write_json(OUT / "venues.json", venues)

    # ----------------------------------------
    # 対バン一覧（検索用）
    # ----------------------------------------
    acts = qall(cur, """
        SELECT id, name
        FROM acts
        ORDER BY name COLLATE NOCASE
    """)
    write_json(OUT / "acts.json", acts)


    # ============================================
    #  イベント詳細（event/{id}.json）
    # ============================================
    for ev in events:
        eid = ev["id"]

        # --- 対バン ---
        acts_raw = qall(cur, """
            SELECT be.seq, a.name, be.act_id
            FROM bandsevent AS be
            JOIN acts AS a ON a.id = be.act_id
            WHERE be.event_id = ?
            ORDER BY be.seq
        """, (eid,))
        acts = [{
            "seq": a["seq"],
            "name": a["name"],
            "act_id": a["act_id"]
        } for a in acts_raw]


        # --- lineup ---
        lineup = qall(cur, """
            SELECT p.id AS person_id, p.name, l.role, COALESCE(l.position,'') AS position, l.ord
            FROM lineup l
            JOIN people p ON p.id = l.member_id
            WHERE l.event_id=?
            ORDER BY l.ord
        """, (eid,))


        # --- setlist（曲順 + performer） ---
        setlist_raw = qall(cur, """
            SELECT es.seq, es.section, es.version, COALESCE(es.note,'') AS note,
                   s.id AS song_id, s.title AS song_title
            FROM setlist es
            JOIN songs s ON s.id = es.song_id
            WHERE es.event_id=?
            ORDER BY es.seq
        """, (eid,))

        perf_raw = qall(cur, """
            SELECT pf.seq, p.id AS person_id, p.name, pf.role, COALESCE(pf.position,'') AS position
            FROM performer pf
            JOIN people p ON p.id = pf.member_id
            WHERE pf.event_id=?
            ORDER BY pf.seq, p.id
        """, (eid,))

        perf_map = defaultdict(list)
        for r in perf_raw:
            perf_map[r["seq"]].append({
                "person_id": r["person_id"],
                "name": r["name"],
                "role": r["role"],
                "position": r["position"]
            })

        setlist = []
        for s in setlist_raw:
            setlist.append({
                "seq": s["seq"],
                "song_id": s["song_id"],
                "song_title": s["song_title"],
                "section": s["section"],
                "version": s["version"],
                "note": s["note"],
                "performer": perf_map.get(s["seq"], [])
            })

        # --- イベント詳細 JSON ---
        event_json = {
            "id": ev["id"],
            "date": ev["date"],
            "title": ev["title"],
            "sub_title": ev["sub_title"],

            "venue": ev["venue"],
            "venue_id": ev["venue_id"],

            "era": ev["era"],
            "era_id": ev["era_id"],

            "tour": ev["tour"],
            "tour_id": ev["tour_id"],

            "form": ev.get("form", ""),

            "video_url": "",
            "acts": acts,
            "lineup": lineup,
            "setlist": setlist
        }

        write_json(OUT / "event" / f"{eid}.json", event_json)

    # ============================================
    #  曲 → 出演イベント一覧（song/{id}.json）
    # ============================================
    song_events = qall(cur, """
        SELECT s.id AS song_id, s.title,
              e.id AS event_id, e.date, e.title AS event_title,
              v.id AS venue_id,
              COALESCE(v.name,'') AS venue,
              COALESCE(e.form,'') AS form,
              es.seq, es.section, COALESCE(es.version,'') AS version
        FROM setlist es
        JOIN songs s  ON s.id = es.song_id
        JOIN events e ON e.id = es.event_id
        LEFT JOIN venues v ON v.id = e.venue_id
        ORDER BY s.id, e.date, es.seq
    """)

    song_map = defaultdict(list)
    song_title_map = {}
    for r in song_events:
        sid = r["song_id"]
        song_title_map[sid] = r["title"]
        song_map[sid].append({
            "event_id": r["event_id"],
            "date": r["date"],
            "title": r["event_title"],
            "venue_id": r["venue_id"],
            "venue": r["venue"],
            "form": r.get("form",""),
            "seq": r["seq"],
            "section": r["section"],
            "version": r["version"]
        })

    for sid, rows in song_map.items():
        write_json(OUT / "song" / f"{sid}.json", {
            "id": sid,
            "title": song_title_map[sid],
            "events": rows
        })


    # ============================================
    #  メンバー → 参加イベント一覧（people/{id}.json）
    # ============================================
    member_events = qall(cur, """
        SELECT DISTINCT
            e.id AS event_id,
            e.date,
            e.title,
            e.form,
            v.id AS venue_id,
            COALESCE(v.name,'') AS venue,
            p.id AS person_id,
            p.name AS person,
            l.role
        FROM lineup l
        JOIN events e ON e.id = l.event_id
        LEFT JOIN venues v ON v.id = e.venue_id
        JOIN people p ON p.id = l.member_id
        ORDER BY person_id, date
    """)

    mem_map  = defaultdict(list)
    mem_name = {}

    for r in member_events:
        pid = r["person_id"]
        mem_name[pid] = r["person"]
        mem_map[pid].append({
            "event_id": r["event_id"],
            "date": r["date"],
            "title": r["title"],
            "venue": r["venue"],
            "venue_id": r["venue_id"],
            "form": r["form"],
            "role": r["role"]
        })

    for p in people:
        pid = p["id"]
        rows = mem_map.get(pid, [])
        write_json(OUT / "people" / f"{pid}.json", {
            "id": pid,
            "name": p["name"],
            "events": rows,
            "birthday":  p["birthday"],
            "joined_on": p["joined_on"],
            "left_on":   p["left_on"],
            "x":         p["x"],
            "instagram": p["instagram"],
            "threads":   p["threads"],
            "facebook":  p["facebook"],
            "youtube":   p["youtube"],
            "tiktok":    p["tiktok"]
        })


    # ============================================
    #  会場 → 出演イベント一覧（venue/{id}.json）
    # ============================================
    venues_all = qall(cur, "SELECT id, name, COALESCE(url,'') AS url FROM venues ORDER BY id")

    for v in venues_all:
        vid = v["id"]

        evs = qall(cur, """
            SELECT id, date, title, COALESCE(form,'') AS form
            FROM events
            WHERE venue_id = ?
            ORDER BY date
        """, (vid,))

        venue_json = {
            "id": vid,
            "name": v["name"],
            "url": v["url"],
            "events": evs
        }

        write_json(OUT / "venue" / f"{vid}.json", venue_json)

    # ============================================
    #  Era → イベント一覧（era/{id}.json）
    # ============================================
    eras_all = qall(cur, "SELECT id, name FROM era ORDER BY id")

    for e in eras_all:
        eid = e["id"]

        evs = qall(cur, """
            SELECT id, date, title, COALESCE(form,'') AS form
            FROM events
            WHERE era_id = ?
            ORDER BY date
        """, (eid,))

        era_json = {
            "id": eid,
            "name": e["name"],
            "events": evs
        }

        write_json(OUT / "era" / f"{eid}.json", era_json)

    # ============================================
    #  Tour → イベント一覧（tour/{id}.json）
    # ============================================
    tours_all = qall(cur, "SELECT id, name FROM tour ORDER BY id")

    for t in tours_all:
        tid = t["id"]

        evs = qall(cur, """
            SELECT id, date, title, COALESCE(form,'') AS form
            FROM events
            WHERE tour_id = ?
            ORDER BY date
        """, (tid,))

        tour_json = {
            "id": tid,
            "name": t["name"],
            "events": evs
        }

        write_json(OUT / "tour" / f"{tid}.json", tour_json)

    # ============================================
    #  Act → 出演イベント一覧（act/{id}.json）
    # ============================================
    acts_all = qall(cur, """
        SELECT
            id,
            name,
            COALESCE(url,'') AS url,
            COALESCE(x,'') AS x,
            COALESCE(instagram,'') AS instagram,
            COALESCE(threads,'') AS threads,
            COALESCE(facebook,'') AS facebook,
            COALESCE(youtube,'') AS youtube,
            COALESCE(tiktok,'') AS tiktok
        FROM acts
        ORDER BY id
    """)

    for a in acts_all:
        aid = a["id"]

        evs = qall(cur, """
            SELECT b.event_id,
                   e.date,
                   e.title,
                   COALESCE(e.form,'') AS form,
                   b.seq
            FROM bandsevent b
            JOIN events e ON e.id = b.event_id
            WHERE b.act_id = ?
            ORDER BY e.date, b.seq
        """, (aid,))

        act_json = {
            "id": aid,
            "name": a["name"],
            "url": a["url"],
            "x": a["x"],
            "instagram": a["instagram"],
            "threads": a["threads"],
            "facebook": a["facebook"],
            "youtube": a["youtube"],
            "tiktok": a["tiktok"],
            "events": evs
        }


        write_json(OUT / "act" / f"{aid}.json", act_json)


    # ============================================
    #  setlist.json（検索用）
    # ============================================
    rows = qall(cur, """
        SELECT event_id, seq, song_id
        FROM setlist
        ORDER BY event_id, seq
    """)
    write_json(OUT / "setlist.json", rows)

    # ============================================
    #  lineup.json（検索用）
    # ============================================
    rows = qall(cur, """
        SELECT event_id, ord, member_id
        FROM lineup
        ORDER BY event_id, ord
    """)
    write_json(OUT / "lineup.json", rows)

    # bandsevent.json
    rows = qall(cur, """
        SELECT event_id, seq, act_id
        FROM bandsevent
        ORDER BY event_id, seq
    """)
    write_json(OUT / "bandsevent.json", rows)

print("JSON Export Completed.")
