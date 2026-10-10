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
