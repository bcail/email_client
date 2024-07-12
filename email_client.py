#!/usr/bin/env python3
import json
import os
import sqlite3

import requests

# https://jmap.io/spec-core.html
# https://jmap.io/client.html

HEADERS = {'Authorization': f'Bearer {os.environ["TOKEN"]}'}


def list_folders():
    session_response = requests.get(os.environ['SESSION_URL'], headers=HEADERS)
    account_id = session_response.json()['primaryAccounts']['urn:ietf:params:jmap:mail']
    request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': [[ "Mailbox/get", {"accountId": account_id, "ids": None}, "0" ]]}
    r = requests.post(session_response.json()['apiUrl'], data=json.dumps(request), headers={**HEADERS, 'Content-type': 'application/json'})
    return [{'id': m['id'], 'name': m['name']} for m in r.json()['methodResponses'][0][1]['list']]


def list_emails(folder_id, limit=10):
    session_response = requests.get(os.environ['SESSION_URL'], headers=HEADERS)
    method_calls = [[ "Email/query", { "filter": { "inMailboxes": [ folder_id ] }, "sort": [ "date desc", "id desc" ], "collapseThreads": False, "position": 0, "limit": 100 }, "0" ]]
    request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': method_calls}
    r = requests.post(session_response.json()['apiUrl'], data=json.dumps(request), headers={**HEADERS, 'Content-type': 'application/json'})
    return r.json()['methodResponses']


class Storage:
    DB_INIT_STATEMENTS = [
        'CREATE TABLE folder ('
            'id INTEGER PRIMARY KEY,'
            'server_id TEXT NOT NULL,'
            'name TEXT NOT NULL,'
            'CHECK (server_id != "")'
            'CHECK (name != "")'
            ') STRICT',
        'CREATE TABLE emails ('
            'id INTEGER PRIMARY KEY,'
            'folder_id INTEGER NOT NULL,'
            'from_header TEXT NOT NULL,'
            'body TEXT NOT NULL,'
            'CHECK (from_header != "")'
            'FOREIGN KEY(folder_id) REFERENCES folders(id)'
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


if __name__ == '__main__':
    folders = list_folders()
    print(folders)
    print(list_emails(folders[0]['id']))
