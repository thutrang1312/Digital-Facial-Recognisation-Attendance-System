import os
import time
from flask import Blueprint, request, jsonify, session, render_template, redirect
from db import get_db
from config import DATASET_DIR
from model import add_embeddings_for_student

dataset = Blueprint("dataset", __name__)

@dataset.route("/add_student", methods=["GET"])
def add_student_page():
    if "user" not in session:
        return redirect("/login")
    return render_template("add_student.html")




@dataset.route("/add_student", methods=["POST"])
def add_student():
    """
    Dùng cho trang add_student.html (admin).
    Form fields: name, student_code, class_name, email, phone
    Trả về: { student_id: <int> }
    """
    if "user" not in session:
        return jsonify({"error": "Chưa đăng nhập"}), 401


    name         = request.form.get("name", "").strip()
    student_code = request.form.get("student_code", "").strip()
    class_name   = request.form.get("class_name", "").strip()
    email        = request.form.get("email", "").strip()
    phone        = request.form.get("phone", "").strip()


    if not name:
        return jsonify({"error": "Tên không được trống"}), 400


    conn = get_db()
    c    = conn.cursor()


    c.execute("""
        INSERT INTO students (student_code, name, class_name, email, phone, embedding_status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
    """, (student_code or None,
          name,
          class_name or None,
          email or None,
          phone or None))
    conn.commit()
    student_id = c.lastrowid


    # Tạo folder dataset
    face_folder = os.path.join(DATASET_DIR, str(student_id))
    os.makedirs(face_folder, exist_ok=True)
    c.execute("UPDATE students SET face_folder=%s WHERE id=%s",
              (face_folder, student_id))
    conn.commit()
    conn.close()


    return jsonify({"student_id": student_id})




@dataset.route("/upload_face", methods=["POST"])
def upload_face():
    sid = request.form.get("student_id", "").strip()
    files = request.files.getlist("images[]")


    if not sid:
        return jsonify({"error": "Thiếu student_id"}), 400


    try:
        sid = int(sid)
    except:
        return jsonify({"error": "student_id không hợp lệ"}), 400


    if not files:
        return jsonify({"error": "Không có ảnh upload"}), 400


    conn = get_db()
    c = conn.cursor(dictionary=True)


    # check student tồn tại
    c.execute("SELECT id, face_folder FROM students WHERE id=%s", (sid,))
    student = c.fetchone()


    if not student:
        conn.close()
        return jsonify({"error": "Student không tồn tại"}), 404


    folder = student["face_folder"] or os.path.join(DATASET_DIR, str(sid))
    os.makedirs(folder, exist_ok=True)

    saved = 0
    saved_paths = []
    for i, f in enumerate(files):
        filename = f"{int(time.time()*1000)}_{i}.jpg"
        path = os.path.join(folder, filename)
        f.save(path)
        saved += 1
        saved_paths.append(path)

    # Cố gắng train incremental từ ảnh vừa upload
    file_objs = []
    try:
        for path in saved_paths:
            file_objs.append(open(path, "rb"))

        ok, message = add_embeddings_for_student(sid, file_objs)

        status_value = 'trained' if ok else 'captured'
        c.execute("""
            UPDATE students
            SET embedding_status=%s
            WHERE id=%s
        """, (status_value, sid))
        conn.commit()

        if ok:
            response = {"status": "ok", "saved": saved, "message": "Upload thành công và đã huấn luyện mô hình"}
        else:
            response = {"status": "fail", "saved": saved, "message": message}

    finally:
        for fh in file_objs:
            try:
                fh.close()
            except:
                pass

    c.close()
    conn.close()

    return jsonify(response)
