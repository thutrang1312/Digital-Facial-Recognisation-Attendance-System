import mysql.connector
from config import DB_NAME

def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="131205",
        database=DB_NAME
    )

def init_mysql():
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="131205"
    )
    cursor = conn.cursor()

    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    cursor.execute(f"USE {DB_NAME}")

    # 1. BẢNG USERS (Lưu tài khoản chung cho Admin, Teacher, Student)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL,
        role ENUM('admin', 'teacher', 'student') DEFAULT 'student'
    )
    """)

    # 2. BẢNG TEACHERS (Thông tin chi tiết giáo viên)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(100),
        department VARCHAR(100),
        user_id INT UNIQUE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    # 3. BẢNG SUBJECTS (Môn học)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INT AUTO_INCREMENT PRIMARY KEY,
        subject_code VARCHAR(20) UNIQUE,
        name VARCHAR(100) NOT NULL,
        teacher_id INT,
        FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE SET NULL
    )
    """)
    

    # 4. BẢNG STUDENTS (Thông tin chi tiết sinh viên)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INT AUTO_INCREMENT PRIMARY KEY,
        student_code VARCHAR(20) UNIQUE NOT NULL,
        name VARCHAR(100) NOT NULL,
        class_name VARCHAR(50),
        email VARCHAR(100),
        phone VARCHAR(20),
        face_folder VARCHAR(255),
        embedding_status VARCHAR(20) DEFAULT 'none',
        user_id INT UNIQUE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    # 5. BẢNG SCHEDULES (Lịch học chi tiết - Có giờ bắt đầu/kết thúc)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INT AUTO_INCREMENT PRIMARY KEY,
        subject_id INT,
        class_name VARCHAR(50),
        date DATE NOT NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        room VARCHAR(20),
        FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            subject_id INT,
            class_name VARCHAR(50),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            days_of_week JSON,   -- [1,2,3]

            start_time TIME NOT NULL,
            end_time TIME NOT NULL,
            room VARCHAR(20),

            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        )
        """)
    # 6. BẢNG ATTENDANCE (Điểm danh - Tính toán trạng thái Muộn/Đúng giờ)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INT AUTO_INCREMENT PRIMARY KEY,
        student_id INT,
        schedule_id INT,
        checkin_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        status ENUM('Present', 'Late', 'Absent') DEFAULT 'Present',
        note TEXT,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
        FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
    )
    """)

    # 7. BẢNG ASSIGNMENTS (Bài tập - Có hạn chót)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assignments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        subject_id INT,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        due_date DATETIME NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
    )
    """)

    # 8. BẢNG SUBMISSIONS (Sinh viên nộp bài)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        assignment_id INT,
        student_id INT,
        file_path VARCHAR(255),
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        grade FLOAT DEFAULT NULL,
        FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
    )
    """)

    # KHỞI TẠO ADMIN MẶC ĐỊNH
    cursor.execute("SELECT * FROM users WHERE username='admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users(username, password, role) VALUES (%s, %s, %s)",
            ("admin", "123456", "admin")
        )

    conn.commit()
    cursor.close()
    conn.close()
    print("Database Re-built Successfully!")