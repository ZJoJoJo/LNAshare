"""SMB lifecycle tests use mocks and do not modify system shares."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lanshare import smb
from lanshare.controller import Controller


class SmbTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.test-artifacts').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.test-artifacts')
        self.directory = Path(self.temp.name)
        self.journal = self.directory / '.smb-session.json'
        self.cfg = {'folder': str(self.directory), 'share': 'TestShare', 'username': "user'name", 'write': False}

    def tearDown(self):
        self.temp.cleanup()

    @patch('lanshare.smb.is_admin', return_value=False)
    def test_admin_required_no_journal(self, admin):
        with self.assertRaises(ValueError):
            smb.create(self.cfg, self.journal)
        self.assertFalse(self.journal.exists())

    @patch('lanshare.smb.is_admin', return_value=True)
    @patch('lanshare.smb.powershell', return_value='')
    def test_create_and_remove_ownership(self, ps, admin):
        record = smb.create(self.cfg, self.journal)
        self.assertEqual(json.loads(self.journal.read_text(encoding='utf-8')), record)
        script = ps.call_args.args[0]
        self.assertIn('EnableSMB2Protocol', script)
        self.assertIn('-ReadAccess', script)
        self.assertIn("user''name", script)
        self.assertIn('Share name already exists', script)
        self.assertNotIn('Set-Acl', script)
        smb.remove(record, self.journal)
        script = ps.call_args.args[0]
        self.assertIn(record['marker'], script)
        self.assertIn('ownership changed', script)
        self.assertNotIn('Remove-Item', script)
        self.assertFalse(self.journal.exists())

    def test_recovery_blocks_other_protocol(self):
        record = {'name': 'TestShare', 'folder': str(self.directory), 'marker': 'LANSharePortable:test'}
        self.journal.write_text(json.dumps(record))
        events = []
        controller = Controller(self.directory, lambda *e: events.append(e))
        self.assertEqual(controller.active, 'smb')
        self.assertEqual(events[0][0], 'recovery')
        with self.assertRaisesRegex(ValueError, '已有服务正在运行'):
            controller.start('ftp', {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
