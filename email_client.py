#!/usr/bin/env python3
from contextlib import contextmanager
import json
import os
import sqlite3


# https://jmap.io/spec-core.html
# https://jmap.io/client.html


class EmailServer:

    def __init__(self):
        self._token = os.environ['TOKEN']
        self._headers = {'Authorization': f'Bearer {self._token}', 'Content-type': 'application/json'}

        session_response = requests.get(os.environ['SESSION_URL'], headers=self._headers)
        self.account_id = session_response.json()['primaryAccounts']['urn:ietf:params:jmap:mail']
        self.api_url = session_response.json()['apiUrl']

    def _post_request(self, request):
        return requests.post(self.api_url, data=json.dumps(request), headers=self._headers)

    def get_folders(self):
        request = {
            'using': ['urn:ietf:params:jmap:mail'],
            'methodCalls': [
                [ "Mailbox/get", {"accountId": self.account_id, "ids": None}, "0" ]
            ],
        }
        r = self._post_request(request)
        method_responses = r.json()['methodResponses']
        return [{'id': m['id'], 'name': m['name'], 'role': m['role'], 'parent_id': m['parentId']} for m in method_responses[0][1]['list']]

    def get_emails(self, folder_id, limit=10):
        method_calls = [
            ["Email/query", {
                "accountId": self.account_id,
                "filter": {"inMailbox": folder_id},
                "sort": [{"property": "receivedAt", "isAscending": False }],
                "collapseThreads": False, "position": 0, "limit": 20
            }, "0" ],
            # Then we fetch the threadId of each of those messages
            [ "Email/get", {
                "accountId": self.account_id,
                "#ids": {
                    "name": "Email/query",
                    "path": "/ids",
                    "resultOf": "0"
                },
                "properties": [ "threadId" ]
            }, "1" ],
            # Next we get the emailIds of the messages in those threads
            [ "Thread/get", {
                "accountId": self.account_id,
                "#ids": {
                    "name": "Email/get",
                    "path": "/list/*/threadId",
                    "resultOf": "1"
                }
            }, "2" ],
            # Finally we get the data for all those emails
            [ "Email/get", {
                "accountId": self.account_id,
                "#ids": {
                    "name": "Thread/get",
                    "path": "/list/*/emailIds",
                    "resultOf": "2"
                },
                "properties": [ "subject", "from" ]
            }, "3" ]
        ]
        request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': method_calls}
        r = self._post_request(request)
        method_responses = r.json()['methodResponses']
        return [{'id': email['id'], 'subject': email['subject']} for email in method_responses[3][1]['list']]

    def get_email_html_data(self, email_id):
        method_calls = [
            [ "Email/get", {
                "accountId": self.account_id,
                "ids": [ email_id ],
                "properties": [
                    "blobId",
                    "messageId",
                    "inReplyTo",
                    "references",
                    "sender",
                    "cc",
                    "bcc",
                    "replyTo",
                    "sentAt",
                    "htmlBody",
                    "bodyValues"
                ],
                "fetchHTMLBodyValues": True
            }, "0"]
        ]
        request = {'using': ['urn:ietf:params:jmap:mail'], 'methodCalls': method_calls}
        r = self._post_request(request)
        method_responses = r.json()['methodResponses']
        return list(method_responses[0][1]['list'][0]['bodyValues'].values())[0]['value']


@contextmanager
def sqlite_txn(cursor):
    cursor.execute('BEGIN IMMEDIATE')
    try:
        yield
        cursor.execute('COMMIT')
    except BaseException as e:
        cursor.execute('ROLLBACK')
        raise


class Storage:
    DB_INIT_STATEMENTS = [
        'CREATE TABLE folders ('
            'id INTEGER PRIMARY KEY,'
            'server_id TEXT NOT NULL UNIQUE,'
            'name TEXT NOT NULL,'
            'parent_server_id TEXT NULL,'
            'role TEXT NULL,'
            'CHECK (server_id != ""),'
            'CHECK (name != ""),'
            'CHECK (parent_server_id != ""),'
            'FOREIGN KEY(parent_server_id) REFERENCES folders(server_id)'
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

    def _create_tables(self):
        cursor = self._conn.cursor()
        with sqlite_txn(cursor):
            for statement in Storage.DB_INIT_STATEMENTS:
                cursor.execute(statement)

    def __init__(self, db_name):
        # creates & initializes database if needed
        self._conn = self._get_db_connection(db_name)
        tables = self._conn.execute('SELECT name from sqlite_master WHERE type="table"').fetchall()
        if not tables:
            self._create_tables()

    @property
    def has_folders(self):
        result = self._conn.execute('SELECT COUNT(*) FROM folders').fetchone()
        if int(result[0]) > 0:
            return True

    def save_folder(self, folder_id, name, role, parent_id):
        cursor = self._conn.cursor()
        with sqlite_txn(cursor):
            cursor.execute('INSERT INTO folders(server_id, name, role, parent_server_id) VALUES(?, ?, ?, ?)', (folder_id, name, role, parent_id))

    def get_folders(self):
        results = self._conn.execute('SELECT server_id,name FROM folders ORDER BY name').fetchall()
        return [{'id': r[0], 'name': r[1]} for r in results]


if __name__ == '__main__':
    import requests
    import markdownify

    email_file = os.environ['EMAIL_FILE']

    storage = Storage(email_file)
    server = EmailServer()

    if not storage.has_folders:
        print(f'Fetching folders...')
        folders = server.get_folders()
        for f in folders:
            storage.save_folder(folder_id=f['id'], name=f['name'], role=f['role'], parent_id=f['parent_id'])

    folders = storage.get_folders()
    for f in folders:
        print(f'{f["id"]} -- {f["name"]}')

    # emails = server.get_emails(folders[0]['id'])
    # for index, email in enumerate(emails):
    #     print(f'{index} -- {email}')
    # while True:
    #     response = input('select email (q to quit): ')
    #     if response.lower() in ['q', '']:
    #         break
    #     html_data = server.get_email_html_data(emails[int(response)]['id'])
    #     print(markdownify.markdownify(html_data))
