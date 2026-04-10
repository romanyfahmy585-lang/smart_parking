# ══════════════════════════════════════════════
# database.py — إنشاء وإدارة قاعدة البيانات
# ══════════════════════════════════════════════

import sqlite3
from datetime import datetime
from config import DATABASE_FILE, TOTAL_SLOTS, GARAGE_ID

def get_connection():
    """
    بتفتح connection مع الـ DB وبترجعه
    بنستخدم row_factory عشان نقدر نوصل للداتا
    باسم العمود مش برقمه
    """
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """
    بتعمل الجداول لو مش موجودة
    وبتعمل الـ slots الابتدائية
    """
    conn = get_connection()
    c = conn.cursor()

    # ── جدول الـ Slots (الأماكن) ───────────────
    # كل مكان له ID وحالة وهل فيه حد جواه
    c.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id          INTEGER PRIMARY KEY,
            slot_number INTEGER UNIQUE,
            status      TEXT DEFAULT 'free',
            garage_id   TEXT
        )
    """)

    # ── جدول الحجوزات ──────────────────────────
    # كل حجز مرتبط بـ NFC UID وـ Slot
    c.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nfc_id           TEXT UNIQUE,
            name             TEXT,
            phone            TEXT,
            slot_id          INTEGER,
            status           TEXT DEFAULT 'reserved',
            reserved_at      TEXT,
            entry_time       TEXT,
            exit_time        TEXT,
            duration_minutes INTEGER,
            fee              REAL DEFAULT 0,
            FOREIGN KEY (slot_id) REFERENCES slots(id)
        )
    """)

    # ── جدول الـ Logs ───────────────────────────
    # بنسجل كل عملية بتحصل في النظام
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nfc_id     TEXT,
            action     TEXT,
            message    TEXT,
            timestamp  TEXT
        )
    """)

    # ── عمل الـ Slots لو مش موجودين ────────────
    # بنشوف عدد الـ slots الموجودين
    count = c.execute("SELECT COUNT(*) FROM slots").fetchone()[0]

    # لو مفيش slots ابعمل العدد المحدد في config
    if count == 0:
        for i in range(1, TOTAL_SLOTS + 1):
            c.execute(
                "INSERT INTO slots (slot_number, status, garage_id) VALUES (?,?,?)",
                (i, 'free', GARAGE_ID)
            )

    conn.commit()
    conn.close()
    print(f"✅ Database جاهزة — {TOTAL_SLOTS} أماكن")


def add_log(nfc_id, action, message):
    """
    بتسجل أي عملية بتحصل في النظام
    مثال: add_log("A3:4F", "entry", "دخل بنجاح")
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO logs (nfc_id, action, message, timestamp) VALUES (?,?,?,?)",
        (nfc_id, action, message, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()