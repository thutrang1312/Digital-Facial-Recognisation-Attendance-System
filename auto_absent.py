import json
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from db import get_db




def _insert_absent_for_ended_sessions():
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)


    try:
        today = date.today()
        now = datetime.now()


        # ── 1. Lấy schedule ──
        cursor.execute("""
            SELECT s.id AS schedule_id,
                   s.start_date,
                   s.end_date,
                   s.end_time,
                   s.days_of_week,
                   sub.class_name
            FROM schedules s
            JOIN subjects sub ON s.subject_id = sub.id
            WHERE s.start_date <= %s
              AND s.end_date   >= %s
        """, (today, today))


        schedules = cursor.fetchall()
        if not schedules:
            return


        for sch in schedules:
            schedule_id = sch['schedule_id']
            class_name = sch['class_name']
            start_date = sch['start_date']


            # ── Parse end_time ──
            end_time_raw = sch['end_time']
            if isinstance(end_time_raw, timedelta):
                end_seconds = int(end_time_raw.total_seconds())
            else:
                h, m, s = str(end_time_raw).split(":")
                end_seconds = int(h) * 3600 + int(m) * 60 + int(s)


            # ── Parse days_of_week ──
            days_raw = sch.get('days_of_week')
            if not days_raw:
                continue


            try:
                days_list = json.loads(days_raw) if isinstance(days_raw, str) else days_raw
            except:
                continue


            if not days_list:
                continue


            # ── Lấy sinh viên ──
            cursor.execute("""
                SELECT id FROM students WHERE class_name = %s
            """, (class_name,))
            students = cursor.fetchall()


            if not students:
                continue


            # ── 2. Duyệt từ start_date → today ──
            current_date = start_date


            while current_date <= today:


                # đúng thứ trong tuần
                if current_date.weekday() in days_list:


                    # 👉 tạo datetime kết thúc tiết học
                    session_end_datetime = datetime.combine(
                        current_date,
                        (datetime.min + timedelta(seconds=end_seconds)).time()
                    )


                    # 👉 nếu chưa kết thúc thì bỏ qua
                    if now < session_end_datetime:
                        current_date += timedelta(days=1)
                        continue


                    checkin_time_str = session_end_datetime.strftime('%Y-%m-%d %H:%M:%S')


                    # ── check đã có attendance chưa ──
                    cursor.execute("""
                        SELECT student_id FROM attendance
                        WHERE schedule_id = %s
                          AND DATE(checkin_time) = %s
                    """, (schedule_id, current_date))


                    existing_ids = {row['student_id'] for row in cursor.fetchall()}


                    # ── insert batch ──
                    to_insert = []
                    for st in students:
                        if st['id'] not in existing_ids:
                            to_insert.append((st['id'], schedule_id, checkin_time_str))


                    if to_insert:
                        cursor.executemany("""
                            INSERT INTO attendance (student_id, schedule_id, checkin_time, status)
                            VALUES (%s, %s, %s, 'Absent')
                        """, to_insert)


                current_date += timedelta(days=1)


        db.commit()
        print(f"[auto_absent] ✅ Done {now}")


    except Exception as e:
        print(f"[auto_absent] 🔥 Lỗi: {e}")
        db.rollback()


    finally:
        cursor.close()
        db.close()




def start_auto_absent_scheduler():
    scheduler = BackgroundScheduler()


    # 🔥 chạy ngay để backfill (fix thiếu 04/05)
    _insert_absent_for_ended_sessions()


    scheduler.add_job(
        _insert_absent_for_ended_sessions,
        trigger='interval',
        minutes=1,
        id='auto_absent',
        replace_existing=True,
        max_instances=1
    )


    scheduler.start()
    print("[auto_absent] 🚀 Scheduler started (5 phút)")


    return scheduler

