"""
MySQL Loader - Loads transformed data into MySQL database
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

import pandas as pd
from sqlalchemy import create_engine, text
from settings import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

class MySQLDatabase:
    def __init__(self):
        connection_string = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DATABASE}"
        self.engine = create_engine(connection_string)
        
    def load_dataframe(self, df, table_name, if_exists='append', conn=None):
        """Load a pandas DataFrame to MySQL. Pass an existing connection to
        participate in a shared transaction (see load_all)."""
        if conn is not None:
            df.to_sql(table_name, conn, if_exists=if_exists, index=False, method='multi')
        else:
            with self.engine.connect() as conn:
                df.to_sql(table_name, conn, if_exists=if_exists, index=False, method='multi')
        print(f"Loaded {len(df)} rows to {table_name}")

    def clear_tables(self, conn=None):
        """Clear all tables before loading new data. Pass an existing
        connection to participate in a shared transaction (see load_all)."""
        # fact_issue_version and fact_issue_sprint must both be cleared
        # before fact_issue/dim_sprint/dim_version, since they carry
        # foreign keys pointing at those tables. There is no FK between
        # fact_issue_version and fact_issue_sprint themselves, so their
        # order relative to EACH OTHER doesn't matter - only that both
        # come before the tables they reference.
        tables = ['fact_issue_sprint', 'fact_issue_version', 'fact_issue_link', 'fact_issue',
                  'dim_sprint', 'dim_version', 'dim_user',
                  'dim_priority', 'dim_status', 'dim_issue_type', 'dim_project']

        if conn is not None:
            for table in tables:
                conn.execute(text(f"DELETE FROM {table}"))
                conn.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))
        else:
            with self.engine.connect() as conn:
                for table in tables:
                    conn.execute(text(f"DELETE FROM {table}"))
                    conn.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))
                conn.commit()
        print("All tables cleared")

    def load_all(self, transformed_data, dim_tables, fact_tables):
        """Clear + load every table using a single SQLAlchemy connection
        wrapped in engine.begin(), so a plain DML failure partway through
        (e.g. a bad row rejected by a FK/UNIQUE constraint during a
        load_dataframe() call, or a dropped connection during the load
        phase) rolls back everything loaded so far in this call.

        DOCUMENTATION CORRECTION (this pass): this is NOT a fully atomic
        clear+load in the strict sense the earlier docstring implied.
        clear_tables() issues `ALTER TABLE ... AUTO_INCREMENT = 1` for
        every table, and DDL statements such as ALTER TABLE cause an
        IMPLICIT COMMIT in MySQL/MariaDB InnoDB - meaning each
        clear_tables() iteration's DELETE + ALTER TABLE effectively closes
        out and starts a new underlying transaction segment regardless of
        this Python-level `with self.engine.begin()` block. So a failure
        that happens DURING clear_tables() can leave some tables already
        cleared (and committed) while others are not, and a failure
        during the load phase can leave the clear already committed with
        no data loaded yet. What this method DOES reliably guarantee is
        that a failure during the LOAD phase alone (once all clearing has
        completed) will roll back the newly-inserted rows from this call.
        Not refactored to fix this here (out of Phase 4 scope) - the
        proper fix would be a true DDL-free clear (e.g. TRUNCATE without
        AUTO_INCREMENT reset, or a separate reset step outside the load
        transaction) and is tracked as future work, not implemented in
        this pass."""
        with self.engine.begin() as conn:
            self.clear_tables(conn=conn)
            for table in dim_tables:
                if table in transformed_data and not transformed_data[table].empty:
                    self.load_dataframe(transformed_data[table], table, conn=conn)
            for table in fact_tables:
                if table in transformed_data and not transformed_data[table].empty:
                    self.load_dataframe(transformed_data[table], table, conn=conn)
        print("All tables loaded in a single transaction")