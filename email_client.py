#!/usr/bin/env python3
from contextlib import contextmanager
import email
import email.policy as email_policy
import json
import os
import sqlite3
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


# https://jmap.io/spec-core.html
# https://jmap.io/client.html


class EmailServer:

    def __init__(self):
        self._token = os.environ['TOKEN']
        self._authorization = f'Bearer {self._token}'
        self._headers = {'Authorization': self._authorization, 'Content-type': 'application/json'}
        self._account_id = None
        self._api_url = None
        self._download_url = None

    def _init_session(self):
        session_response = requests.get(os.environ['SESSION_URL'], headers=self._headers)
        session_info = session_response.json()
        self._account_id = session_info['primaryAccounts']['urn:ietf:params:jmap:mail']
        self._api_url = session_info['apiUrl']
        self._download_url = session_info['downloadUrl']

    def _post_request(self, method_calls):
        request = {
            'using': [
                'urn:ietf:params:jmap:core',
                'urn:ietf:params:jmap:mail',
            ],
            'methodCalls': method_calls,
        }
        r = requests.post(self.api_url, data=json.dumps(request), headers=self._headers)
        if r.ok:
            return r
        else:
            raise Exception(f'server error: {r.status_code} -- {r.content.decode("utf8")}')

    @property
    def account_id(self):
        if not self._account_id:
            self._init_session()
        return self._account_id

    @property
    def api_url(self):
        if not self._api_url:
            self._init_session()
        return self._api_url

    @property
    def download_url(self):
        # eg. https://localhost/jmap/download/{accountId}/{blobId}/{name}?type={type}
        if not self._download_url:
            self._init_session()
        return self._download_url

    def get_folders(self):
        method_calls = [
            [ 'Mailbox/get', {'accountId': self.account_id, 'ids': None}, '0' ],
        ]
        r = self._post_request(method_calls)
        folders_info = r.json()
        method_responses = folders_info['methodResponses']
        mailbox_get_info = method_responses[0][1]
        state = mailbox_get_info['state']
        return state, [{'server_id': m['id'], 'name': m['name'], 'role': m['role'], 'parent_id': m['parentId'], 'sort_order': m['sortOrder']}
                       for m in mailbox_get_info['list']]

    def get_folder_changes(self, state):
        method_calls = [
            ['Mailbox/changes', {'accountId': self.account_id, 'sinceState': state}, '0'],
            ['Mailbox/get',
             {'accountId': self.account_id, '#ids': { 'resultOf': '0', 'name': 'Mailbox/changes', 'path': '/created' }},
             '1'],
            ['Mailbox/get',
             {'accountId': self.account_id, '#ids': { 'resultOf': '0', 'name': 'Mailbox/changes', 'path': '/updated' }},
             '2'],
        ]
        r = self._post_request(method_calls)
        method_responses = r.json()['methodResponses']
        mailbox_changes_info = method_responses[0][1]
        new_state = mailbox_changes_info['newState']
        changes = {
            'created': [],
            'deleted': [{'id': id} for id in mailbox_changes_info['destroyed']],
            'updated': [],
        }
        if mailbox_changes_info['created']:
            changes['created'] = [{'id': m['id'], 'name': m['name'], 'role': m['role'], 'parent_id': m['parentId'], 'sort_order': m['sortOrder']}
                                  for m in method_responses[1][1]['list']]
        # don't worry about counts at this point - only update folders that have had properties updated
        if mailbox_changes_info['updated'] and not mailbox_changes_info['updatedProperties']:
            changes['updated'] = [{'id': m['id'], 'name': m['name'], 'role': m['role'], 'parent_id': m['parentId'], 'sort_order': m['sortOrder']}
                                  for m in method_responses[2][1]['list']]
        return new_state, changes

    def get_emails(self, folder_id, limit=10):
        method_calls = [
            ["Email/query", {
                "accountId": self.account_id,
                "filter": {"inMailbox": folder_id},
                "sort": [{"property": "receivedAt", "isAscending": False }],
                "collapseThreads": False, "position": 0, "limit": 20
            }, "0" ],
            [ "Email/get", {
                "accountId": self.account_id,
                "#ids": {
                    "name": "Email/query",
                    "path": "/ids",
                    "resultOf": "0"
                },
                "properties": ['subject', 'from', 'sentAt', 'blobId']
            }, "1" ],
        ]
        r = self._post_request(method_calls)
        method_responses = r.json()['methodResponses']
        return [
            {'server_id': e['id'], 'subject': e['subject'], 'from': e['from'], 'sent_at': e['sentAt'], 'blob_id': e['blobId']}
            for e in method_responses[1][1]['list']
        ]

    def get_email_obj(self, blob_id):
        url = self.download_url.replace('{accountId}', self.account_id).replace('{blobId}', blob_id).replace('{name}', 'email').replace('{type}', 'application/octet-stream')
        r = requests.get(url, headers={'Authorization': self._authorization})
        if r.ok:
            return email.message_from_bytes(r.content, _class=email.message.EmailMessage, policy=email_policy.default)
        raise Exception(f'server error downloading email: {r.status_code} -- {r.content.decode("utf8")}')


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
        'CREATE TABLE accounts ('
            'id INTEGER PRIMARY KEY,'
            'name TEXT NOT NULL UNIQUE,'
            'type TEXT NOT NULL,'
            'CHECK (name != ""),'
            'CHECK (type = "JMAP" OR type = "IMAP" or type = "local")'
            ') STRICT',
        'CREATE TABLE folders ('
            'id INTEGER PRIMARY KEY,'
            'account_id INTEGER NOT NULL,'
            'server_id TEXT NOT NULL UNIQUE,'
            'name TEXT NOT NULL,'
            'parent_server_id TEXT NULL,'
            'role TEXT NULL,'
            'sort_order INTEGER NOT NULL DEFAULT 0,'
            'CHECK (server_id != ""),'
            'CHECK (name != ""),'
            'CHECK (parent_server_id != ""),'
            'FOREIGN KEY(account_id) REFERENCES accounts(id),'
            'FOREIGN KEY(parent_server_id) REFERENCES folders(server_id)'
            ') STRICT',
        'CREATE TABLE emails ('
            'id INTEGER PRIMARY KEY,'
            'folder_id INTEGER NOT NULL,'
            'from_header TEXT NOT NULL,'
            'CHECK (from_header != ""),'
            'FOREIGN KEY(folder_id) REFERENCES folders(id)'
            ') STRICT',
        'CREATE TABLE email_data ('
            'id INTEGER PRIMARY KEY,'
            'email_id INTEGER NOT NULL,'
            'data BLOB NOT NULL,'
            'FOREIGN KEY(email_id) REFERENCES emails(id)'
            ') STRICT',
        'CREATE TABLE misc ('
            'key TEXT UNIQUE NOT NULL,'
            'value TEXT NOT NULL,'
            'CHECK (key != "")'
            'CHECK (value != "")'
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
    def folders_state(self):
        result = self._conn.execute("SELECT value FROM misc WHERE key = 'folders-state'").fetchone()
        if result:
            return result[0]

    def delete_folders(self):
        cursor = self._conn.cursor()
        with sqlite_txn(cursor):
            cursor.execute("DELETE FROM misc WHERE key = 'folders-state'")
            cursor.execute('DELETE FROM folders')

    def save_folders(self, folders, state, account_name):
        cursor = self._conn.cursor()
        with sqlite_txn(cursor):
            cursor.execute('INSERT INTO accounts(name, type) VALUES(?, ?)', (account_name, 'JMAP'))
            account_id = cursor.lastrowid
            for f in folders:
                cursor.execute('INSERT INTO folders(account_id, server_id, name, role, parent_server_id, sort_order) VALUES(?, ?, ?, ?, ?, ?)',
                               (account_id, f['server_id'], f['name'], f['role'], f['parent_id'], f['sort_order']))
            cursor.execute('INSERT INTO misc(key, value) VALUES(?, ?)', ('folders-state', state))

    def update_folders(self, folder_changes, state):
        cursor = self._conn.cursor()
        with sqlite_txn(cursor):
            for f in folder_changes['created']:
                print(f'creating folder {f}')
                cursor.execute('INSERT INTO folders(server_id, name, role, parent_server_id, sort_order) VALUES(?, ?, ?, ?, ?)',
                               (f['id'], f['name'], f['role'], f['parent_id'], f['sort_order']))
            for f in folder_changes['updated']:
                print(f'updating folder {f}')
                cursor.execute('UPDATE folders SET name=?, role=?, parent_server_id=?, sort_order=? WHERE server_id=?',
                               (f['name'], f['role'], f['parent_id'], f['sort_order'], f['id']))
            for f in folder_changes['deleted']:
                print(f'deleting folder {f}')
                cursor.execute('DELETE FROM folders WHERE server_id=?', (f['id'],))
            cursor.execute('UPDATE misc SET value = ? WHERE key = ?', (state, 'folders-state'))

    def get_folders(self, parent_id=None):
        fields = 'server_id, name'
        folders = []
        if parent_id:
            results = self._conn.execute(f'SELECT {fields} FROM folders WHERE parent_server_id = ? ORDER BY sort_order,name', (parent_id,)).fetchall()
        else:
            results = self._conn.execute(f'SELECT {fields} FROM folders WHERE parent_server_id IS NULL ORDER BY sort_order,name').fetchall()
        folders.extend([{'server_id': r[0], 'name': r[1]} for r in results])
        return folders

    def get_folder(self, folder_id):
        fields = 'server_id, name'
        folder = self._conn.execute(f'SELECT {fields} FROM folders WHERE server_id = ?', (folder_id,)).fetchone()
        return {'id': folder[0], 'name': folder[1]}


class GUI:

    def __init__(self, storage, server):
        self.storage = storage
        self.server = server

        self.root = tk.Tk()
        self.root.title('Email Client')

        #make sure root container is set to resize properly
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        #this frame will contain everything the user sees
        self.content_frame = ttk.Frame(master=self.root, padding=(1, 1, 1, 1))
        self.content_frame.columnconfigure(0, weight=1)
        self.content_frame.columnconfigure(1, weight=1)
        self.content_frame.columnconfigure(2, weight=2)
        self.content_frame.rowconfigure(0, weight=1)
        self.content_frame.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.S, tk.E))

        self.emails_frame = None
        self.email_frame = None

        self.show_folders()

    def show_folders(self):
        columns = ('name')
        self.folders_tree = ttk.Treeview(master=self.content_frame, columns=columns, show='headings')
        self.folders_tree.heading('name', text='Folders')

        folders = self.storage.get_folders()
        for f in folders:
            values = (f['name'],)
            self.folders_tree.insert(parent='', index=tk.END, iid=f['server_id'], values=values)

        self.folders_tree.bind('<Button-1>', self._folder_selected)
        self.folders_tree.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.S, tk.E))

        self.show_emails()

    def _folder_selected(self, event):
        folder_id = self.folders_tree.identify_row(event.y)
        self.show_emails(folder_id=folder_id)

    def show_emails(self, folder_id=None):
        if self.emails_frame:
            self.emails_frame.destroy()

        self.emails_frame = ttk.Frame(master=self.content_frame)
        self.emails_frame.columnconfigure(0, weight=1)
        self.emails_frame.rowconfigure(1, weight=1)

        if folder_id:
            folder_info = self.storage.get_folder(folder_id)
            ttk.Label(master=self.emails_frame, text=folder_info['name']).grid(row=0, column=0, sticky=(tk.N, tk.W, tk.S, tk.E))

            self.emails = self.server.get_emails(folder_id=folder_info['id'])

            columns = ('subject', 'from', 'sent_at')
            self.emails_tree = ttk.Treeview(master=self.emails_frame, columns=columns, show='headings')
            self.emails_tree.heading('subject', text='Subject')
            self.emails_tree.heading('from', text='From')
            self.emails_tree.heading('sent_at', text='Date')

            for index, email in enumerate(self.emails):
                values = (email['subject'], email['from'][0]['name'], email['sent_at'])
                self.emails_tree.insert(parent='', index=tk.END, iid=index, values=values)

            self.emails_tree.bind('<Button-1>', self._email_selected)

            self.emails_tree.grid(row=1, column=0, sticky=(tk.N, tk.W, tk.S, tk.E))
        else:
            ttk.Label(master=self.emails_frame, text='Emails').grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E))

        self.emails_frame.grid(row=0, column=1, sticky=(tk.N, tk.W, tk.S, tk.E))

    def _email_selected(self, event):
        email_index = self.emails_tree.identify_row(event.y)
        email_info = self.emails[int(email_index)]
        blob_id = email_info['blob_id']

        self.show_email(blob_id=blob_id)

    def show_email(self, blob_id):
        if self.email_frame:
            self.email_frame.destroy()

        self.email_frame = ttk.Frame(master=self.content_frame)
        self.email_frame.columnconfigure(0, weight=1)
        self.email_frame.rowconfigure(0, weight=1)

        email_obj = self.server.get_email_obj(blob_id)
        text_widget = ScrolledText(master=self.email_frame)
        text_widget.insert(tk.END, email_obj.get_body())
        text_widget.grid(row=1, column=0, sticky=(tk.N, tk.W, tk.S, tk.E))

        self.email_frame.grid(row=0, column=2, sticky=(tk.N, tk.W, tk.S, tk.E))


if __name__ == '__main__':
    import requests

    email_file = os.environ['EMAIL_FILE']

    storage = Storage(email_file)
    server = EmailServer()

    if not storage.folders_state:
        print(f'Fetching folders for the first time...')
        state, folders = server.get_folders()
        storage.save_folders(folders, state, server.account_id)
    else:
        print(f'Checking for folder updates...')
        state, folder_changes = server.get_folder_changes(storage.folders_state)
        storage.update_folders(folder_changes, state)

    app = GUI(storage, server)
    app.root.mainloop()
