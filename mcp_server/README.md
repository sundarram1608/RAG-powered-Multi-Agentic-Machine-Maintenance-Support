# MCP tool server

The tools the agents call are plain Python functions in `mcp_tools/`, grouped by kind.
`server.py` registers them with FastMCP so the LangGraph agents can call them as MCP tools.

> **Design:** 
>
> - fixed operations use **hardcoded, parameterized SQL** (deterministic, injection-safe). 
> - Only `run_readonly_query` runs **LLM-generated** SQL — and that is read-only + validated (see `safety.py`). 
> - Writes happen only via the scoped write tools, never via generated SQL.

## Structure

```
mcp_server/
├── README.md
├── server.py                 # FastMCP — registers all tools (stdio + HTTP)
├── safety.py                 # SQL validation + read-only connection
├── setup_db_users.py         # one-time: create the read-only + write MySQL users
└── mcp_tools/
    ├── _common.py            # shared: import-path setup + run_query/run_write + REFERENCE_TODAY
    ├── read/                 # SELECT-only tools
    ├── rag_wrappers/         # wrap rag/retriever.py
    ├── write/                # scoped writes (create/book/update incident)
    └── other/                # run_readonly_query · send_email
```

`_common.py` puts the existing `db_connection` (DB) and `rag/` (retriever) on the import path and provides `run_query(sql, params)` (parameterized read → list of dicts) and `run_write(statements)` (one or more writes in a single transaction, via the least-privilege write connection). `REFERENCE_TODAY = 2026-06-16` is the dataset's "current date" (seeded data is anchored to June 2026; the real `date.today()` is intentionally not used) to demonstrate how the Agentic app works.

**Three MySQL identities, each matched to its job** (read-only + write users are created once by `setup_db_users.py`):


| User                         | Privilege                                                              | Used by                               |
| ---------------------------- | ---------------------------------------------------------------------- | ------------------------------------- |
| admin (`DB_USER`, e.g. root) | everything                                                             | data generation, schema, test cleanup |
| `maint_readonly`             | `SELECT` only                                                          | `run_readonly_query` (generated SQL)  |
| `maint_write`                | `SELECT` + `INSERT/UPDATE` on `incidents` + `technician_schedule` only | the write tools                       |


---

## The server (`server.py`) — how it's built, transports, and running it

**How it's built.** `server.py` imports the plain tool functions and registers
each with FastMCP via `mcp.add_tool(fn)` — no decorators in the tool files (they
stay standalone-testable). FastMCP derives each tool's schema from the function's
**name** (tool name), **docstring** (the *full* description the LLM sees), and
**type hints** (the input schema). That's why the tool docstrings are written for
the model, not just for humans.

**Two transports, split by tool group** — the system uses *both* at once:


| Transport           | Tool group                                           | How it runs                                                                           | Why                                                         |
| ------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| **stdio** (default) | local data plane — 8 read + 2 RAG + 3 write tools    | the agent **auto-spawns** `server.py` as a child process; no port, no network surface | tools bundled with the agent, touching local MySQL + Chroma |
| **streamable-HTTP** | shared services — `run_readonly_query`, `send_email` | runs as a **separate `127.0.0.1:8000` process**; the agent connects by URL            | "service-style" tools you could host separately later       |


A single FastMCP instance serves one transport, so these are two server
instances (built from the same file). The agent layer aggregates both with
`langchain-mcp-adapters`' `MultiServerMCPClient`, presenting the union of all 15
tools to the LLM. Switching the HTTP group to a remote host later is a one-line
change (`host`/`port`), with auth/TLS added only if exposed beyond localhost.

**Running it**

```bash
# 1. start the HTTP "services" server (separate process, stays up)
python mcp_server/server.py http        # -> http://127.0.0.1:8000/mcp

# 2. the stdio "local data" server is auto-spawned by the agent (Phase 3);
#    to run it by hand: python mcp_server/server.py            # (default = stdio)

# smoke test — list the tools each transport exposes, no LLM/network:
python mcp_server/server.py --selftest   # expect 13 stdio + 2 http tools
```

> **First-time prerequisites:** `python mcp_server/setup_db_users.py` (DB users)
> and, for live email, the `AGENT_EMAIL_*` vars in `.env` (see *Other tools*).

---

## Read tools (`mcp_tools/read/`)

### `get_machine(machine_id)`

- **Purpose:** validate a machine exists and resolve its version/status/location.
- **What it does:** joins `machines` + `machine_versions` on `mvc_code`.
- **Input:** `machine_id: str` (case-insensitive, normalized to upper).
- **Output:** `{exists, machine_id, mvc_code, model_name, status, location}` or `{exists: False}`.
- **Used by:** Intake (validate id), Diagnosis (get `mvc_code` for RAG).
- **Edge cases:** not found → `{exists: False}` (Intake re-asks); `m01`→`M01` (normalized); a **Decommissioned** machine returns `exists: True` with `status` flagged so the agent won't troubleshoot a retired unit.

### `get_overdue_status(machine_id)`

- **Purpose:** determine if a machine is overdue for preventive maintenance.
- **What it does:** latest `service_date` (maintenance_history) + `service_interval_days` (version) → `next_due`; compares to `REFERENCE_TODAY`.
- **Input:** `machine_id: str`.
- **Output:** `{machine_id, last_service_date, interval_days, next_due_date, overdue, days_overdue}`; or `{has_history: False}`; or `{exists: False}`.
- **Used by:** Diagnosis (overdue is a strong root-cause signal).
- **Edge cases:** never serviced → `{has_history: False, overdue: None}`; machine missing → `{exists: False}`; uses `REFERENCE_TODAY` (not real today).

### `get_maintenance_history(machine_id, limit=5)`

- **Purpose:** recent preventive-service records for a machine.
- **What it does:** `SELECT … ORDER BY service_date DESC LIMIT n`.
- **Input:** `machine_id: str`, `limit: int = 5`.
- **Output:** `[{service_id, service_date, performed_by, technician_comments}, …]` (newest first).
- **Used by:** Diagnosis (service context).
- **Edge cases:** no history → `[]`; `limit` caps rows.

### `get_incident_history(machine_id, limit=5)`

- **Purpose:** past incidents for a machine (prior-case context).
- **What it does:** `SELECT … ORDER BY reported_date DESC LIMIT n`.
- **Input:** `machine_id: str`, `limit: int = 5`.
- **Output:** `[{incident_id, reported_date, user_complaint, agent_root_cause, agentic_resolution, technician_comments, incident_closure_date}, …]`.
- **Used by:** Diagnosis ("has this happened before / how was it fixed?").
- **Edge cases:** **PII-minimized** — omits `reported_by`/`technician_id`; **open** incidents (NULL resolution/closure) are included; none → `[]`.

### `get_incident(incident_id)`

- **Purpose:** fetch ONE incident by id with its current state (for acting on a known incident).
- **What it does:** `SELECT … WHERE incident_id = %s`; adds `status` (`open`/`closed`).
- **Input:** `incident_id: str` (case-insensitive, e.g. `inc_26`).
- **Output:** `{exists: True, incident_id, machine_id, status, reported_date, reported_by, user_complaint, agent_root_cause, agentic_resolution, technician_id, work_date, work_slot, technician_comments, incident_closure_date}` · or `{exists: False}`.
- **Used by:** Manage Incident (confirm existence, show state for approval, find who to notify).
- **Edge cases:** unknown id → `{exists: False}`; returns `reported_by`/`technician_id` (employee_ids, not PII — `send_email` resolves addresses internally). Distinct from `get_incident_history` (which is keyed by machine).

### `check_inventory(part)`

- **Purpose:** stock / availability / bin / compatibility for a part.
- **What it does:** matches `part_id` exactly or `part_name` via `LIKE`; adds `in_stock`/`low_stock` flags.
- **Input:** `part: str` (id or name fragment).
- **Output:** `[{part_id, part_name, category, compatible_mvc, quantity_on_hand, reorder_threshold, unit, bin_location, in_stock, low_stock}, …]`.
- **Used by:** Diagnosis & Action (is the part available before recommending/booking?).
- **Edge cases:** no match → `[]`; out of stock (qty 0) → `in_stock: False`; qty ≤ threshold → `low_stock: True`; ambiguous name → returns all matches.

### `find_available_technician(booking_moment=None)`

- **Purpose:** propose who should do the work and **on what date/slot**; **escalate to a supervisor** if no technician is free. Read-only — it only proposes; `book_technician_slot` commits.
- **What it does:** searches `technician_schedule` for the earliest `Available` slot on the **booking day** (only slots *after* the booking time), then **+1 day**, then **+2 days**; if none across those three days, escalates to an active supervisor and computes a **1-hour slot** inside their 9AM-5PM shift (10:00–16:00), on the booking day if one still fits, else +1.
- **Input:** `booking_moment: str` — `"YYYY-MM-DD HH:MM:SS"` (or just a date); defaults to `REFERENCE_TODAY` + the current clock time.
- **Output:** `{assignee_role: "Technician", employee_id, date, availability_slot, shift_time, escalated: False}` · or `{assignee_role: "Supervisor", employee_id, date, availability_slot, escalated: True, note}` · or `{available: False, note}`. The returned `date` is the scheduled **work date** and may differ from the booking date.
- **Used by:** Action (allocate someone to an incident).
- **Edge cases:** booking late in the day rolls forward to the next day's slots; no technician within 3 days → supervisor escalation with a computed slot; no active supervisor either → `{available: False}`. Returns `employee_id` only — **never the email** (PII; `send_email` resolves it internally).

### `list_available_technicians(from_date=None, employee_id=None)`

- **Purpose:** list assignable technicians (earliest free slot **per** technician) so a manager can **choose** — vs `find_available_technician`, which auto-picks one and escalates.
- **What it does:** `Available` slots on/after `from_date` joined to active `Technician`s, deduped to the earliest per technician; optional `employee_id` restricts to one (empty ⇒ unavailable).
- **Input:** `from_date: str` (default `REFERENCE_TODAY`), `employee_id: str` (optional).
- **Output:** `[{employee_id, date, availability_slot, shift_time}, …]` (soonest first) · `[]` if none.
- **Used by:** Manage Incident (present technicians to choose for assign/reassign).
- **Edge cases:** excludes supervisor-inserted rows (technicians only); named technician with no free slot → `[]`.

---

## RAG tools (`mcp_tools/rag_wrappers/`)

Thin MCP adapters over `rag/retriever.py` so retrieval is exposed alongside the DB
tools (one uniform tool interface for the agent). Each flattens the retriever's
nested citation fields (`source_file`, `page_start`, `page_end`) to the top level
and drops internal metadata (`mvc_code`, `doc_type`) the LLM doesn't need.

### `user_manual_retrieval(query, mvc_code, k=5)`

- **Purpose:** retrieve the most relevant user-manual passages for a specific machine version (primary grounding for troubleshooting / how-to answers).
- **What it does:** embeds `query` with BGE-M3, runs an `mvc_code`-filtered cosine search over Chroma, flattens the top-k chunks.
- **Input:** `query: str` (symptom/question), `mvc_code: str` (from `get_machine`), `k: int = 5`.
- **Output:** `[{text, source_file, page_start, page_end, distance}, …]` (smaller distance = more relevant).
- **Used by:** Diagnosis, Guidance.
- **Edge cases:** results are **scoped to one `mvc_code`** so a different model's manual never leaks in; unknown `mvc_code` / empty index → `[]`; blank `query` → `[]` (guard avoids a meaningless search).

### `safety_retrieval(query, k=2)`

- **Purpose:** retrieve safety-guide passages so a recommended fix carries the right precautions.
- **What it does:** embeds `query`, runs a `doc_type='safety'`-filtered cosine search (**no `mvc_code` filter** — the safety guide applies to all models), flattens the top-k chunks.
- **Input:** `query: str`, `k: int = 2`.
- **Output:** `[{text, source_file, page_start, page_end, distance}, …]`.
- **Used by:** Diagnosis, Guidance — called whenever a fix involves physically handling any hazard on the machine (agent-layer policy).
- **Edge cases:** empty index → `[]`; blank `query` → `[]`.

---

## Write tools (`mcp_tools/write/`)

The only ways the workflow mutates the database. Write-safety model:

- **No generic write tool** — only these three, each with hardcoded, parameterized
SQL touching a **fixed set of columns**.
- **Only two tables are ever written:** `incidents` and `technician_schedule`. All
master/reference data is read-only.
- **Least-privilege user:** all writes go through `maint_write` (`run_write`), which
the DB restricts to `INSERT/UPDATE` on those two tables — no DELETE, no DDL, no
master data. (Proven: a `DELETE`/`UPDATE employees`/`DROP` on this connection is
denied with MySQL error 1142.)
- **Main details immutable:** the reported facts, root cause, and resolution can be
*created* but never *edited*.

### `create_incident(machine_id, reported_by, user_complaint, agent_root_cause, agentic_resolution)`

- **Purpose:** open a new incident for a diagnosed fault.
- **What it does:** generates `inc_{max+1}`, `INSERT`s the create-fields (`reported_date`=`REFERENCE_TODAY`, `reported_time`=current clock time); leaves `technician_id`/`work_date`/`work_slot`/`technician_comments`/`incident_closure_date` NULL → **open**.
- **Input:** the five fields above (`machine_id`, `reported_by` = `employee_id`).
- **Output:** `{ok: True, incident_id, machine_id, status: "open"}` · `{ok: False, error}`.
- **Used by:** Action.
- **Edge cases:** unknown `machine_id`/`reported_by` → friendly validation error (not a raw FK error).

### `book_technician_slot(incident_id, employee_id, date, availability_slot)`

- **Purpose:** assign someone to an open incident and book their slot. Consumes `find_available_technician`'s output (or a manager-chosen slot from `list_available_technicians`).
- **What it does (one transaction):** **reassign** — if the incident already has a different assignee/slot, first frees that prior slot (→ `Available`); then if a `(date, employee_id)` schedule row exists & is `Available` → `UPDATE` it to `Booked` (**technician**), or if no row exists → `INSERT` a `Booked` row (**supervisor escalation**, `shift_time` NULL); then `UPDATE incidents` `technician_id` + `work_date` + `work_slot`.
- **Input:** `incident_id`, `employee_id`, `date` (`YYYY-MM-DD`), `availability_slot` (e.g. `09:00-11:00`).
- **Output:** `{ok: True, incident_id, employee_id, assignment_type: "technician"|"supervisor", booked_slot:{date, availability_slot}, reassigned_from}` · `{ok: False, error}`.
- **Used by:** Action, Manage Incident (after `find_available_technician` / a chosen slot).
- **Edge cases:** unknown/closed incident → reject; unknown employee → reject; slot already `Booked` → reject (availability enforced — no overload); **reassign** auto-frees the prior assignee's slot (`reassigned_from`).
- **Edge cases:** unknown/closed incident → reject; unknown employee → reject; slot already `Booked` → reject; supervisor (no calendar row) → a new `Booked` row is inserted for them.

### `update_incident(incident_id, technician_comments, close=True)`

- **Purpose:** record an incident's outcome and (by default) close it.
- **What it does:** `UPDATE`s **only** `technician_comments` and (if `close`) `incident_closure_date`=`REFERENCE_TODAY`. Cannot touch any other column.
- **Input:** `incident_id`, `technician_comments`, `close: bool = True`.
- **Output:** `{ok: True, incident_id, status: "closed"|"open"}` · `{ok: False, error}`.
- **Used by:** Action.
- **Edge cases:** unknown incident → reject; closing an already-closed incident → reject; closing requires non-empty `technician_comments`.

---

## Other tools (`mcp_tools/other/`)

### `run_readonly_query(sql)`

The only tool that runs **LLM-generated** SQL (for the Text-to-SQL agent). All
other tools use hardcoded, parameterized SQL. Protected by **two independent
layers** (defense in depth):


| Layer                                          | Guarantees                                                                                        | File                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **Read-only MySQL user** (`GRANT SELECT` only) | the database *physically* rejects any write/DDL — the hard backstop                               | `setup_db_users.py` → `safety.get_readonly_connection()` |
| **Code validation**                            | fast, friendly rejection before the DB; blocks PII, multiple statements, comments, write keywords | `safety.validate_select_sql()`                           |


- **Purpose:** ad-hoc analytical SELECTs the purpose-built tools don't cover (counts, group-bys, cross-table summaries).
- **What it does:** validates the SQL, then runs the cleaned query on the SELECT-only connection.
- **Input:** `sql: str` — a single read-only `SELECT`/`WITH` statement.
- **Output:** `{ok: True, row_count, rows, sql_executed}` · `{ok: False, error, category: "validation"}` · `{ok: False, error, category: "database"}`.
- **Used by:** Text-to-SQL agent.
- **Validation rules (`safety.validate_select_sql`):** non-empty · no comments (`--`, `#`, `/* */`) · single statement (no stacked `;`) · must start `SELECT`/`WITH` · no write/DDL/file keywords (`INSERT, UPDATE, DELETE, DROP, …, INTO, OUTFILE`) · **no PII column `phone`** (`email`/`full_name` are allowed — in-office policy) · auto-`LIMIT 200` if none given.
- **Edge cases:** write/DDL → blocked at validation **and** denied by the read-only user; PII (`phone`) → validation reject **and**, as a backstop, any `phone` column is stripped from the result rows (so a `SELECT `* on `employees` can't surface it); multi-statement/comment → reject; no `LIMIT` → auto-capped; bad column/syntax → `category: "database"` so the agent can self-correct.

#### One-time setup — the read-only + write MySQL users

The read path (`run_readonly_query`) needs a SELECT-only account and the write
tools need the least-privilege write account. Create **both** at once with the
helper (uses your existing admin creds in `.env`, generates each password, and
writes `DB_READONLY_`* / `DB_WRITE_*` back into `.env`):

```bash
python mcp_server/setup_db_users.py
```

Equivalent manual SQL (if you prefer to run it yourself, then set the
`DB_READONLY_*` / `DB_WRITE_*` vars in `.env`):

```sql
-- read-only (generated SQL)
CREATE USER 'maint_readonly'@'localhost' IDENTIFIED BY '<password>';
GRANT SELECT ON maintenance.* TO 'maint_readonly'@'localhost';

-- write (scoped to the two mutable tables; no DELETE/DDL/master data)
CREATE USER 'maint_write'@'localhost' IDENTIFIED BY '<password>';
GRANT SELECT ON maintenance.* TO 'maint_write'@'localhost';
GRANT INSERT, UPDATE ON maintenance.incidents TO 'maint_write'@'localhost';
GRANT INSERT, UPDATE ON maintenance.technician_schedule TO 'maint_write'@'localhost';
FLUSH PRIVILEGES;
```

### `send_email(to_employee_id, incident_id, dry_run=False)`

- **Purpose:** notify an employee about an incident, from **"Agentic FDM Services"**.
- **What it does:** resolves the recipient's address **internally** from `employee_id`, picks the template by their **role** (operator = report confirmation incl. the allocated person's name; technician = work assignment; supervisor = escalation), fills it from the incident, and sends via Gmail SMTP.
- **Input:** `to_employee_id: str`, `incident_id: str`, `dry_run: bool = False` (set `True` to compose without sending).
- **Output:** `{ok: True, dry_run: False, to_employee_id, role, subject, sent: True}` · `{ok: True, dry_run: True, …, subject, body}` · `{ok: False, error}`.
- **Used by:** Action.
- **PII:** the email address is **never** passed in or returned — only `to_employee_id` + `role` appear in the output (the address is resolved internally; `send_email` is the only place it's used). `full_name` may appear in the body (in-office policy).
- **Edge cases:** unknown employee/incident → error; no email on file → error; `dry_run=False` but `AGENT_EMAIL`/app password unset → `{ok: False, error: "email not configured"}` (never a silent failure).

#### One-time setup — the Agentic FDM Services Gmail (free)

`send_email` sends through Gmail SMTP (`smtplib`, free). On the sender account
(`AGENT_EMAIL`, e.g. `fdm.service.agent@gmail.com`): enable **2-Step
Verification**, then **Google Account → Security → App passwords** to generate a
16-char password. Put both in `.env`:

```
AGENT_EMAIL=fdm.service.agent@gmail.com
AGENT_EMAIL_APP_PASSWORD=<16-char app password>
```

Recipients see the `From:` as `Agentic FDM Services <AGENT_EMAIL>`. (In the seeded data
every employee email points to an inbox you control, so live sends are safe to test.)