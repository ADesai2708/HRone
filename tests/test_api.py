import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
import app.main as main_module

TEST_DB_NAME = "hrone_attendance_test"
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="session")
def test_db():
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    db = client[TEST_DB_NAME]
    old_db = main_module.db
    main_module.db = db
    main_module.create_indexes()

    yield db

    client.drop_database(TEST_DB_NAME)
    main_module.db = old_db
    client.close()


@pytest.fixture(autouse=True)
def clean_db(test_db):
    test_db.employees.delete_many({})
    test_db.attendance_logs.delete_many({})
    yield


@pytest.fixture
def client(test_db):
    return TestClient(main_module.app)


# --------------------------------------------------------------------------- #
# Helper to convert IST datetime to epoch milliseconds
# --------------------------------------------------------------------------- #
def to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# 1. Health endpoint success and failure
def test_health_success_and_failure(client, monkeypatch):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"

    # The health handler calls `client.admin.command("ping")`.
    # `client.admin` is a property that returns a **new** Database object on
    # every access, so monkeypatching the Database instance has no effect.
    # The correct approach is to replace main_module.client with a stub whose
    # .admin.command(...) raises PyMongoError — exactly what the handler catches.
    from pymongo.errors import PyMongoError

    class _AdminStub:
        def command(self, *args, **kwargs):
            raise PyMongoError("Simulated DB failure")

    class _ClientStub:
        admin = _AdminStub()

    monkeypatch.setattr(main_module, "client", _ClientStub())
    res_fail = client.get("/health")
    assert res_fail.status_code == 503
    # The handler raises HTTPException(503), so FastAPI returns {"detail": ...}.
    assert "detail" in res_fail.json()


# 2. Employee creation success and duplicate handling
def test_employee_creation_and_duplicate(client):
    payload = {
        "emp_code": "EMP0101",
        "name": "Aarav Sharma",
        "email": "aarav.sharma@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0101"
    assert "_id" not in data
    assert isinstance(data["created_at"], int)
    assert data["created_at"] >= 100000000000

    # Duplicate code returns 409
    res_dup = client.post("/employees", json=payload)
    assert res_dup.status_code == 409


# 3. Employee input validation
def test_employee_validation(client):
    # Invalid emp_code regex
    res = client.post(
        "/employees",
        json={
            "emp_code": "INVALID",
            "name": "Test",
            "email": "test@example.com",
            "department": "Engineering",
            "joined_on": "2026-01-05",
        },
    )
    assert res.status_code == 422

    # Shift start equal to shift end
    res = client.post(
        "/employees",
        json={
            "emp_code": "EMP0102",
            "name": "Test",
            "email": "test@example.com",
            "department": "Engineering",
            "shift_start": "10:00",
            "shift_end": "10:00",
            "joined_on": "2026-01-05",
        },
    )
    assert res.status_code == 422

    # Invalid email
    res = client.post(
        "/employees",
        json={
            "emp_code": "EMP0103",
            "name": "Test",
            "email": "not-an-email",
            "department": "Engineering",
            "joined_on": "2026-01-05",
        },
    )
    assert res.status_code == 422


# 4. Employee listing filters and pagination
def test_employee_listing_and_pagination(client):
    # Seed 25 employees (15 Engineering, 10 HR)
    for i in range(1, 26):
        dept = "Engineering" if i <= 15 else "HR"
        client.post(
            "/employees",
            json={
                "emp_code": f"EMP{i:04d}",
                "name": f"User {i}",
                "email": f"user{i}@example.com",
                "department": dept,
                "joined_on": "2026-01-01",
            },
        )

    # Page 1 default size 20
    res1 = client.get("/employees")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["total"] == 25
    assert len(data1["items"]) == 20
    assert data1["page"] == 1
    assert data1["page_size"] == 20
    assert data1["items"][0]["emp_code"] == "EMP0001"

    # Page 2
    res2 = client.get("/employees?page=2&page_size=20")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["items"]) == 5

    # Department filter exact match
    res_dept = client.get("/employees?department=HR")
    assert res_dept.status_code == 200
    data_dept = res_dept.json()
    assert data_dept["total"] == 10
    assert len(data_dept["items"]) == 10

    # Max page_size 100 validation
    res_err = client.get("/employees?page_size=101")
    assert res_err.status_code == 422


# 5. Punch-in success and duplicate handling
def test_punch_in_success_and_duplicate(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0001",
            "name": "User 1",
            "email": "u1@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    punch_in_time = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    epoch_ms = to_epoch_ms(punch_in_time)

    res = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": epoch_ms, "status": "PRESENT"},
    )
    # OpenAPI spec defines 201 Created for a successful punch-in.
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["date"] == "2026-01-05"
    assert data["status"] == "PRESENT"
    assert data["punch_in"] == epoch_ms
    assert data["late_minutes"] == 0

    # Duplicate on same date returns 409
    res_dup = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": epoch_ms + 1000},
    )
    assert res_dup.status_code == 409

    # Unknown employee returns 404
    res_404 = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP9999", "punched_at": epoch_ms},
    )
    assert res_404.status_code == 404


# 6. Late grace-period boundaries (10m vs 10m 1s)
def test_late_grace_period_boundaries(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0002",
            "name": "User 2",
            "email": "u2@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Exactly 10 minutes late (09:40:00 IST) -> late_minutes == 0
    t1 = datetime(2026, 1, 6, 9, 40, 0, tzinfo=IST)
    res1 = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(t1)},
    )
    # OpenAPI spec defines 201 Created for a successful punch-in.
    assert res1.status_code == 201
    assert res1.json()["late_minutes"] == 0

    # 10 minutes and 1 second late (09:40:01 IST) -> late_minutes == 10
    t2 = datetime(2026, 1, 7, 9, 40, 1, tzinfo=IST)
    res2 = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(t2)},
    )
    assert res2.status_code == 201
    assert res2.json()["late_minutes"] == 10


# 7. Overnight shift attendance-date calculations
def test_overnight_shift_date_calculation(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0003",
            "name": "User 3",
            "email": "u3@example.com",
            "department": "Security",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-01-01",
        },
    )

    # 05:45 IST on Oct 10 belongs to the Oct 9 shift
    t = datetime(2026, 10, 10, 5, 45, tzinfo=IST)
    res = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0003", "punched_at": to_epoch_ms(t)},
    )
    # OpenAPI spec defines 201 Created for a successful punch-in.
    assert res.status_code == 201
    assert res.json()["date"] == "2026-10-09"


# 8. Overtime threshold boundary (< 30 min -> 0, >= 30 min -> counted)
def test_overtime_threshold_boundary(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0004",
            "name": "User 4",
            "email": "u4@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    t_in = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(t_in)},
    )

    # 29 minutes after shift_end (18:59 IST) -> overtime_minutes == 0
    t_out_29 = datetime(2026, 1, 5, 18, 59, tzinfo=IST)
    res = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(t_out_29)},
    )
    assert res.status_code == 200
    assert res.json()["overtime_minutes"] == 0

    # Next day: punch-in at 09:30, punch-out at 19:05 (35 min after 18:30) -> 35 min
    t_in2 = datetime(2026, 1, 6, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(t_in2)},
    )
    t_out_35 = datetime(2026, 1, 6, 19, 5, tzinfo=IST)
    res2 = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(t_out_35)},
    )
    assert res2.status_code == 200
    assert res2.json()["overtime_minutes"] == 35


# 9. Work-hours half-up rounding & 10. Half-day threshold
def test_work_hours_and_half_day_threshold(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0005",
            "name": "User 5",
            "email": "u5@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Test work hours: 4 hours 29 minutes = 4.4833... -> 4.48 (< 4.50 -> half_day True)
    t_in = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0005", "punched_at": to_epoch_ms(t_in)},
    )
    t_out = datetime(2026, 1, 5, 13, 59, tzinfo=IST)
    res = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0005", "punched_at": to_epoch_ms(t_out)},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 4.48
    assert data["half_day"] is True

    # Test work hours: 4 hours 30 minutes = 4.50 (>= 4.50 -> half_day False)
    t_in2 = datetime(2026, 1, 6, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0005", "punched_at": to_epoch_ms(t_in2)},
    )
    t_out2 = datetime(2026, 1, 6, 14, 0, tzinfo=IST)
    res2 = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0005", "punched_at": to_epoch_ms(t_out2)},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["work_hours"] == 4.50
    assert data2["half_day"] is False


# 11. Punch-out validation and repeated punch-out
def test_punch_out_validation_and_repeated(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0006",
            "name": "User 6",
            "email": "u6@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Punch-out without punch-in -> 404
    t_out = datetime(2026, 1, 5, 18, 30, tzinfo=IST)
    res = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0006", "punched_at": to_epoch_ms(t_out)},
    )
    assert res.status_code == 404

    # Punch in
    t_in = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0006", "punched_at": to_epoch_ms(t_in)},
    )

    # Punch out earlier than punch in -> 422
    # The OpenAPI spec is explicit: "422 if punched_at <= punch_in".
    t_early = datetime(2026, 1, 5, 9, 0, tzinfo=IST)
    res_early = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0006", "punched_at": to_epoch_ms(t_early)},
    )
    assert res_early.status_code == 422

    # Successful punch out
    res_ok = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0006", "punched_at": to_epoch_ms(t_out)},
    )
    assert res_ok.status_code == 200

    # Repeated punch out -> 409
    res_repeat = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0006", "punched_at": to_epoch_ms(t_out) + 1000},
    )
    assert res_repeat.status_code == 409


# 13. Attendance listing and filtering
def test_attendance_listing_and_filtering(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0007",
            "name": "User 7",
            "email": "u7@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    # Create 3 records: Jan 5, Jan 6, Jan 7
    for day in [5, 6, 7]:
        t_in = datetime(2026, 1, day, 9, 30, tzinfo=IST)
        client.post(
            "/attendance/punch-in",
            json={"emp_code": "EMP0007", "punched_at": to_epoch_ms(t_in)},
        )

    # Query with date filter
    res = client.get("/attendance?emp_code=EMP0007&date_from=2026-01-05&date_to=2026-01-06")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["date"] == "2026-01-06"
    assert data["items"][1]["date"] == "2026-01-05"

    # Invalid range: date_from > date_to -> 422
    res_err = client.get("/attendance?date_from=2026-01-07&date_to=2026-01-06")
    assert res_err.status_code == 422


# 14. Regularization history and no-op behavior
def test_attendance_regularization(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0008",
            "name": "User 8",
            "email": "u8@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        },
    )

    t_in = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0008", "punched_at": to_epoch_ms(t_in)},
    )

    # No-op regularize -> 422
    res_noop = client.patch(
        "/attendance/EMP0008/2026-01-05",
        json={
            "status": "PRESENT",
            "reason": "Correcting attendance record",
            "regularized_by": "admin",
        },
    )
    assert res_noop.status_code == 422

    # Change status to WFH -> success, 1 history entry
    res_wfh = client.patch(
        "/attendance/EMP0008/2026-01-05",
        json={
            "status": "WFH",
            "reason": "Worked from home approved",
            "regularized_by": "manager",
        },
    )
    assert res_wfh.status_code == 200
    record = res_wfh.json()
    assert record["status"] == "WFH"
    assert len(record["history"]) == 1
    h = record["history"][0]
    assert h["by"] == "manager"
    assert h["reason"] == "Worked from home approved"
    assert "status" in h["changes"]
    assert h["changes"]["status"]["from"] == "PRESENT"
    assert h["changes"]["status"]["to"] == "WFH"

    # Regularize to LEAVE with punch_in provided -> 422
    res_leave_err = client.patch(
        "/attendance/EMP0008/2026-01-05",
        json={
            "status": "LEAVE",
            "punch_in": to_epoch_ms(t_in),
            "reason": "Should fail with punch times",
            "regularized_by": "admin",
        },
    )
    assert res_leave_err.status_code == 422

    # Regularize to LEAVE without punch times -> punches cleared
    res_leave = client.patch(
        "/attendance/EMP0008/2026-01-05",
        json={
            "status": "LEAVE",
            "reason": "Applied for sick leave",
            "regularized_by": "admin",
        },
    )
    assert res_leave.status_code == 200
    rec_leave = res_leave.json()
    assert rec_leave["status"] == "LEAVE"
    assert rec_leave["punch_in"] is None
    assert rec_leave["punch_out"] is None
    assert len(rec_leave["history"]) == 2


# 15. Monthly analytics with records and without records
def test_monthly_analytics(client):
    # EMP0009 joined mid-month on Jan 12, 2026 (Monday)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0009",
            "name": "User 9",
            "email": "u9@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-12",
        },
    )

    # In Jan 2026, working days from Jan 12 to Jan 31:
    # Week 1: Jan 12-16 (5 days)
    # Week 2: Jan 19-23 (5 days)
    # Week 3: Jan 26-30 (5 days)
    # Total = 15 working days.

    # 1. Without attendance logs -> valid zeros
    res_empty = client.get("/analytics/employees/EMP0009/monthly?month=2026-01")
    assert res_empty.status_code == 200
    data_empty = res_empty.json()
    assert data_empty["working_days"] == 15
    assert data_empty["present_days"] == 0.0
    assert data_empty["leave_days"] == 0
    assert data_empty["attendance_pct"] == 0.0

    # 2. Add records:
    # Full day on Jan 12: punch-in 09:30, punch-out 18:30
    # Half day on Jan 13: punch-in 09:30, punch-out 13:30 (4.0 hrs -> half_day True)
    # Late on Jan 14: punch-in 10:00 (30 min late), punch-out 18:30
    t_12_in = datetime(2026, 1, 12, 9, 30, tzinfo=IST)
    t_12_out = datetime(2026, 1, 12, 18, 30, tzinfo=IST)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_12_in)})
    client.post("/attendance/punch-out", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_12_out)})

    t_13_in = datetime(2026, 1, 13, 9, 30, tzinfo=IST)
    t_13_out = datetime(2026, 1, 13, 13, 30, tzinfo=IST)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_13_in)})
    client.post("/attendance/punch-out", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_13_out)})

    t_14_in = datetime(2026, 1, 14, 10, 0, tzinfo=IST)
    t_14_out = datetime(2026, 1, 14, 18, 30, tzinfo=IST)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_14_in)})
    client.post("/attendance/punch-out", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_14_out)})

    # Jan 15: Leave record
    t_15_in = datetime(2026, 1, 15, 9, 30, tzinfo=IST)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0009", "punched_at": to_epoch_ms(t_15_in)})
    client.patch("/attendance/EMP0009/2026-01-15", json={"status": "LEAVE", "reason": "Sick leave", "regularized_by": "admin"})

    res_with_logs = client.get("/analytics/employees/EMP0009/monthly?month=2026-01")
    assert res_with_logs.status_code == 200
    data = res_with_logs.json()
    assert data["working_days"] == 15
    # Full day (1.0) + Half day (0.5) + Full day (1.0) = 2.5 present days
    assert data["present_days"] == 2.5
    assert data["leave_days"] == 1
    assert data["late_count"] == 1
    assert data["total_late_minutes"] == 30
    # attendance_pct: 2.5 / 15 * 100 = 16.6667
    assert data["attendance_pct"] == 16.6667


# 17. Department summary headcount and non-inflated logs
def test_department_summary(client):
    # Two employees in Finance: E1 joined Jan 1, E2 joined Jan 15. E3 joined Feb 1.
    client.post("/employees", json={"emp_code": "EMP0011", "name": "F1", "email": "f1@ex.com", "department": "Finance", "joined_on": "2026-01-01"})
    client.post("/employees", json={"emp_code": "EMP0012", "name": "F2", "email": "f2@ex.com", "department": "Finance", "joined_on": "2026-01-15"})
    client.post("/employees", json={"emp_code": "EMP0013", "name": "F3", "email": "f3@ex.com", "department": "Finance", "joined_on": "2026-02-01"})

    # E1 has 3 attendance logs in Jan
    for d in [5, 6, 7]:
        t_in = datetime(2026, 1, d, 9, 30, tzinfo=IST)
        t_out = datetime(2026, 1, d, 18, 30, tzinfo=IST)
        client.post("/attendance/punch-in", json={"emp_code": "EMP0011", "punched_at": to_epoch_ms(t_in)})
        client.post("/attendance/punch-out", json={"emp_code": "EMP0011", "punched_at": to_epoch_ms(t_out)})

    # E2 has 0 attendance logs.
    res = client.get("/analytics/departments/summary?month=2026-01&department=Finance")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    dept = data["items"][0]
    # Headcount must be 2 (E1 + E2). E3 joined in Feb so excluded. Not inflated to 4 by multiple logs!
    assert dept["headcount"] == 2
    assert dept["present_days"] == 3.0
    assert dept["avg_work_hours"] == 9.0


# 18. Leaderboard ties and ranking cutoff
def test_late_leaderboard_ties(client):
    # 4 employees with late minutes
    client.post("/employees", json={"emp_code": "EMP0021", "name": "P1", "email": "p1@ex.com", "department": "Support", "joined_on": "2026-01-01"})
    client.post("/employees", json={"emp_code": "EMP0022", "name": "P2", "email": "p2@ex.com", "department": "Support", "joined_on": "2026-01-01"})
    client.post("/employees", json={"emp_code": "EMP0023", "name": "P3", "email": "p3@ex.com", "department": "Support", "joined_on": "2026-01-01"})
    client.post("/employees", json={"emp_code": "EMP0024", "name": "P4", "email": "p4@ex.com", "department": "Support", "joined_on": "2026-01-01"})

    # P1: 60 min late  (shift 09:30 -> punch-in 10:30)
    # P2: 40 min late  (shift 09:30 -> punch-in 10:10)
    # P3: 40 min late  (shift 09:30 -> punch-in 10:10)
    # P4: 15 min late  (shift 09:30 -> punch-in 09:45)
    # Ranks: P1 -> 1, P2 -> 2, P3 -> 2, P4 -> 4 (standard competition ranking: 1, 2, 2, 4)
    #
    # Use timedelta arithmetic to avoid minute-overflow (e.g. 9:30 + 60 min
    # cannot be written as datetime(..., minute=90), which raises ValueError).
    shift_start = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    for code, late_m in [("EMP0021", 60), ("EMP0022", 40), ("EMP0023", 40), ("EMP0024", 15)]:
        t_in = shift_start + timedelta(minutes=late_m)
        client.post("/attendance/punch-in", json={"emp_code": code, "punched_at": to_epoch_ms(t_in)})

    # Limit = 2: should return EMP0021 (rank 1), EMP0022 (rank 2), EMP0023 (rank 2) = 3 items!
    res = client.get("/analytics/leaderboard/late?month=2026-01&limit=2&department=Support")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 3
    assert data["items"][0]["rank"] == 1
    assert data["items"][0]["emp_code"] == "EMP0021"
    assert data["items"][1]["rank"] == 2
    assert data["items"][2]["rank"] == 2


# 19. Department trend weekends, missing dates, moving average
def test_department_trend(client):
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0031",
            "name": "Dev",
            "email": "dev@ex.com",
            "department": "Platform",
            "joined_on": "2026-01-01",
        },
    )

    # Mon Jan 5 to Sun Jan 11 (7 days)
    # Punch in on Jan 5 (Mon) and Jan 6 (Tue)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0031", "punched_at": to_epoch_ms(datetime(2026, 1, 5, 9, 30, tzinfo=IST))})
    client.post("/attendance/punch-in", json={"emp_code": "EMP0031", "punched_at": to_epoch_ms(datetime(2026, 1, 6, 9, 30, tzinfo=IST))})

    res = client.get("/analytics/departments/Platform/trend?from=2026-01-05&to=2026-01-11")
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) == 7

    # Jan 5 (Mon): working day, headcount 1, present 1, rate 1.0, moving_avg 1.0
    assert items[0]["date"] == "2026-01-05"
    assert items[0]["is_working_day"] is True
    assert items[0]["attendance_rate"] == 1.0

    # Jan 6 (Tue): rate 1.0, moving_avg (1.0 + 1.0)/2 = 1.0
    assert items[1]["date"] == "2026-01-06"
    assert items[1]["attendance_rate"] == 1.0

    # Jan 7 (Wed): working day with no attendance -> rate 0.0
    assert items[2]["date"] == "2026-01-07"
    assert items[2]["is_working_day"] is True
    assert items[2]["attendance_rate"] == 0.0

    # Jan 10 (Sat): weekend -> attendance_rate is None
    assert items[5]["date"] == "2026-01-10"
    assert items[5]["is_working_day"] is False
    assert items[5]["attendance_rate"] is None


# 20. Department trend validations (> 92 days, to < from, 404)
def test_department_trend_validations(client):
    client.post(
        "/employees",
        json={"emp_code": "EMP0032", "name": "Dev2", "email": "dev2@ex.com", "department": "Platform", "joined_on": "2026-01-01"},
    )

    # > 92 days -> 422
    res_long = client.get("/analytics/departments/Platform/trend?from=2026-01-01&to=2026-05-01")
    assert res_long.status_code == 422

    # to < from -> 422
    res_rev = client.get("/analytics/departments/Platform/trend?from=2026-01-10&to=2026-01-05")
    assert res_rev.status_code == 422

    # Unknown department -> 404
    res_404 = client.get("/analytics/departments/GhostDept/trend?from=2026-01-01&to=2026-01-05")
    assert res_404.status_code == 404


# 21. Admin explain endpoints and index usage
def test_admin_explain_endpoints(client):
    client.post(
        "/employees",
        json={"emp_code": "EMP0041", "name": "ExUser", "email": "ex@ex.com", "department": "QA", "joined_on": "2026-01-01"},
    )

    # 1. attendance_list
    exp1 = client.get("/admin/explain/attendance_list?emp_code=EMP0041")
    assert exp1.status_code == 200
    d1 = exp1.json()
    assert d1["collection"] == "attendance_logs"
    assert "explain" in d1

    # 2. employee_monthly
    exp2 = client.get("/admin/explain/employee_monthly?emp_code=EMP0041&month=2026-01")
    assert exp2.status_code == 200
    d2 = exp2.json()
    assert d2["collection"] == "attendance_logs"
    assert "explain" in d2

    # 3. department_summary
    exp3 = client.get("/admin/explain/department_summary?month=2026-01&department=QA")
    assert exp3.status_code == 200
    d3 = exp3.json()
    assert d3["collection"] == "employees"
    assert "explain" in d3

    # 4. late_leaderboard
    exp4 = client.get("/admin/explain/late_leaderboard?month=2026-01")
    assert exp4.status_code == 200
    d4 = exp4.json()
    assert d4["collection"] == "attendance_logs"
    assert "explain" in d4

    # 5. department_trend
    exp5 = client.get("/admin/explain/department_trend?department=QA&from=2026-01-01&to=2026-01-10")
    assert exp5.status_code == 200
    d5 = exp5.json()
    assert d5["collection"] == "employees"
    assert "explain" in d5

    # 6. Invalid endpoint name -> 422
    exp_err = client.get("/admin/explain/invalid_endpoint")
    assert exp_err.status_code == 422


# 22. Department trend with no attendance records (regression for $densify empty-range fix)
def test_department_trend_no_attendance(client):
    """A department that has employees but zero attendance logs must return
    the correct number of rows with rate 0.0 on working days and None on weekends."""
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0051",
            "name": "NoLog",
            "email": "nolog@ex.com",
            "department": "EmptyDept",
            "joined_on": "2026-01-01",
        },
    )

    # Mon Jan 5 to Fri Jan 9 (5 working days, no weekend in range, no attendance)
    res = client.get("/analytics/departments/EmptyDept/trend?from=2026-01-05&to=2026-01-09")
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) == 5

    for item in items:
        # All days Mon–Fri are working days
        assert item["is_working_day"] is True
        # headcount is 1 (EMP0051 joined Jan 1, before each day)
        assert item["headcount"] == 1
        # No attendance records -> present_count 0, rate 0.0
        assert item["present_count"] == 0
        assert item["attendance_rate"] == 0.0
