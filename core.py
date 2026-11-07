from datetime import date, timedelta

import hmac
import secrets
import sqlite3
import threading
import time
from functools import wraps
from flask import Flask, jsonify, request, g, session
from config import (
    ALLOW_REGISTRATION,
    BASE_DIR,
    DB_PATH,
    DEBUG,
    HOST,
    PORT,
    SECRET_KEY,
    SESSION_COOKIE_SECURE,
)

BASE = BASE_DIR
DB = DB_PATH
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["JSON_SORT_KEYS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
app.config["ALLOW_REGISTRATION"] = ALLOW_REGISTRATION
# Bound request body size and session lifetime; idle sessions expire.
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
app.config["PERMANENT_SESSION_REFRESH"] = True

_rate_limit_hits = {}
_rate_limit_lock = threading.Lock()

def rate_limit(max_requests, window_seconds):
    """Small in-process request limiter for the local application server."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            now = time.monotonic()
            key = (request.endpoint, request.remote_addr or "unknown")
            with _rate_limit_lock:
                hits = [hit for hit in _rate_limit_hits.get(key, []) if now - hit < window_seconds]
                if len(hits) >= max_requests:
                    retry_after = max(1, int(window_seconds - (now - hits[0])))
                    response = jsonify({"error": "Too many attempts. Please wait before trying again."})
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry_after)
                    _rate_limit_hits[key] = hits
                    return response
                hits.append(now)
                _rate_limit_hits[key] = hits
                for stale_key in [item for item, timestamps in _rate_limit_hits.items() if not timestamps or now - timestamps[-1] >= window_seconds]:
                    _rate_limit_hits.pop(stale_key, None)
            return view(*args, **kwargs)
        return wrapped
    return decorate

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token

@app.context_processor
def inject_template_security_values():
    return {"csrf_token": csrf_token, "allow_registration": app.config["ALLOW_REGISTRATION"]}

@app.after_request
def prevent_dynamic_response_caching(response):
    # API responses can contain user-specific state. Keep shared proxies/CDNs from
    # accidentally caching it; static files remain cacheable by CloudFront policies.
    if request.path.startswith("/api/") or request.path in {"/", "/login", "/register"}:
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    # Site-wide security hardening (safe defaults, no inline script breakage).
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # Allow Steam CDN images and same-origin resources; block everything else.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self' data:; "
        "frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
    )
    return response


@app.errorhandler(413)
def _request_too_large(_error):
    return jsonify({"error": "Request is too large. Please send a smaller payload."}), 413

def current_user_id():
    return int(session.get("user_id", 0))

def current_user():
    if not current_user_id(): return None
    return row("SELECT id, name, username, email FROM users WHERE id=?", (current_user_id(),))

@app.before_request
def require_login_for_personal_api():
    if request.path.startswith("/api/") and request.path not in ("/api/auth/me", "/api/auth/login", "/api/auth/register", "/api/auth/logout") and not request.path.startswith("/api/games") and not current_user_id():
        return jsonify({"error": "Please log in to continue", "auth_required": True}), 401

@app.before_request
def protect_state_changing_requests():
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None
    expected = session.get("_csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return jsonify({"error": "Your session security token is missing or expired. Reload the page and try again."}), 400

def conn():
    if "db" not in g:
        DB.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB, timeout=10, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 10000")
        g.db.execute("PRAGMA synchronous = NORMAL")
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

def rows(query, args=()): return [dict(x) for x in conn().execute(query, args).fetchall()]
def row(query, args=()):
    r = conn().execute(query, args).fetchone()
    return dict(r) if r else None
def ok(data, status=200): return jsonify(data), status
_ALLOWED_TABLES = {"users", "games", "user_games"}
_ALLOWED_ORDERS = {
    "g.rating DESC",
    "ug.added_at DESC",
    "g.rating DESC, g.title",
    "g.title",
    "g.release_date DESC",
    "g.playtime",
    "g.playtime ASC",
    "g.playtime DESC",
    "CASE ug.priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END",
}

def add_column(db, table, definition):
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table for migration: {table!r}")
    column = definition.split()[0]
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
        raise ValueError(f"Invalid column definition: {definition!r}")
    if column not in {item[1] for item in db.execute(f"PRAGMA table_info({table})")}:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

def game_sql(where="", order="g.rating DESC"):
    if order not in _ALLOWED_ORDERS:
        raise ValueError(f"Invalid order clause: {order!r}")
    if where and not (where.lstrip().startswith("WHERE ") and "WHERE" not in where.lstrip()[6:].upper()):
        raise ValueError(f"Invalid where clause: {where!r}")
    uid = int(current_user_id())
    return f'''SELECT g.*, group_concat(DISTINCT p.name) platforms,
    ug.status, ug.hours_played, ug.personal_rating, ug.notes, ug.completion, ug.favorite, ug.priority,
    ug.added_at, ug.last_played_at
    FROM games g LEFT JOIN game_platforms gp ON gp.game_id=g.id LEFT JOIN platforms p ON p.id=gp.platform_id
    LEFT JOIN user_games ug ON ug.game_id=g.id AND ug.user_id={uid} {where}
    GROUP BY g.id ORDER BY {order}'''

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10); db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA busy_timeout=10000")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT, email TEXT, password_hash TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, title TEXT UNIQUE NOT NULL, subtitle TEXT, genres TEXT NOT NULL, rating REAL, description TEXT, developer TEXT, publisher TEXT, release_date TEXT, playtime INTEGER, tags TEXT, cover TEXT, price REAL, original_price REAL);
    CREATE TABLE IF NOT EXISTS platforms(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS game_platforms(game_id INTEGER, platform_id INTEGER, PRIMARY KEY(game_id,platform_id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE, FOREIGN KEY(platform_id) REFERENCES platforms(id));
    CREATE TABLE IF NOT EXISTS user_games(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Backlog', hours_played REAL DEFAULT 0, personal_rating REAL, notes TEXT DEFAULT '', completion INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, priority TEXT DEFAULT 'Medium', added_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS wishlist(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS game_plans(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, plan_date TEXT NOT NULL, planned_hours REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id));
    CREATE TABLE IF NOT EXISTS achievements(id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS user_achievements(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, achievement_id INTEGER NOT NULL, completed INTEGER DEFAULT 0, UNIQUE(user_id,achievement_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS user_platforms(user_id INTEGER, platform_id INTEGER, PRIMARY KEY(user_id,platform_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(platform_id) REFERENCES platforms(id));
    CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS user_settings(user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id,key), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS user_collections(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,name), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS collection_games(collection_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(collection_id,game_id), FOREIGN KEY(collection_id) REFERENCES user_collections(id) ON DELETE CASCADE, FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS saved_views(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, filters_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,name), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE INDEX IF NOT EXISTS idx_user_games_status ON user_games(user_id,status); CREATE INDEX IF NOT EXISTS idx_plans_date ON game_plans(plan_date);
    CREATE INDEX IF NOT EXISTS idx_collection_games_game ON collection_games(game_id);
    CREATE INDEX IF NOT EXISTS idx_saved_views_user ON saved_views(user_id,name);
    ''')
    # Additive user-account migration. Existing demo data remains attached to user 1.
    for definition in ['username TEXT', 'email TEXT', 'password_hash TEXT', 'created_at TEXT', "profile_description TEXT NOT NULL DEFAULT ''"]:
        add_column(db, 'users', definition)
    add_column(db, 'user_games', 'last_played_at TEXT')
    # Keep the existing account and its data, but make the published demo login unusable.
    db.execute(
        "UPDATE users SET password_hash=NULL "
        "WHERE lower(coalesce(username,''))='vaultdemo' "
        "OR lower(coalesce(email,''))='demo@gamesvault.local'"
    )
    # Personal tables must allow the same game to belong to multiple accounts.
    # Fresh schemas already have composite unique constraints; older DBs are migrated below.
    for table, old_unique, create_sql, copy_sql in [
        ('user_games','game_id INTEGER NOT NULL UNIQUE',"CREATE TABLE user_games_new(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Backlog', hours_played REAL DEFAULT 0, personal_rating REAL, notes TEXT DEFAULT '', completion INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, priority TEXT DEFAULT 'Medium', added_at TEXT DEFAULT CURRENT_TIMESTAMP, last_played_at TEXT, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE)",'INSERT INTO user_games_new(id,user_id,game_id,status,hours_played,personal_rating,notes,completion,favorite,priority,added_at,last_played_at) SELECT id,user_id,game_id,status,hours_played,personal_rating,notes,completion,favorite,priority,added_at,last_played_at FROM user_games'),
        ('wishlist','game_id INTEGER NOT NULL UNIQUE',"CREATE TABLE wishlist_new(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE)",'INSERT INTO wishlist_new SELECT id,user_id,game_id,added_at FROM wishlist'),
        ('user_achievements','achievement_id INTEGER NOT NULL UNIQUE',"CREATE TABLE user_achievements_new(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, achievement_id INTEGER NOT NULL, completed INTEGER DEFAULT 0, UNIQUE(user_id,achievement_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE)",'INSERT INTO user_achievements_new SELECT id,user_id,achievement_id,completed FROM user_achievements')]:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] or ''
        if old_unique in sql:
            db.execute(f'DROP TABLE IF EXISTS {table}_new')
            db.execute(create_sql); db.execute(copy_sql); db.execute(f'DROP TABLE {table}'); db.execute(f'ALTER TABLE {table}_new RENAME TO {table}')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL')
    db.execute('CREATE INDEX IF NOT EXISTS idx_user_games_status ON user_games(user_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_plans_date ON game_plans(plan_date)')
    # Additive migration: this never recreates games or touches user-owned tables.
    for definition in ['steam_appid INTEGER', 'short_description TEXT', 'developers TEXT', 'publishers TEXT', 'cover_image TEXT', 'header_image TEXT', 'background_image TEXT', 'screenshots TEXT', 'discount INTEGER', 'store_url TEXT']:
        add_column(db, 'games', definition)
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_games_steam_appid ON games(steam_appid) WHERE steam_appid IS NOT NULL')
    # One-time identity bridge for the local fallback collection. Runtime sync itself
    # only ever matches rows by Steam App ID, never by a title.
    known_steam_ids = {'Elden Ring':1245620,'Hades':1145360,'Persona 5 Royal':1687950,'Cyberpunk 2077':1091500,'The Witcher 3':292030,'Baldur’s Gate 3':1086940,'Hollow Knight':367520,'Sekiro: Shadows Die Twice':814380,'Red Dead Redemption 2':1174180,'Stardew Valley':413150,'NieR:Automata':524220,'Final Fantasy VII Rebirth':2909400,'Metaphor: ReFantazio':2679460}
    for title, steam_appid in known_steam_ids.items():
        db.execute('UPDATE games SET steam_appid=? WHERE title=? AND steam_appid IS NULL', (steam_appid, title))

    # Backfill a reliable Steam portrait-art URL for existing catalogue rows.
    # This is intentionally limited to rows with a known Steam App ID and only
    # replaces the placeholder image, never a real/custom cover.
    db.execute(
        "UPDATE games SET cover_image='https://steamcdn-a.akamaihd.net/steam/apps/' || steam_appid || '/library_600x900_2x.jpg',"
        " cover=CASE WHEN cover IS NULL OR cover='' OR cover='/static/images/cover.svg' "
        " THEN 'https://steamcdn-a.akamaihd.net/steam/apps/' || steam_appid || '/library_600x900_2x.jpg' ELSE cover END "
        "WHERE steam_appid IS NOT NULL AND (cover_image IS NULL OR cover_image='' OR cover_image='/static/images/cover.svg')"
    )
    if not db.execute("SELECT 1 FROM games LIMIT 1").fetchone():
        db.execute("INSERT INTO users(id,name,username,email) VALUES(1,'Alex Morgan','demo','demo@localhost')")
        data=[
        ('Elden Ring','Rise, Tarnished.','Action RPG, Open World',4.8,'An epic fantasy action RPG in a vast world of danger and discovery.','FromSoftware','Bandai Namco','2022-02-25',58,'soulslike,fantasy,challenging',39.99,59.99),
        ('Hades','Escape the Underworld.','Roguelike, Action',4.9,'Battle out of the Underworld in this god-like rogue-like dungeon crawler.','Supergiant Games','Supergiant Games','2020-09-17',24,'mythology,indie,fast-paced',12.49,24.99),
        ('Persona 5 Royal','Take your heart.','JRPG, Social Sim',4.8,'Don the mask of Joker and join the Phantom Thieves of Hearts.','Atlus','SEGA','2022-10-21',102,'anime,story-rich,turn-based',35.99,59.99),
        ('Cyberpunk 2077','Welcome to Night City.','RPG, Action',4.5,'An open-world action-adventure story set in a megalopolis obsessed with power.','CD PROJEKT RED','CD PROJEKT RED','2020-12-10',62,'sci-fi,open-world,story-rich',29.99,59.99),
        ('The Witcher 3','Wild Hunt.','RPG, Open World',4.8,'A story-driven open world RPG set in a visually stunning fantasy universe.','CD PROJEKT RED','CD PROJEKT RED','2015-05-18',101,'fantasy,choices,story-rich',9.99,49.99),
        ('Baldur’s Gate 3','Gather your party.','RPG, Strategy',4.9,'An epic RPG of adventure, freedom, and choice.','Larian Studios','Larian Studios','2023-08-03',110,'fantasy,choices,co-op',47.99,59.99),
        ('Hollow Knight','Descend into Hallownest.','Metroidvania, Indie',4.8,'Forge your own path in a ruined kingdom of insects and heroes.','Team Cherry','Team Cherry','2017-02-24',28,'indie,atmospheric,challenging',7.49,14.99),
        ('Sekiro: Shadows Die Twice','Hesitation is defeat.','Action, Adventure',4.7,'Carve your own clever path to vengeance in a brutal late 1500s Japan.','FromSoftware','Activision','2019-03-22',30,'samurai,challenging,stealth',29.99,59.99),
        ('Red Dead Redemption 2','Outlaws for life.','Adventure, Open World',4.8,'America, 1899. Arthur Morgan and the Van der Linde gang are on the run.','Rockstar Games','Rockstar Games','2019-11-05',50,'western,story-rich,open-world',19.99,59.99),
        ('Minecraft','Build anything.','Sandbox, Survival',4.6,'Explore, build, and survive in an infinite blocky world.','Mojang Studios','Mojang Studios','2011-11-18',0,'creative,co-op,sandbox',14.99,29.99),
        ('Stardew Valley','Your new life awaits.','Simulation, RPG',4.8,'Turn overgrown fields into a thriving home in this open-ended country-life RPG.','ConcernedApe','ConcernedApe','2016-02-26',55,'cozy,farming,indie',8.99,14.99),
        ('NieR:Automata','Glory to mankind.','Action RPG, Sci-Fi',4.7,'Humanity has been driven from Earth by mechanical beings from another world.','PlatinumGames','Square Enix','2017-03-17',38,'sci-fi,anime,story-rich',23.99,39.99),
        ('Final Fantasy VII Rebirth','The unknown journey continues.','JRPG, Adventure',4.7,'Cloud and friends embark on a journey across a sprawling world.','Square Enix','Square Enix','2024-02-29',48,'fantasy,story-rich,anime',52.49,69.99),
        ('Metaphor: ReFantazio','Forge a new destiny.','JRPG, Fantasy',4.7,'Journey alongside fairy companion Gallica to lift the curse.','Studio Zero','Atlus','2024-10-11',65,'fantasy,turn-based,anime',48.99,69.99)]
        for i,x in enumerate(data,1): db.execute('INSERT INTO games(id,title,subtitle,genres,rating,description,developer,publisher,release_date,playtime,tags,cover,price,original_price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,*x[:3],x[3],x[4],x[5],x[6],x[7],x[8],x[9],'/static/images/cover.svg',x[10],x[11]))
        for p in ['PC','PlayStation 5','PlayStation 4','Xbox Series X/S','Xbox One','Nintendo Switch','Steam Deck']: db.execute('INSERT INTO platforms(name) VALUES(?)',(p,))
        for gid in range(1,15):
            for pid in ([1,2,7] if gid%3 else [1,6]): db.execute('INSERT INTO game_platforms VALUES(?,?)',(gid,pid))
        library=[(1,1,'Playing',18,4.8,'Exploring every side path.',35,1,'High'),(1,2,'Playing',9,4.9,'One more escape.',42,1,'High'),(1,3,'Backlog',0,None,'',0,0,'Medium'),(1,5,'Completed',116,5,'A modern classic.',100,1,'Low'),(1,7,'Backlog',0,None,'',0,0,'High'),(1,11,'Playing',14,4.7,'Relaxing evening farm.',25,0,'Low'),(1,9,'Dropped',6,3.5,'Will revisit later.',10,0,'Low'),(1,6,'Backlog',0,None,'',0,0,'Medium')]
        db.executemany('INSERT INTO user_games(user_id,game_id,status,hours_played,personal_rating,notes,completion,favorite,priority) VALUES(?,?,?,?,?,?,?,?,?)',library)
        db.executemany('INSERT OR IGNORE INTO wishlist(user_id,game_id) VALUES(?,?)',[(1,4),(1,13),(1,14)])
        db.executemany('INSERT INTO game_plans(user_id,game_id,plan_date,planned_hours) VALUES(?,?,?,?)',[(1,3,str(date.today()),2),(1,1,str(date.today()+timedelta(days=2)),3)])
        db.execute('INSERT INTO user_platforms VALUES(1,1)'); db.execute('INSERT INTO user_platforms VALUES(1,2)')

    # Re-run the identity bridge after seed insertion as well, so a completely new
    # database receives Steam IDs for the original starter games on its first run.
    for title, steam_appid in known_steam_ids.items():
        db.execute('UPDATE games SET steam_appid=? WHERE title=? AND steam_appid IS NULL', (steam_appid, title))

    # Ensure seeded and pre-existing Steam-backed games have useful portrait art
    # immediately, even before the first manual Steam refresh.
    db.execute(
        "UPDATE games SET cover_image='https://steamcdn-a.akamaihd.net/steam/apps/' || steam_appid || '/library_600x900_2x.jpg',"
        " cover=CASE WHEN cover IS NULL OR cover='' OR cover='/static/images/cover.svg' "
        " THEN 'https://steamcdn-a.akamaihd.net/steam/apps/' || steam_appid || '/library_600x900_2x.jpg' ELSE cover END "
        "WHERE steam_appid IS NOT NULL AND (cover_image IS NULL OR cover_image='' OR cover_image='/static/images/cover.svg')"
    )
    # Ensure seeded Minecraft has real official artwork too. Minecraft is not on Steam,
    # so it cannot use an App-ID-derived Steam CDN image.
    minecraft_art = 'https://www.minecraft.net/content/dam/minecraftnet/games/minecraft/key-art/NewKeyArt_Header.jpg'
    minecraft_background = 'https://www.minecraft.net/content/dam/minecraftnet/games/minecraft/key-art/New_Key-Art_04.jpg'
    db.execute(
        "UPDATE games SET cover_image=CASE WHEN cover_image IS NULL OR cover_image='' OR cover_image='/static/images/cover.svg' THEN ? ELSE cover_image END, "
        "header_image=CASE WHEN header_image IS NULL OR header_image='' THEN ? ELSE header_image END, "
        "background_image=CASE WHEN background_image IS NULL OR background_image='' OR background_image=? THEN ? ELSE background_image END, "
        "cover=CASE WHEN cover IS NULL OR cover='' OR cover='/static/images/cover.svg' THEN ? ELSE cover END "
        "WHERE lower(title)='minecraft'",
        (minecraft_art, minecraft_art, minecraft_art, minecraft_background, minecraft_art),
    )
    # Extra local catalogue records make the app useful before any network sync.
    # They are sample catalogue metadata; artwork URLs are derived from Steam App IDs.
    mock_games = [
        (730,'Counter-Strike 2','Tactical rounds, split-second decisions.','Action, FPS, Multiplayer',4.3,'A tactical competitive shooter built around team play and precision.','Valve','Valve','2023-09-27',34,'competitive,fps,multiplayer',0,0),
        (570,'Dota 2','Every match tells a different story.','Strategy, MOBA, Free to Play',4.2,'A deep competitive strategy game with a huge roster of heroes.','Valve','Valve','2013-07-09',72,'competitive,strategy,team-based',0,0),
        (620,'Portal 2','Think with portals.','Puzzle, Comedy, First-Person',4.8,'A mind-bending puzzle adventure built around portals and physics.','Valve','Valve','2011-04-18',12,'puzzle,comedy,co-op',9.99,9.99),
        (550,'Left 4 Dead 2','Survive together.','Action, Co-op, Horror',4.7,'Fight through a zombie apocalypse with a team of survivors.','Valve','Valve','2009-11-17',18,'co-op,zombies,action',9.99,9.99),
        (440,'Team Fortress 2','Choose your class.','Action, FPS, Multiplayer',4.4,'A class-based multiplayer shooter full of distinct roles.','Valve','Valve','2007-10-10',80,'fps,team-based,free-to-play',0,0),
        (105600,'Terraria','Dig, fight, explore, build.','Sandbox, Survival, Adventure',4.8,'Explore, build and fight through a procedurally generated world.','Re-Logic','Re-Logic','2011-05-16',46,'sandbox,crafting,survival',9.99,9.99),
        (504230,'Celeste','A mountain worth climbing.','Platformer, Indie, Precision',4.8,'Help Madeline survive her journey to the top of Celeste Mountain.','Maddy Makes Games','Extremely OK Games','2018-01-25',9,'precision-platformer,indie,story-rich',19.99,19.99),
        (588650,'Dead Cells','Kill, die, learn, repeat.','Roguelike, Action, Metroidvania',4.7,'A fast-paced action platformer with branching routes and permanent learning.','Motion Twin','Motion Twin','2018-08-07',28,'roguelike,action,challenging',24.99,24.99),
        (268910,'Cuphead','A hand-drawn boss rush.','Run and Gun, Difficult, Indie',4.6,'A classic-style action game focused on boss battles and precise play.','Studio MDHR','Studio MDHR','2017-09-29',11,'boss-rush,hand-drawn,challenging',19.99,19.99),
        (261570,'Ori and the Blind Forest','A beautiful journey home.','Metroidvania, Platformer, Adventure',4.7,'An atmospheric platform adventure about courage and connection.','Moon Studios','Xbox Game Studios','2015-03-11',11,'atmospheric,platformer,emotional',19.99,19.99),
        (753640,'Outer Wilds','Explore a solar system in a loop.','Exploration, Mystery, Adventure',4.8,'Explore a handcrafted solar system trapped in an ever-repeating cycle.','Mobius Digital','Annapurna Interactive','2019-05-28',16,'mystery,exploration,story-rich',24.99,24.99),
        (632360,'Risk of Rain 2','One more run.','Roguelike, Third-Person Shooter, Co-op',4.6,'Battle escalating alien hordes in a replayable 3D roguelike.','Hopoo Games','Gearbox Publishing','2020-08-11',24,'roguelike,co-op,action',24.99,24.99),
        (264710,'Subnautica','A world beneath the waves.','Survival, Open World, Underwater',4.7,'Survive and explore an alien ocean after crash-landing on a water planet.','Unknown Worlds','Unknown Worlds','2018-01-23',31,'underwater,survival,exploration',29.99,29.99),
        (275850,'No Man’s Sky','A universe to explore.','Open World, Exploration, Survival',4.3,'Explore an enormous procedurally generated universe.','Hello Games','Hello Games','2016-08-12',52,'space,crafting,exploration',59.99,59.99),
        (892970,'Valheim','Build your Viking saga.','Survival, Crafting, Open World',4.6,'Explore a mythic wilderness and build a Viking home alone or with friends.','Iron Gate AB','Coffee Stain Publishing','2021-02-02',38,'survival,crafting,co-op',19.99,19.99),
        (739630,'Phasmophobia','Something is in the house.','Horror, Online Co-op, Investigation',4.5,'Investigate paranormal locations with a team of ghost hunters.','Kinetic Games','Kinetic Games','2020-09-18',22,'horror,co-op,investigation',19.99,19.99),
        (945360,'Among Us','Find the impostor.','Social Deduction, Multiplayer, Casual',4.2,'Work together to prepare your spaceship while identifying impostors.','Innersloth','Innersloth','2018-11-16',16,'social-deduction,party,co-op',4.99,4.99),
        (1966720,'Lethal Company','Collect scrap. Stay alive.','Horror, Co-op, Survival',4.5,'Work for a company collecting scrap from abandoned moons.','Zeekerss','Zeekerss','2023-10-23',12,'horror,co-op,voice-chat',9.99,9.99),
        (2379780,'Balatro','Just one more hand.','Roguelike, Card Game, Strategy',4.9,'A poker-inspired roguelike deckbuilder full of surprising synergies.','LocalThunk','Playstack','2024-02-20',21,'cards,roguelike,strategy',14.99,14.99),
        (1794680,'Vampire Survivors','Survive the night.','Roguelike, Bullet Hell, Casual',4.8,'A time-survival game where smart choices create overwhelming power.','poncle','poncle','2022-10-20',19,'bullet-hell,casual,roguelite',4.99,4.99),
        (1868140,'DAVE THE DIVER','Dive by day, serve by night.','Adventure, RPG, Management',4.8,'Explore the Blue Hole and manage a sushi restaurant after dark.','MINTROCKET','MINTROCKET','2023-06-28',26,'management,underwater,adventure',19.99,19.99),
        (582010,'Monster Hunter: World','Hunt bigger prey.','Action, RPG, Co-op',4.5,'Track and hunt gigantic monsters in a living ecosystem.','CAPCOM','CAPCOM','2018-08-09',63,'hunting,co-op,action',29.99,29.99),
        (2050650,'Resident Evil 4','Survive the village.','Survival Horror, Action, Horror',4.8,'A reimagining of a survival-horror classic with modern action.','CAPCOM','CAPCOM','2023-03-24',17,'horror,action,story-rich',39.99,39.99),
        (870780,'Control Ultimate Edition','Reality is not what it seems.','Action, Supernatural, Adventure',4.5,'Investigate a shifting secret agency building in a supernatural action adventure.','Remedy Entertainment','505 Games','2020-08-27',19,'supernatural,story-rich,action',39.99,39.99),
        (782330,'DOOM Eternal','Rip and tear.','FPS, Action, Sci-Fi',4.6,'Fight through demonic forces with fast movement and an arsenal of weapons.','id Software','Bethesda Softworks','2020-03-20',16,'fps,fast-paced,action',39.99,39.99),
        (205100,'Dishonored','Revenge is a choice.','Stealth, Action, Immersive Sim',4.6,'Use supernatural abilities to take on a city consumed by corruption.','Arkane Studios','Bethesda Softworks','2012-10-09',14,'stealth,choices,immersive-sim',9.99,9.99),
        (480490,'Prey','Question everything.','Immersive Sim, Sci-Fi, Action',4.5,'Wake aboard a space station where an alien threat has taken over.','Arkane Studios','Bethesda Softworks','2017-05-04',22,'sci-fi,immersive-sim,choices',29.99,29.99),
        (646570,'Slay the Spire','Build the perfect deck.','Deckbuilder, Roguelike, Strategy',4.8,'Craft a unique deck and climb a procedurally generated spire.','Mega Crit','Mega Crit','2019-01-23',31,'deckbuilder,roguelike,strategy',24.99,24.99),
        (294100,'RimWorld','A story generator on the edge of space.','Colony Sim, Strategy, Survival',4.7,'Manage a colony of people with personalities on a distant world.','Ludeon Studios','Ludeon Studios','2018-10-17',84,'colony-sim,management,sandbox',34.99,34.99),
        (427520,'Factorio','Automate everything.','Automation, Base Building, Strategy',4.9,'Build factories, automate production and defend against local wildlife.','Wube Software','Wube Software','2020-08-14',61,'automation,base-building,optimization',35.00,35.00),
        (108600,'Project Zomboid','How will you die?','Survival, Zombies, Open World',4.6,'Try to survive a zombie apocalypse in a detailed sandbox world.','The Indie Stone','The Indie Stone','2013-11-08',45,'zombies,survival,sandbox',19.99,19.99),
        (255710,'Cities: Skylines','Build the city you imagine.','City Builder, Simulation, Strategy',4.7,'Build and manage a modern city with detailed simulation systems.','Colossal Order','Paradox Interactive','2015-03-10',40,'city-builder,simulation,management',29.99,29.99),
        (322330,'Don’t Starve Together','Together, survive the constant.','Survival, Co-op, Adventure',4.7,'Explore a strange world and survive its seasons and creatures with friends.','Klei Entertainment','Klei Entertainment','2016-04-21',29,'survival,co-op,crafting',14.99,14.99),
        (227300,'Euro Truck Simulator 2','The long haul.','Simulation, Driving, Open World',4.7,'Drive across Europe delivering cargo and growing a transport business.','SCS Software','SCS Software','2012-10-18',43,'driving,simulation,relaxing',19.99,19.99),
        (1551360,'Forza Horizon 5','Your road, your rules.','Racing, Open World, Driving',4.6,'Race through diverse landscapes in an open-world festival in Mexico.','Playground Games','Xbox Game Studios','2021-11-09',35,'racing,cars,open-world',59.99,59.99),
        (1172470,'Apex Legends','Squad up.','Battle Royale, FPS, Multiplayer',4.2,'A character-driven battle royale shooter with squad-based abilities.','Respawn Entertainment','Electronic Arts','2020-11-04',55,'battle-royale,fps,competitive',0,0),
        (230410,'Warframe','Become a space ninja.','Action RPG, Free to Play, Co-op',4.5,'Master powerful Warframes in a fast-paced science-fiction action game.','Digital Extremes','Digital Extremes','2013-03-25',68,'co-op,action,loot',0,0),
        (377160,'Fallout 4','War never changes.','RPG, Open World, Post-apocalyptic',4.5,'Explore a devastated wasteland and shape the fate of its settlements.','Bethesda Game Studios','Bethesda Softworks','2015-11-10',62,'post-apocalyptic,open-world,choices',19.99,19.99),
        (22380,'Fallout: New Vegas','Choose your side.','RPG, Open World, Choices Matter',4.6,'Navigate power struggles across the Mojave in a choice-driven RPG.','Obsidian Entertainment','Bethesda Softworks','2010-10-19',38,'choices,role-playing,open-world',9.99,9.99),
        (1328670,'Mass Effect Legendary Edition','Your crew. Your choices.','RPG, Sci-Fi, Story Rich',4.7,'Experience a galaxy-spanning trilogy with its single-player expansions.','BioWare','Electronic Arts','2021-05-14',70,'sci-fi,choices,story-rich',59.99,59.99),
        (1426210,'It Takes Two','Two players, one adventure.','Co-op, Platformer, Adventure',4.7,'A genre-bending platform adventure designed for two players.','Hazelight Studios','Electronic Arts','2021-03-26',13,'co-op,platformer,story-rich',39.99,39.99),
        (1222140,'Detroit: Become Human','Every choice matters.','Interactive Fiction, Choices Matter, Sci-Fi',4.7,'Shape a branching story about androids and humanity.','Quantic Dream','Quantic Dream','2020-06-18',12,'choices,story-rich,cinematic',39.99,39.99),
        (305620,'The Long Dark','Alone against the cold.','Survival, Open World, Exploration',4.4,'Survive a geomagnetic disaster in the unforgiving Canadian wilderness.','Hinterland Studio','Hinterland Studio','2017-08-01',33,'survival,exploration,atmospheric',19.99,19.99),
    ]
    base_platforms=['PC','PlayStation 5','PlayStation 4','Xbox Series X/S','Xbox One','Nintendo Switch','Steam Deck']
    for platform_name in base_platforms:
        db.execute('INSERT OR IGNORE INTO platforms(name) VALUES(?)',(platform_name,))
    for item in mock_games:
        appid,title,subtitle,genres,rating,description,developer,publisher,release_date,playtime,tags,price,original_price=item
        cover_url=f'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg'
        existing=db.execute('SELECT id,steam_appid FROM games WHERE title=?',(title,)).fetchone()
        if existing:
            gid=existing[0]
            if not existing[1]:
                db.execute("""UPDATE games SET steam_appid=?,
                    cover_image=CASE WHEN cover_image IS NULL OR cover_image='' THEN ? ELSE cover_image END,
                    cover=CASE WHEN cover IS NULL OR cover='' OR cover=? THEN ? ELSE cover END
                    WHERE id=?""",(appid,cover_url,'/static/images/cover.svg',cover_url,gid))
        else:
            db.execute('''INSERT OR IGNORE INTO games(steam_appid,title,subtitle,genres,rating,description,developer,publisher,release_date,playtime,tags,cover,price,original_price,cover_image,store_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(appid,title,subtitle,genres,rating,description,developer,publisher,release_date,playtime,tags,cover_url,price,original_price,cover_url,f'https://store.steampowered.com/app/{appid}/'))
            found=db.execute('SELECT id FROM games WHERE steam_appid=?',(appid,)).fetchone()
            if not found: continue
            gid=found[0]
        # Every catalogue item gets its supported platforms.
        db.execute("INSERT OR IGNORE INTO game_platforms(game_id,platform_id) SELECT ?,id FROM platforms WHERE name IN (?,?)",(gid,'PC','Steam Deck'))

    # (Single canonical Steam portrait-art backfill above covers pre-existing rows;
    # seeded rows already have the same URL via the identity bridge + mock insert.)
    # Ensure seeded Minecraft has real official artwork too. Minecraft is not on Steam,
    # so it cannot use an App-ID-derived Steam CDN image.
    minecraft_art = 'https://www.minecraft.net/content/dam/minecraftnet/games/minecraft/key-art/NewKeyArt_Header.jpg'
    minecraft_background = 'https://www.minecraft.net/content/dam/minecraftnet/games/minecraft/key-art/New_Key-Art_04.jpg'
    db.execute(
        "UPDATE games SET cover_image=CASE WHEN cover_image IS NULL OR cover_image='' OR cover_image='/static/images/cover.svg' THEN ? ELSE cover_image END, "
        "header_image=CASE WHEN header_image IS NULL OR header_image='' THEN ? ELSE header_image END, "
        "background_image=CASE WHEN background_image IS NULL OR background_image='' OR background_image=? THEN ? ELSE background_image END, "
        "cover=CASE WHEN cover IS NULL OR cover='' OR cover='/static/images/cover.svg' THEN ? ELSE cover END "
        "WHERE lower(title)='minecraft'",
        (minecraft_art, minecraft_art, minecraft_art, minecraft_background, minecraft_art),
    )
    db.commit(); db.close()
