"""
create_faker_tables.py
----------------------
Builds the GENERATED (high-volume / transactional) tables for the `maintenance`
database. Same MySQL tables as the static ones — the difference is the rows are
generated (Faker + random + datetime) rather than hand-authored.

Each table has its own create_* function. build_faker_tables(conn) calls them in
foreign-key dependency order. Each create_* reads valid FKs from the already-built
static tables (so the SQL phase must run first).

Determinism: random/Faker are seeded so re-runs produce the same dataset.
"""

import random
from calendar import monthrange
from datetime import date, time, timedelta

from faker import Faker

# Seeded for reproducible synthetic data.
SEED = 42
random.seed(SEED)
Faker.seed(SEED)
fake = Faker()

# The dataset's reference month (the agent's "current date" will live in this
# month; the real get_current_time will be hardcoded to it later).
SCHEDULE_YEAR = 2026
SCHEDULE_MONTH = 6  # June 2026

# Valid start hours for a 2-hour incident slot within each shift.
# (start, start+2) must stay inside the shift window.
SHIFT_SLOT_START_HOURS = {
    "7AM-3PM":  [7, 8, 9, 10, 11, 12, 13],     # 07:00-15:00
    "3PM-11PM": [15, 16, 17, 18, 19, 20, 21],  # 15:00-23:00
    "11PM-7AM": [23, 0, 1, 2, 3, 4, 5],        # 23:00-07:00 (overnight)
}

PREBOOKED_RATIO = 0.20  # ~20% of slots seeded as already Booked

# --- shared reference date ---
# The dataset's reference "today" (the agent's hardcoded current date will match
# this). History runs from HISTORY_START through "yesterday".
REFERENCE_TODAY = date(2026, 6, 16)
HISTORY_START = date(2026, 4, 1)
HISTORY_END = REFERENCE_TODAY - timedelta(days=1)  # 2026-06-15 ("yesterday")

# --- maintenance_history config (PREVENTIVE only) ---
PREVENTIVE_INTERVAL_DAYS = 21
OVERDUE_MACHINES = ["M03", "M07"]   # latest preventive service intentionally stale

# All preventive services log the same constant comment — the manuals give one
# generic 2-4 week SOP across every model, so there's no per-version distinction.
PREVENTIVE_COMMENT = "Performed the regular SOP"

# --- incidents config ---
NUM_INCIDENTS = 25
NUM_OPEN_INCIDENTS = 4          # most-recent incidents still live (no closure yet)
NUM_AUGMENTED_COMMENTS = 2      # closed incidents where the technician added steps

# Coherent (user_complaint, agent_root_cause, agentic_resolution) scenarios,
# grounded in real FDM faults / firmware errors from the manuals.
INCIDENT_SCENARIOS = [
    ("Prints aren't sticking to the bed; the first layer keeps lifting.",
     "Bed not level / first-layer Z-offset too high.",
     "Re-run bed leveling (G29) and adjust Z-offset; clean the bed with IPA."),
    ("Printer halted mid-print showing a MINTEMP error.",
     "Hotend thermistor wiring broken or disconnected.",
     "Inspect and replace the hotend thermistor; secure the wiring connection."),
    ("Hotend overheated and the printer stopped with MAXTEMP.",
     "Thermistor fault or heater stuck on.",
     "Replace the heater cartridge / thermistor; verify firmware temperature limits."),
    ("Nozzle is clogged and no filament is extruding.",
     "Partial clog / debris in the nozzle.",
     "Perform a cold pull; replace the 0.5mm nozzle if needed."),
    ("Layers are shifting and the print comes out misaligned.",
     "Loose GT2 belt or a binding axis.",
     "Re-tension the GT2 belt; check pulley grub screws; lubricate the rods."),
    ("Filament ran out and the printer did not resume.",
     "Filament runout; spool empty or tangled.",
     "Reload filament; clear the extruder path; resume the print."),
    ("The bed probe keeps failing with a PROBE FAIL message.",
     "Dirty nozzle tip preventing a clean wipe / probe.",
     "Clean the nozzle tip, reseat the probe connector, re-run the bed mesh."),
    ("Loud grinding from the extruder and under-extrusion.",
     "Extruder hobbed gear slipping / partial clog.",
     "Clear the clog, clean the hobbed gear, check idler tension."),
    ("Knocking noise from the Z axis during prints.",
     "Z lead screw needs lubrication / slight binding.",
     "Lubricate the lead screw; check coupler alignment."),
    ("Cooling fan isn't spinning and prints look melted.",
     "Part cooling fan failure.",
     "Replace the part cooling fan; verify airflow."),
]

# Technician comment: confirms the agentic resolution (default) or augments it.
TECH_CONFIRM_COMMENT = (
    "Confirmed to agentic resolution; performed as advised and verified the fix."
)
TECH_AUGMENTATIONS = [
    " Additionally replaced the PTFE liner that showed heat creep.",
    " Also re-calibrated e-steps and ran a test print to confirm.",
]


def build_faker_tables(conn) -> None:
    """Create all generated tables, in FK dependency order."""
    print("Building Faker (generated) tables…")
    cursor = conn.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS=0;")
    cursor.close()

    create_technician_schedule(conn)
    create_maintenance_history(conn)
    create_incidents(conn)

    cursor = conn.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS=1;")
    cursor.close()
    print("Faker (generated) tables done.\n")


def _working_days(year: int, month: int) -> list:
    """Return all weekdays (Mon-Fri) of the given month as date objects."""
    num_days = monthrange(year, month)[1]
    days = []
    for d in range(1, num_days + 1):
        the_day = date(year, month, d)
        if the_day.weekday() < 5:  # 0=Mon .. 4=Fri
            days.append(the_day)
    return days


def _random_slot(shift_time: str) -> str:
    """Pick a random 2-hour window string (e.g. '09:00-11:00') within a shift."""
    start = random.choice(SHIFT_SLOT_START_HOURS[shift_time])
    end = (start + 2) % 24
    return f"{start:02d}:00-{end:02d}:00"


# Shift windows as minute-of-day ranges (for placing a report time within shift).
SHIFT_MINUTE_RANGES = {
    "7AM-3PM":  (7 * 60, 15 * 60),    # 07:00-15:00
    "3PM-11PM": (15 * 60, 23 * 60),   # 15:00-23:00
    "11PM-7AM": (23 * 60, 31 * 60),   # 23:00-07:00 next day (mod 1440)
}


def _random_time_in_shift(shift_time: str) -> time:
    """Return a random time-of-day that falls within the given shift window."""
    start_min, end_min = SHIFT_MINUTE_RANGES[shift_time]
    minute_of_day = random.randrange(start_min, end_min) % 1440
    return time(hour=minute_of_day // 60, minute=minute_of_day % 60,
                second=random.randint(0, 59))


def create_technician_schedule(conn) -> None:
    """
    Generate `technician_schedule` — a monthly availability calendar of incident
    work slots, one row per active technician per working day.

    Each row carries a random 2-hour incident slot inside that technician's shift
    (shift_time pulled from `employees`). availability_status starts mostly
    'Available'; the agent flips it to 'Booked' when assigning a ticket later.
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS technician_schedule;")

    cursor.execute(
        """
        CREATE TABLE technician_schedule (
            `date`               DATE        NOT NULL,
            employee_id          VARCHAR(10) NOT NULL,
            shift_time           ENUM('7AM-3PM','3PM-11PM','11PM-7AM') NULL,
            availability_slot    VARCHAR(15) NULL,
            availability_status  ENUM('Available','Booked') NOT NULL DEFAULT 'Available',
            PRIMARY KEY (`date`, employee_id),
            FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    # Pull active technicians + their shift from employees (shift_time is sourced
    # here, not invented).
    cursor.execute(
        """
        SELECT employee_id, shift_time
        FROM employees
        WHERE role = 'Technician' AND status = 'Active';
        """
    )
    technicians = cursor.fetchall()  # list of (employee_id, shift_time)

    working_days = _working_days(SCHEDULE_YEAR, SCHEDULE_MONTH)

    rows = []
    for the_day in working_days:
        for employee_id, shift_time in technicians:
            slot = _random_slot(shift_time)
            status = "Booked" if random.random() < PREBOOKED_RATIO else "Available"
            rows.append((the_day, employee_id, shift_time, slot, status))

    cursor.executemany(
        """
        INSERT INTO technician_schedule
            (`date`, employee_id, shift_time, availability_slot, availability_status)
        VALUES (%s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(
        f"  ✓ technician_schedule: created and seeded {len(rows)} rows "
        f"({len(technicians)} technicians × {len(working_days)} working days)"
    )


def create_maintenance_history(conn) -> None:
    """
    Generate `maintenance_history` — the log of PREVENTIVE (regular ~21-day)
    services performed on machines between HISTORY_START and "yesterday".

    The preventive cadence is driven only by the previous preventive date. The
    agent's overdue check reads the latest preventive service + 21 days vs today.
    Reactive / incident-driven work is recorded separately in `incidents`.

    Seeded edge case: OVERDUE_MACHINES have their latest preventive service left
    intentionally stale (>21 days before REFERENCE_TODAY).

    Covers Operational + Under Maintenance machines; Decommissioned are excluded.
    `performed_by` is drawn from active technicians only.
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS maintenance_history;")

    cursor.execute(
        """
        CREATE TABLE maintenance_history (
            service_id           VARCHAR(12)  NOT NULL,
            machine_id           VARCHAR(10)  NOT NULL,
            service_date         DATE         NOT NULL,
            performed_by         VARCHAR(10)  NOT NULL,
            technician_comments  VARCHAR(300) NOT NULL,
            PRIMARY KEY (service_id),
            FOREIGN KEY (machine_id) REFERENCES machines (machine_id),
            FOREIGN KEY (performed_by) REFERENCES employees (employee_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    # Machines that receive preventive service (exclude Decommissioned).
    cursor.execute(
        "SELECT machine_id FROM machines "
        "WHERE status IN ('Operational','Under Maintenance');"
    )
    covered = [r[0] for r in cursor.fetchall()]

    # Active technicians only.
    cursor.execute(
        "SELECT employee_id FROM employees "
        "WHERE role='Technician' AND status='Active';"
    )
    techs = [r[0] for r in cursor.fetchall()]

    # Each tuple: (machine_id, service_date, performed_by, technician_comments)
    services = []
    for machine_id in covered:
        if machine_id in OVERDUE_MACHINES:
            # latest preventive intentionally stale -> overdue
            last_preventive = REFERENCE_TODAY - timedelta(days=random.randint(25, 45))
        else:
            # latest preventive within the interval -> not overdue
            last_preventive = REFERENCE_TODAY - timedelta(days=random.randint(1, 20))

        d = last_preventive
        while d >= HISTORY_START:
            services.append(
                (machine_id, d, random.choice(techs), PREVENTIVE_COMMENT)
            )
            d = d - timedelta(days=PREVENTIVE_INTERVAL_DAYS)

    # Order by date and assign sequential service_id (serv_1, serv_2, ...).
    services.sort(key=lambda s: s[1])
    rows = [
        (f"serv_{i}", m, d, by, comments)
        for i, (m, d, by, comments) in enumerate(services, start=1)
    ]

    cursor.executemany(
        """
        INSERT INTO maintenance_history
            (service_id, machine_id, service_date, performed_by, technician_comments)
        VALUES (%s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(f"  ✓ maintenance_history: created and seeded {len(rows)} rows (preventive)")


def create_incidents(conn) -> None:
    """
    Generate `incidents` — the agentic-workflow case record for reactive /
    incident-driven faults on Operational machines.

    Each row captures the full lifecycle: the operator's confirmed complaint, the
    agent's root cause + proposed resolution, the allocated technician, the
    scheduled work date + slot, the technician's comments, and the closure date.

    Edge cases:
    - The most-recent NUM_OPEN_INCIDENTS incidents are still LIVE: a technician is
      allocated but technician_comments and incident_closure_date are NULL.
    - NUM_AUGMENTED_COMMENTS closed incidents have technician steps beyond the
      agentic resolution; the rest simply confirm it (>95%).

    reported_by = active operators; technician_id = active technicians.
    reported_time falls within the reporting operator's shift.
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS incidents;")

    cursor.execute(
        """
        CREATE TABLE incidents (
            incident_id            VARCHAR(12)  NOT NULL,
            machine_id             VARCHAR(10)  NOT NULL,
            reported_date          DATE         NOT NULL,
            reported_time          TIME         NOT NULL,
            reported_by            VARCHAR(10)  NOT NULL,
            user_complaint         VARCHAR(300) NOT NULL,
            agent_root_cause       VARCHAR(300) NOT NULL,
            agentic_resolution     VARCHAR(300) NOT NULL,
            technician_id          VARCHAR(10)  NULL,
            work_date              DATE         NULL,
            work_slot              VARCHAR(15)  NULL,
            technician_comments    VARCHAR(350) NULL,
            incident_closure_date  DATE         NULL,
            PRIMARY KEY (incident_id),
            FOREIGN KEY (machine_id) REFERENCES machines (machine_id),
            FOREIGN KEY (reported_by) REFERENCES employees (employee_id),
            FOREIGN KEY (technician_id) REFERENCES employees (employee_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    # Incidents occur on Operational machines only.
    cursor.execute("SELECT machine_id FROM machines WHERE status = 'Operational';")
    machines = [r[0] for r in cursor.fetchall()]

    # Reporters = active operators (with their shift, to place reported_time).
    cursor.execute(
        "SELECT employee_id, shift_time FROM employees "
        "WHERE role='Operator' AND status='Active';"
    )
    operators = cursor.fetchall()  # list of (employee_id, shift_time)

    # Resolvers = active technicians (+ their shift, to place a coherent work slot).
    cursor.execute(
        "SELECT employee_id, shift_time FROM employees "
        "WHERE role='Technician' AND status='Active';"
    )
    tech_rows = cursor.fetchall()
    technicians = [r[0] for r in tech_rows]
    tech_shift = {emp: shift for emp, shift in tech_rows}

    span_days = (HISTORY_END - HISTORY_START).days

    # Build the raw incidents (machine, reporter, scenario, reported date).
    incidents = []
    for _ in range(NUM_INCIDENTS):
        complaint, root_cause, resolution = random.choice(INCIDENT_SCENARIOS)
        operator_id, op_shift = random.choice(operators)
        incidents.append({
            "machine_id": random.choice(machines),
            "operator_id": operator_id,
            "op_shift": op_shift,
            "complaint": complaint,
            "root_cause": root_cause,
            "resolution": resolution,
            "reported_date": HISTORY_START + timedelta(days=random.randint(0, span_days)),
        })

    # Sort by reported date; the most-recent NUM_OPEN_INCIDENTS stay open.
    incidents.sort(key=lambda x: x["reported_date"])
    open_cutoff = len(incidents) - NUM_OPEN_INCIDENTS

    # Pick which closed incidents get augmented technician comments.
    closed_indices = list(range(open_cutoff))
    augmented = set(random.sample(
        closed_indices, min(NUM_AUGMENTED_COMMENTS, len(closed_indices))
    ))

    rows = []
    for i, inc in enumerate(incidents):
        reported_time = _random_time_in_shift(inc["op_shift"])
        technician_id = random.choice(technicians)  # agent allocates a technician
        # The scheduled work slot falls inside the assigned technician's shift.
        work_slot = _random_slot(tech_shift[technician_id])

        if i >= open_cutoff:                          # LIVE / open incident
            technician_comments = None
            closure_date = None
            # Scheduled (technician allocated) but the work isn't done yet.
            work_date = inc["reported_date"] + timedelta(days=random.randint(0, 2))
        else:                                         # closed incident
            technician_comments = TECH_CONFIRM_COMMENT
            if i in augmented:
                technician_comments += random.choice(TECH_AUGMENTATIONS)
            closure_date = min(
                inc["reported_date"] + timedelta(days=random.randint(0, 5)),
                HISTORY_END,
            )
            # Work happened between the report and the closure.
            work_span = (closure_date - inc["reported_date"]).days
            work_date = inc["reported_date"] + timedelta(
                days=random.randint(0, work_span) if work_span > 0 else 0
            )

        rows.append((
            f"inc_{i + 1}",
            inc["machine_id"],
            inc["reported_date"],
            reported_time,
            inc["operator_id"],
            inc["complaint"],
            inc["root_cause"],
            inc["resolution"],
            technician_id,
            work_date,
            work_slot,
            technician_comments,
            closure_date,
        ))

    cursor.executemany(
        """
        INSERT INTO incidents
            (incident_id, machine_id, reported_date, reported_time, reported_by,
             user_complaint, agent_root_cause, agentic_resolution,
             technician_id, work_date, work_slot, technician_comments,
             incident_closure_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(
        f"  ✓ incidents: created and seeded {len(rows)} rows "
        f"({len(rows) - NUM_OPEN_INCIDENTS} closed, {NUM_OPEN_INCIDENTS} open)"
    )
