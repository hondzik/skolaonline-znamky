"""Konstanty integrace Škola OnLine — známky."""

DOMAIN = "skolaonline_znamky"

CONF_STUDENTS = "students"  # dict[student_id, student_name] vybraných dětí
CONF_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
CONF_MARKS_PER_SUBJECT = "marks_per_subject"

DEFAULT_UPDATE_MINUTES = 30
MARKS_PER_SUBJECT = 5

EVENT_NEW_MARK = f"{DOMAIN}_new_mark"

STORE_VERSION = 1
STORE_KEY_TEMPLATE = f"{DOMAIN}.{{entry_id}}"

# Pololetí se stahuje jen jednou za tento interval (nebo hned, když dnešek
# přeteče `semester.date_to`) — nemá smysl ho ověřovat při každém refreshi.
SEMESTER_REFRESH_HOURS = 24
