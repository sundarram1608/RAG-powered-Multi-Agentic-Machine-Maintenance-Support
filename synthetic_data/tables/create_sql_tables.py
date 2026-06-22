"""
create_sql_tables.py
---------------------
Builds the STATIC / reference tables (hand-authored rows) for the `maintenance`
database.

Each table has its own `create_*` function that executes its DDL + seed data.
`build_sql_tables(conn)` calls them in foreign-key dependency order.

Design choices:
- SQL is embedded directly in each function (self-contained, easy to read).
- Tables are DROPPED and RECREATED on each run, so the synthetic dataset can be
  rebuilt freely during development. (Drops happen in reverse dependency order
  inside the orchestrator when more tables are added.)
"""


def build_sql_tables(conn) -> None:
    """Create all static reference tables, in FK dependency order.

    Foreign-key checks are disabled around the build so DROP TABLE works
    regardless of order on a re-run (a parent can't otherwise be dropped while
    a child still references it). Checks are re-enabled at the end.
    """
    print("Building SQL (static) tables…")
    cursor = conn.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS=0;")
    cursor.close()

    create_machine_versions(conn)
    create_employees(conn)
    create_inventory(conn)
    create_machines(conn)

    cursor = conn.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS=1;")
    cursor.close()
    print("SQL (static) tables done.\n")


def create_machine_versions(conn) -> None:
    """
    Create and seed `machine_versions` — the Machine Version Code (MVC) registry.

    This is the spine of the schema: the physical `machines` table references
    `mvc_code`. `manual_path` links each version to its RAG source document.

    `service_interval_days` is the recommended preventive-maintenance interval
    (per version) — folded in here (SAP-Machine-Master style) rather than in a
    separate one-column table. Descriptive specs (build volume, temps, etc.)
    live in the manuals (RAG), not here.
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS machine_versions;")

    cursor.execute(
        """
        CREATE TABLE machine_versions (
            mvc_code               VARCHAR(10)  NOT NULL,
            model_name             VARCHAR(80)  NOT NULL,
            machine_type           VARCHAR(40)  NOT NULL,
            manufacturer           VARCHAR(80)  NOT NULL,
            manual_path            VARCHAR(255) NOT NULL,
            firmware               VARCHAR(40)  NULL,
            service_interval_days  INT          NULL,
            date_onboarded         DATE         NULL,
            PRIMARY KEY (mvc_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    manufacturer = "Aleph Objects, Inc. (LulzBot)"
    manual_dir = "synthetic_data/documents/user_manuals"

    # (mvc_code, model_name, machine_type, manufacturer, manual_path,
    #  firmware, service_interval_days, date_onboarded)
    # service_interval_days = 21 for all versions: grounded in the LulzBot
    # manuals' "quick check ... every 2 to 4 weeks" (14-28 days); 21 = midpoint.
    # The manuals give identical guidance across all four models.
    rows = [
        ("MVC01", "LulzBot Mini", "FDM 3D Printer", manufacturer,
         f"{manual_dir}/lulzbot_mini_user_manual.pdf", "Marlin", 21, "2023-02-10"),
        ("MVC02", "LulzBot TAZ 6", "FDM 3D Printer", manufacturer,
         f"{manual_dir}/lulzbot_taz6_user_manual.pdf", "Marlin", 21, "2023-05-18"),
        ("MVC03", "LulzBot TAZ Workhorse", "FDM 3D Printer", manufacturer,
         f"{manual_dir}/lulzbot_taz_workhorse_user_manual.pdf", "Marlin", 21, "2024-01-15"),
        ("MVC04", "LulzBot TAZ Pro", "FDM 3D Printer", manufacturer,
         f"{manual_dir}/lulzbot_taz_pro_user_manual.pdf", "Marlin", 21, "2024-03-22"),
    ]

    cursor.executemany(
        """
        INSERT INTO machine_versions
            (mvc_code, model_name, machine_type, manufacturer,
             manual_path, firmware, service_interval_days, date_onboarded)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(f"  ✓ machine_versions: created and seeded {len(rows)} rows")


def create_employees(conn) -> None:
    """
    Create and seed `employees` — all staff across three roles
    (Operator / Technician / Supervisor).

    Referenced by technician_schedule, maintenance_history, and incidents.

    Notes:
    - Employee IDs (E01..E20) are intentionally MIXED across roles, not grouped.
    - All operator emails point to one inbox, all technician emails to another,
      and the two supervisors to two more — so notification tests are easy to see.
    - 4 staff are Inactive (2 operators, 2 technicians); the rest Active.
    - Supervisors work 9AM-5PM; everyone else is on one of three rotating shifts.
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS employees;")

    cursor.execute(
        """
        CREATE TABLE employees (
            employee_id   VARCHAR(10)  NOT NULL,
            full_name     VARCHAR(80)  NOT NULL,
            role          ENUM('Operator','Technician','Supervisor') NOT NULL,
            email         VARCHAR(120) NOT NULL,
            phone         VARCHAR(20)  NULL,
            shift_time    ENUM('7AM-3PM','3PM-11PM','11PM-7AM','9AM-5PM') NULL,
            status        ENUM('Active','Inactive') NOT NULL DEFAULT 'Active',
            date_joined   DATE         NULL,
            PRIMARY KEY (employee_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    op_email = "sundarram1997@gmail.com"
    tech_email = "sundarram1997@arizona.edu"
    sup1_email = "sundarusa1608@gmail.com"
    sup2_email = "krithikamusic2@gmail.com"
    phone = "9999999999"

    # (employee_id, full_name, role, email, shift_time, status, date_joined)
    # phone is identical for all and added during insert.
    rows = [
        ("E01", "Arjun Sharma",      "Operator",   op_email,   "7AM-3PM",  "Active",   "2021-03-15"),
        ("E02", "Michael Johnson",   "Technician", tech_email, "3PM-11PM", "Active",   "2021-06-20"),
        ("E03", "Jack Wilson",       "Operator",   op_email,   "11PM-7AM", "Active",   "2022-01-10"),
        ("E04", "Elena Petrova",     "Supervisor", sup1_email, "9AM-5PM",  "Active",   "2019-08-01"),
        ("E05", "Wei Chen",          "Technician", tech_email, "7AM-3PM",  "Active",   "2022-04-05"),
        ("E06", "Hans Müller",       "Operator",   op_email,   "3PM-11PM", "Active",   "2021-09-12"),
        ("E07", "Priya Nair",        "Technician", tech_email, "11PM-7AM", "Active",   "2023-02-18"),
        ("E08", "Olivia Smith",      "Operator",   op_email,   "7AM-3PM",  "Inactive", "2022-07-22"),
        ("E09", "Dmitri Volkov",     "Technician", tech_email, "3PM-11PM", "Inactive", "2020-11-30"),
        ("E10", "Sophie Dubois",     "Operator",   op_email,   "11PM-7AM", "Active",   "2023-05-14"),
        ("E11", "Li Na",             "Technician", tech_email, "7AM-3PM",  "Active",   "2021-12-01"),
        ("E12", "Ethan Brown",       "Operator",   op_email,   "3PM-11PM", "Active",   "2022-09-09"),
        ("E13", "Rajesh Kumar",      "Technician", tech_email, "11PM-7AM", "Active",   "2023-08-25"),
        ("E14", "Charlotte Taylor",  "Operator",   op_email,   "7AM-3PM",  "Active",   "2024-01-20"),
        ("E15", "James Anderson",    "Supervisor", sup2_email, "9AM-5PM",  "Active",   "2018-05-10"),
        ("E16", "Anastasia Ivanova", "Technician", tech_email, "3PM-11PM", "Inactive", "2020-03-03"),
        ("E17", "Marco Rossi",       "Operator",   op_email,   "11PM-7AM", "Active",   "2023-11-11"),
        ("E18", "Zhang Wei",         "Technician", tech_email, "7AM-3PM",  "Active",   "2024-03-30"),
        ("E19", "Aishwarya Reddy",   "Operator",   op_email,   "3PM-11PM", "Inactive", "2022-02-14"),
        ("E20", "Lucas Schmidt",     "Operator",   op_email,   "11PM-7AM", "Active",   "2024-06-01"),
    ]

    cursor.executemany(
        """
        INSERT INTO employees
            (employee_id, full_name, role, email, phone, shift_time, status, date_joined)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """,
        [(r[0], r[1], r[2], r[3], phone, r[4], r[5], r[6]) for r in rows],
    )

    conn.commit()
    cursor.close()
    print(f"  ✓ employees: created and seeded {len(rows)} rows")


def create_inventory(conn) -> None:
    """
    Create and seed `inventory` — spare parts stock for the FDM printers.

    Hit by the Inventory agent's `check_inventory` tool. Parts are real FDM
    spares so they line up with the corrective steps the error codes reference.

    Seeded edge cases (deliberate, for the agents to catch):
    - LOW stock  (quantity_on_hand < reorder_threshold): PRT-001, PRT-008, PRT-010
    - OUT of stock (quantity_on_hand = 0):                PRT-002, PRT-014
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS inventory;")

    cursor.execute(
        """
        CREATE TABLE inventory (
            part_id            VARCHAR(10)  NOT NULL,
            part_name          VARCHAR(100) NOT NULL,
            category           VARCHAR(40)  NULL,
            compatible_mvc     VARCHAR(50)  NULL,
            quantity_on_hand   INT          NOT NULL,
            reorder_threshold  INT          NOT NULL,
            unit               VARCHAR(15)  NULL,
            bin_location       VARCHAR(20)  NULL,
            PRIMARY KEY (part_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    # (part_id, part_name, category, compatible_mvc, qty_on_hand, reorder_threshold, unit, bin)
    rows = [
        ("PRT-001", "Hotend thermistor",        "Hotend",      "ALL",                  3, 5,  "pcs",   "A-01"),  # LOW
        ("PRT-002", "Heater cartridge 24V",     "Hotend",      "ALL",                  0, 4,  "pcs",   "A-02"),  # OUT
        ("PRT-003", "Nozzle 0.5mm",             "Hotend",      "ALL",                 25, 10, "pcs",   "A-03"),
        ("PRT-004", "Heat break",               "Hotend",      "ALL",                 12, 5,  "pcs",   "A-04"),
        ("PRT-005", "PTFE liner tube",          "Hotend",      "ALL",                 30, 10, "pcs",   "A-05"),
        ("PRT-006", "Extruder hobbed gear",     "Extruder",    "ALL",                  8, 4,  "pcs",   "B-01"),
        ("PRT-007", "Stepper motor NEMA 17",    "Motion",      "ALL",                  6, 3,  "pcs",   "B-02"),
        ("PRT-008", "GT2 timing belt",          "Motion",      "ALL",                  2, 4,  "pcs",   "B-03"),  # LOW
        ("PRT-009", "Linear bearing LM8UU",     "Motion",      "ALL",                 40, 12, "pcs",   "B-04"),
        ("PRT-010", "PEI bed sheet",            "Bed",         "MVC02,MVC03,MVC04",    1, 3,  "pcs",   "C-01"),  # LOW
        ("PRT-011", "Part cooling fan",         "Electronics", "ALL",                  9, 4,  "pcs",   "D-01"),
        ("PRT-012", "Hotend cooling fan",       "Electronics", "ALL",                  7, 4,  "pcs",   "D-02"),
        ("PRT-013", "Endstop switch",           "Electronics", "ALL",                 15, 6,  "pcs",   "D-03"),
        ("PRT-014", "Bed leveling probe",       "Electronics", "ALL",                  0, 3,  "pcs",   "D-04"),  # OUT
        ("PRT-015", "Coupler 5x8mm",            "Motion",      "ALL",                 20, 8,  "pcs",   "B-05"),
        ("PRT-016", "Control board (RAMBo)",    "Electronics", "ALL",                  4, 2,  "pcs",   "E-01"),
        ("PRT-017", "Power supply 24V",         "Electronics", "ALL",                  5, 2,  "pcs",   "E-02"),
        ("PRT-018", "LCD display module",       "Electronics", "ALL",                  6, 3,  "pcs",   "E-03"),
        ("PRT-019", "PLA filament roll",        "Consumable",  "ALL",                 50, 20, "rolls", "F-01"),
        ("PRT-020", "ABS filament roll",        "Consumable",  "ALL",                 25, 20, "rolls", "F-02"),
        ("PRT-021", "PETG filament roll",       "Consumable",  "ALL",                 22, 15, "rolls", "F-03"),
        ("PRT-022", "Lead screw",               "Motion",      "MVC03,MVC04",          3, 2,  "pcs",   "B-06"),
    ]

    cursor.executemany(
        """
        INSERT INTO inventory
            (part_id, part_name, category, compatible_mvc,
             quantity_on_hand, reorder_threshold, unit, bin_location)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(f"  ✓ inventory: created and seeded {len(rows)} rows")


def create_machines(conn) -> None:
    """
    Create and seed `machines` — the physical-unit asset registry.

    Each row is one printer (M01..M20) tagged to its version via `mvc_code`
    (FK -> machine_versions). Model/type/manufacturer are NOT duplicated here;
    they live in machine_versions.

    Seeding:
    - 5 units per MVC (20 total); machine IDs are scattered across versions.
    - Per MVC: 3 Operational, 1 Under Maintenance, 1 Decommissioned (no Idle).
    """
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS machines;")

    cursor.execute(
        """
        CREATE TABLE machines (
            machine_id     VARCHAR(10) NOT NULL,
            mvc_code       VARCHAR(10) NOT NULL,
            serial_number  VARCHAR(50) NULL,
            location       VARCHAR(60) NULL,
            status         ENUM('Operational','Under Maintenance','Idle','Decommissioned')
                               NOT NULL DEFAULT 'Operational',
            install_date   DATE        NULL,
            PRIMARY KEY (machine_id),
            FOREIGN KEY (mvc_code) REFERENCES machine_versions (mvc_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    # (machine_id, mvc_code, serial_number, location, status, install_date)
    rows = [
        ("M01", "MVC02", "LZ-TAZ6-2023-0101",   "Floor 1 - Cell A", "Operational",       "2023-06-01"),
        ("M02", "MVC04", "LZ-TAZPRO-2024-0201", "Floor 1 - Cell B", "Operational",       "2024-04-01"),
        ("M03", "MVC01", "LZ-MINI-2023-0301",   "Floor 1 - Cell C", "Operational",       "2023-02-20"),
        ("M04", "MVC04", "LZ-TAZPRO-2024-0202", "Floor 1 - Cell D", "Operational",       "2024-04-20"),
        ("M05", "MVC03", "LZ-TAZWH-2024-0401",  "Floor 2 - Cell A", "Operational",       "2024-02-01"),
        ("M06", "MVC04", "LZ-TAZPRO-2024-0203", "Floor 2 - Cell B", "Operational",       "2024-05-10"),
        ("M07", "MVC01", "LZ-MINI-2023-0302",   "Floor 2 - Cell C", "Operational",       "2023-03-15"),
        ("M08", "MVC02", "LZ-TAZ6-2023-0102",   "Floor 2 - Cell D", "Operational",       "2023-07-12"),
        ("M09", "MVC03", "LZ-TAZWH-2024-0402",  "Floor 3 - Cell A", "Operational",       "2024-02-20"),
        ("M10", "MVC04", "LZ-TAZPRO-2024-0204", "Floor 3 - Cell B", "Under Maintenance", "2024-06-01"),
        ("M11", "MVC01", "LZ-MINI-2023-0303",   "Floor 3 - Cell C", "Operational",       "2023-04-10"),
        ("M12", "MVC02", "LZ-TAZ6-2023-0103",   "Floor 3 - Cell D", "Operational",       "2023-08-20"),
        ("M13", "MVC03", "LZ-TAZWH-2024-0403",  "Floor 1 - Cell E", "Operational",       "2024-03-10"),
        ("M14", "MVC02", "LZ-TAZ6-2023-0104",   "Floor 1 - Cell F", "Under Maintenance", "2023-09-15"),
        ("M15", "MVC03", "LZ-TAZWH-2024-0404",  "Floor 2 - Cell E", "Under Maintenance", "2024-04-05"),
        ("M16", "MVC01", "LZ-MINI-2023-0304",   "Floor 2 - Cell F", "Under Maintenance", "2023-06-05"),
        ("M17", "MVC04", "LZ-TAZPRO-2024-0205", "Floor 3 - Cell E", "Decommissioned",    "2024-07-15"),
        ("M18", "MVC02", "LZ-TAZ6-2023-0105",   "Floor 3 - Cell F", "Decommissioned",    "2023-10-30"),
        ("M19", "MVC03", "LZ-TAZWH-2024-0405",  "Floor 1 - Cell G", "Decommissioned",    "2024-05-15"),
        ("M20", "MVC01", "LZ-MINI-2023-0305",   "Floor 2 - Cell G", "Decommissioned",    "2023-07-20"),
    ]

    cursor.executemany(
        """
        INSERT INTO machines
            (machine_id, mvc_code, serial_number, location, status, install_date)
        VALUES (%s, %s, %s, %s, %s, %s);
        """,
        rows,
    )

    conn.commit()
    cursor.close()
    print(f"  ✓ machines: created and seeded {len(rows)} rows")
