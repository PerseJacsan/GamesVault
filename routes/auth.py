"""Authentication and account-preference endpoints."""

import re
import sqlite3
from flask import Blueprint, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from core import app, conn, current_user, current_user_id, ok, rate_limit, row, rows

auth_api = Blueprint("auth_api", __name__)

# Dummy hash so missing-user lookups take similar time to real checks.
_DUMMY_HASH = generate_password_hash("gamesvault-dummy-password-for-timing-parity")

@auth_api.post('/api/auth/register')
@rate_limit(3, 3600)
def register():
    if not app.config["ALLOW_REGISTRATION"]:
        return ok({'error': 'Account registration is disabled for this deployment.'}, 404)
    data=request.get_json() or {}; name=(data.get('name') or '').strip(); username=(data.get('username') or '').strip().lower(); email=(data.get('email') or '').strip().lower(); password=data.get('password') or ''
    if len(name)<2 or len(name)>60 or not re.fullmatch(r'[a-z0-9_.-]{3,24}',username) or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(password)<12 or len(password)>128:
        return ok({'error':'Enter a name (2-60 characters), valid email, username (3-24 letters/numbers/._-), and password of 12-128 characters.'},400)
    try:
        cur=conn().execute('INSERT INTO users(name,username,email,password_hash) VALUES(?,?,?,?)',(name,username,email,generate_password_hash(password)))
        uid=cur.lastrowid; conn().commit(); session.clear(); session['user_id']=uid; session['_csrf_token']=''
        return ok({'user':current_user()},201)
    except sqlite3.IntegrityError:
        return ok({'error':'That username or email is already registered.'},409)

@auth_api.post('/api/auth/login')
@rate_limit(5, 900)
def login():
    data=request.get_json() or {}; identity=(data.get('identity') or '').strip().lower(); password=data.get('password') or ''
    if len(password) > 128:
        return ok({'error':'Invalid username/email or password.'},401)
    user=row('SELECT * FROM users WHERE lower(username)=? OR lower(email)=?',(identity,identity))
    if not user or not user.get('password_hash') or not check_password_hash(user['password_hash'],password):
        # Timing parity: always run a password check even for unknown users.
        if not user or not user.get('password_hash'):
            check_password_hash(_DUMMY_HASH, password)
        return ok({'error':'Invalid username/email or password.'},401)
    session.clear(); session['user_id']=user['id']; session['_csrf_token']=''; return ok({'user':current_user()})

@auth_api.post('/api/auth/logout')
def logout(): session.clear(); return ok({'message':'Logged out'})

@auth_api.get('/api/auth/me')
def auth_me(): return ok({'user':current_user()})

@auth_api.route('/api/settings',methods=['GET','PUT'])
def settings():
    uid=current_user_id()
    if request.method=='GET':
        values={r['key']:r['value'] for r in rows('SELECT key,value FROM user_settings WHERE user_id=?',(uid,))}
        defaults={'theme':'dark','accent':'amber','compact_mode':'false','show_prices':'true','default_view':'grid','reduced_motion':'false','confirm_destructive':'true'}
        defaults.update(values); return ok({'settings':defaults,'user':current_user()})
    data=request.get_json() or {}; allowed={'theme':{'dark','light','system'},'accent':{'amber','violet','blue','green'},'compact_mode':{'true','false'},'show_prices':{'true','false'},'default_view':{'grid','list'},'reduced_motion':{'true','false'},'confirm_destructive':{'true','false'}}
    for key,choices in allowed.items():
        if key in data and str(data[key]) in choices:
            conn().execute('INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value',(uid,key,str(data[key])))
    conn().commit(); return ok({'message':'Settings saved'})

@auth_api.post('/api/auth/change-password')
@rate_limit(5, 900)
def change_password():
    data=request.get_json() or {}; old=data.get('current_password') or ''; new=data.get('new_password') or ''; user=row('SELECT password_hash FROM users WHERE id=?',(current_user_id(),))
    if not user or not user.get('password_hash') or not check_password_hash(user['password_hash'],old): return ok({'error':'Current password is incorrect.'},400)
    if len(new)<12 or len(new)>128: return ok({'error':'New password must contain 12-128 characters.'},400)
    if new == old: return ok({'error':'New password must differ from the current password.'},400)
    conn().execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(new),current_user_id())); conn().commit()
    # Rotate CSRF token so other clients must reload after a password change.
    session['_csrf_token']=''
    return ok({'message':'Password changed'})
