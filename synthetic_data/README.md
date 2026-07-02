# Data layer

> This is a synthesized data layer replicating indutry level data management platform of a Maintenance team.

This folder holds the project's ** data layer**: 

- the MySQL database (tables + seed data + schema metadata) 
- the RAG source documents (PDFs).

Everything here is reproducible from scratch with the steps below.

## What's in here

```
synthetic_data/
├── README.md                     # this file (data-layer guide)
├── documents/                    # RAG source documents
│   ├── ATTRIBUTIONS.md           #   sources + licenses
│   ├── download_documents.py     #   fetches the 5 PDFs
│   ├── user_manuals/             #   4 LulzBot FDM manuals(CC BY-SA 4.0)-(git-ignored)
│   └── safety_guidelines/        #   NIOSH safe-3D-printing guide (public domain)-(git-ignored)
└── tables/                       # MySQL database
    ├── readme_database_creation.md   # MySQL install + DB setup details
    ├── db_connection.py              # shared connection (reads .env)
    ├── create_sql_tables.py          # static tables
    ├── create_faker_tables.py        # generated tables (Faker)
    ├── generate_data.py              # ENTRYPOINT: builds everything
    └── metadata/                     # data dictionary
        ├── table_descriptions.py     #   Details on tables
        ├── generate_metadata.py      #   introspect DB + enrich -> JSON + MD
        ├── schema_metadata.json      #   catalog for agents
        └── schema_metadata.md        #   catalog replica
```

**What data gets produced**

- **7 MySQL tables** in the `maintenance` database: `machine_versions`, `employees`, `inventory`, `machines`, `technician_schedule`, `maintenance_history`, `incidents`.
- **5 PDFs** under `documents/`.
- **Schema metadata** (`schema_metadata.json` / `.md`) generated from the live DB.



## How to replicate the data layer (end to end)

> Prerequisites: **Python 3.11** and a local **MySQL Community Server**.
> All commands run from the **project root**.



### 1. Virtual environment + dependencies

```bash
python3 -m venv preventivemaintenance3.11
source preventivemaintenance3.11/bin/activate
pip install -r requirements.txt
```



### 2. MySQL + the `maintenance` database

Follow `[tables/readme_database_creation.md](tables/readme_database_creation.md)`
to install/start MySQL and create the `maintenance` database.

### 3. Configure `.env`

Copy the template and fill in your DB password:

```bash
cp .env.example .env      # then edit DB_PASSWORD
```



### 4. Download the RAG documents

The PDFs are git-ignored, so fetch them (one command, idempotent):

```bash
python synthetic_data/documents/download_documents.py
# expect: 5/5 documents ready
```



### 5. Build the database (tables + seed data + metadata)

```bash
python synthetic_data/tables/generate_data.py
```

This runs three phases: static tables → Faker-generated tables → metadata
(`schema_metadata.json` / `.md`). Re-running drops and rebuilds everything.

### 6. Verify

```bash
mysql -u root -p -e "USE maintenance; SHOW TABLES;"
# expect the 7 tables listed above
```



## Notes

- **Reference date:** the seed data is anchored to **2026-06-16** as "today"
(history runs Apr 1 → Jun 15, 2026). The MCP tools use this as `REFERENCE_TODAY`
(the real `date.today()` is intentionally not used for demo, as data are synthesized and not real).
- **Seeded edge cases** (intentional, for the agents to handle): machines `M03`
and `M07` are overdue for preventive maintenance; some inventory parts are
low/out of stock; 4 incidents are still open.
- **Incident scheduling:** each incident carries a scheduled `work_date` /
`work_slot` (when the assigned technician — or escalated supervisor — is booked
to do the work), distinct from the reported and closure dates. See
`schema_metadata.md` for the per-column detail.
- **Licenses:** see `[documents/ATTRIBUTIONS.md](documents/ATTRIBUTIONS.md)`. The
LulzBot manuals are CC BY-SA 4.0; the NIOSH guide is public domain.
- **Metadata sync:** `schema_metadata.`* is generated from the live DB by
`generate_metadata.py` (called automatically by `generate_data.py`). After any
out-of-band schema change, re-run it.

