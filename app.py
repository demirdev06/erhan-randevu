from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'appointments.db'
SECRET_KEY = os.environ.get('ERHAN_SECRET_KEY', 'change-this-secret-key')
OWNER_USERNAME = os.environ.get('ERHAN_OWNER_USERNAME', 'erhan')
OWNER_PASSWORD = os.environ.get('ERHAN_OWNER_PASSWORD', 'Erhan123!')

app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET_KEY)


def get_db() -> sqlite3.Connection:
    db = getattr(g, '_database', None)
    if db is None:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        g._database = db
    return db


@app.teardown_appcontext
def close_connection(exception: Exception | None) -> None:
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            service TEXT NOT NULL,
            note TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Bekliyor',
            created_at TEXT NOT NULL
        )
        '''
    )
    db.commit()
    db.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('owner_logged_in'):
            return redirect(url_for('admin_login_page'))
        return fn(*args, **kwargs)

    return wrapper


def normalize_phone(phone: str) -> str:
    return ' '.join(phone.strip().split())


def validate_appointment(data: dict[str, Any]) -> tuple[bool, str]:
    required_fields = ['name', 'phone', 'date', 'time', 'service']
    for field in required_fields:
        if not str(data.get(field, '')).strip():
            return False, 'Lütfen tüm zorunlu alanları doldurun.'

    try:
        datetime.strptime(str(data['date']), '%Y-%m-%d')
        datetime.strptime(str(data['time']), '%H:%M')
    except ValueError:
        return False, 'Tarih veya saat formatı geçersiz.'

    return True, ''


@app.route('/')
def home():
    return render_template('public_site.html')


@app.route('/erhan-giris')
def admin_login_page():
    if session.get('owner_logged_in'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html', demo_username=OWNER_USERNAME, demo_password=OWNER_PASSWORD)


@app.route('/erhan-panel')
@login_required
def admin_dashboard():
    return render_template('admin_dashboard.html')


@app.post('/api/appointments')
def create_appointment():
    data = request.get_json(silent=True) or request.form.to_dict()
    is_valid, error = validate_appointment(data)
    if not is_valid:
        return jsonify({'ok': False, 'message': error}), 400

    db = get_db()
    created_at = datetime.now().isoformat(timespec='seconds')
    db.execute(
        '''
        INSERT INTO appointments (name, phone, date, time, service, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(data['name']).strip(),
            normalize_phone(str(data['phone'])),
            str(data['date']).strip(),
            str(data['time']).strip(),
            str(data['service']).strip(),
            str(data.get('note', '')).strip(),
            created_at,
        ),
    )
    db.commit()
    return jsonify({'ok': True, 'message': 'Randevunuz alındı.'})


@app.post('/admin/login')
def admin_login():
    data = request.get_json(silent=True) or request.form.to_dict()
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))

    if username == OWNER_USERNAME and password == OWNER_PASSWORD:
        session['owner_logged_in'] = True
        return jsonify({'ok': True, 'redirect': url_for('admin_dashboard')})

    return jsonify({'ok': False, 'message': 'Kullanıcı adı veya şifre hatalı.'}), 401


@app.post('/admin/logout')
@login_required
def admin_logout():
    session.clear()
    return jsonify({'ok': True, 'redirect': url_for('admin_login_page')})


@app.get('/admin/api/appointments')
@login_required
def admin_list_appointments():
    db = get_db()
    rows = db.execute(
        '''
        SELECT id, name, phone, date, time, service, note, status, created_at
        FROM appointments
        ORDER BY date ASC, time ASC, id DESC
        '''
    ).fetchall()

    items = [dict(row) for row in rows]
    today = datetime.now().strftime('%Y-%m-%d')
    upcoming = 0
    for item in items:
        try:
            dt = datetime.strptime(f"{item['date']} {item['time']}", '%Y-%m-%d %H:%M')
            if dt >= datetime.now():
                upcoming += 1
        except ValueError:
            pass

    stats = {
        'total': len(items),
        'today': sum(1 for item in items if item['date'] == today),
        'upcoming': upcoming,
        'services': len(set(item['service'] for item in items)),
    }
    return jsonify({'ok': True, 'appointments': items, 'stats': stats})


@app.post('/admin/api/appointments/<int:appointment_id>/toggle-status')
@login_required
def toggle_status(appointment_id: int):
    db = get_db()
    current = db.execute('SELECT status FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
    if not current:
        return jsonify({'ok': False, 'message': 'Randevu bulunamadı.'}), 404

    new_status = 'Onaylandı' if current['status'] != 'Onaylandı' else 'Bekliyor'
    db.execute('UPDATE appointments SET status = ? WHERE id = ?', (new_status, appointment_id))
    db.commit()
    return jsonify({'ok': True, 'message': 'Durum güncellendi.'})


@app.delete('/admin/api/appointments/<int:appointment_id>')
@login_required
def delete_appointment(appointment_id: int):
    db = get_db()
    db.execute('DELETE FROM appointments WHERE id = ?', (appointment_id,))
    db.commit()
    return jsonify({'ok': True, 'message': 'Randevu silindi.'})


@app.delete('/admin/api/appointments')
@login_required
def clear_appointments():
    db = get_db()
    db.execute('DELETE FROM appointments')
    db.commit()
    return jsonify({'ok': True, 'message': 'Tüm randevular temizlendi.'})


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
