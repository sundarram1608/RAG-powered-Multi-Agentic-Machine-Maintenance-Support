# Agentic RAG + MCP — FDM Service Assistant

A multi-agent AI workflow for **manufacturing equipment troubleshooting, maintenance and service**, built with
LangGraph (orchestration), RAG over a vector database (knowledge), and MCP (tools/actions).

> Status: 🚧 Agents Loading.....

---
## Building the Project from Scratch

### 0. Prerequisites:  
> - **Python 3.11** (this project was created with Python 3.11.6)
> - a local **MySQL Community Server**
> - macOS / Linux / Windows with the `venv` module (bundled with Python)

Run all commands from the project root.

--- 

### 1. Environment — virtual environment + dependencies
**Check your Python version:**

```bash
python3 --version   # expect Python 3.11.x
```

**Create the virtual environment**

The environment is named **`preventivemaintenance3.11`** (the `3.11` reflects the Python version).

```bash
# from the project root: .../agentic_ai_projects/agenticragmcp
python3 -m venv preventivemaintenance3.11
```

**Activate the environment**

**macOS / Linux (zsh / bash):**

```bash
source preventivemaintenance3.11/bin/activate
```

**(Optional) Upgrade pip**

```bash
python -m pip install --upgrade pip
```

**Install dependencies**

`requirements.txt`

```bash
pip install -r requirements.txt
```

**Deactivate when done**

```bash
deactivate
```

**Consolidated list of codes for environment setup** 
```bash
python3 --version
python3 -m venv preventivemaintenance3.11
source preventivemaintenance3.11/bin/activate
pip install -r requirements.txt
deactivate
```

---

### 2. Knowledge Base

The **Knowledge Base** has two layers and both feed the context to the LLM agents: 
- **Database** (structured facts/ SQL Tables) 
- **RAG** (user manuals & Safety documents/ Vector store).

> **2a. Database layer** — MySQL tables + seed data + schema metadata.
> The database layer is built with well though out tables that provide the structured knowledge base to the agentic LLMs. This layer is hosted in MySQL server.
> It's dual-purpose — a knowledge source *and* the operational system of record. Agent tools **read** facts from it **and write** to it at runtime (logging incidents, booking technician slots, recording incident outcomes). Only the `incidents` and `technician_schedule` tables are ever written; all other tables are read-only.
>  - Full guide → [`synthetic_data/README.md`](synthetic_data/README.md)
>  - (MySQL install/setup → [`synthetic_data/tables/readme_database_creation.md`](synthetic_data/tables/readme_database_creation.md))

  ```bash
  python synthetic_data/tables/generate_data.py
  ```
<br>

> **2b. RAG layer** — vector index built from the source PDFs.
> The RAG knowledge base is built from publicly available, legally reusable documents in
> [`synthetic_data/documents/`](synthetic_data/documents/):
> - **User manuals (FDM)** — LulzBot Mini, TAZ 6, TAZ Workhorse, TAZ Pro (CC BY-SA 4.0)
> - **Safety guidelines** — NIOSH *Approaches to Safe 3D Printing* (public domain)
> See [`synthetic_data/documents/ATTRIBUTIONS.md`](synthetic_data/documents/ATTRIBUTIONS.md) for full source URLs and license terms.
> RAG is **read-only** knowledge (the manuals).
> The source PDFs are not committed to version control (see `.gitignore`).
>  - Full guide to build the RAG layer → [`rag/README.md`](rag/README.md)
  
  ```bash
  python rag/orchestrator.py
  ```
  <br>
  
**Note on the Database layers:**  Both the layers are consulted for knowledge but, only the DB is mutated at runtime. 

---

### 3. MCP tool layer

The **tools** are the only way the agents act on the Knowledge Base. Each tool is a plain Python function in `mcp_server/mcp_tools/`;
`mcp_server/server.py` registers them with **FastMCP**, which turns each function's name + docstring + type hints into the schema the LLM sees.

**13 tools, in four groups:**
- **read (6)** — `get_machine`, `get_overdue_status`, `get_maintenance_history`, `get_incident_history`, `check_inventory`, `find_available_technician` (DB reads).
- **rag (2)** — `user_manual_retrieval`, `safety_retrieval` (thin wrappers over `rag/retriever.py`).
- **write (3)** — `create_incident`, `book_technician_slot`, `update_incident` (scoped writes to `incidents` / `technician_schedule` only).
- **other (2)** — `run_readonly_query` (LLM-generated read-only SQL), `send_email` (notifications from "Agentic FDM Services").

**Two MCP transports** (both operational — see `mcp_server/README.md`):
- **stdio** (default) serves the 11 local-data tools (read + rag + write); the agent auto-spawns it.
- **streamable-HTTP** (`127.0.0.1:8000`) serves the 2 "service" tools (`run_readonly_query`, `send_email`) as a separate process.

**Safety & PII**: three MySQL identities — admin, `maint_readonly` (SELECT only, for generated SQL), and `maint_write` (INSERT/UPDATE on the two
mutable tables only, no DELETE/DDL/master data). Generated SQL is validated (read-only, single statement, no `phone`); writes go only through the scoped tools; `phone` never enter the agent's context.

```bash
# one-time: create the read-only + write DB users (writes creds into .env)
python mcp_server/setup_db_users.py

# start the HTTP "services" server (separate process)
python mcp_server/server.py http        # -> http://127.0.0.1:8000/mcp
# (the stdio server is auto-spawned by the agent; run by hand: python mcp_server/server.py)

# smoke test — list the tools each transport exposes
python mcp_server/server.py --selftest  # expect 11 stdio + 2 http tools
```
For live `send_email`, also set `AGENT_EMAIL` + `AGENT_EMAIL_APP_PASSWORD` in `.env`.
Full guide → [`mcp_server/README.md`](mcp_server/README.md)

---

### 4. Agents / app — *(coming soon)*



---

## Notes

- The `preventivemaintenance3.11/` folder is the virtual environment and should **not** be committed to
  version control. Add it to `.gitignore`:

  ```gitignore
  preventivemaintenance3.11/
  ```

- If you ever need to start fresh, delete the folder and recreate it:

  ```bash
  rm -rf preventivemaintenance3.11
  python3 -m venv preventivemaintenance3.11
  ```
