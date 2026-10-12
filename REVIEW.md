# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | The original calculation did not correctly account for overnight shifts and their attendance dates. | Test a 22:00–06:00 shift with a punch-in at 00:30 IST. | Added overnight attendance-date resolution and corrected late-minute calculation. |
| 2 | Employee listing route | `GET /employees` was accidentally registered on the punch-in handler instead of the employee-listing handler. | Request `GET /employees`; it incorrectly expects a punch-in request body. | Registered `GET /employees` on `list_employees`. |
| 3 | Punch-out endpoint | The starter marked punch-out as unimplemented. | Attempt to close an existing open attendance record. | Implemented punch-out with duration validation, derived-field calculation, and a conditional update to handle concurrent requests. |
| 4 | Attendance regularization endpoint | The starter marked attendance regularization as unimplemented. | Attempt to correct an existing attendance record. | Implemented validation, derived-field recalculation, change history, and a conditional update to protect against concurrent edits. |
| 5 | Analytics endpoints | The starter marked monthly analytics, department summary, leaderboard, and department trend as TODOs. | Request each documented analytics endpoint. | Implemented the analytics endpoints and their aggregation logic. |
| 6 | Admin explain endpoint | The starter marked query-plan inspection as unimplemented. | Request an explain plan for a supported endpoint. | Implemented an explain endpoint that reports MongoDB execution statistics. |

## Reviewed but not considered defects

- A `404` response for an undefined root route (`/`) is expected; the assignment does not require a root endpoint.
- Missing attendance records are not automatically treated as absences. This preserves the distinction between no record and an explicit `ABSENT` status.