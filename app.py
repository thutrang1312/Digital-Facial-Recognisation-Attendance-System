from flask import Flask, redirect, session, render_template, request, jsonify, send_file
from datetime import datetime, date, timedelta
import threading
import io
import os
import json
import time
import base64
import numpy as np
import cv2
import ipaddress

from admin_routes import admin_bp
from auto_absent import start_auto_absent_scheduler
from config import DATASET_DIR
from db import init_mysql, get_db
from model import train_model, predict_stream
from route.auth_routes import auth
from route.dataset_routes import dataset

# ================= IP WHITELIST =================
ALLOWED_NETWORKS = [
    ipaddress.ip_network("172.26.19.66"),  # WiFi trường P523 5G
]

def is_allowed_ip(request):
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    client_ip = client_ip.split(",")[0].strip()
    try:
        ip_obj = ipaddress.ip_address(client_ip)
        return any(ip_obj in net for net in ALLOWED_NETWORKS)
    except ValueError:
        return False

# ================= INIT =================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(APP_DIR, "dataset")
STATUS_FILE = os.path.join(APP_DIR, "train_status.json")

os.makedirs(DATASET_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "123456"

init_mysql()
start_auto_absent_scheduler()

app.register_blueprint(auth)
app.register_blueprint(dataset)
app.register_blueprint(admin_bp)

# ================= HOME =================
@app.route("/")
def home():
    return redirect("/login")



# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")

    role = session.get("role")
    username = session.get("user")
    week_offset = int(request.args.get('week', 0))
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    try:
        # ================= ADMIN =================
        if role == "admin":
            cursor.execute("SELECT COUNT(*) AS total_students FROM students")
            student_count = cursor.fetchone().get('total_students', 0) or 0

            cursor.execute("SELECT COUNT(*) AS total_teachers FROM teachers")
            teacher_count = cursor.fetchone().get('total_teachers', 0) or 0

            cursor.execute("SELECT COUNT(DISTINCT class_name) AS total_classes FROM schedules")
            class_count = cursor.fetchone().get('total_classes', 0) or 0

            cursor.execute("SELECT COUNT(*) AS present_count FROM attendance WHERE DATE(checkin_time)=CURDATE() AND status IN ('Present','Late')")
            present_count = cursor.fetchone().get('present_count', 0) or 0

            cursor.execute("SELECT COUNT(*) AS total_today FROM attendance WHERE DATE(checkin_time)=CURDATE()")
            total_today = cursor.fetchone().get('total_today', 0) or 0

            attendance_rate = round((present_count / total_today) * 100, 2) if total_today > 0 else 0

            train_state = read_status()
            if train_state.get('running'):
                model_status = 'Đang huấn luyện'
            elif train_state.get('progress') == 100:
                model_status = 'Sẵn sàng'
            else:
                model_status = 'Chưa huấn luyện'

            cursor.execute("""
                SELECT
                    s.name AS student_name,
                    COALESCE(sub.name, '') AS subject_name,
                    COALESCE(t.name, '') AS teacher_name,
                    DATE_FORMAT(a.checkin_time, '%H:%i:%s') AS time,
                    a.status
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.id
                LEFT JOIN schedules sch ON a.schedule_id = sch.id
                LEFT JOIN subjects sub ON sch.subject_id = sub.id
                LEFT JOIN teachers t ON sub.teacher_id = t.id
                ORDER BY a.checkin_time DESC
                LIMIT 5
            """
            )
            latest_attendance = cursor.fetchall()

            return render_template(
                "index.html",
                user_name=username,
                student_count=student_count,
                teacher_count=teacher_count,
                class_count=class_count,
                attendance_rate=attendance_rate,
                model_status=model_status,
                latest_attendance=latest_attendance
            )

        # ================= TEACHER =================
        elif role == "teacher":

            cursor.execute("""
                SELECT id, name
                FROM teachers
                WHERE user_id = (
                    SELECT id
                    FROM users
                    WHERE username = %s
                )
            """, (username,))
            teacher = cursor.fetchone()

            timetable = {i: [] for i in range(7)}

            # ================= TUẦN ĐANG XEM =================
            week_offset = int(request.args.get("week", 0))

            today = date.today()

            start_week = (
                today
                - timedelta(days=today.weekday())
                + timedelta(weeks=week_offset)
            )

            end_week = start_week + timedelta(days=6)

            if teacher:

                cursor.execute("""
                    SELECT
                        s.id AS schedule_id,
                        s.room,
                        sub.class_name,
                        sub.name AS subject_name,
                        TIME_FORMAT(s.start_time, '%H:%i') AS start_time,
                        TIME_FORMAT(s.end_time, '%H:%i') AS end_time,
                        s.start_date,
                        s.end_date,
                        s.days_of_week
                    FROM schedules s
                    JOIN subjects sub
                        ON s.subject_id = sub.id
                    WHERE sub.teacher_id = %s
                    AND s.start_date <= %s
                    AND s.end_date >= %s
                    AND s.days_of_week IS NOT NULL
                """, (
                    teacher["id"],
                    end_week,
                    start_week
                ))

                rows = cursor.fetchall()

                import copy

                for row in rows:

                    days = row["days_of_week"]

                    if isinstance(days, str):
                        days = json.loads(days)

                    for d in days:

                        # ngày thực tế của tiết học trong tuần đang xem
                        lesson_date = start_week + timedelta(days=d)

                        # chỉ hiển thị nếu ngày đó nằm trong khoảng học
                        if row["start_date"] <= lesson_date <= row["end_date"]:

                            if d in timetable:
                                timetable[d].append(copy.deepcopy(row))

                        # Chỉ highlight hôm nay khi đang ở tuần hiện tại
                        if week_offset == 0:
                            today_code = datetime.now().weekday()
                            today_classes = len(timetable[today_code])
                        else:
                            today_code = -1
                            today_classes = 0


                        return render_template(
                            "teacher_home.html",
                            teacher=teacher,
                            timetable=timetable,
                            today_code=today_code,
                            today_classes=today_classes,
                            week_offset=week_offset,
                            start_week=start_week,
                            end_week=end_week
                        )



        elif role == "student":
                    cursor.execute("""
                        SELECT id, name, class_name
                        FROM students
                        WHERE user_id = (SELECT id FROM users WHERE username = %s)
                    """, (username,))
                    student = cursor.fetchone()

                    timetable = {i: [] for i in range(7)}
                    attendance_rate = 0
                    absent_count = 0
                    today_code = -1

                    today = date.today()
                    start_week = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
                    end_week = start_week + timedelta(days=6)

                    # ================= HELPER =================
                    def format_time(val):
                        if isinstance(val, str):
                            return val[:5]
                        elif isinstance(val, timedelta):
                            total = int(val.total_seconds())
                            return f"{total//3600:02d}:{(total%3600)//60:02d}"
                        elif val:
                            return val.strftime("%H:%M")
                        return ""

                    if student:
                        student_id = student['id']
                        class_name = student['class_name']

                        # ================= THỐNG KÊ MỚI =================
                        cursor.execute("""
                            SELECT COUNT(*) AS present
                            FROM attendance
                            WHERE student_id = %s AND status IN ('Present')
                        """, (student_id,))
                        present = cursor.fetchone()['present']

                        cursor.execute("""
                            SELECT COUNT(*) AS late
                            FROM attendance
                            WHERE student_id = %s AND status = 'Late'
                        """, (student_id,))
                        late = cursor.fetchone()['late']

                        # 🔥 Đếm số tiết đã qua mà chưa có record → tính là vắng
                        # cursor.execute("""
                        #     SELECT COUNT(*) AS unrecorded
                        #     FROM schedules s
                        #     JOIN subjects sub ON s.subject_id = sub.id
                        #     WHERE sub.class_name = %s
                        #     AND CURDATE() BETWEEN s.start_date AND s.end_date
                        #     AND JSON_CONTAINS(s.days_of_week, CAST(%s AS JSON))
                        #     AND s.end_time < CURTIME()
                        #     AND s.id NOT IN (
                        #         SELECT schedule_id FROM attendance
                        #         WHERE student_id = %s
                        #             AND DATE(checkin_time) = CURDATE()
                        #             AND schedule_id IS NOT NULL
                        #     )
                        # """, (class_name, str(datetime.now().weekday()), student_id))
                        # unrecorded = cursor.fetchone()['unrecorded']

                        # Tổng buổi vắng = Absent trong DB + chưa điểm hôm nay
                        cursor.execute("""
                            SELECT COUNT(*) AS absent
                            FROM attendance
                            WHERE student_id = %s AND status = 'Absent'
                        """, (student_id,))
                        absent_count = cursor.fetchone()['absent']

                        # absent_count = absent_db + unrecorded

                        total = present + late + absent_count

                        attendance_rate = round(
                            ((present + 0.75 * late) / total) * 100,
                            2
                        ) if total > 0 else 0
                        attendance_rate = round(((present + 0.75*late) / total) * 100, 2 ) if total > 0 else 0

                        # ================= TIMETABLE =================
                        cursor.execute("""
                            SELECT s.id AS schedule_id,
                                s.room,
                                sub.name AS subject_name,
                                t.name AS teacher_name,
                                s.start_time,
                                s.end_time,
                                s.start_date,
                                s.end_date,
                                s.days_of_week
                            FROM schedules s
                            JOIN subjects sub ON s.subject_id = sub.id
                            JOIN teachers t ON sub.teacher_id = t.id
                            WHERE sub.class_name = %s
                            AND s.start_date <= %s
                            AND s.end_date >= %s
                            AND s.days_of_week IS NOT NULL
                        """, (class_name, end_week, start_week))

                        rows = cursor.fetchall()

                        import copy

                        for row in rows:
                            row["start_time"] = format_time(row.get("start_time"))
                            row["end_time"] = format_time(row.get("end_time"))

                            days = row["days_of_week"]
                            if isinstance(days, str):
                                days = json.loads(days)

                            for d in days:
                                lesson_date = start_week + timedelta(days=d)

                                if row["start_date"] <= lesson_date <= row["end_date"]:
                                     timetable[d].append(copy.deepcopy(row))
                        
                        # ================= HIGHLIGHT TIẾT HIỆN TẠI =================
                        now = datetime.now()
                        current_day = now.weekday()
                        current_time = now.strftime("%H:%M")

                        if week_offset == 0:
                            today_code = current_day

                            for item in timetable.get(current_day, []):
                                if item["start_time"] <= current_time <= item["end_time"]:
                                    item["is_now"] = True
                                else:
                                    item["is_now"] = False
                        else:
                            today_code = -1

                    return render_template(
                        "student_home.html",
                        student=student,
                        timetable=timetable,
                        today_code=today_code,
                        attendance_rate=attendance_rate,
                        absent_count=absent_count,
                        week_offset=week_offset,
                        start_week=start_week,
                        end_week=end_week
                    )
    except Exception as e:
        print("🔥 Dashboard Error:", e)
        return redirect("/login")

    finally:
        try:
            cursor.close()
            db.close()
        except:
            pass

# ================= TRAIN STATUS (THREAD SAFE) =================
_status_lock = threading.Lock()
_status_cache = {"running": False, "progress": 0, "message": ""}


def write_status(data):
    global _status_cache
    with _status_lock:
        _status_cache = data
        for _ in range(3):
            try:
                with open(STATUS_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                break
            except Exception:
                time.sleep(0.05)


def read_status():
    with _status_lock:
        return dict(_status_cache)


# ================= TRAIN MODEL =================
@app.route("/train_model")
def train():
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    if read_status()["running"]:
        return jsonify({"status": "running", "message": "Đang huấn luyện..."}), 202

    def run():
        write_status({"running": True, "progress": 0, "message": "Start"})
        try:
            ok = train_model(DATASET_DIR, lambda p, m: write_status({"running": True, "progress": p, "message": m}))
            write_status({"running": False, "progress": 100 if ok else 0, "message": "Done" if ok else "Training failed"})
        except Exception as e:
            write_status({"running": False, "progress": 0, "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/train_model_auto")
def train_auto():
    if read_status()["running"]:
        return jsonify({"status": "running", "message": "Đang huấn luyện..."}), 202

    def run():
        write_status({"running": True, "progress": 0, "message": "Start"})
        try:
            ok = train_model(DATASET_DIR, lambda p, m: write_status({"running": True, "progress": p, "message": m}))
            write_status({"running": False, "progress": 100 if ok else 0, "message": "Done" if ok else "Training failed"})
        except Exception as e:
            write_status({"running": False, "progress": 0, "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/train_status")
def train_status():
    return jsonify(read_status())


# ================= MARK ATTENDANCE PAGE =================
@app.route("/mark_attendance")
def mark():
    if "user" not in session:
        return redirect("/login")
    return render_template("mark_attendance.html")


@app.route('/api/today_schedule_check')
def today_schedule_check():
    """Return whether the student has a schedule right now (used by frontend)."""
    from datetime import datetime

    if "user" not in session:
        return jsonify({"has_class_today": False})

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        username = session.get('user')
        cursor.execute("""
            SELECT id, class_name FROM students
            WHERE user_id = (SELECT id FROM users WHERE username = %s)
        """, (username,))
        student = cursor.fetchone()

        if not student:
            return jsonify({"has_class_today": False})

        class_name = student['class_name']
        now = datetime.now()
        today = now.date()
        current_time = now.strftime('%H:%M:%S')
        current_day_index = now.weekday()

        cursor.execute("""
            SELECT s.id
            FROM schedules s
            WHERE s.class_name = %s
              AND %s BETWEEN s.start_date AND s.end_date
              AND JSON_CONTAINS(s.days_of_week, CAST(%s AS JSON))
              AND s.start_time <= %s
              AND s.end_time >= %s
            LIMIT 1
        """, (class_name, today, str(current_day_index), current_time, current_time))

        schedule = cursor.fetchone()
        return jsonify({"has_class_today": bool(schedule)})

    except Exception as e:
        print("🔥 today_schedule_check ERROR:", e)
        return jsonify({"has_class_today": False})
    finally:
        try:
            cursor.close()
            db.close()
        except:
            pass


# ================= RECOGNIZE FACE (legacy) =================
@app.route("/recognize_face", methods=["POST"])
def recognize():
    file = request.files.get("image")
    if not file:
        return jsonify({"recognized": False, "error": "No image"}), 400

    label, conf = predict_stream(file.stream)

    if label == "no_model":
        return jsonify({"recognized": False, "reason": "Chưa có model"})
    if label == "no_face":
        return jsonify({"recognized": False, "reason": "Không có khuôn mặt"})
    if label == "unknown":
        return jsonify({"recognized": False, "reason": "Không nhận diện được"})
    if not str(label).isdigit():
        return jsonify({"recognized": False, "error": "Invalid label"}), 400

    label = int(label)

    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT id, student_code, name FROM students WHERE id=%s OR student_code=%s", (label, str(label)))
    student = c.fetchone()

    if not student:
        conn.close()
        return jsonify({"recognized": False, "error": "Student not found"})

    student_id = student["id"]
    now = datetime.now()
    today = date.today()

    c.execute("SELECT id FROM attendance WHERE student_id=%s AND DATE(checkin_time)=%s", (student_id, today))
    existed = c.fetchone()

    if not existed:
        c.execute("""
            INSERT INTO attendance (student_id, checkin_time, status)
            VALUES (%s, %s, 'Present')
        """, (student_id, now))
        conn.commit()
        is_new = True
    else:
        is_new = False

    conn.close()
    return jsonify({
        "recognized": True,
        "student_id": student_id,
        "name": student["name"],
        "confidence": float(conf),
        "time": str(now),
        "is_new": is_new
    })


# ================= MARK ATTENDANCE (FACE CAM) =================
@app.route('/mark_attendance_single', methods=['POST'])
def mark_attendance_single():

    # 🔒 Chỉ cho phép WiFi trường
    if not is_allowed_ip(request):
        return jsonify({
            "status": "fail",
            "message": "Bạn phải kết nối WiFi trường để điểm danh!"
        }), 403

    data = request.get_json()
    image_data = data.get('image')

    # ===== decode ảnh =====
    try:
        header, encoded = image_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print("❌ Decode lỗi:", e)
        return jsonify({"status": "fail", "message": "Ảnh lỗi"})

    if frame is None:
        return jsonify({"status": "fail", "message": "Ảnh lỗi"})

    print("✅ Ảnh OK")

    # ===== nhận diện =====
    _, buffer = cv2.imencode('.jpg', frame)
    file_like = io.BytesIO(buffer)
    label, conf = predict_stream(file_like)

    if label in ["no_face", "unknown", "no_model"]:
        return jsonify({"status": "fail", "message": label})

    student_id = int(label)

    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    try:
        # ===== lấy sinh viên =====
        cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
        student = cursor.fetchone()

        if not student:
            return jsonify({"status": "fail", "message": "Student not found"})

        class_name = student["class_name"]

        # ===== thời gian =====
        now = datetime.now()
        today = now.date()
        current_time = now.strftime("%H:%M:%S")
        current_day_index = now.weekday()

        # ===== tìm lịch học =====
        cursor.execute("""
            SELECT s.*
            FROM schedules s
            JOIN subjects sub ON s.subject_id = sub.id
            WHERE sub.class_name = %s
              AND %s BETWEEN s.start_date AND s.end_date
              AND JSON_CONTAINS(s.days_of_week, CAST(%s AS JSON))
              AND s.start_time <= %s
              AND s.end_time >= %s
            ORDER BY s.id ASC
            LIMIT 1
        """, (class_name, today, str(current_day_index), current_time, current_time))

        schedule = cursor.fetchone()

        if not schedule:
            return jsonify({
                "status": "fail",
                "message": "Hiện tại không có tiết học"
            })

        schedule_id = schedule["id"]

        # ===== check đã điểm danh chưa (FIX BUG) =====
        cursor.execute("""
            SELECT id FROM attendance
            WHERE student_id = %s 
              AND schedule_id = %s
              AND DATE(checkin_time) = %s
        """, (student_id, schedule_id, today))

        existed = cursor.fetchone()

        if existed:
            return jsonify({
                "status": "fail",
                "message": "Đã điểm danh rồi"
            })

        # ===== tính trạng thái =====
        start_time = schedule['start_time']
        end_time = schedule['end_time']

        # convert start_time
        if isinstance(start_time, timedelta):
            start_seconds = int(start_time.total_seconds())
        else:
            h, m, s = str(start_time).split(":")
            start_seconds = int(h) * 3600 + int(m) * 60 + int(s)

        # convert end_time
        if isinstance(end_time, timedelta):
            end_seconds = int(end_time.total_seconds())
        else:
            h, m, s = str(end_time).split(":")
            end_seconds = int(h) * 3600 + int(m) * 60 + int(s)

        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        late_threshold = 15 * 60  # 15 phút

        # ❌ quá giờ học
        if now_seconds > end_seconds:
            return jsonify({
                "status": "fail",
                "message": "Đã quá giờ điểm danh"
            })

        # ⏰ trễ hay đúng giờ
        if (now_seconds - start_seconds) > late_threshold:
            status = "Late"
            message = "Bạn đã điểm danh trễ"
            color = "#FFC107"   # 🟡 vàng đẹp
        else:
            status = "Present"
            message = "Điểm danh thành công"
            color = "green"
        # ===== insert =====
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute("""
            INSERT INTO attendance (student_id, schedule_id, checkin_time, status)
            VALUES (%s, %s, %s, %s)
        """, (student_id, schedule_id, now_str, status))

        db.commit()

        return jsonify({
            "status": "success",
            "message": message,
            "student": student["name"],
            "time": now_str,
            "attendance_status": status,
            "color": color
        })

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"status": "fail", "message": "Lỗi server"})

    finally:
        cursor.close()
        db.close()

# ================= ATTENDANCE RECORD =================
@app.route("/attendance_record")
def record():
    if "user" not in session:
        return redirect("/login")

    period = request.args.get("period", "all")
    conn = get_db()
    c = conn.cursor(dictionary=True)

    query = """
        SELECT a.id, s.student_code, s.name, s.class_name,
               a.checkin_time, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.id
    """

    if period == "daily":
        query += "\nWHERE DATE(a.checkin_time) = CURDATE()"
    elif period == "weekly":
        query += "\nWHERE YEARWEEK(a.checkin_time, 1) = YEARWEEK(CURDATE(), 1)"
    elif period == "monthly":
        query += "\nWHERE MONTH(a.checkin_time) = MONTH(CURDATE()) AND YEAR(a.checkin_time) = YEAR(CURDATE())"

    query += "\nORDER BY a.checkin_time DESC"

    c.execute(query)
    rows = c.fetchall()
    conn.close()
    return render_template("attendance_record.html", records=rows, period=period)


# ================= EXPORT CSV =================
@app.route("/download_csv")
def download_csv():
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("""
        SELECT a.id, s.student_code, s.name, s.class_name,
               a.checkin_time, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        ORDER BY a.checkin_time DESC
    """)
    rows = c.fetchall()
    conn.close()

    out = io.StringIO()
    out.write("\ufeff")
    out.write("id,student_code,name,class_name,checkin_time,status\n")
    for r in rows:
        out.write(f"{r['id']},{r['student_code']},{r['name']},"
                  f"{r['class_name']},{r['checkin_time']},{r['status']}\n")

    mem = io.BytesIO(out.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(
        mem,
        download_name="attendance.csv",
        as_attachment=True,
        mimetype="text/csv; charset=utf-8"
    )


# ================= LIST STUDENTS =================
@app.route("/students")
def list_students():
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("""
        SELECT id, student_code, name, class_name,
               email, phone, embedding_status
        FROM students ORDER BY id DESC
    """)
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)


# ================= API: SUBJECT ATTENDANCE HISTORY =================
# @app.route("/api/subject_attendance/<int:schedule_id>")
# def subject_attendance(schedule_id):
#     if "user" not in session:
#         return jsonify({"today": None, "history": [], "is_ended": False})

#     db = get_db()
#     cursor = db.cursor(dictionary=True)

#     try:
#         username = session.get("user")

#         cursor.execute("""
#             SELECT id FROM students
#             WHERE user_id = (SELECT id FROM users WHERE username = %s)
#         """, (username,))
#         student = cursor.fetchone()

#         if not student:
#             return jsonify({"today": None, "history": [], "is_ended": False})

#         student_id = student['id']

#         # Kiểm tra tiết đã kết thúc chưa
#         cursor.execute("SELECT end_time FROM schedules WHERE id = %s", (schedule_id,))
#         sch = cursor.fetchone()
#         is_ended = False
#         end_time_str = None  # 🔥 lưu lại để dùng khi insert

#         if sch:
#             end_time = sch["end_time"]
#             if isinstance(end_time, timedelta):
#                 end_sec = int(end_time.total_seconds())
#                 h = end_sec // 3600
#                 m = (end_sec % 3600) // 60
#                 s = end_sec % 60
#                 end_time_str = f"{h:02d}:{m:02d}:{s:02d}"
#             else:
#                 end_time_str = str(end_time)
#                 h, m, s = end_time_str.split(":")
#                 end_sec = int(h) * 3600 + int(m) * 60 + int(s)

#             now = datetime.now()
#             now_sec = now.hour * 3600 + now.minute * 60 + now.second
#             is_ended = now_sec > end_sec

#         # 🔥 Nếu tiết đã kết thúc + chưa có record hôm nay → insert Absent với giờ kết thúc tiết
#         if is_ended:
#             cursor.execute("""
#                 SELECT id FROM attendance
#                 WHERE student_id = %s
#                   AND schedule_id = %s
#                   AND DATE(checkin_time) = CURDATE()
#                 LIMIT 1
#             """, (student_id, schedule_id))
#             if not cursor.fetchone():
#                 today_str = datetime.now().strftime('%Y-%m-%d')
#                 checkin_time_str = f"{today_str} {end_time_str}"  # 🔥 dùng giờ kết thúc tiết
#                 cursor.execute("""
#                     INSERT INTO attendance (student_id, schedule_id, checkin_time, status)
#                     VALUES (%s, %s, %s, 'Absent')
#                 """, (student_id, schedule_id, checkin_time_str))
#                 db.commit()

#         # Lịch sử điểm danh môn này
#         cursor.execute("""
#             SELECT DATE_FORMAT(checkin_time, '%d/%m/%Y') AS date,
#                    TIME_FORMAT(checkin_time, '%H:%i') AS checkin_time,
#                    status
#             FROM attendance
#             WHERE student_id = %s AND schedule_id = %s
#             ORDER BY checkin_time DESC
#         """, (student_id, schedule_id))
#         history = cursor.fetchall()

#         # Hôm nay đã điểm chưa
#         cursor.execute("""
# SELECT TIME_FORMAT(checkin_time, '%H:%i') AS checkin_time, status
#             FROM attendance
#             WHERE student_id = %s
#               AND schedule_id = %s
#               AND DATE(checkin_time) = CURDATE()
#             LIMIT 1
#         """, (student_id, schedule_id))
#         today_row = cursor.fetchone()

#         today = {"checkin_time": today_row["checkin_time"], "status": today_row["status"]} if today_row else None

#         return jsonify({"today": today, "history": history, "is_ended": is_ended})

#     except Exception as e:
#         print("🔥 API subject_attendance Error:", e)
#         return jsonify({"today": None, "history": [], "is_ended": False})

#     finally:
#         cursor.close()
#         db.close()
# ================= API: SUBJECT ATTENDANCE HISTORY =================
@app.route("/api/subject_attendance/<int:schedule_id>")
def subject_attendance(schedule_id):

    import datetime
    import json

    now = datetime.datetime.now()
    today_dow = now.weekday()

    # ================= CHECK LOGIN =================
    if "user" not in session:
        return jsonify({
            "today": None,
            "history": [],
            "is_ended": False,
            "not_started": False,
            "is_today": False
        })

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        username = session.get("user")

        # ================= GET STUDENT =================
        cursor.execute("""
            SELECT id FROM students
            WHERE user_id = (SELECT id FROM users WHERE username = %s)
        """, (username,))
        student = cursor.fetchone()

        if not student:
            return jsonify({
                "today": None,
                "history": [],
                "is_ended": False,
                "not_started": False,
                "is_today": False
            })

        student_id = student["id"]

        # ================= GET SCHEDULE =================
        cursor.execute("""
            SELECT start_time, end_time, days_of_week
            FROM schedules
            WHERE id = %s
        """, (schedule_id,))
        sch = cursor.fetchone()

        if not sch:
            return jsonify({
                "today": None,
                "history": [],
                "is_ended": False,
                "not_started": False,
                "is_today": False
            })

        # ================= PARSE DAYS =================
        days_raw = sch["days_of_week"]

        if isinstance(days_raw, str):
            try:
                days_list = json.loads(days_raw)
            except:
                days_list = []
        elif isinstance(days_raw, list):
            days_list = days_raw
        else:
            days_list = []

        # ================= CHECK TODAY =================
        is_today = today_dow in days_list

        # ================= CONVERT TIME =================
        def to_sec(t):
            if isinstance(t, datetime.timedelta):
                return int(t.total_seconds())
            h, m, s = map(int, str(t).split(":"))
            return h * 3600 + m * 60 + s

        start_sec = to_sec(sch["start_time"])
        end_sec = to_sec(sch["end_time"])

        now_sec = now.hour * 3600 + now.minute * 60 + now.second

        # ================= LOGIC STATE =================
        if not is_today:
            not_started = True
            is_ended = False
        else:
            not_started = now_sec < start_sec
            is_ended = now_sec > end_sec

        # ================= TODAY RECORD =================
        cursor.execute("""
            SELECT
                TIME_FORMAT(checkin_time, '%H:%i') AS checkin_time,
                LOWER(status) AS status
            FROM attendance
            WHERE student_id = %s
              AND schedule_id = %s
              AND DATE(checkin_time) = CURDATE()
            LIMIT 1
        """, (student_id, schedule_id))

        today_row = cursor.fetchone()

        today = None
        if today_row:
            today = {
                "checkin_time": today_row["checkin_time"],
                "status": today_row["status"]
            }

        # ================= AUTO ABSENT =================
        if is_ended and not today:
            today = {
                "checkin_time": None,
                "status": "absent"
            }

        # ================= HISTORY =================
        cursor.execute("""
            SELECT
                DATE_FORMAT(checkin_time, '%d/%m/%Y') AS date,
                TIME_FORMAT(checkin_time, '%H:%i') AS checkin_time,
                status
            FROM attendance
            WHERE student_id = %s
              AND schedule_id = %s
            ORDER BY checkin_time DESC
        """, (student_id, schedule_id))

        history = cursor.fetchall()

        # ================= RESPONSE =================
        return jsonify({
            "today": today,
            "history": history,
            "is_ended": is_ended,
            "not_started": not_started,
            "is_today": is_today
        })

    except Exception as e:
        print("🔥 API subject_attendance Error:", e)
        return jsonify({
            "today": None,
            "history": [],
            "is_ended": False,
            "not_started": False,
            "is_today": False
        })

    finally:
        cursor.close()
        db.close()

# ================= API: CLASS STUDENTS BY SCHEDULE =================
@app.route('/api/class_students/<int:schedule_id>')
def class_students(schedule_id):
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    conn = get_db()
    c = conn.cursor(dictionary=True)
    try:
        today = date.today()

        # Lấy subject_id và class_name từ schedule_id hiện tại
        c.execute("""
            SELECT subject_id, class_name 
            FROM schedules 
            WHERE id = %s
        """, (schedule_id,))
        sch_info = c.fetchone()

        if not sch_info:
            return jsonify({"error": "Không tìm thấy lịch học"}), 404

        subject_id = sch_info['subject_id']
        class_name = sch_info['class_name']

        c.execute("""
            SELECT
                st.id            AS student_id,
                st.student_code,
                st.name,

                -- Trạng thái HÔM NAY (chỉ buổi này)
                (SELECT a.status 
                 FROM attendance a
                 WHERE a.student_id  = st.id
                   AND a.schedule_id = %s
                   AND DATE(a.checkin_time) = %s
                 LIMIT 1
                ) AS today_status,

                -- Tổng số buổi của MÔN HỌC NÀY (tất cả schedule cùng subject)
                (SELECT COUNT(*) 
                 FROM attendance a
                 JOIN schedules s ON a.schedule_id = s.id
                 WHERE a.student_id = st.id
                   AND s.subject_id = %s
                   AND s.class_name = %s
                ) AS total_sessions,

                -- Có mặt toàn môn
                (SELECT COUNT(*) 
                 FROM attendance a
                 JOIN schedules s ON a.schedule_id = s.id
                 WHERE a.student_id = st.id
                   AND s.subject_id = %s
                   AND s.class_name = %s
                   AND a.status = 'Present'
                ) AS present_count,

                -- Vắng toàn môn
                (SELECT COUNT(*) 
                 FROM attendance a
                 JOIN schedules s ON a.schedule_id = s.id
                 WHERE a.student_id = st.id
                   AND s.subject_id = %s
                   AND s.class_name = %s
                   AND a.status = 'Absent'
                ) AS absent_count,

                -- Trễ toàn môn
                (SELECT COUNT(*) 
                 FROM attendance a
                 JOIN schedules s ON a.schedule_id = s.id
                 WHERE a.student_id = st.id
                   AND s.subject_id = %s
                   AND s.class_name = %s
                   AND a.status = 'Late'
                ) AS late_count

            FROM students st
            WHERE st.class_name = %s
            ORDER BY st.name
        """, (
            schedule_id, today,              # today_status
            subject_id, class_name,          # total_sessions
            subject_id, class_name,          # present_count
            subject_id, class_name,          # absent_count
            subject_id, class_name,          # late_count
            class_name                       # WHERE students
        ))

        rows = c.fetchall()

        for r in rows:
            r['total_sessions'] = int(r['total_sessions'] or 0)
            r['present_count']  = int(r['present_count']  or 0)
            r['absent_count']   = int(r['absent_count']   or 0)
            r['late_count']     = int(r['late_count']     or 0)
            r['today_status']   = r['today_status'] or 'Absent'

            if r['total_sessions'] > 0:
                score = r['present_count'] * 1.0 + r['late_count'] * 0.75
                r['rate'] = round((score / r['total_sessions']) * 100)
            else:
                r['rate'] = 0

        return jsonify(rows)

    except Exception as e:
        print("🔥 ERROR /api/class_students:", e)
        return jsonify({"error": str(e)}), 500
    finally:
        c.close()
        conn.close()
@app.route('/api/my_subjects')
def list_subject():
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    conn = get_db()
    c = conn.cursor(dictionary=True)

    try:
        username = session.get('user')

        # Lấy student_id và class_name
        c.execute("""
            SELECT s.id, s.class_name
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE u.username = %s
        """, (username,))
        student = c.fetchone()

        if not student:
            return jsonify({"error": "Không tìm thấy sinh viên"}), 404

        student_id = student['id']
        class_name  = student['class_name']

        # Lấy danh sách môn + tính chuyên cần
        c.execute("""
            SELECT
                sub.id    AS subject_id,
                sub.name  AS subject_name,
                t.name    AS teacher_name,

                COUNT(a.id)                                     AS total,
                SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present,
                SUM(CASE WHEN a.status = 'Absent'  THEN 1 ELSE 0 END) AS absent,
                SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late,

                ROUND(
                  (
                    SUM(CASE WHEN a.status = 'Present' THEN 1.0 ELSE 0 END) + 0.75 * SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END))
                    / NULLIF(COUNT(a.id), 0) * 100
                ) AS rate

            FROM schedules sch
            JOIN subjects  sub ON sch.subject_id = sub.id
            JOIN teachers  t   ON sub.teacher_id  = t.id

            LEFT JOIN attendance a
                ON a.schedule_id = sch.id
                AND a.student_id  = %s

            WHERE sch.class_name = %s

            GROUP BY sub.id, sub.name, t.name
        """, (student_id, class_name))
        rows = c.fetchall()

        # Đảm bảo rate không NULL (môn chưa có buổi nào)
        for r in rows:
            r['rate']    = int(r['rate'])    if r['rate']    is not None else 0
            r['present'] = int(r['present']) if r['present'] is not None else 0
            r['absent']  = int(r['absent'])  if r['absent']  is not None else 0
            r['total']   = int(r['total'])   if r['total']   is not None else 0

        # ✅ Trả về đúng format mà frontend đọc: data.subjects
        return jsonify({"subjects": rows})

    except Exception as e:
        print("🔥 ERROR /api/my_subjects:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        c.close()
        conn.close()



# ================= API: CLASS STUDENTS BY SCHEDULE =================
@app.route("/api/manual_attendance/<int:schedule_id>")
def get_class_students(schedule_id):
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    conn = get_db()
    c = conn.cursor(dictionary=True)

    try:
        today = date.today()

        # Lấy thông tin schedule
        c.execute("""
            SELECT subject_id, class_name 
            FROM schedules 
            WHERE id = %s
        """, (schedule_id,))
        sch = c.fetchone()

        if not sch:
            return jsonify({"error": "Không tìm thấy lịch học"}), 404

        subject_id = sch["subject_id"]
        class_name = sch["class_name"]

        # Query chính
        c.execute("""
            SELECT
                st.id AS student_id,
                st.student_code,
                st.name,

                -- trạng thái hôm nay
                COALESCE(a.status, 'Absent') AS today_status,
                COALESCE(a.note, '') AS note,
                COALESCE(a.is_manual, FALSE) AS is_manual,

                -- tổng buổi
                (
                    SELECT COUNT(*)
                    FROM attendance a2
                    JOIN schedules s2 ON a2.schedule_id = s2.id
                    WHERE a2.student_id = st.id
                      AND s2.subject_id = %s
                      AND s2.class_name = %s
                ) AS total_sessions,

                -- present
                (
                    SELECT COUNT(*)
                    FROM attendance a2
                    JOIN schedules s2 ON a2.schedule_id = s2.id
                    WHERE a2.student_id = st.id
                      AND s2.subject_id = %s
                      AND s2.class_name = %s
                      AND a2.status = 'Present'
                ) AS present_count,

                -- absent
                (
                    SELECT COUNT(*)
                    FROM attendance a2
                    JOIN schedules s2 ON a2.schedule_id = s2.id
                    WHERE a2.student_id = st.id
                      AND s2.subject_id = %s
                      AND s2.class_name = %s
                      AND a2.status = 'Absent'
                ) AS absent_count,

                -- late
                (
                    SELECT COUNT(*)
                    FROM attendance a2
                    JOIN schedules s2 ON a2.schedule_id = s2.id
                    WHERE a2.student_id = st.id
                      AND s2.subject_id = %s
                      AND s2.class_name = %s
                      AND a2.status = 'Late'
                ) AS late_count

            FROM students st

            LEFT JOIN attendance a
                ON a.student_id = st.id
                AND a.schedule_id = %s
                AND DATE(a.checkin_time) = %s

            WHERE st.class_name = %s
            ORDER BY st.name
        """, (
            subject_id, class_name,
subject_id, class_name,
            subject_id, class_name,
            subject_id, class_name,
            schedule_id, today,
            class_name
        ))

        rows = c.fetchall()

        # xử lý thêm
        for r in rows:
            r['total_sessions'] = int(r['total_sessions'] or 0)
            r['present_count']  = int(r['present_count'] or 0)
            r['absent_count']   = int(r['absent_count'] or 0)
            r['late_count']     = int(r['late_count'] or 0)

            # tính điểm chuyên cần
            if r['total_sessions'] > 0:
                score = r['present_count'] * 1.0 + r['late_count'] * 0.75
                r['rate'] = round((score / r['total_sessions']) * 100)
            else:
                r['rate'] = 0

        return jsonify(rows)

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        c.close()
        conn.close()

@app.route("/api/attendance/manual", methods=["POST"])
def save_manual_attendance():
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    data = request.get_json()

    schedule_id = data.get("schedule_id")
    student_code = data.get("student_code")
    status = data.get("status")
    note = data.get("note", "")
    is_manual = True

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        # 🔹 Kiểm tra giờ học — chỉ cho sửa sau 15p kể từ start_time
        cursor.execute("SELECT start_time FROM schedules WHERE id = %s", (schedule_id,))
        sch = cursor.fetchone()

        if sch:
            # start_time từ MySQL trả về dạng timedelta
            start_td = sch["start_time"]
            start_dt = datetime.combine(date.today(), 
                           (datetime.min + start_td).time())
            allowed_from = start_dt + timedelta(minutes=15)

            if datetime.now() < allowed_from:
                return jsonify({
                    "error": f"Chưa đến giờ chỉnh sửa. Vui lòng chờ đến {allowed_from.strftime('%H:%M')}"
                }), 403

        # 🔹 lấy student_id
        cursor.execute("""
            SELECT id FROM students WHERE student_code = %s
        """, (student_code,))
        student = cursor.fetchone()

        if not student:
            return jsonify({"error": "Student not found"}), 404

        student_id = student["id"]

        # 🔹 check đã có record chưa
        cursor.execute("""
            SELECT id FROM attendance
            WHERE student_id = %s
              AND schedule_id = %s
              AND DATE(checkin_time) = CURDATE()
        """, (student_id, schedule_id))

        existed = cursor.fetchone()

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if existed:
            cursor.execute("""
                UPDATE attendance
                SET status = %s,
                    note = %s,
                    is_manual = %s
                WHERE id = %s
            """, (status, note, is_manual, existed["id"]))
        else:
            cursor.execute("""
                INSERT INTO attendance (student_id, schedule_id, checkin_time, status, note, is_manual)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (student_id, schedule_id, now, status, note, is_manual))

        db.commit()
        return jsonify({"message": "Saved"})

    except Exception as e:
        print("🔥 manual save error:", e)
        db.rollback()
        return jsonify({"error": "Server error"}), 500

    finally:
        cursor.close()
        db.close()


@app.route("/api/attendance/manual/bulk", methods=["POST"])
def save_bulk_attendance():
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401

    data = request.get_json()

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 🔹 Kiểm tra giờ 1 lần duy nhất (lấy schedule_id từ item đầu tiên)
        if data:
            schedule_id_check = data[0]["schedule_id"]
            cursor.execute("SELECT start_time FROM schedules WHERE id = %s", (schedule_id_check,))
            sch = cursor.fetchone()

            if sch:
                start_td = sch["start_time"]
                start_dt = datetime.combine(date.today(),
                               (datetime.min + start_td).time())
                allowed_from = start_dt + timedelta(minutes=15)

                if datetime.now() < allowed_from:
                    return jsonify({
                        "error": f"Chưa đến giờ chỉnh sửa. Vui lòng chờ đến {allowed_from.strftime('%H:%M')}"
                    }), 403

        for item in data:
            schedule_id = item["schedule_id"]
            student_code = item["student_code"]
            status = item["status"]
            note = item.get("note", "")

            cursor.execute("SELECT id FROM students WHERE student_code = %s", (student_code,))
            student = cursor.fetchone()

            if not student:
                print(f"⚠️ Không tìm thấy student_code: {student_code}")
                continue

            student_id = student["id"]

            cursor.execute("""
                SELECT id FROM attendance
                WHERE student_id = %s
                  AND schedule_id = %s
                  AND DATE(checkin_time) = CURDATE()
            """, (student_id, schedule_id))

            existed = cursor.fetchone()

            if existed:
                cursor.execute("""
                    UPDATE attendance
                    SET status=%s, note=%s, is_manual=TRUE
                    WHERE id=%s
                """, (status, note, existed["id"]))
            else:
                cursor.execute("""
                    INSERT INTO attendance
                    (student_id, schedule_id, checkin_time, status, note, is_manual)
                    VALUES (%s,%s,%s,%s,%s,TRUE)
                """, (student_id, schedule_id, now, status, note))

        db.commit()
        return jsonify({"message": "Bulk saved"})

    except Exception as e:
        print("🔥 bulk save error:", e)
        db.rollback()
        return jsonify({"error": "Server error"}), 500

    finally:
        cursor.close()
        db.close()


# ── API: danh sách lớp + số sinh viên ──
@app.route("/api/admin/classes")
def admin_classes():
    if "user" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    c = conn.cursor(dictionary=True)

    try:
        c.execute("""
            SELECT
                s.class_name,
                (
                    SELECT COUNT(*)
                    FROM students st
                    WHERE st.class_name = s.class_name
                ) AS count
            FROM schedules s
            GROUP BY s.class_name
            ORDER BY s.class_name
        """)

        return jsonify(c.fetchall())

    finally:
        c.close()
        conn.close()


# ── API: danh sách sinh viên theo lớp + thống kê ──
@app.route("/api/admin/class/<class_name>/students")
def admin_class_students(class_name):
    if "user" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    c = conn.cursor(dictionary=True)
    try:
        c.execute("""
            SELECT
                st.id, st.student_code AS code, st.name,
                SUM(a.status = 'Present') AS present,
                SUM(a.status = 'Absent')  AS absent,
                SUM(a.status = 'Late')    AS late,
                COUNT(a.id)               AS total,
                ROUND(
                  (SUM(a.status='Present') + 0.75*SUM(a.status='Late'))
                  / NULLIF(COUNT(a.id),0) * 100
                ) AS rate
            FROM students st
            LEFT JOIN attendance a ON a.student_id = st.id
            WHERE st.class_name = %s
            GROUP BY st.id, st.student_code, st.name
            ORDER BY st.name
        """, (class_name,))
        rows = c.fetchall()
        for r in rows:
            r['present'] = int(r['present'] or 0)
            r['absent']  = int(r['absent']  or 0)
            r['late']    = int(r['late']    or 0)
            r['total']   = int(r['total']   or 0)
            r['rate']    = int(r['rate']    or 0)
        return jsonify(rows)
    finally:
        c.close(); conn.close()


@app.route('/download_class_csv/<class_name>')
def download_class_csv(class_name):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/login")

    conn = get_db()
    c = conn.cursor(dictionary=True)
    try:
        c.execute("""
            SELECT
                st.student_code,
                st.name,
                st.class_name,
                SUM(a.status = 'Present') AS present,
                SUM(a.status = 'Absent') AS absent,
                SUM(a.status = 'Late') AS late,
                COUNT(a.id) AS total,
                ROUND(
                  (SUM(a.status='Present') + 0.75*SUM(a.status='Late'))
                  / NULLIF(COUNT(a.id),0) * 100
                ) AS rate
            FROM students st
            LEFT JOIN attendance a ON a.student_id = st.id
            WHERE st.class_name = %s
            GROUP BY st.id, st.student_code, st.name, st.class_name
            ORDER BY st.name
        """, (class_name,))

        rows = c.fetchall()

        out = io.StringIO()
        out.write("\ufeff")
        out.write("student_code,name,class_name,present,absent,late,total,rate\n")
        for r in rows:
            out.write(
                f"{r['student_code']},{r['name']},{r['class_name']}"
                f",{int(r['present'] or 0)},{int(r['absent'] or 0)},{int(r['late'] or 0)}"
                f",{int(r['total'] or 0)},{int(r['rate'] or 0)}\n"
            )

        mem = io.BytesIO(out.getvalue().encode('utf-8'))
        mem.seek(0)
        filename = f"attendance_{class_name.replace(' ', '_')}.csv"
        return send_file(
            mem,
            download_name=filename,
            as_attachment=True,
            mimetype="text/csv; charset=utf-8"
        )
    finally:
        c.close(); conn.close()


# ── API: chi tiết sinh viên theo từng môn ──
@app.route("/api/admin/student/<int:student_id>/subjects")
def admin_student_subjects(student_id):
    if "user" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    c = conn.cursor(dictionary=True)
    try:
        c.execute("""
            SELECT
                sub.name AS subject_name,
                t.name   AS teacher_name,
                SUM(a.status = 'Present') AS present,
                SUM(a.status = 'Absent')  AS absent,
                SUM(a.status = 'Late')    AS late,
                COUNT(a.id)               AS total,
                ROUND(
                  (SUM(a.status='Present') + 0.75*SUM(a.status='Late'))
                  / NULLIF(COUNT(a.id),0) * 100
                ) AS rate
            FROM attendance a
            JOIN schedules  sch ON a.schedule_id = sch.id
            JOIN subjects   sub ON sch.subject_id = sub.id
                  JOIN teachers   t   ON sub.teacher_id = t.id
            WHERE a.student_id = %s
            GROUP BY sub.id, sub.name, t.name
            ORDER BY sub.name
        """, (student_id,))
        rows = c.fetchall()
        for r in rows:
            r['present'] = int(r['present'] or 0)
            r['absent']  = int(r['absent']  or 0)
            r['late']    = int(r['late']    or 0)
            r['total']   = int(r['total']   or 0)
            r['rate']    = int(r['rate']    or 0)
        return jsonify(rows)
    finally:
        c.close(); conn.close()



# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)