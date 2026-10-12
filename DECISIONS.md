# DECISIONS.md

## 1. Indexes
I created a unique index on `employees.emp_code` to prevent duplicate employee codes. A unique compound index on `attendance_logs.emp_code` and `date` prevents duplicate daily attendance records. A date-and-employee index supports attendance listing and sorting. I rejected adding indexes for every filter because extra indexes increase storage and write costs.

## 2. Punch-in race
Two identical requests may both check that an employee exists. The unique compound index allows only one attendance record per employee and date. The successful request returns the created record; the duplicate request receives HTTP 409.

## 3. Leaderboard ties
Employees with equal late-minute totals share the same competition rank. Employee code provides deterministic ordering. The query includes everyone whose rank is within the requested limit, even when ties make the result longer than the limit.

## 4. Headcount
The department summary starts with employees who joined by the period's end and looks up their attendance. Employees without attendance logs remain included in headcount.

## 5. Scaling to 100x data
I would run realistic load tests, inspect execution plans, monitor query latency, and review index efficiency. If necessary, I would consider archival or partitioning after measuring the actual bottlenecks.