"""Regression tests for optional base-only build ID lookup on unknown games."""
from pathlib import Path
from unittest import TestCase, mock
import sys
import tempfile
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_ipa_from_nsp as nx


class TestUpdatedBuildLookup(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.family = {
            'base': self.folder / 'base.nsp',
            'updates': [(65536, self.folder / 'update.nsp')],
            'dlc': [],
        }
        self.log_names = []

    def fake_run(self, cmd, log, **kwargs):
        self.log_names.append(log.name)
        if cmd[3] == '-':
            raise nx.BuildError('base-only ExeFS integrity verification failed')
        target = Path(cmd[4]) / 'ExeFS'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'main').write_bytes(bytes(0x40) + bytes.fromhex('12' * 16))

    def test_updated_merged_pass_not_blocked_by_bad_base_only(self):
        # A matching regional profile may exist, but it is optional. If the
        # updated ExeFS was readable, the later survey should still run even
        # when the unpatched base's ExeFS fails an integrity check.
        with mock.patch.dict(sys.modules, {'game': types.SimpleNamespace(by_build=lambda _: None)}), \
             mock.patch.object(nx, 'build_nx_extract', return_value=self.folder / 'extractor'), \
             mock.patch.object(nx, 'run', side_effect=self.fake_run):
            found = nx.same_build(self.folder, 0x0100A7C01B792000, self.family,
                                  self.folder / 'keys', self.folder, {})
        self.assertIsNone(found)
        self.assertEqual(self.log_names, ['exefs_update_v65536.log', 'exefs_base.log'])

    def test_updated_merged_failure_is_still_fatal(self):
        def fail_update(cmd, log, **kwargs):
            raise nx.BuildError('updated ExeFS integrity verification failed')
        with mock.patch.dict(sys.modules, {'game': types.SimpleNamespace(by_build=lambda _: None)}), \
             mock.patch.object(nx, 'build_nx_extract', return_value=self.folder / 'extractor'), \
             mock.patch.object(nx, 'run', side_effect=fail_update):
            with self.assertRaisesRegex(nx.BuildError, 'updated ExeFS'):
                nx.same_build(self.folder, 0x0100A7C01B792000, self.family,
                              self.folder / 'keys', self.folder, {})
