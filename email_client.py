#!/usr/bin/env python3
import json
import os
import sqlite3


# https://jmap.io/spec-core.html
# https://jmap.io/client.html


class EmailServer:
    HEADERS = {'Authorization': f'Bearer {os.environ.get("TOKEN", "")}'}

    def __init__(self):
        session_response = requests.get(os.environ['SESSION_URL'], headers=self.HEADERS)
        self.account_id = session_response.json()['primaryAccounts']['urn:ietf:params:jmap:mail']
        self.api_url = session_response.json()['apiUrl']

    def get_folders(self):
        request = {
            'using': ['urn:ietf:params:jmap:mail'],
            'methodCalls': [
                [ "Mailbox/get", {"accountId": self.account_id, "ids": None}, "0" ]
            ],
        }
        r = requests.post(self.api_url, data=json.dumps(request), headers={**self.HEADERS, 'Content-type': 'application/json'})
        method_responses = r.json()['methodResponses']
        return [{'id': m['id'], 'name': m['name']} for m in method_responses[0][1]['list']]

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
        r = requests.post(self.api_url, data=json.dumps(request), headers={**self.HEADERS, 'Content-type': 'application/json'})
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
        r = requests.post(self.api_url, data=json.dumps(request), headers={**self.HEADERS, 'Content-type': 'application/json'})
        method_responses = r.json()['methodResponses']
        return list(method_responses[0][1]['list'][0]['bodyValues'].values())[0]['value']


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
    import requests
    import markdownify

    server = EmailServer()
    folders = server.get_folders()
    print(folders)
    emails = server.get_emails(folders[0]['id'])
    for index, email in enumerate(emails):
        print(f'{index} -- {email}')
    while True:
        response = input('select email (q to quit): ')
        if response.lower() in ['q', '']:
            break
        html_data = server.get_email_html_data(emails[int(response)]['id'])
        print(markdownify.markdownify(html_data))
