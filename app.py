# ══════════════════════════════════════════════
# app.py — السيرفر الرئيسي + كل الـ API
# ══════════════════════════════════════════════

from flask import Flask, request, jsonify, render_template
from datetime import datetime, timedelta
from database import get_connection, init_database, add_log
from config import *

app = Flask(__name__)


# ════════════════════════════════════════════════════════════
# GET /status
# بترجع حالة كل الأماكن وعدد الفاضي منها
# الـ ESP32 والموقع بيستخدموها عشان يعرفوا الوضع الحالي
# ════════════════════════════════════════════════════════════
@app.route('/status', methods=['GET'])
def get_status():
    conn = get_connection()

    # جيب كل الـ slots مع معلومات الحجز لو موجود
    slots = conn.execute("""
        SELECT 
            s.slot_number,
            s.status,
            r.name,
            r.nfc_id,
            r.entry_time
        FROM slots s
        LEFT JOIN reservations r 
            ON s.id = r.slot_id 
            AND r.status IN ('reserved', 'inside')
        ORDER BY s.slot_number
    """).fetchall()

    # عد الأماكن الفاضية
    free_count = sum(1 for s in slots if s['status'] == 'free')

    conn.close()

    # رجّع البيانات بصيغة JSON
    return jsonify({
        "garage":    GARAGE_NAME,
        "total":     TOTAL_SLOTS,
        "available": free_count,
        "occupied":  TOTAL_SLOTS - free_count,
        "slots": [
            {
                "slot_number": s['slot_number'],
                "status":      s['status'],
                "occupant":    s['name'] or "—",
                "nfc_id":      s['nfc_id'] or "—",
                "since":       s['entry_time'] or "—"
            }
            for s in slots
        ]
    })


# ════════════════════════════════════════════════════════════
# POST /reserve
# المستخدم بيحجز مكان بـ NFC UID بتاعه
# بيستقبل: nfc_id (إجباري), name (اختياري), phone (اختياري)
# ════════════════════════════════════════════════════════════
@app.route('/reserve', methods=['POST'])
def reserve():
    data   = request.json or {}
    nfc_id = data.get('nfc_id', '').strip().upper()
    name   = data.get('name',  'زائر')
    phone  = data.get('phone', '')

    # ── تحقق إن الـ NFC UID موجود ──────────────
    if not nfc_id:
        return jsonify({"success": False, "message": "nfc_id مطلوب"}), 400

    conn = get_connection()

    # ── شوف لو الـ NFC ده عنده حجز قديم ─────────
    existing = conn.execute(
        "SELECT * FROM reservations WHERE nfc_id=? AND status IN ('reserved','inside')",
        (nfc_id,)
    ).fetchone()

    if existing:
        conn.close()
        return jsonify({"success": False, "message": "الكارت ده عنده حجز موجود بالفعل"})

    # ── دور على أول مكان فاضي ────────────────────
    free_slot = conn.execute(
        "SELECT * FROM slots WHERE status='free' ORDER BY slot_number LIMIT 1"
    ).fetchone()

    if not free_slot:
        conn.close()
        return jsonify({"success": False, "message": "مفيش أماكن متاحة دلوقتي"})

    # ── عمل الحجز ────────────────────────────────
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO reservations (nfc_id, name, phone, slot_id, status, reserved_at)
        VALUES (?, ?, ?, ?, 'reserved', ?)
    """, (nfc_id, name, phone, free_slot['id'], now))

    # ── غير حالة الـ Slot لـ reserved ────────────
    conn.execute(
        "UPDATE slots SET status='reserved' WHERE id=?",
        (free_slot['id'],)
    )

    conn.commit()
    conn.close()

    add_log(nfc_id, "reserve", f"{name} حجز Slot {free_slot['slot_number']}")

    return jsonify({
        "success":     True,
        "message":     "تم الحجز بنجاح",
        "slot_number": free_slot['slot_number'],
        "name":        name,
        "timeout":     f"لازم توصل خلال {RESERVATION_TIMEOUT_MINUTES} دقيقة"
    })


# ════════════════════════════════════════════════════════════
# POST /entry
# ESP32 بيبعت الـ NFC UID لما حد يقرب الكارت عند الدخول
# بيتحقق من الحجز ويسجل وقت الدخول ويفتح البوابة
# ════════════════════════════════════════════════════════════
@app.route('/entry', methods=['POST'])
def entry():
    data   = request.json or {}
    nfc_id = data.get('nfc_id', '').strip().upper()

    if not nfc_id:
        return jsonify({"success": False, "message": "nfc_id مطلوب"}), 400

    conn = get_connection()

    # ── شوف لو في حجز لـ NFC ده ─────────────────
    reservation = conn.execute(
        "SELECT * FROM reservations WHERE nfc_id=? AND status='reserved'",
        (nfc_id,)
    ).fetchone()

    if not reservation:
        conn.close()
        add_log(nfc_id, "entry_failed", "مفيش حجز لـ NFC ده")
        return jsonify({"success": False, "message": "مفيش حجز — مش مسموح بالدخول"})

    # ── شوف لو الحجز منتهيش (timeout) ───────────
    reserved_at = datetime.fromisoformat(reservation['reserved_at'])
    timeout     = reserved_at + timedelta(minutes=RESERVATION_TIMEOUT_MINUTES)

    if datetime.now() > timeout:
        # الحجز انتهى — أعد الـ Slot لـ free
        conn.execute(
            "UPDATE reservations SET status='cancelled' WHERE id=?",
            (reservation['id'],)
        )
        conn.execute(
            "UPDATE slots SET status='free' WHERE id=?",
            (reservation['slot_id'],)
        )
        conn.commit()
        conn.close()
        add_log(nfc_id, "entry_failed", "الحجز انتهت مدته")
        return jsonify({"success": False, "message": "الحجز انتهت مدته — احجز تاني"})

    # ── سجل وقت الدخول وغير الحالة لـ inside ────
    now = datetime.now().isoformat()

    conn.execute("""
        UPDATE reservations 
        SET status='inside', entry_time=?
        WHERE id=?
    """, (now, reservation['id']))

    # ── غير حالة الـ Slot لـ occupied ─────────────
    conn.execute(
        "UPDATE slots SET status='occupied' WHERE id=?",
        (reservation['slot_id'],)
    )

    conn.commit()

    # جيب رقم الـ Slot عشان ترجعه
    slot = conn.execute(
        "SELECT slot_number FROM slots WHERE id=?",
        (reservation['slot_id'],)
    ).fetchone()

    conn.close()

    add_log(nfc_id, "entry", f"دخل Slot {slot['slot_number']}")

    return jsonify({
        "success":     True,
        "message":     "أهلاً — تفضل",
        "name":        reservation['name'],
        "slot_number": slot['slot_number'],
        "entry_time":  now
    })


# ════════════════════════════════════════════════════════════
# POST /exit
# ESP32 بيبعت الـ NFC UID لما حد يقرب الكارت عند الخروج
# بيحسب الوقت والسعر ويفتح البوابة
# ════════════════════════════════════════════════════════════
@app.route('/exit', methods=['POST'])
def exit_parking():
    data   = request.json or {}
    nfc_id = data.get('nfc_id', '').strip().upper()

    if not nfc_id:
        return jsonify({"success": False, "message": "nfc_id مطلوب"}), 400

    conn = get_connection()

    # ── شوف لو الشخص ده جوا فعلاً ───────────────
    reservation = conn.execute(
        "SELECT * FROM reservations WHERE nfc_id=? AND status='inside'",
        (nfc_id,)
    ).fetchone()

    if not reservation:
        conn.close()
        return jsonify({"success": False, "message": "مفيش دخول مسجل لـ NFC ده"})

    # ── حساب الوقت ────────────────────────────────
    entry_time = datetime.fromisoformat(reservation['entry_time'])
    exit_time  = datetime.now()
    duration   = exit_time - entry_time

    # تحويل الوقت لدقائق وساعات
    total_minutes = int(duration.total_seconds() / 60)
    total_hours   = duration.total_seconds() / 3600

    # ── حساب السعر ────────────────────────────────
    # أقل مبلغ هو MIN_CHARGE حتى لو بضع دقائق
    fee = max(MIN_CHARGE, round(total_hours * PRICE_PER_HOUR, 2))

    # ── حدّث الحجز بالخروج والسعر ────────────────
    conn.execute("""
        UPDATE reservations
        SET status='completed', exit_time=?, duration_minutes=?, fee=?
        WHERE id=?
    """, (exit_time.isoformat(), total_minutes, fee, reservation['id']))

    # ── أعد الـ Slot لـ free ──────────────────────
    conn.execute(
        "UPDATE slots SET status='free' WHERE id=?",
        (reservation['slot_id'],)
    )

    conn.commit()
    conn.close()

    add_log(nfc_id, "exit", f"خرج بعد {total_minutes} دقيقة — {fee} جنيه")

    return jsonify({
        "success":         True,
        "message":         "مع السلامة",
        "name":            reservation['name'],
        "duration_minutes": total_minutes,
        "fee":             fee
    })


# ════════════════════════════════════════════════════════════
# GET /logs
# بترجع آخر 50 عملية اتسجلت في النظام
# ════════════════════════════════════════════════════════════
@app.route('/logs', methods=['GET'])
def get_logs():
    conn = get_connection()
    logs = conn.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()

    return jsonify([dict(l) for l in logs])


# ════════════════════════════════════════════════════════════
# GET / — الصفحة الرئيسية
# ════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return render_template('index.html',
                           garage_name=GARAGE_NAME,
                           total_slots=TOTAL_SLOTS)


# ── تشغيل السيرفر ─────────────────────────────────────────
if __name__ == '__main__':
    init_database()
    print(f"🚀 السيرفر شغال على http://localhost:{PORT}")
import os

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port, debug=False)
