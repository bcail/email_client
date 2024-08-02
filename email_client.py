#!/usr/bin/env python3
import json
import os
import sqlite3

import requests

# https://jmap.io/spec-core.html
# https://jmap.io/client.html

HEADERS = {'Authorization': f'Bearer {os.environ["TOKEN"]}'}


def connect_info():
    session_response = requests.get(os.environ['SESSION_URL'], headers=HEADERS)
    account_id = session_response.json()['primaryAccounts']['urn:ietf:params:jmap:mail']
    api_url = session_response.json()['apiUrl']
    return api_url, account_id


def list_folders(api_url, account_id):
    request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': [[ "Mailbox/get", {"accountId": account_id, "ids": None}, "0" ]]}
    r = requests.post(api_url, data=json.dumps(request), headers={**HEADERS, 'Content-type': 'application/json'})
    return [{'id': m['id'], 'name': m['name']} for m in r.json()['methodResponses'][0][1]['list']]


def list_emails(api_url, account_id, folder_id, limit=10):
    method_calls = [
        ["Email/query", {
            "accountId": account_id,
            "filter": {"inMailbox": folder_id},
            "sort": [{"property": "receivedAt", "isAscending": False }],
            "collapseThreads": False, "position": 0, "limit": 2
        }, "0" ],
        # Then we fetch the threadId of each of those messages
        [ "Email/get", {
            "accountId": account_id,
            "#ids": {
                "name": "Email/query",
                "path": "/ids",
                "resultOf": "0"
            },
            "properties": [ "threadId" ]
        }, "1" ],
        # Next we get the emailIds of the messages in those threads
        [ "Thread/get", {
            "accountId": account_id,
            "#ids": {
                "name": "Email/get",
                "path": "/list/*/threadId",
                "resultOf": "1"
            }
        }, "2" ],
        # Finally we get the data for all those emails
        [ "Email/get", {
            "accountId": account_id,
            "#ids": {
                "name": "Thread/get",
                "path": "/list/*/emailIds",
                "resultOf": "2"
            },
            "properties": [ "subject", "from" ]
        }, "3" ]
    ]
    request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': method_calls}
    r = requests.post(api_url, data=json.dumps(request), headers={**HEADERS, 'Content-type': 'application/json'})
    for email in r.json()['methodResponses'][3][1]['list']:
        print(f'{email["id"]} -- {email["subject"]} -- {email["from"]}')


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
    api_url, account_id = connect_info()
    folders = list_folders(api_url, account_id)
    print(folders)
    list_emails(api_url, account_id, folders[0]['id'])
