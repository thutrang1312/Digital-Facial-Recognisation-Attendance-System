from flask import Blueprint, request, render_template, redirect, session, jsonify
from db import get_db
import os
from config import DATASET_DIR

auth = Blueprint("auth", __name__)


# ================= LOGIN =================
@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        c = conn.cursor(dictionary=True)

        c.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = c.fetchone()
        conn.close()

        if user and user["password"] == password:
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/dashboard")

        return render_template("login.html", error="Sai tài khoản hoặc mật khẩu")

    return render_template("login.html")


# ================= REGISTER =================
@auth.route("/register", methods=["POST"])
def register():
    try:
        name         = request.form.get("name", "").strip()
        student_code = request.form.get("student_code", "").strip()
        class_name   = request.form.get("class_name", "").strip()
        email        = request.form.get("email", "").strip()
        phone        = request.form.get("phone", "").strip()
        password     = request.form.get("password", "").strip()

        # Validate
        if not name or not student_code or not class_name:
            return jsonify({"error": "Thiếu thông tin"}), 400

        if len(password) < 8:
            return jsonify({"error": "Mật khẩu phải >= 8 ký tự"}), 400

        conn = get_db()
        c = conn.cursor(dictionary=True)

        # check trùng student
        c.execute("SELECT id FROM students WHERE student_code=%s", (student_code,))
        if c.fetchone():
            return jsonify({"error": "Mã sinh viên đã tồn tại"}), 409

        # check trùng user
        c.execute("SELECT id FROM users WHERE username=%s", (student_code,))
        if c.fetchone():
            return jsonify({"error": "Tài khoản đã tồn tại"}), 409

        # ================= 1. INSERT USER =================
        c.execute(
            "INSERT INTO users(username, password, role) VALUES (%s,%s,%s)",
            (student_code, password, "student")
        )
        user_id = c.lastrowid

        # ================= 2. INSERT STUDENT =================
        c.execute("""
            INSERT INTO students
            (student_code, name, class_name, email, phone, face_folder, embedding_status, user_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            student_code,
            name,
            class_name,
            email or None,
            phone or None,
            "",  # tạm thời
            "pending",
            user_id
        ))

        student_id = c.lastrowid

        # ================= 3. CREATE FOLDER =================
        face_folder = os.path.join(DATASET_DIR, str(student_id))
        os.makedirs(face_folder, exist_ok=True)

        # update folder
        c.execute(
            "UPDATE students SET face_folder=%s WHERE id=%s",
            (face_folder, student_id)
        )

        conn.commit()
        conn.close()

        return jsonify({
            "student_id": student_id,
            "user_id": user_id,
            "message": "Đăng ký thành công"
        }), 201

    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": str(e)}), 500


# ================= LOGOUT =================
@auth.route("/logout")
def logout():
    session.clear()
    return redirect("/login")