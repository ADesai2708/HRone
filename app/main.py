"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
# Load environment variables from the .env file.
load_dotenv()

# Read database configuration from environment variables.
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

if not MONGO_URI or not MONGO_DB:
    raise RuntimeError(
        "MONGO_URI and MONGO_DB must be configured in the environment."
    )

# Create the MongoDB client.
client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=3000,
)

# Select the application database.
db = client[MONGO_DB]
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
def truncate_to_seconds(value: datetime) -> datetime:
    """Remove microseconds without changing the represented instant."""
    return value.replace(microsecond=0)
def round_half_up(value: float, places: int = 2) -> float:
    """Round a numeric value using the ROUND_HALF_UP rule."""
    quantum = Decimal("1").scaleb(-places)

    rounded = Decimal(str(value)).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )

    return float(rounded)
def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    """Calculate worked hours rounded to two decimal places."""

    # Remove fractional seconds before calculating the duration.
    punch_in = truncate_to_seconds(punch_in)
    punch_out = truncate_to_seconds(punch_out)

    # Calculate the duration in seconds.
    duration_seconds = (
        punch_out - punch_in
    ).total_seconds()

    # Convert seconds into hours.
    hours = duration_seconds / 3600

    # Apply the assignment's half-up rounding rule.
    return round_half_up(hours, 2)
def compute_half_day(work_hours: float) -> bool:
    """Return True when rounded work hours are below 4.50."""
    return work_hours < 4.50
def compute_overtime_minutes(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    attendance_date: str,
) -> int:
    """Calculate qualifying overtime minutes."""

    # Remove fractional seconds before calculating overtime.
    punch_out = truncate_to_seconds(punch_out)

    # Parse the shift start and end times.
    start_hour, start_minute = map(
        int, shift_start.split(":")
    )
    end_hour, end_minute = map(
        int, shift_end.split(":")
    )

    # Build the scheduled shift end in IST.
    shift_end_time = datetime.fromisoformat(
        attendance_date
    ).replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )

    # An end time at or before the start time indicates
    # that the shift ends on the following calendar day.
    if (end_hour, end_minute) <= (
        start_hour, start_minute
    ):
        shift_end_time += timedelta(days=1)

    # Calculate complete minutes beyond the shift end.
    elapsed_seconds = (
        punch_out - shift_end_time
    ).total_seconds()

    overtime_minutes = int(elapsed_seconds // 60)

    # Overtime is counted only when it reaches 30 minutes.
    if overtime_minutes >= 30:
        return overtime_minutes

    return 0
def resolve_attendance_date(punch_in, shift_start, shift_end):
    """
    Determine the attendance date using IST.

    For an overnight shift, a punch-in before the shift's end time
    belongs to the previous day's shift.
    """
    local_punch_in = truncate_to_seconds(punch_in).astimezone(IST)

    start_time = datetime.strptime(shift_start, "%H:%M").time()
    end_time = datetime.strptime(shift_end, "%H:%M").time()

    attendance_date = local_punch_in.date()

    # An overnight shift ends on the following calendar day.
    if end_time <= start_time:
        punch_in_clock = (
            local_punch_in.hour,
            local_punch_in.minute,
            local_punch_in.second,
        )

        shift_end_clock = (
            end_time.hour,
            end_time.minute,
            0,
        )

        if punch_in_clock < shift_end_clock:
            attendance_date -= timedelta(days=1)

    return attendance_date


def compute_late_minutes(punch_in, shift_start, shift_end):
    """
    Calculate late minutes using the shift's actual start date.

    Lateness is counted only when the employee is more than
    10 minutes late. The elapsed minutes are then rounded down.
    """
    punch_in = truncate_to_seconds(punch_in)
    local_punch_in = punch_in.astimezone(IST)

    attendance_date = resolve_attendance_date(
        punch_in,
        shift_start,
        shift_end,
    )

    start_time = datetime.strptime(shift_start, "%H:%M").time()

    shift_start_datetime = datetime.combine(
        attendance_date,
        start_time,
    ).replace(tzinfo=IST)

    elapsed_seconds = (
        local_punch_in - shift_start_datetime
    ).total_seconds()

    # Exactly 10 minutes late is not considered late.
    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)
def create_indexes():
    """Create required indexes safely and idempotently."""

    # Each employee code must be unique.
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    # Each employee can have only one attendance record per date.
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    # Support attendance filtering and date-based sorting.
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="ix_attendance_date_emp_code",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MongoDB indexes when the application starts."""

    try:
        # Verify that MongoDB is reachable.
        client.admin.command("ping")

        # Ensure required indexes exist.
        create_indexes()

        yield

    finally:
        client.close()


# Create the FastAPI application.
app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    department: str = Field(min_length=1, max_length=100)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code", "name", "department")
    @classmethod
    def validate_non_empty_strings(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("This field cannot be empty.")

        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("Time must use HH:MM format.")

        if parsed.strftime("%H:%M") != value:
            raise ValueError("Time must use HH:MM format.")

        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD format.")

        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError("joined_on must use YYYY-MM-DD format.")

        return value

    @field_validator("shift_end")
    @classmethod
    def validate_shift_end(
        cls,
        shift_end: str,
        info,
    ) -> str:
        shift_start = info.data.get("shift_start")

        if shift_start is not None and shift_end == shift_start:
            raise ValueError("Shift start and end cannot be equal.")

        return shift_end

class PunchInRequest(BaseModel):
    emp_code: str = Field(min_length=1, max_length=30)
    punched_at: int | None = None
    status: str = "PRESENT"

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Employee code cannot be empty")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed_statuses = {"PRESENT", "WFH", "ON_DUTY"}

        if value not in allowed_statuses:
            raise ValueError(
                "Status must be PRESENT, WFH, or ON_DUTY"
            )

        return value
class PunchOutRequest(BaseModel):
    emp_code: str = Field(min_length=1, max_length=30)
    punched_at: int | None = None

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Employee code cannot be empty")

        return value
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    """Create a new employee."""

    # Build the document from validated request data.
    employee = body.model_dump()

    # Set the creation timestamp in UTC.
    created_at = datetime.now(UTC).replace(microsecond=0)
    employee["created_at"] = created_at

    try:
        # Insert the document into MongoDB.
        db.employees.insert_one(employee)

    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Employee code already exists",
        )

    # Construct a response explicitly.
    response = body.model_dump()
    response["created_at"] = int(created_at.timestamp() * 1000)

    return response
@app.get("/employees")
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    # 1. Find the employee.
    employee = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Determine the punch-in timestamp.
    if body.punched_at is None:
        punched_at = datetime.now(UTC)
    else:
        try:
            punched_at = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=UTC,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punch-in timestamp",
            )

    # 3. Truncate the timestamp to whole seconds.
    punched_at = truncate_to_seconds(punched_at)

    # 4. Resolve the attendance date using the employee's shift.
    attendance_date = resolve_attendance_date(
        punched_at,
        employee["shift_start"],
        employee["shift_end"],
    )

    attendance_date_str = attendance_date.isoformat()

    # 5. Calculate late minutes.
    late_minutes = compute_late_minutes(
        punched_at,
        employee["shift_start"],
        employee["shift_end"],
    )

    # 6. Construct the attendance record.
    record = {
        "emp_code": body.emp_code,
        "date": attendance_date_str,
        "punch_in": punched_at,
        "punch_out": None,
        "status": body.status,
        "late_minutes": late_minutes,
        "work_hours": None,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # 7. Insert the record. The unique index protects against races.
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this employee and date",
        )

    # 8. Return a clean API response without MongoDB's _id.
    return {
        "emp_code": body.emp_code,
        "date": attendance_date_str,
        "punch_in": int(punched_at.timestamp() * 1000),
        "punch_out": None,
        "status": body.status,
        "late_minutes": late_minutes,
        "work_hours": None,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # Build the MongoDB filter.
    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        query["status"] = status

    # Validate and apply the inclusive date range.
    if date_from is not None:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_from must use YYYY-MM-DD format",
            )

        query.setdefault("date", {})
        query["date"]["$gte"] = date_from

    if date_to is not None:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_to must use YYYY-MM-DD format",
            )

        query.setdefault("date", {})
        query["date"]["$lte"] = date_to

    if (
        date_from is not None
        and date_to is not None
        and date_from > date_to
    ):
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be later than date_to",
        )

    # Count records after applying all filters.
    total = db.attendance_logs.count_documents(query)

    # Apply deterministic sorting and pagination.
    skip = (page - 1) * page_size

    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    # Convert stored UTC datetimes to epoch milliseconds.
    for item in items:
        for field in ("punch_in", "punch_out"):
            timestamp = item.get(field)

            if timestamp is not None:
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)

                item[field] = int(timestamp.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
@app.post("/attendance/punch-out")

class AttendanceUpdateRequest(BaseModel):
    status: str | None = None
    punch_in: int | None = None
    punch_out: int | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None:
            allowed_statuses = {
                "PRESENT",
                "WFH",
                "ON_DUTY",
                "ABSENT",
                "LEAVE",
            }

            if value not in allowed_statuses:
                raise ValueError(
                    "Status must be PRESENT, WFH, ON_DUTY, ABSENT, or LEAVE"
                )

        return value


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: AttendanceUpdateRequest,
):
    # 1. Validate the attendance date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()

        if parsed_date.isoformat() != date:
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    # 2. Find the employee.
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 3. Find the attendance record.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    # 4. Extract the requested changes.
    requested = body.model_dump(exclude_unset=True)

    if not requested:
        raise HTTPException(
            status_code=422,
            detail="At least one field must be provided",
        )

    allowed_statuses = {
        "PRESENT",
        "WFH",
        "ON_DUTY",
        "ABSENT",
        "LEAVE",
    }

    requested_status = requested.get("status", record["status"])

    if requested_status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # 5. Convert supplied timestamps from epoch milliseconds to UTC.
    def parse_timestamp(value, field_name):
        if value is None:
            return None

        try:
            parsed = datetime.fromtimestamp(value / 1000, tz=UTC)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {field_name} timestamp",
            )

        return truncate_to_seconds(parsed)

    new_punch_in = (
        parse_timestamp(requested["punch_in"], "punch_in")
        if "punch_in" in requested
        else record.get("punch_in")
    )

    new_punch_out = (
        parse_timestamp(requested["punch_out"], "punch_out")
        if "punch_out" in requested
        else record.get("punch_out")
    )

    if new_punch_in is not None and new_punch_in.tzinfo is None:
        new_punch_in = new_punch_in.replace(tzinfo=UTC)

    if new_punch_out is not None and new_punch_out.tzinfo is None:
        new_punch_out = new_punch_out.replace(tzinfo=UTC)

    # 6. ABSENT and LEAVE records cannot retain punch times.
    if requested_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in requested and requested["punch_in"] is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )

        if "punch_out" in requested and requested["punch_out"] is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )

        new_punch_in = None
        new_punch_out = None

    # 7. Presence statuses require punch-in.
    elif new_punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence statuses require punch_in",
        )

    # 8. Punch-in must belong to the attendance date under R1.
    if new_punch_in is not None:
        local_punch_in = new_punch_in.astimezone(IST)

        resolved_date = resolve_attendance_date(
            new_punch_in,
            employee["shift_start"],
            employee["shift_end"],
        )

        if resolved_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in does not belong to the requested attendance date",
            )

    # 9. Validate the punch-out interval.
    if new_punch_out is not None:
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )

        duration = (new_punch_out - new_punch_in).total_seconds()

        if duration <= 0 or duration > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    # 10. Recalculate derived fields.
    if requested_status in {"ABSENT", "LEAVE"}:
        new_late_minutes = 0
        new_work_hours = None
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_late_minutes = compute_late_minutes(
            new_punch_in,
            employee["shift_start"],
            employee["shift_end"],
        )

        if new_punch_out is not None:
            new_work_hours = compute_work_hours(
                new_punch_in,
                new_punch_out,
            )

            new_overtime_minutes = compute_overtime_minutes(
                new_punch_out,
                employee["shift_start"],
                employee["shift_end"],
                date,
            )

            new_half_day = compute_half_day(new_work_hours)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False

    desired = {
        "status": requested_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "late_minutes": new_late_minutes,
        "work_hours": new_work_hours,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day,
    }

    # 11. Determine which fields actually changed.
    changes = {}

    for field, new_value in desired.items():
        old_value = record.get(field)

        if old_value != new_value:
            changes[field] = {
                "old": old_value,
                "new": new_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )

    # 12. Append one audit entry containing only changed fields.
    history_entry = {
        "changed_at": datetime.now(UTC).replace(microsecond=0),
        "changes": changes,
    }

    update_fields = {
        field: desired[field]
        for field in changes
    }

    # 13. Use a conditional update to prevent silent concurrent overwrites.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": record.get("history", []),
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": update_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    # 14. Return the updated record.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        timestamp = updated.get(field)

        if timestamp is not None:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)

            updated[field] = int(timestamp.timestamp() * 1000)

    for entry in updated.get("history", []):
        changed_at = entry.get("changed_at")

        if changed_at is not None:
            if changed_at.tzinfo is None:
                changed_at = changed_at.replace(tzinfo=UTC)

            entry["changed_at"] = int(changed_at.timestamp() * 1000)

        for change in entry.get("changes", {}).values():
            for key in ("old", "new"):
                value = change.get(key)

                if isinstance(value, datetime):
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=UTC)

                    change[key] = int(value.timestamp() * 1000)

    return updated
def punch_out(body: PunchOutRequest):
    # 1. Verify that the employee exists.
    employee = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Determine the punch-out timestamp.
    if body.punched_at is None:
        punched_at = datetime.now(UTC)
    else:
        try:
            punched_at = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=UTC,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punch-out timestamp",
            )

    # 3. Truncate to whole seconds.
    punched_at = truncate_to_seconds(punched_at)

    # 4. Find the most recent open attendance record.
    attendance = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched_at},
            "punch_out": None,
            "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]},
        },
        sort=[("punch_in", -1)],
    )

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="No open attendance record found",
        )

    punch_in_time = attendance["punch_in"]

    # PyMongo may return naive datetimes representing UTC.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=UTC)

    punch_in_time = truncate_to_seconds(punch_in_time)

    # 5. Validate the punch-out timestamp.
    duration_seconds = (
        punched_at - punch_in_time
    ).total_seconds()

    if duration_seconds <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if duration_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    # 6. Calculate derived attendance values.
    work_hours = compute_work_hours(
        punch_in_time,
        punched_at,
    )

    overtime_minutes = compute_overtime_minutes(
        punched_at,
        employee["shift_start"],
        employee["shift_end"],
        attendance["date"],
    )

    half_day = compute_half_day(work_hours)

    # 7. Atomically close the record.
    # Only one concurrent request can update an open record.
    result = db.attendance_logs.update_one(
        {
            "_id": attendance["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record has already been closed",
        )

    # 8. Return the updated record without MongoDB's internal ID.
    updated = db.attendance_logs.find_one(
        {"_id": attendance["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        timestamp = updated.get(field)

        if timestamp is not None:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)

            updated[field] = int(timestamp.timestamp() * 1000)

    return updated
def list_employees(
    department: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List employees with optional department filtering."""

    # Build the MongoDB filter.
    query = {}

    if department is not None:
        query["department"] = department

    # Count only records matching the filter.
    total = db.employees.count_documents(query)

    # Calculate how many records to skip.
    skip = (page - 1) * page_size

    # Fetch the requested page from MongoDB.
    employees = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    # Return the paginated response.
    return {
        "items": employees,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.get("/health")
def health():
    """Return service health based on MongoDB availability."""

    try:
        client.admin.command("ping")
        return {"status": "ok"}

    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )
# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    # 1. Find the employee.
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Determine the requested month's date range.
    month_start = datetime(year, month, 1).date()

    if month == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(year, month + 1, 1).date()

    month_end = next_month_start - timedelta(days=1)

    # 3. Account for employees who joined during the month.
    joined_on = datetime.strptime(
        employee["joined_on"], "%Y-%m-%d"
    ).date()

    effective_start = max(month_start, joined_on)

    # 4. Calculate working days (Monday-Friday).
    working_days = 0
    current_day = effective_start

    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1

        current_day += timedelta(days=1)

    # 5. Aggregate attendance records in MongoDB.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    result = list(db.attendance_logs.aggregate(pipeline))

    # 6. Handle employees without attendance records.
    if result:
        summary = result[0]
    else:
        summary = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0,
        }

    # 7. Round numeric values according to the assignment.
    present_days = round_half_up(summary["present_days"], 2)

    attendance_pct = (
        round_half_up((present_days / working_days) * 100, 4)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "year": year,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary["leave_days"],
        "late_count": summary["late_count"],
        "total_late_minutes": summary["total_late_minutes"],
        "total_overtime_minutes": summary["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }
@app.get("/analytics/departments/summary")
def department_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    department: str | None = None,
):
    # Determine the requested month's date range.
    month_start = datetime(year, month, 1).date()

    if month == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(year, month + 1, 1).date()

    month_end = next_month_start - timedelta(days=1)

    # Employees who joined after the month ends are excluded.
    employee_match = {
        "joined_on": {"$lte": month_end.isoformat()}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        # Start with employees so employees without logs are retained.
        {
            "$match": employee_match
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start.isoformat(),
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            month_end.isoformat(),
                                        ]
                                    },
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$attendance.status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            "$attendance.half_day",
                                            True,
                                        ]
                                    },
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            "$attendance.work_hours",
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$attendance.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            "$attendance.work_hours",
                                            None,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$attendance.late_minutes",
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$ifNull": [
                            "$attendance.late_minutes",
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$eq": [
                                    "$attendance.status",
                                    "LEAVE",
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$eq": [
                                    "$attendance.status",
                                    "ON_DUTY",
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        # Omit departments with zero eligible employees.
        {
            "$match": {
                "headcount": {"$gt": 0}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$divide": [
                                "$work_hours_sum",
                                "$work_hours_count",
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {
            "$sort": {
                "department": 1
            }
        },
    ]

    results = list(db.employees.aggregate(pipeline))

    # Apply the assignment's half-up rounding rules.
    for item in results:
        item["present_days"] = round_half_up(
            item["present_days"], 2
        )

        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = round_half_up(
                item["avg_work_hours"], 2
            )

    return {
        "year": year,
        "month": month,
        "items": results,
    }