"""Catalogue, game-detail, and optional Steam synchronization endpoints."""

import json
import os
import threading
import sqlite3
from flask import Blueprint, request
from core import conn, game_sql, ok, rate_limit, row, rows
from services.steam import SteamSyncError, sync_catalogue

games_api = Blueprint("games_api", __name__)

_sync_lock = threading.Lock()
_sync_in_progress = False

def _safe_int_env(name, default, lo, hi):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if lo <= value <= hi else default

@games_api.get('/api/games')
def games():
    q=request.args.get('q','').strip(); genre=request.args.get('genre',''); platform=request.args.get('platform',''); year=request.args.get('year',''); minimum=request.args.get('min_rating',''); sort=request.args.get('sort','rating')
    clauses=[]; args=[]
    if q:
        clauses.append("(g.title LIKE ? OR COALESCE(g.genres,'') LIKE ? OR COALESCE(g.tags,'') LIKE ? OR COALESCE(g.description,'') LIKE ? OR COALESCE(g.developer,'') LIKE ? OR COALESCE(g.publisher,'') LIKE ? OR COALESCE(g.short_description,'') LIKE ?)")
        args.extend(['%'+q[:100]+'%']*7)
    if genre: clauses.append('g.genres LIKE ?'); args.append('%'+genre[:80]+'%')
    if platform: clauses.append('p.name=?'); args.append(platform[:80])
    if year: clauses.append('substr(g.release_date,1,4)=?'); args.append(year[:4])
    if minimum:
        try:
            minimum = float(minimum)
        except (TypeError, ValueError):
            return ok({'error':'min_rating must be a number'},400)
        if not 0 <= minimum <= 5:
            return ok({'error':'min_rating must be between 0 and 5'},400)
        clauses.append('g.rating>=?'); args.append(minimum)
    order={'recent':'g.release_date DESC','alpha':'g.title','short':'g.playtime ASC','long':'g.playtime DESC','rating':'g.rating DESC'}.get(sort,'g.rating DESC')
    return ok(rows(game_sql((' WHERE '+ ' AND '.join(clauses)) if clauses else '',order),args))

@games_api.get('/api/games/<int:game_id>')
def game_detail(game_id):
    item=row(game_sql(' WHERE g.id=?'),(game_id,))
    if not item: return ok({'error':'Game not found'},404)
    try:
        item['screenshots'] = json.loads(item.get('screenshots') or '[]')
    except (TypeError, ValueError):
        item['screenshots'] = []
    item['in_library'] = bool(item.get('status')); return ok(item)

@games_api.get('/api/steam/status')
def steam_status():
    setting = row("SELECT value FROM app_settings WHERE key='steam_last_sync'")
    return ok({'last_sync': setting['value'] if setting else None, 'catalogue_count': row('SELECT COUNT(*) AS n FROM games WHERE steam_appid IS NOT NULL')['n'], 'total_games': row('SELECT COUNT(*) AS n FROM games')['n'], 'target': _safe_int_env('STEAM_CATALOGUE_TARGET',750,1,50000)})

@games_api.post('/api/steam/sync')
@rate_limit(2, 3600)
def steam_sync():
    global _sync_in_progress
    with _sync_lock:
        if _sync_in_progress:
            return ok({'success': False, 'error': 'A Steam catalogue refresh is already in progress. Please wait.'}, 409)
        _sync_in_progress = True
    try:
        result = sync_catalogue(conn())
        result['catalogue_count'] = row('SELECT COUNT(*) AS n FROM games WHERE steam_appid IS NOT NULL')['n']
        result['total_games'] = row('SELECT COUNT(*) AS n FROM games')['n']
        return ok(result)
    except SteamSyncError as exc:
        return ok({'success': False, 'error': str(exc)}, 503)
    except sqlite3.Error:
        return ok({'success': False, 'error': 'GamesVault could not save the Steam catalogue safely.'}, 500)
    finally:
        with _sync_lock:
            _sync_in_progress = False
