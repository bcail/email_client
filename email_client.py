import sqlite3


class Storage:
    DB_INIT_STATEMENTS = [
        'CREATE TABLE emails ('
            'id INTEGER PRIMARY KEY,'
            'from_header TEXT NOT NULL,'
            'body TEXT NOT NULL,'
            'CHECK (from_header != "")'
            ') STRICT',
        'CREATE TABLE attachments ('
            'id INTEGER PRIMARY KEY,'
            'email_id INTEGER NOT NULL,'
            'data BLOB NOT NULL,'
            'FOREIGN KEY(email_id) REFERENCES emails(id)'
            ') STRICT',
    ]

    @staticmethod
    def _get_db_connection(db_name):
        conn = sqlite3.connect(db_name, isolation_level=None)
        conn.execute('PRAGMA foreign_keys = ON;')
        return conn

    @staticmethod
    def _begin_txn(cursor):
        cursor.execute('BEGIN IMMEDIATE')

    @staticmethod
    def _rollback(cursor):
        try:
            cursor.execute('ROLLBACK')
        except sqlite3.OperationalError as e:
            if str(e) == 'cannot rollback - no transaction is active':
                pass
            else:
                raise

    def _create_tables(self):
        cursor = self._conn.cursor()
        Storage._begin_txn(cursor)
        try:
            for statement in Storage.DB_INIT_STATEMENTS:
                cursor.execute(statement)
            cursor.execute('COMMIT')
        except:
            Storage._rollback(cursor)
            raise

    def __init__(self, db_name):
        # creates & initializes database if needed
        self._conn = self._get_db_connection(db_name)
        tables = self._conn.execute('SELECT name from sqlite_master WHERE type="table"').fetchall()
        if not tables:
            self._create_tables()
