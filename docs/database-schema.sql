-- Structural snapshot of the current GamesVault database schema.
-- Contains schema only; no account or game rows are included.
-- The active app has no schema-version table; upgrades are additive in app.init_db().

CREATE INDEX idx_collection_games_game ON collection_games(game_id);

CREATE UNIQUE INDEX idx_games_steam_appid ON games(steam_appid) WHERE steam_appid IS NOT NULL;

CREATE INDEX idx_plans_date ON game_plans(plan_date);

CREATE INDEX idx_saved_views_user ON saved_views(user_id,name);

CREATE INDEX idx_user_games_status ON user_games(user_id,status);

CREATE UNIQUE INDEX idx_users_email ON users(email) WHERE email IS NOT NULL;

CREATE UNIQUE INDEX idx_users_username ON users(username) WHERE username IS NOT NULL;

CREATE TABLE achievements(id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);

CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE collection_games(collection_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(collection_id,game_id), FOREIGN KEY(collection_id) REFERENCES user_collections(id) ON DELETE CASCADE, FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);

CREATE TABLE game_plans(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, plan_date TEXT NOT NULL, planned_hours REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id));

CREATE TABLE game_platforms(game_id INTEGER, platform_id INTEGER, PRIMARY KEY(game_id,platform_id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE, FOREIGN KEY(platform_id) REFERENCES platforms(id));

CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT UNIQUE NOT NULL, subtitle TEXT, genres TEXT NOT NULL, rating REAL, description TEXT, developer TEXT, publisher TEXT, release_date TEXT, playtime INTEGER, tags TEXT, cover TEXT, price REAL, original_price REAL, steam_appid INTEGER, short_description TEXT, developers TEXT, publishers TEXT, cover_image TEXT, header_image TEXT, background_image TEXT, discount INTEGER, store_url TEXT, screenshots TEXT);

CREATE TABLE platforms(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);

CREATE TABLE saved_views(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, filters_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,name), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);

CREATE TABLE "user_achievements"(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, achievement_id INTEGER NOT NULL, completed INTEGER DEFAULT 0, UNIQUE(user_id,achievement_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE);

CREATE TABLE user_collections(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,name), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);

CREATE TABLE "user_games"(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Backlog', hours_played REAL DEFAULT 0, personal_rating REAL, notes TEXT DEFAULT '', completion INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, priority TEXT DEFAULT 'Medium', added_at TEXT DEFAULT CURRENT_TIMESTAMP, last_played_at TEXT, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);

CREATE TABLE user_platforms(user_id INTEGER, platform_id INTEGER, PRIMARY KEY(user_id,platform_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(platform_id) REFERENCES platforms(id));

CREATE TABLE user_settings(user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id,key), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);

CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT, email TEXT, password_hash TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, profile_description TEXT NOT NULL DEFAULT '');

CREATE TABLE "wishlist"(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,game_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE);
