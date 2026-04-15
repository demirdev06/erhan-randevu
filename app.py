from __future__ import annotations

import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "appointments.db"

SECRET_KEY = os.environ.get("ERHAN_SECRET_KEY", "super-secret-key-change-this")
OWNER_USERNAME = os.environ.get("ERHAN_OWNER_USERNAME", "Erhan")
OWNER_PASSWORD = os.environ.get("ERHAN_OWNER_PASSWORD", "Erhan!2026#Kuafor")

LOGIN_ATTEMPTS = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_SECONDS = 300  # 5 dakika

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    db = get_db()
    db.execute(
        """
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
        """
    )
    db.commit()
    db.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login_page"))
        return fn(*args, **kwargs)
    return wrapper


def get_client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_ip_blocked(ip: str) -> bool:
    now = time.time()
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOGIN_BLOCK_SECONDS]
    return len(LOGIN_ATTEMPTS[ip]) >= MAX_LOGIN_ATTEMPTS


def register_failed_attempt(ip: str) -> None:
    now = time.time()
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOGIN_BLOCK_SECONDS]
    LOGIN_ATTEMPTS[ip].append(now)


def clear_failed_attempts(ip: str) -> None:
    LOGIN_ATTEMPTS.pop(ip, None)


@app.get("/")
def public_site():
    return render_template("public_site.html")


@app.post("/api/appointments")
def create_appointment():
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    date = data.get("date", "").strip()
    time_value = data.get("time", "").strip()
    service = data.get("service", "").strip()
    note = data.get("note", "").strip()

    if not all([name, phone, date, time_value, service]):
        return jsonify({
            "ok": False,
            "message": "Lütfen tüm zorunlu alanları doldurun."
        }), 400

    db = get_db()
    exists = db.execute(
        """
        SELECT id FROM appointments
        WHERE date = ? AND time = ? AND phone = ?
        LIMIT 1
        """,
        (date, time_value, phone),
    ).fetchone()

    if exists:
        db.close()
        return jsonify({
            "ok": False,
            "message": "Bu bilgilerle aynı saat için zaten randevu var."
        }), 409

    db.execute(
        """
        INSERT INTO appointments (name, phone, date, time, service, note, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            phone,
            date,
            time_value,
            service,
            note,
            "Bekliyor",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    db.close()

    return jsonify({
        "ok": True,
        "message": "Randevunuz alındı."
    })


@app.get("/erhan-giris")
def admin_login_page():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")


@app.post("/admin/login")
def admin_login():
    ip = get_client_ip()

    if is_ip_blocked(ip):
        return jsonify({
            "ok": False,
            "message": "Çok fazla hatalı deneme. 5 dakika sonra tekrar deneyin."
        }), 429

    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if username == OWNER_USERNAME and password == OWNER_PASSWORD:
        session["admin_logged_in"] = True
        clear_failed_attempts(ip)
        return jsonify({
            "ok": True,
            "redirect": url_for("admin_dashboard")
        })

    register_failed_attempt(ip)
    return jsonify({
        "ok": False,
        "message": "Kullanıcı adı veya şifre hatalı."
    }), 401


@app.post("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    return jsonify({
        "ok": True,
        "redirect": url_for("admin_login_page")
    })


@app.get("/erhan-panel")
@login_required
def admin_dashboard():
    return render_template("admin_dashboard.html")


@app.get("/admin/api/appointments")
@login_required
def admin_list_appointments():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, name, phone, date, time, service, note, status, created_at
        FROM appointments
        ORDER BY date ASC, time ASC, id DESC
        """
    ).fetchall()
    db.close()

    items = [dict(row) for row in rows]
    today = datetime.now().strftime("%Y-%m-%d")

    upcoming = 0
    for item in items:
        try:
            dt = datetime.strptime(f"{item['date']} {item['time']}", "%Y-%m-%d %H:%M")
            if dt >= datetime.now():
                upcoming += 1
        except ValueError:
            pass

    return jsonify({
        "ok": True,
        "items": items,
        "stats": {
            "total": len(items),
            "today": sum(1 for item in items if item["date"] == today),
            "upcoming": upcoming,
            "services": len(set(item["service"] for item in items if item["service"])),
        },
    })


@app.post("/admin/api/appointments/<int:appointment_id>/toggle-status")
@login_required
def admin_toggle_status(appointment_id: int):
    db = get_db()
    row = db.execute(
        "SELECT status FROM appointments WHERE id = ?",
        (appointment_id,),
    ).fetchone()

    if not row:
        db.close()
        return jsonify({"ok": False, "message": "Randevu bulunamadı."}), 404

    new_status = "Onaylandı" if row["status"] == "Bekliyor" else "Bekliyor"
    db.execute(
        "UPDATE appointments SET status = ? WHERE id = ?",
        (new_status, appointment_id),
    )
    db.commit()
    db.close()

    return jsonify({"ok": True, "message": "Durum güncellendi."})


@app.delete("/admin/api/appointments/<int:appointment_id>")
@login_required
def admin_delete_appointment(appointment_id: int):
    db = get_db()
    db.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True, "message": "Randevu silindi."})


@app.delete("/admin/api/appointments")
@login_required
def admin_clear_appointments():
    db = get_db()
    db.execute("DELETE FROM appointments")
    db.commit()
    db.close()
    return jsonify({"ok": True, "message": "Tüm randevular silindi."})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)