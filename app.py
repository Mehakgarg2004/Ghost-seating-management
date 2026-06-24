from flask import Flask, request, jsonify, render_template, redirect
import json
from flask_mysqldb import MySQL
from dotenv import load_dotenv
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os

load_dotenv()

app = Flask(__name__)

app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')
app.secret_key = os.getenv('SECRET_KEY')

mysql = MySQL(app)

@app.route('/')
def home():
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM seats")
    count = cursor.fetchone()
    return f'Database connected! Total seats: {count[0]}'

@app.route('/allocate', methods=['POST'])
def allocate():
    data = request.get_json()
    seat_number = data.get('seat_number')
    student_roll = data.get('student_roll')

    cursor = mysql.connection.cursor()

    cursor.execute("SELECT is_blocked, blocked_until FROM penalties WHERE student_roll = %s", (student_roll,))
    penalty = cursor.fetchone()
    if penalty and penalty[0] and penalty[1] > datetime.now():
        return jsonify({'success': False, 'message': 'You are blocked from booking seats for 24 hours due to ghost-seat reports!'})

    cursor.execute("SELECT * FROM seats WHERE seat_number = %s", (seat_number,))
    seat = cursor.fetchone()

    if seat[2]:
        return jsonify({'success': False, 'message': 'Seat already occupied!'})

    allocated_at = datetime.now()
    expires_at = allocated_at + timedelta(hours=2)

    cursor.execute("""
        UPDATE seats 
        SET is_occupied=TRUE, student_roll=%s, allocated_at=%s, expires_at=%s 
        WHERE seat_number=%s
    """, (student_roll, allocated_at, expires_at, seat_number))
    mysql.connection.commit()

    return jsonify({'success': True, 'message': f'Seat {seat_number} allocated to {student_roll} for 2 hours!'})

@app.route('/release', methods=['POST'])
def release():
    data = request.get_json()
    seat_number = data.get('seat_number')

    cursor = mysql.connection.cursor()
    cursor.execute("""
        UPDATE seats 
        SET is_occupied=FALSE, student_roll=NULL, allocated_at=NULL, expires_at=NULL 
        WHERE seat_number=%s
    """, (seat_number,))
    mysql.connection.commit()

    return jsonify({'success': True, 'message': f'Seat {seat_number} released!'})

@app.route('/seats', methods=['GET'])
def get_seats():
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT seat_number, is_occupied, student_roll, expires_at FROM seats")
    rows = cursor.fetchall()

    seats = []
    for row in rows:
        seats.append({
            'seat_number': row[0],
            'is_occupied': bool(row[1]),
            'student_roll': row[2],
            'expires_at': str(row[3]) if row[3] else None
        })

    return jsonify(seats)

@app.route('/report', methods=['POST'])
def report():
    data = request.get_json()
    seat_number = data.get('seat_number')

    cursor = mysql.connection.cursor()
    cursor.execute("SELECT student_roll FROM seats WHERE seat_number = %s", (seat_number,))
    seat = cursor.fetchone()

    if not seat or not seat[0]:
        return jsonify({'success': False, 'message': 'Seat is not occupied!'})

    student_roll = seat[0]

    cursor.execute("SELECT * FROM penalties WHERE student_roll = %s", (student_roll,))
    penalty = cursor.fetchone()

    if not penalty:
        cursor.execute("""
            INSERT INTO penalties (student_roll, report_count, is_blocked, last_reported) 
            VALUES (%s, 1, FALSE, NOW())
        """, (student_roll,))
    else:
        new_count = penalty[2] + 1
        is_blocked = new_count >= 2
        blocked_until = datetime.now() + timedelta(hours=24) if is_blocked else None
        cursor.execute("""
            UPDATE penalties 
            SET report_count=%s, is_blocked=%s, blocked_until=%s, last_reported=NOW()
            WHERE student_roll=%s
        """, (new_count, is_blocked, blocked_until, student_roll))

    mysql.connection.commit()

    if penalty and penalty[2] + 1 >= 2:
        return jsonify({'success': True, 'message': f'{student_roll} has been blocked for 24 hours!'})
    return jsonify({'success': True, 'message': f'Warning issued to {student_roll}!'})

@app.route('/penalties', methods=['GET'])
def get_penalties():
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT student_roll, report_count, is_blocked, blocked_until FROM penalties")
    rows = cursor.fetchall()

    penalties = []
    for row in rows:
        penalties.append({
            'student_roll': row[0],
            'report_count': row[1],
            'is_blocked': bool(row[2]),
            'blocked_until': str(row[3]) if row[3] else None
        })

    return jsonify(penalties)
@app.route('/analytics', methods=['GET'])
def analytics():
    cursor = mysql.connection.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM seats WHERE is_occupied = TRUE")
    occupied = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM seats WHERE is_occupied = FALSE")
    free = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM seats")
    total = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT HOUR(allocated_at), COUNT(*) 
        FROM seats 
        WHERE allocated_at IS NOT NULL 
        GROUP BY HOUR(allocated_at) 
        ORDER BY HOUR(allocated_at)
    """)
    hourly = cursor.fetchall()
    
    hourly_data = {str(row[0]): row[1] for row in hourly}
    
    utilization = round((occupied / total) * 100, 1) if total > 0 else 0
    
    return jsonify({
        'total': total,
        'occupied': occupied,
        'free': free,
        'utilization_percent': utilization,
        'hourly_data': hourly_data
    })
@app.route('/qr/<seat_number>')
def generate_qr(seat_number):
    import qrcode
    import io
    from flask import send_file
    
    url = f'http://127.0.0.1:5000/scan/{seat_number}'
    
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color='black', back_color='white')
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    return send_file(buf, mimetype='image/png')
def auto_expire_seats():
    with app.app_context():
        cursor = mysql.connection.cursor()
        cursor.execute("""
            UPDATE seats 
            SET is_occupied=FALSE, student_roll=NULL, allocated_at=NULL, expires_at=NULL 
            WHERE is_occupied=TRUE AND expires_at < NOW()
        """)
        mysql.connection.commit()
        cursor.close()

scheduler = BackgroundScheduler()
scheduler.add_job(func=auto_expire_seats, trigger='interval', minutes=1)
scheduler.start()

from flask import render_template
import json

@app.route('/admin')
def admin():
    cursor = mysql.connection.cursor()
    
    cursor.execute("SELECT seat_number, is_occupied, student_roll, allocated_at, expires_at FROM seats")
    rows = cursor.fetchall()
    
    seats = []
    for row in rows:
        now = datetime.now()
        expires = row[4]
        if not row[1]:
            status = 'free'
        elif expires and (expires - now).total_seconds() < 900:
            status = 'expiring'
        else:
            status = 'occupied'
        seats.append({
            'seat_number': row[0],
            'is_occupied': bool(row[1]),
            'student_roll': row[2] or '',
            'expires_at': str(row[4]) if row[4] else None,
            'status': status
        })

    cursor.execute("SELECT student_roll, report_count, is_blocked, blocked_until FROM penalties")
    pen_rows = cursor.fetchall()
    penalties = [{'student_roll': r[0], 'report_count': r[1], 'is_blocked': bool(r[2]), 'blocked_until': str(r[3]) if r[3] else None} for r in pen_rows]

    total = len(seats)
    available = sum(1 for s in seats if s['status'] == 'free')
    occupied = sum(1 for s in seats if s['status'] != 'free')
    expiring = sum(1 for s in seats if s['status'] == 'expiring')
    blocked_count = sum(1 for p in penalties if p['is_blocked'])
    occ_pct = round((occupied / total) * 100) if total > 0 else 0
    avail_pct = round((available / total) * 100) if total > 0 else 0

    return render_template('admin.html',
        seats=seats,
        seats_json=json.dumps(seats),
        penalties=penalties,
        activity=[],
        total_seats=total,
        available=available,
        occupied=occupied,
        expiring=expiring,
        blocked_count=blocked_count,
        occ_pct=occ_pct,
        avail_pct=avail_pct
    )

@app.route('/scan/<seat_number>')
def scan(seat_number):
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT is_occupied, student_roll, expires_at FROM seats WHERE seat_number = %s", (seat_number,))
    seat = cursor.fetchone()

    if not seat:
        return "Seat not found", 404

    is_occupied = bool(seat[0])
    expires_at = str(seat[2]) if seat[2] else None

    return render_template('scan.html',
        seat_number=seat_number,
        is_occupied=is_occupied,
        expires_at=expires_at,
        is_blocked=False,
        blocked_until=None
    )

@app.route('/analytics-page')
def analytics_page():
    cursor = mysql.connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM seats WHERE is_occupied = TRUE")
    occupied = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM seats")
    total = cursor.fetchone()[0]

    cursor.execute("""
        SELECT HOUR(allocated_at), COUNT(*) 
        FROM seats 
        WHERE allocated_at IS NOT NULL 
        GROUP BY HOUR(allocated_at) 
        ORDER BY HOUR(allocated_at)
    """)
    hourly_rows = cursor.fetchall()

    all_hours = {str(h): 0 for h in range(8, 21)}
    for row in hourly_rows:
        all_hours[str(row[0])] = row[1]

    max_val = max(all_hours.values()) if all_hours else 1
    hourly_labels = list(all_hours.keys())
    hourly_data = [round((v / max_val) * 100) if max_val > 0 else 0 for v in all_hours.values()]

    cursor.execute("SELECT seat_number, COUNT(*) as bookings FROM seats GROUP BY seat_number")
    hmap_rows = cursor.fetchall()
    max_bookings = max([r[1] for r in hmap_rows], default=1)
    heatmap_data = [{'seat': r[0], 'pct': round((r[1] / max_bookings) * 100)} for r in hmap_rows]

    day_stats = [
        {'name': 'Mon', 'pct': 68},
        {'name': 'Tue', 'pct': 74},
        {'name': 'Wed', 'pct': 82},
        {'name': 'Thu', 'pct': 88},
        {'name': 'Fri', 'pct': 70},
        {'name': 'Sat', 'pct': 45},
    ]

    occ_pct = round((occupied / total) * 100) if total > 0 else 0

    return render_template('analytics.html',
        total_sessions=occupied,
        peak_pct=occ_pct,
        peak_hour='12pm',
        expired_count=0,
        avg_session=90,
        hourly_labels=json.dumps(hourly_labels),
        hourly_data=json.dumps(hourly_data),
        heatmap_data=json.dumps(heatmap_data),
        day_stats=day_stats,
        logs=[]
    )
@app.route('/free', methods=['POST'])
def free_seat():
    seat_number = request.form.get('seat_number')
    cursor = mysql.connection.cursor()
    cursor.execute("""
        UPDATE seats 
        SET is_occupied=FALSE, student_roll=NULL, allocated_at=NULL, expires_at=NULL 
        WHERE seat_number=%s
    """, (seat_number,))
    mysql.connection.commit()
    return redirect('/admin')



if __name__ == '__main__':
    app.run(debug=True)