"""Completed-stage recovery, upload protocol and progress with synthetic inputs."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_ipa_from_nsp as builder  # noqa: E402
import mcd2_activity as activity  # noqa: E402
import mcd2_stages as stages  # noqa: E402
from mcd2_drive_transport import resumable_http, safe_failure  # noqa: E402

IDENTITY = {'inputs': ['verified-base', 'verified-update'], 'toolchain': 'test', 'chain': 'test'}


def seed(work):
    files = {
        'package-context.json': json.dumps({'schema': 1, 'game': stages.GAME,
            'values': {'GAME': stages.GAME, 'DM_PSO_GPUS': 'none'},
            'paths': {'W': 'ipa', 'PATCHED': 'patched_v65536', 'MSL_IOS': 'shader/ios',
                      'ROMFS': 'extract/v65536/RomFS.img', 'DM_GAMES_DIR': 'prepared_profile'}}),
        'patched_v65536.key': 'a' * 64,
        f'prepared_profile/{stages.GAME}/game.tsv': 'synthetic profile',
        f'prepared_profile/{stages.GAME}/c1_full.tsv': 'synthetic derived reflection',
        'shader/ios/b-test.key': 'b' * 64,
        'shader/ios/b-test/inputs.tsv': f'game\t{stages.GAME}\ninputs\tsynthetic\n',
        'shader/ios/b-test/example_b.metallib': 'synthetic compiled library',
        'shader/cache/b-test/_b_build.json': '[{"key":"example","st":"ok"}]',
    }
    for module in ('main', 'sdk', 'subsdk0'):
        files[f'patched_v65536/{module}.patched.bin'] = 'synthetic rewritten module'
        files[f'patched_v65536/{module}.fixups.tsv'] = 'synthetic fixups'
    for name, data in files.items():
        path = work / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)
    return files


class TestPreparedRecovery(unittest.TestCase):
    def test_fresh_runner_retains_full_code_and_shader_outputs_not_keys_or_romfs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work, archive, restored = root / 'work', root / 'ready.tar', root / 'new'
            files = seed(work)
            (work / 'prod.keys').write_text('never archive this')
            (work / 'RomFS.img').write_text('resources are re-extracted')
            count, _ = stages.save_stage(work, archive, IDENTITY)
            self.assertEqual(count, len(files))
            self.assertEqual(stages.restore_stage(archive, restored, IDENTITY), count)
            for name in files: self.assertEqual((work / name).read_bytes(), (restored / name).read_bytes())
            self.assertFalse((restored / 'prod.keys').exists())
            self.assertFalse((restored / 'RomFS.img').exists())
            env = stages.package_environment(restored)
            self.assertEqual(env['MSL_IOS'], str(restored / 'shader/ios'))
            self.assertEqual(env['ROMFS'], str(restored / 'extract/v65536/RomFS.img'))

    def test_bad_content_or_changed_inputs_publish_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work, archive, restored = root / 'work', root / 'ready.tar', root / 'new'
            seed(work); stages.save_stage(work, archive, IDENTITY)
            with self.assertRaisesRegex(ValueError, 'dependencies'):
                stages.restore_stage(archive, restored, dict(IDENTITY, toolchain='changed'))
            self.assertFalse(restored.exists())
            archive.write_bytes(archive.read_bytes().replace(b'synthetic compiled', b'corrupted compiled'))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                stages.restore_stage(archive, restored, IDENTITY)
            self.assertFalse(restored.exists())

    def test_counter_completion_does_not_replace_success_stamp_or_summary(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td); seed(work)
            for name in ('shader/ios/b-test.key', 'shader/cache/b-test/_b_build.json',
                         'patched_v65536.key', 'patched_v65536/sdk.fixups.tsv'):
                path = work / name; data = path.read_bytes(); path.unlink()
                with self.subTest(name=name), self.assertRaises((ValueError, FileNotFoundError)):
                    stages.save_stage(work, work / 'incomplete.tar', IDENTITY)
                path.write_bytes(data)

    def test_archive_paths_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, kind in (('../escape', tarfile.REGTYPE), ('patched_v65536/link', tarfile.SYMTYPE)):
                with tarfile.open(root / 'bad.tar', 'w') as out:
                    entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = '../escape'
                    out.addfile(entry, io.BytesIO(b''))
                with self.assertRaises(ValueError): stages.restore_stage(root / 'bad.tar', root / 'new', IDENTITY)
                self.assertFalse((root / 'new').exists())

    def test_packaging_changes_can_resume_but_preparation_changes_cannot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); shader = root / 'shader.py'; shader.write_text('shader-v1')
            source = dict(IDENTITY, source='old-commit')
            with patch.object(stages, 'environment_identity', return_value=source.copy()), \
                    patch.object(stages.subprocess, 'check_output', return_value=b'shader.py\0'):
                before = stages.preparation_identity(root, root)
            with patch.object(stages, 'environment_identity', return_value=dict(source, source='packaging-fix')), \
                    patch.object(stages.subprocess, 'check_output', return_value=b'shader.py\0'):
                self.assertEqual(before, stages.preparation_identity(root, root))
                shader.write_text('shader-v2')
                self.assertNotEqual(before, stages.preparation_identity(root, root))

    def test_package_uses_prepared_inputs_without_running_the_nsp_builder(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); work = folder / f'{stages.GAME}_build'; seed(work)
            with patch.object(sys, 'argv', ['stage', 'package', str(folder)]), \
                    patch.object(stages, 'guard'), patch.object(stages.subprocess, 'run') as run:
                self.assertEqual(stages.main(), 0)
                command = run.call_args.args[0]
                self.assertEqual(Path(command[1]).name, 'build_ipa.sh')
                self.assertEqual(len(command), 3)
                self.assertEqual(run.call_args.kwargs['env']['PATCHED'], str(work / 'patched_v65536'))

    def test_portable_context_does_not_serialize_inherited_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work = root / 'work'; work.mkdir(); profile = root / 'profile'; profile.mkdir()
            (profile / 'game.tsv').write_text('profile')
            game = SimpleNamespace(id=stages.GAME, dir=profile)
            env = {'GAME': stages.GAME, 'W': str(work / 'ipa'), 'DM_ROOT': str(work),
                   'MCD2_GDRIVE_SA_JSON': 'private-token', 'KEYS_TEXT': 'secret-keys'}
            context = builder.portable_package_context(work, game, env)
            self.assertEqual(context['paths']['DM_ROOT'], '.')
            text = json.dumps(context)
            self.assertNotIn('private-token', text); self.assertNotIn('secret-keys', text)
            self.assertTrue((work / f'prepared_profile/{stages.GAME}/game.tsv').is_file())

    def test_workflow_saves_before_packaging_and_restores_without_retranslation(self):
        text = (Path(__file__).resolve().parents[2] / '.github/workflows/mcd2-resumable.yml').read_text()
        self.assertLess(text.index('resumable_build.py save'), text.index('resumable_build.py package'))
        self.assertIn("if: steps.restore.outputs.restored != 'true'", text)
        self.assertIn("if: steps.restore.outputs.restored == 'true'", text)
        self.assertIn('mcd2_storage_probe.py', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertNotIn('path: $RUNNER_TEMP', text)


class TestUploadAndVisibility(unittest.TestCase):
    def test_308_remains_an_upload_response_and_real_redirects_are_retained(self):
        transport = SimpleNamespace(redirect_codes=frozenset({301, 302, 303, 307, 308}))
        self.assertIs(resumable_http(transport), transport)
        self.assertEqual(transport.redirect_codes, {301, 302, 303, 307})

    def test_diagnostics_expose_category_and_status_without_request_credentials(self):
        class SyntheticError(RuntimeError):
            resp = SimpleNamespace(status=403)
            content = b'{"error":{"errors":[{"reason":"storageQuotaExceeded"}]}}'
        exc = SyntheticError('https://private/upload?token=secret')
        report = safe_failure(exc)
        self.assertEqual(report['http_status'], 403)
        self.assertEqual(report['reasons'], ['storageQuotaExceeded'])
        self.assertNotIn('token', json.dumps(report))

    def test_process_activity_is_descendant_only_and_hides_arguments(self):
        rows = '\n'.join(('10 1 01:00 0.0 python observer.py',
                          '11 10 00:30 8.0 /usr/bin/xcodebuild --token PRIVATE',
                          '12 11 00:05 40.0 /usr/bin/clang file.m -secret SECRET',
                          '13 1 10:00 90.0 /usr/bin/metal unrelated'))
        result = activity.processes(rows, 10)
        self.assertEqual({r['role'] for r in result}, {'Building iOS app', 'Compiling app'})
        self.assertNotIn('PRIVATE', json.dumps(result)); self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(activity.elapsed('1-01:02:03'), 90123)

    def test_highest_index_does_not_claim_all_parallel_shaders_completed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'shader_map.tsv').write_text('p.bin\tx\tx\ta\tx\np.bin\tx\tx\tb\tx\np.bin\tx\tx\tc\tx\n')
            game = SimpleNamespace(table=lambda name: root / name)
            watch = builder.ShaderWatch(game, root, root)
            watch.w, watch.t1 = root, 0.0
            with patch.object(watch, 'written', return_value={'c'}): result = watch()
            self.assertEqual(result['total'], 3); self.assertEqual(result['done'], 1)
            watch.hits = {'a'}
            with patch.object(watch, 'written', return_value={'b'}): self.assertEqual(watch()['done'], 3)


if __name__ == '__main__':
    unittest.main()
