import unittest

import email_client


class StorageTests(unittest.TestCase):
    def test(self):
        storage = email_client.Storage(':memory:')


if __name__ == '__main__':
    unittest.main()
