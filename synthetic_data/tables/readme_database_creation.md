# Database setup — MySQL (`maintenance`)

This guide covers installing a **free** local MySQL server and creating the
`maintenance` database that will hold all the project's tables
(machine_versions, employees, inventory, machines, technician_schedule,
maintenance_history, incidents).

> MySQL **Community Server** is free and open source (GPL). This satisfies the
> project's "zero cost" constraint.

---

## 1. Prerequisites

- Admin rights to install software
- A terminal (zsh)

Note: These steps assume macOS.

Check whether MySQL is already installed:

```bash
mysql --version
```

If you get a version string, skip to **Step 4**.

---

## 2. Install MySQL Community Server

### Option A — Homebrew (recommended on macOS)

```bash
# install Homebrew first if you don't have it: https://brew.sh
brew update
brew install mysql
```

### Option B — Official installer

1. Download the macOS DMG from <https://dev.mysql.com/downloads/mysql/>
   (choose **MySQL Community Server**).
2. Run the installer. During setup you'll be asked to set a **root password** —
   remember it.
3. Add the MySQL binaries to your `PATH` (so `mysql` works in the terminal):

   ```bash
   echo 'export PATH="/usr/local/mysql/bin:$PATH"' >> ~/.zshrc
   source ~/.zshrc
   ```

---

## 3. Start the MySQL server

### Homebrew

```bash
# start now and on login
brew services start mysql

# (to stop later: brew services stop mysql)
```

### Official installer

Start it from **System Settings → MySQL → Start MySQL Server**, or:

```bash
sudo /usr/local/mysql/support-files/mysql.server start
```

Verify the server is up:

```bash
mysqladmin ping        # expect: "mysqld is alive"
```

---

## 4. Secure the installation (optional but recommended)

For a Homebrew install, `root` initially has **no password**. To set one and
apply sensible defaults:

```bash
mysql_secure_installation
```

Follow the prompts (set a root password, remove anonymous users, etc.).

---

## 5. Log in to MySQL

```bash
# if root has no password yet (fresh Homebrew install):
mysql -u root

# if you set a password:
mysql -u root -p      # then enter the password
```

You should land at the `mysql>` prompt.

---

## 6. Create the `maintenance` database

At the `mysql>` prompt:

```sql
CREATE DATABASE IF NOT EXISTS maintenance
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- verify it exists
SHOW DATABASES;

-- switch into it
USE maintenance;
```

> `utf8mb4` is used so the database fully supports Unicode (including any special
> characters in manual text, model names, etc.).

---

## 7. (Optional) Create a dedicated app user

Instead of using `root` from the application, create a limited user:

```sql
CREATE USER 'maint_app'@'localhost' IDENTIFIED BY 'change_me';
GRANT ALL PRIVILEGES ON maintenance.* TO 'maint_app'@'localhost';
FLUSH PRIVILEGES;
```

> **Note:** this `maint_app` user is only an *optional convenience* for the
> data-generation scripts (it replaces `root` in your `.env` if you prefer).
> It is **separate** from the runtime least-privilege MCP users
> (`maint_readonly` = SELECT only, `maint_write` = INSERT/UPDATE on `incidents` +
> `technician_schedule` only), which are created automatically by
> [`mcp_server/setup_db_users.py`](../../mcp_server/setup_db_users.py). You don't
> need `maint_app` for the agent to run.

---

## 8. Connection details (used by the data scripts and the app)

These values will go into the project's `.env` file (never commit real
passwords — `.env` is git-ignored):

| Setting   | Value                          |
|-----------|--------------------------------|
| Host      | `localhost`                    |
| Port      | `3306` (MySQL default)         |
| Database  | `maintenance`                  |
| User      | `root` or `maint_app`          |
| Password  | _(whatever you set)_           |

Example `.env` entries (template):

```dotenv
DB_HOST=localhost
DB_PORT=3306
DB_NAME=maintenance
DB_USER=root
DB_PASSWORD=your_password_here
```

---

## 9. Build the data

With the database created, the server running, and `.env` filled in (and the
project's virtual environment activated with `requirements.txt` installed),
generate all tables + seed data + metadata in one command, run from the project
root:

```bash
python synthetic_data/tables/generate_data.py
```

Verify the 7 tables were created:

```bash
mysql -u root -p -e "USE maintenance; SHOW TABLES;"
# expect 7 tables: machine_versions, employees, inventory, machines,
# technician_schedule, maintenance_history, incidents
```

---

## 10. Metadata / data dictionary

`metadata/schema_metadata.json` (for agents) and `metadata/schema_metadata.md`
(for developers) are **generated from the live database** by
`metadata/generate_metadata.py` (structure is introspected from
`information_schema`; only the prose lives in `metadata/table_descriptions.py`).

`generate_data.py` runs `generate_metadata` as its final phase, so the catalog is
rebuilt automatically whenever you rebuild the database.

> ⚠️ The catalog is **not** auto-tied to live DDL. If you ever change the schema
> *outside* `generate_data.py` (e.g. a manual `ALTER TABLE`), re-run
> `generate_metadata.py` (or `generate_data.py`) so the metadata stays in sync.
