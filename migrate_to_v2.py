"""
Safe migration script for Finance Tracker v2 production DB.

What this does (ALL operations are non-destructive):
  1. Creates `users` table if not exists
  2. Creates `user_settings` table if not exists
  3. Creates `budgets` table if not exists
  4. Creates `goals` table if not exists
  5. Adds `user_id` column to `operations` if it doesn't already exist
     (sets NULL, does NOT drop old rows - existing data is preserved)
  6. Ensures a default system user exists for legacy (pre-v2) operations
  7. Assigns legacy operations (user_id IS NULL) to that system user

Run manually on production with:
    PYTHONPATH=. python3 migrate_to_v2.py

NOTE: Review and confirm the SQL steps before running against production.
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2-binary is not installed.")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False
cur = conn.cursor()

try:
    print("=== Finance Tracker v2 — Safe Migration ===")

    # Step 1: Create users table
    print("[1/7] Creating users table (IF NOT EXISTS)...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Step 2: Create user_settings table
    print("[2/7] Creating user_settings table (IF NOT EXISTS)...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            language TEXT DEFAULT 'RU',
            theme TEXT DEFAULT 'light',
            currency TEXT DEFAULT 'KZT'
        )
    """)

    # Step 3: Create budgets table
    print("[3/7] Creating budgets table (IF NOT EXISTS)...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
            period TEXT DEFAULT 'month',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Step 4: Create goals table
    print("[4/7] Creating goals table (IF NOT EXISTS)...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            target_amount NUMERIC(14, 2) NOT NULL CHECK (target_amount > 0),
            current_amount NUMERIC(14, 2) DEFAULT 0 CHECK (current_amount >= 0),
            deadline TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Step 5: Add user_id column to operations IF NOT EXISTS (NULLABLE for now)
    print("[5/7] Adding user_id column to operations (if missing)...")
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'operations' AND column_name = 'user_id'
            ) THEN
                ALTER TABLE operations ADD COLUMN user_id BIGINT;
                RAISE NOTICE 'user_id column added to operations';
            ELSE
                RAISE NOTICE 'user_id column already exists in operations — skipping';
            END IF;
        END $$;
    """)

    # Step 6: Create or fetch a legacy system user for pre-v2 operations
    print("[6/7] Ensuring legacy system user exists for pre-v2 data...")
    LEGACY_EMAIL = "legacy@finance-tracker.internal"
    LEGACY_HASH = "LEGACY_NO_LOGIN"  # Not a valid hash — cannot log in with this account

    cur.execute("SELECT id FROM users WHERE email = %s", (LEGACY_EMAIL,))
    row = cur.fetchone()
    if row:
        legacy_user_id = row[0]
        print(f"    Legacy user already exists: id={legacy_user_id}")
    else:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (LEGACY_EMAIL, LEGACY_HASH)
        )
        legacy_user_id = cur.fetchone()[0]
        # Create settings for legacy user
        cur.execute("INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (legacy_user_id,))
        print(f"    Legacy user created: id={legacy_user_id}")

    # Step 7: Assign legacy operations (user_id IS NULL) to the legacy user
    print("[7/7] Assigning legacy operations (user_id IS NULL) to legacy user...")
    cur.execute("UPDATE operations SET user_id = %s WHERE user_id IS NULL", (legacy_user_id,))
    updated = cur.rowcount
    print(f"    {updated} legacy operation(s) assigned to user_id={legacy_user_id}")

    # Commit all
    conn.commit()
    print()
    print("=== Migration completed successfully ===")
    print()
    print("IMPORTANT NOTES:")
    print(f"  - Legacy operations have been assigned to user '{LEGACY_EMAIL}' (id={legacy_user_id})")
    print("  - This legacy account CANNOT log in (no valid password hash)")
    print("  - No data was deleted or modified destructively")
    print("  - New users can register normally and see only their own data")

except Exception as e:
    conn.rollback()
    print(f"ERROR: Migration failed — {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    cur.close()
    conn.close()
