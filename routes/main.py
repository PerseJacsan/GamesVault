"""HTML page routes for the local application shell."""

from flask import Blueprint, redirect, render_template
from core import app, current_user, ok

main_pages = Blueprint("main_pages", __name__)

@main_pages.route('/')
def home():
    # Keep the application shell completely separate from authentication pages.
    if not current_user():
        return redirect('/login')
    return render_template('index.html')


@main_pages.get('/login')
def login_page():
    if current_user():
        return redirect('/')
    return render_template('auth.html', mode='login')


@main_pages.get('/register')
def register_page():
    if not app.config["ALLOW_REGISTRATION"]:
        return ok({'error': 'Account registration is disabled for this deployment.'}, 404)
    if current_user():
        return redirect('/')
    return render_template('auth.html', mode='register')
