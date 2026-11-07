"""Library, wishlist, planning, statistics, and recommendation endpoints."""

import random
from datetime import date
from flask import Blueprint, request
from core import conn, current_user_id, game_sql, ok, row, rows

library_routes = Blueprint("library_routes", __name__)

_VALID_STATUSES = {'Playing', 'Backlog', 'Completed', 'Dropped'}
_VALID_PRIORITIES = {'Low', 'Medium', 'High'}

def _number(value, lo, hi, field):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f'{field} must be a number.'
    if not (lo <= number <= hi):
        return None, f'{field} must be between {lo} and {hi}.'
    return number, None

@library_routes.route('/api/library',methods=['GET','POST'])
def library():
    if request.method=='GET':
        status=request.args.get('status'); where=f' WHERE ug.user_id={current_user_id()}'+(' AND ug.status=?' if status else ''); return ok(rows(game_sql(where,'ug.added_at DESC'),(status,) if status else ()))
    d=request.get_json() or {}; gid=d.get('game_id')
    if not gid or not row('SELECT id FROM games WHERE id=?',(gid,)): return ok({'error':'Valid game_id required'},400)
    try:
        conn().execute("INSERT OR IGNORE INTO user_games(user_id,game_id,status) VALUES(?,?, 'Backlog')",(current_user_id(),gid)); conn().commit()
    except Exception:
        try: conn().rollback()
        except Exception: pass
        return ok({'error':'Could not add to library. Please try again.'},500)
    return ok({'message':'Added to library'},201)

@library_routes.route('/api/library/<int:game_id>',methods=['PUT','DELETE'])
def update_library(game_id):
    if request.method=='DELETE':
        conn().execute('DELETE FROM collection_games WHERE game_id=? AND collection_id IN (SELECT id FROM user_collections WHERE user_id=?)',(game_id,current_user_id()))
        conn().execute('DELETE FROM user_games WHERE user_id=? AND game_id=?',(current_user_id(),game_id)); conn().commit(); return ok({'message':'Removed from library'})
    d=request.get_json() or {}
    # Ensure the entry exists so UPDATE does not silently no-op.
    existing = row('SELECT 1 FROM user_games WHERE user_id=? AND game_id=?', (current_user_id(), game_id))
    if not existing:
        return ok({'error':'Game is not in your library'},404)
    updates = {}
    if 'status' in d:
        if d['status'] not in _VALID_STATUSES:
            return ok({'error':'status must be one of Playing, Backlog, Completed, Dropped'},400)
        updates['status'] = d['status']
    if 'hours_played' in d:
        value, error = _number(d['hours_played'], 0, 100000, 'hours_played')
        if error: return ok({'error': error}, 400)
        updates['hours_played'] = value
    if 'personal_rating' in d:
        if d['personal_rating'] is None:
            updates['personal_rating'] = None
        else:
            value, error = _number(d['personal_rating'], 0, 5, 'personal_rating')
            if error: return ok({'error': error}, 400)
            updates['personal_rating'] = value
    if 'notes' in d:
        if not isinstance(d['notes'], str) or len(d['notes']) > 2000:
            return ok({'error':'notes must be 2000 characters or fewer'},400)
        updates['notes'] = d['notes']
    if 'completion' in d:
        value, error = _number(d['completion'], 0, 100, 'completion')
        if error: return ok({'error': error}, 400)
        updates['completion'] = int(value)
    if 'favorite' in d:
        updates['favorite'] = 1 if d['favorite'] else 0
    if 'priority' in d:
        if d['priority'] not in _VALID_PRIORITIES:
            return ok({'error':'priority must be Low, Medium or High'},400)
        updates['priority'] = d['priority']
    if not updates: return ok({'error':'No valid fields supplied'},400)
    fields=list(updates)
    conn().execute('UPDATE user_games SET '+','.join(f'{x}=?' for x in fields)+' WHERE user_id=? AND game_id=?',[updates[x] for x in fields]+[current_user_id(),game_id]); conn().commit(); return ok({'message':'Library updated'})

@library_routes.route('/api/wishlist',methods=['GET','POST'])
def wishlist():
    if request.method=='GET': return ok(rows('SELECT g.*, w.id wishlist_id, w.added_at wishlist_added_at FROM wishlist w JOIN games g ON g.id=w.game_id WHERE w.user_id=? ORDER BY w.added_at DESC',(current_user_id(),)))
    gid=(request.get_json() or {}).get('game_id');
    if not gid: return ok({'error':'game_id required'},400)
    if not row('SELECT id FROM games WHERE id=?', (gid,)): return ok({'error':'Game not found'},404)
    conn().execute('INSERT OR IGNORE INTO wishlist(user_id,game_id) VALUES(?,?)',(current_user_id(),gid)); conn().commit(); return ok({'message':'Added to wishlist'},201)

@library_routes.delete('/api/wishlist/<int:game_id>')
def delete_wishlist(game_id): conn().execute('DELETE FROM wishlist WHERE user_id=? AND game_id=?',(current_user_id(),game_id)); conn().commit(); return ok({'message':'Removed from wishlist'})

@library_routes.get('/api/backlog')
def backlog():
    sort=request.args.get('sort','priority'); order={'priority':"CASE ug.priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END",'rating':'g.rating DESC','playtime':'g.playtime','release':'g.release_date DESC'}.get(sort,'g.rating DESC'); return ok(rows(game_sql(f" WHERE ug.user_id={current_user_id()} AND ug.status='Backlog'",order)))

@library_routes.put('/api/backlog/<int:game_id>')
def update_backlog(game_id): return update_library(game_id)

@library_routes.route('/api/planner',methods=['GET','POST'])
def planner():
    if request.method=='GET': return ok(rows('SELECT gp.*, g.title,g.cover,g.cover_image,g.header_image FROM game_plans gp JOIN games g ON g.id=gp.game_id WHERE gp.user_id=? ORDER BY plan_date',(current_user_id(),)))
    d=request.get_json() or {}
    if not all(d.get(k) for k in ['game_id','plan_date','planned_hours']): return ok({'error':'game_id, plan_date and planned_hours required'},400)
    if not row('SELECT id FROM games WHERE id=?', (d['game_id'],)): return ok({'error':'Game not found'},404)
    try:
        parsed = date.fromisoformat(str(d['plan_date']))
    except (TypeError, ValueError):
        return ok({'error':'plan_date must be a valid date (YYYY-MM-DD)'},400)
    value, error = _number(d['planned_hours'], 0.5, 24, 'planned_hours')
    if error: return ok({'error':error},400)
    conn().execute('INSERT INTO game_plans(user_id,game_id,plan_date,planned_hours) VALUES(?,?,?,?)',(current_user_id(),d['game_id'],parsed.isoformat(),value)); conn().commit(); return ok({'message':'Session planned'},201)

@library_routes.delete('/api/planner/<int:plan_id>')
def delete_plan(plan_id): conn().execute('DELETE FROM game_plans WHERE id=? AND user_id=?',(plan_id,current_user_id())); conn().commit(); return ok({'message':'Session removed'})

@library_routes.get('/api/statistics')
def statistics():
    stats=row('''SELECT count(*) total, sum(status!='Backlog') played, sum(status='Completed') completed, sum(status='Dropped') dropped, sum(status='Backlog') backlog, round(sum(hours_played),1) hours, round(avg(personal_rating),1) average_rating FROM user_games WHERE user_id=?''',(current_user_id(),)); stats['completion_rate']=round((stats['completed'] or 0)/(stats['total'] or 1)*100); stats['genres']=rows("SELECT g.genres, count(*) count FROM user_games ug JOIN games g ON g.id=ug.game_id WHERE ug.user_id=? GROUP BY g.genres ORDER BY count DESC LIMIT 5",(current_user_id(),)); stats['platforms']=rows('SELECT p.name, count(*) count FROM game_platforms gp JOIN platforms p ON p.id=gp.platform_id JOIN user_games ug ON ug.game_id=gp.game_id WHERE ug.user_id=? GROUP BY p.name',(current_user_id(),)); return ok(stats)

@library_routes.get('/api/recommendations')
def recommendation():
    # Recommend from the full catalogue, excluding games already in this user's library.
    # The client sends recently suggested IDs so consecutive rolls don't repeat.
    try:
        excluded={int(part) for part in request.args.get('exclude','').split(',') if part.strip().isdigit()}
    except ValueError:
        excluded=set()
    candidates=rows(game_sql(' WHERE ug.id IS NULL', 'g.rating DESC, g.title'))
    if not candidates:
        return ok({'error':'You have every game in the current catalogue in your library. Refresh the catalogue or remove a game to discover something new.'},404)
    unseen=[g for g in candidates if g['id'] not in excluded]
    history_reset=False
    if unseen:
        candidates=unseen
    elif excluded:
        history_reset=True  # The client has cycled through every available recommendation.
    owned=rows("SELECT g.genres,g.tags,ug.personal_rating,ug.favorite FROM user_games ug JOIN games g ON g.id=ug.game_id WHERE ug.user_id=?",(current_user_id(),))
    genre_weights={}; tag_weights={}
    for game in owned:
        weight=1.0 + (1.5 if game.get('favorite') else 0) + (max(0,(game.get('personal_rating') or 0)-3)*0.4)
        for token in (game.get('genres') or '').lower().replace('/',',').split(','):
            token=token.strip()
            if token: genre_weights[token]=genre_weights.get(token,0)+weight
        for token in (game.get('tags') or '').lower().replace(' ', '').split(','):
            token=token.strip()
            if token: tag_weights[token]=tag_weights.get(token,0)+weight
    scored=[]
    for game in candidates:
        genres=[v.strip().lower() for v in (game.get('genres') or '').replace('/',',').split(',') if v.strip()]
        tags=[v.strip().lower().replace(' ','') for v in (game.get('tags') or '').split(',') if v.strip()]
        genre_hits=[g for g in genres if g in genre_weights]
        tag_hits=[t for t in tags if t in tag_weights]
        rating=float(game.get('rating') or 0)
        score=rating*4 + sum(genre_weights[g] for g in genre_hits)*1.8 + sum(tag_weights[t] for t in tag_hits)*0.9 + random.uniform(0,4.5)
        reasons=[]
        if genre_hits: reasons.append('Shares genres with games you already enjoy: '+', '.join(genre_hits[:3]))
        if tag_hits: reasons.append('Matches tags from your library: '+', '.join(tag_hits[:3]))
        if rating>=4.5: reasons.append(f'Highly rated catalogue pick ({rating:.1f}/5)')
        if not reasons: reasons.append('A fresh pick from the full GamesVault catalogue')
        game['score']=round(score,2); game['reasons']=reasons[:3]
        scored.append(game)
    scored.sort(key=lambda g:g['score'],reverse=True)
    # Randomly choose among the stronger matches. History exclusion handles variety;
    # this small top pool keeps recommendations relevant rather than completely arbitrary.
    pool=scored[:min(12,len(scored))]
    pick=random.choice(pool)
    pick['history_reset']=history_reset
    pick['recommendation_pool']=len(candidates)
    return ok(pick)
