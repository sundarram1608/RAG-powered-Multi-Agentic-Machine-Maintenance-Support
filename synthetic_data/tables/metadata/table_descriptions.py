"""
table_descriptions.py
---------------------
Hand-authored PROSE for the `maintenance` database's data dictionary: the
database overview and, per table, a description / when_to_use / per-column
descriptions.

This file holds ONLY semantics. Structure (column types, nullability, PK/FK) and
sample values are introspected from the live DB by generate_metadata.py, so they
can never drift. Tables are added here incrementally; generate_metadata.py merges
them with the live schema and warns about anything missing or orphaned.
"""

DATABASE_OVERVIEW = (
                    "This database is named maintenance and contains all the Operational data for a 3D-printing (FDM) plant's preventive and reactive maintenance "
                    "It holds the machine fleet and their versions/manuals, staff, "
                    "spare-parts inventory, technician availability, preventive-service history, "
                    "and reported incidents with their agentic diagnoses and resolutions taken during the maintenance process."
                )

# Per table: description, when_to_use, and a columns dict (column -> description).
# No types/keys/examples here — those come from the live DB.
TABLE_DESCRIPTIONS = {
    "machine_versions": {
        "description": (
            "Registry of the distinct machine versions (models) the plant owns, "
            "each identified by a Machine Version Code (MVC). The spine of the "
            "schema — links physical machines to their manuals and service "
            "intervals."
        ),
        "when_to_use": (
            "To resolve a machine's version into its model, manufacturer, "
            "firmware, recommended service interval, or the path to its "
            "instruction manual (the RAG source document)."
        ),
        "columns": {
            "mvc_code": "Machine Version Code — unique identifier for a machine model/version.",
            "model_name": "OEM model name.",
            "machine_type": "Equipment category (all FDM 3D printers in this dataset).",
            "manufacturer": "Name of the original equipment manufacturer.",
            "manual_path": "Relative path to this version's user-manual PDF (the RAG source document).",
            "firmware": "Firmware the version runs (context for firmware error messages such as MINTEMP).",
            "service_interval_days": "Recommended preventive-maintenance interval, in days; drives the overdue check.",
            "date_onboarded": "Date this version was added to the registry.",
        },
    },
    "employees": {
        "description": (
            "All staff at the plant across three roles — operators (who report "
            "incidents via the chat medium), technicians (who perform maintenance "
            "and resolve incidents), and supervisors (who oversee escalations)."
        ),
        "when_to_use": (
            "The main purpose of this table is to look up a person's role, their "
            "current employment status, their contact email for notifying them "
            "when an incident is booked, the shift in which they are working, "
            "e.g. to find a technician to assign, get the requester's email to "
            "notify, or check whether someone is active."
        ),
        "columns": {
            "employee_id": "Unique staff identifier.",
            "full_name": "Employee's full name.",
            "role": "Job role — operators report faults, technicians resolve them, supervisors oversee/escalate.",
            "email": "Email address used for notifications.",
            "phone": "Contact phone number.",
            "shift_time": "Working shift window — supervisors work 9AM-5PM; others rotate across the three 8-hour shifts.",
            "status": "Whether the employee is currently active (assignable) or inactive.",
            "date_joined": "Date the employee joined the organization.",
        },
    },
    "inventory": {
        "description": (
            "Spare-parts stock for the FDM printers — the parts a technician "
            "would draw on to resolve incidents or perform maintenance (hotend "
            "components, motion parts, electronics, bed parts, and consumables "
            "such as filament)."
        ),
        "when_to_use": (
            "To check whether a part is in stock, how many are on hand, where it "
            "is stored (bin), whether it is at/below its reorder threshold, and "
            "which machine versions it fits — e.g. before assigning a repair, "
            "confirm the needed part is available."
        ),
        "columns": {
            "part_id": "Unique part identifier.",
            "part_name": "Human-readable name of the spare part.",
            "category": "Part grouping (Hotend, Motion, Electronics, Bed, Extruder, Consumable).",
            "compatible_mvc": "Which machine versions the part fits (ALL or a comma-separated list of MVC codes).",
            "quantity_on_hand": "Current number of units in stock.",
            "reorder_threshold": "Stock level at/below which the part should be reordered (low-stock signal).",
            "unit": "Unit of measure (pcs, rolls).",
            "bin_location": "Storage location/bin where the part is kept.",
        },
    },
    "machines": {
        "description": (
            "The physical-unit FDM machine (printer) asset registry — every "
            "individual printer on the floor (named as M01, M02, …), each tagged "
            "to its machine version (mvc_code). The model/manual/interval details "
            "come from machine_versions via mvc_code. It also has the physical "
            "location of the printer."
        ),
        "when_to_use": (
            "To identify a specific machine the user refers to, confirm it exists, "
            "find its version (to reach the right instruction_manual/specs), its "
            "physical location, current operational status, or when it was "
            "installed — e.g. validate \"M03\" and resolve it to its version "
            "before diagnosing."
        ),
        "columns": {
            "machine_id": "Unique identifier for a physical machine/unit.",
            "mvc_code": "The machine's version code — links to its model, manual, and service interval.",
            "serial_number": "OEM serial number of the unit.",
            "location": "Physical location of the machine within the plant (floor/cell).",
            "status": "Current operational state of the machine.",
            "install_date": "Date the machine was installed/commissioned.",
        },
    },
    "technician_schedule": {
        "description": (
            "A monthly availability calendar of technicians for working on "
            "incidents — one row per active technician per working day. Each row "
            "gives that technician's daily 2-hour window (inside their shift) for "
            "taking on incident work, and whether that slot is still free."
        ),
        "when_to_use": (
            "To find when a technician is available to be assigned an incident — "
            "check which technicians have an Available slot on a given date, and "
            "book one by flipping its status to Booked."
        ),
        "columns": {
            "date": "The working day this availability row is for.",
            "employee_id": "The technician this slot belongs to.",
            "shift_time": "The technician's shift that day (sourced from employees); the slot falls within it.",
            "availability_slot": "The 2-hour incident-work window for that day (e.g. 09:00-11:00).",
            "availability_status": "Whether the slot is free or already booked for an incident; the agent flips it to Booked when assigning.",
        },
    },
    "maintenance_history": {
        "description": (
            "The log of PREVENTIVE (regular ~21-day) services performed on "
            "machines. Reactive / incident-driven work is not recorded here — it "
            "lives in the incidents table."
        ),
        "when_to_use": (
            "To find a machine's preventive-service history — most importantly "
            "the latest preventive service date, which (plus the 21-day interval) "
            "drives the overdue check; also to see who serviced it and what was "
            "done."
        ),
        "notes": (
            "Preventive cadence is driven only by the previous preventive service "
            "date — reactive/incident work does not reset it. Overdue check = "
            "latest preventive service_date + the version's service_interval_days "
            "(21) vs. today."
        ),
        "columns": {
            "service_id": "Unique identifier for a service record (e.g. serv_1).",
            "machine_id": "The machine that was serviced.",
            "service_date": "Date the preventive service was performed.",
            "performed_by": "Technician who performed the service.",
            "technician_comments": "Notes on the work done (the standard preventive SOP).",
        },
    },
    "incidents": {
        "description": (
            "The entire case record for reactive, incident-driven faults — one "
            "row per reported incident. Captures the full lifecycle: the "
            "operator's confirmed complaint, the agent's root-cause and proposed "
            "resolution, the allocated technician, the technician's comments, and "
            "the closure date."
        ),
        "when_to_use": (
            "To review past or live incidents for a machine or update the "
            "reported incident post booking it — what was reported, how the "
            "agentic workflow diagnosed and resolved it, who fixed it, and whether "
            "it's closed or still open. Useful as prior-incident context when "
            "diagnosing a new fault."
        ),
        "notes": (
            "An open/live incident has technician_comments and "
            "incident_closure_date as NULL (technician allocated but work not yet "
            "completed); a closed incident has both filled. The scheduled work "
            "date/slot (work_date, work_slot) is the day and window the assigned "
            "technician — or, on escalation, the supervisor — is booked to do the "
            "work, distinct from when the incident was reported or closed."
        ),
        "columns": {
            "incident_id": "Unique identifier for an incident (e.g. inc_1).",
            "machine_id": "The machine the fault occurred on.",
            "reported_date": "Date the incident was reported.",
            "reported_time": "Time the incident was reported.",
            "reported_by": "Operator who reported the incident.",
            "user_complaint": "The agent's understanding of the user's query, confirmed by the user before the incident is posted.",
            "agent_root_cause": "Root cause identified by the agentic workflow.",
            "agentic_resolution": "Solution proposed by the agentic workflow.",
            "technician_id": "Technician (or escalated supervisor) allocated to resolve the incident.",
            "work_date": "Scheduled day the allocated technician/supervisor is booked to do the work (may differ from reported_date). NULL until a slot is booked.",
            "work_slot": "Scheduled time window for the work, e.g. 09:00-11:00 (a 2-hour technician slot, or a 1-hour supervisor slot on escalation). NULL until booked.",
            "technician_comments": "Tasks the technician performed; usually confirms the agentic resolution. NULL while the incident is open.",
            "incident_closure_date": "Date the incident was closed. NULL while the incident is open.",
        },
    },
}
