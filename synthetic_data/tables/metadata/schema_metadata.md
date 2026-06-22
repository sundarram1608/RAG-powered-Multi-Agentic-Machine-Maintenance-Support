# Data dictionary — `maintenance` database

This database is named maintenance and contains all the Operational data for a 3D-printing (FDM) plant's preventive and reactive maintenance It holds the machine fleet and their versions/manuals, staff, spare-parts inventory, technician availability, preventive-service history, and reported incidents with their agentic diagnoses and resolutions taken during the maintenance process.

## Relationships

- `incidents.machine_id -> machines.machine_id`
- `incidents.reported_by -> employees.employee_id`
- `incidents.technician_id -> employees.employee_id`
- `machines.mvc_code -> machine_versions.mvc_code`
- `maintenance_history.machine_id -> machines.machine_id`
- `maintenance_history.performed_by -> employees.employee_id`
- `technician_schedule.employee_id -> employees.employee_id`

## Tables

### `employees`

All staff at the plant across three roles — operators (who report incidents via the chat medium), technicians (who perform maintenance and resolve incidents), and supervisors (who oversee escalations).

**When to use:** The main purpose of this table is to look up a person's role, their current employment status, their contact email for notifying them when an incident is booked, the shift in which they are working, e.g. to find a technician to assign, get the requester's email to notify, or check whether someone is active.

**Primary key:** employee_id

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `employee_id` | varchar(10) | No | PK | Unique staff identifier. | E01 |
| `full_name` | varchar(80) | No |  | Employee's full name. | Arjun Sharma |
| `role` | enum('Operator','Technician','Supervisor') | No |  | Job role — operators report faults, technicians resolve them, supervisors oversee/escalate. | Operator |
| `email` | varchar(120) | No |  | Email address used for notifications. | sundarram1997@gmail.com |
| `phone` | varchar(20) | Yes |  | Contact phone number. | 9999999999 |
| `shift_time` | enum('7AM-3PM','3PM-11PM','11PM-7AM','9AM-5PM') | Yes |  | Working shift window — supervisors work 9AM-5PM; others rotate across the three 8-hour shifts. | 7AM-3PM |
| `status` | enum('Active','Inactive') | No |  | Whether the employee is currently active (assignable) or inactive. | Active |
| `date_joined` | date | Yes |  | Date the employee joined the organization. | 2021-03-15 |

### `incidents`

The entire case record for reactive, incident-driven faults — one row per reported incident. Captures the full lifecycle: the operator's confirmed complaint, the agent's root-cause and proposed resolution, the allocated technician, the technician's comments, and the closure date.

**When to use:** To review past or live incidents for a machine or update the reported incident post booking it — what was reported, how the agentic workflow diagnosed and resolved it, who fixed it, and whether it's closed or still open. Useful as prior-incident context when diagnosing a new fault.

**Notes:** An open/live incident has technician_comments and incident_closure_date as NULL (technician allocated but work not yet completed); a closed incident has both filled. The scheduled work date/slot (work_date, work_slot) is the day and window the assigned technician — or, on escalation, the supervisor — is booked to do the work, distinct from when the incident was reported or closed.

**Primary key:** incident_id
**Foreign keys:** machine_id → machines.machine_id; reported_by → employees.employee_id; technician_id → employees.employee_id

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `incident_id` | varchar(12) | No | PK | Unique identifier for an incident (e.g. inc_1). | inc_8 |
| `machine_id` | varchar(10) | No | FK | The machine the fault occurred on. | M01 |
| `reported_date` | date | No |  | Date the incident was reported. | 2026-04-06 |
| `reported_time` | time | No |  | Time the incident was reported. | 23:12:42 |
| `reported_by` | varchar(10) | No | FK | Operator who reported the incident. | E01 |
| `user_complaint` | varchar(300) | No |  | The agent's understanding of the user's query, confirmed by the user before the incident is posted. | Printer halted mid-print showing a MINTEMP error. |
| `agent_root_cause` | varchar(300) | No |  | Root cause identified by the agentic workflow. | Hotend thermistor wiring broken or disconnected. |
| `agentic_resolution` | varchar(300) | No |  | Solution proposed by the agentic workflow. | Inspect and replace the hotend thermistor; secure the wiring connection. |
| `technician_id` | varchar(10) | Yes | FK | Technician (or escalated supervisor) allocated to resolve the incident. | E02 |
| `work_date` | date | Yes |  | Scheduled day the allocated technician/supervisor is booked to do the work (may differ from reported_date). NULL until a slot is booked. | 2026-04-07 |
| `work_slot` | varchar(15) | Yes |  | Scheduled time window for the work, e.g. 09:00-11:00 (a 2-hour technician slot, or a 1-hour supervisor slot on escalation). NULL until booked. | 10:00-12:00 |
| `technician_comments` | varchar(350) | Yes |  | Tasks the technician performed; usually confirms the agentic resolution. NULL while the incident is open. | Confirmed to agentic resolution; performed as advised and verified the fix. |
| `incident_closure_date` | date | Yes |  | Date the incident was closed. NULL while the incident is open. | 2026-04-08 |

### `inventory`

Spare-parts stock for the FDM printers — the parts a technician would draw on to resolve incidents or perform maintenance (hotend components, motion parts, electronics, bed parts, and consumables such as filament).

**When to use:** To check whether a part is in stock, how many are on hand, where it is stored (bin), whether it is at/below its reorder threshold, and which machine versions it fits — e.g. before assigning a repair, confirm the needed part is available.

**Primary key:** part_id

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `part_id` | varchar(10) | No | PK | Unique part identifier. | PRT-001 |
| `part_name` | varchar(100) | No |  | Human-readable name of the spare part. | Hotend thermistor |
| `category` | varchar(40) | Yes |  | Part grouping (Hotend, Motion, Electronics, Bed, Extruder, Consumable). | Hotend |
| `compatible_mvc` | varchar(50) | Yes |  | Which machine versions the part fits (ALL or a comma-separated list of MVC codes). | ALL |
| `quantity_on_hand` | int | No |  | Current number of units in stock. | 3 |
| `reorder_threshold` | int | No |  | Stock level at/below which the part should be reordered (low-stock signal). | 5 |
| `unit` | varchar(15) | Yes |  | Unit of measure (pcs, rolls). | pcs |
| `bin_location` | varchar(20) | Yes |  | Storage location/bin where the part is kept. | A-01 |

### `machine_versions`

Registry of the distinct machine versions (models) the plant owns, each identified by a Machine Version Code (MVC). The spine of the schema — links physical machines to their manuals and service intervals.

**When to use:** To resolve a machine's version into its model, manufacturer, firmware, recommended service interval, or the path to its instruction manual (the RAG source document).

**Primary key:** mvc_code

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `mvc_code` | varchar(10) | No | PK | Machine Version Code — unique identifier for a machine model/version. | MVC01 |
| `model_name` | varchar(80) | No |  | OEM model name. | LulzBot Mini |
| `machine_type` | varchar(40) | No |  | Equipment category (all FDM 3D printers in this dataset). | FDM 3D Printer |
| `manufacturer` | varchar(80) | No |  | Name of the original equipment manufacturer. | Aleph Objects, Inc. (LulzBot) |
| `manual_path` | varchar(255) | No |  | Relative path to this version's user-manual PDF (the RAG source document). | synthetic_data/documents/user_manuals/lulzbot_mini_user_manual.pdf |
| `firmware` | varchar(40) | Yes |  | Firmware the version runs (context for firmware error messages such as MINTEMP). | Marlin |
| `service_interval_days` | int | Yes |  | Recommended preventive-maintenance interval, in days; drives the overdue check. | 21 |
| `date_onboarded` | date | Yes |  | Date this version was added to the registry. | 2023-02-10 |

### `machines`

The physical-unit FDM machine (printer) asset registry — every individual printer on the floor (named as M01, M02, …), each tagged to its machine version (mvc_code). The model/manual/interval details come from machine_versions via mvc_code. It also has the physical location of the printer.

**When to use:** To identify a specific machine the user refers to, confirm it exists, find its version (to reach the right instruction_manual/specs), its physical location, current operational status, or when it was installed — e.g. validate "M03" and resolve it to its version before diagnosing.

**Primary key:** machine_id
**Foreign keys:** mvc_code → machine_versions.mvc_code

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `machine_id` | varchar(10) | No | PK | Unique identifier for a physical machine/unit. | M03 |
| `mvc_code` | varchar(10) | No | FK | The machine's version code — links to its model, manual, and service interval. | MVC01 |
| `serial_number` | varchar(50) | Yes |  | OEM serial number of the unit. | LZ-TAZ6-2023-0101 |
| `location` | varchar(60) | Yes |  | Physical location of the machine within the plant (floor/cell). | Floor 1 - Cell A |
| `status` | enum('Operational','Under Maintenance','Idle','Decommissioned') | No |  | Current operational state of the machine. | Operational |
| `install_date` | date | Yes |  | Date the machine was installed/commissioned. | 2023-06-01 |

### `maintenance_history`

The log of PREVENTIVE (regular ~21-day) services performed on machines. Reactive / incident-driven work is not recorded here — it lives in the incidents table.

**When to use:** To find a machine's preventive-service history — most importantly the latest preventive service date, which (plus the 21-day interval) drives the overdue check; also to see who serviced it and what was done.

**Notes:** Preventive cadence is driven only by the previous preventive service date — reactive/incident work does not reset it. Overdue check = latest preventive service_date + the version's service_interval_days (21) vs. today.

**Primary key:** service_id
**Foreign keys:** machine_id → machines.machine_id; performed_by → employees.employee_id

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `service_id` | varchar(12) | No | PK | Unique identifier for a service record (e.g. serv_1). | serv_20 |
| `machine_id` | varchar(10) | No | FK | The machine that was serviced. | M01 |
| `service_date` | date | No |  | Date the preventive service was performed. | 2026-04-01 |
| `performed_by` | varchar(10) | No | FK | Technician who performed the service. | E02 |
| `technician_comments` | varchar(300) | No |  | Notes on the work done (the standard preventive SOP). | Performed the regular SOP |

### `technician_schedule`

A monthly availability calendar of technicians for working on incidents — one row per active technician per working day. Each row gives that technician's daily 2-hour window (inside their shift) for taking on incident work, and whether that slot is still free.

**When to use:** To find when a technician is available to be assigned an incident — check which technicians have an Available slot on a given date, and book one by flipping its status to Booked.

**Primary key:** date, employee_id
**Foreign keys:** employee_id → employees.employee_id

| Column | Type | Nullable | Key | Description | Example |
|---|---|---|---|---|---|
| `date` | date | No | PK | The working day this availability row is for. | 2026-06-01 |
| `employee_id` | varchar(10) | No | PK | The technician this slot belongs to. | E02 |
| `shift_time` | enum('7AM-3PM','3PM-11PM','11PM-7AM') | Yes |  | The technician's shift that day (sourced from employees); the slot falls within it. | 3PM-11PM |
| `availability_slot` | varchar(15) | Yes |  | The 2-hour incident-work window for that day (e.g. 09:00-11:00). | 20:00-22:00 |
| `availability_status` | enum('Available','Booked') | No |  | Whether the slot is free or already booked for an incident; the agent flips it to Booked when assigning. | Booked |
