"""Protect unrelated boot configuration and exercise interrupted-install recovery."""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'tft50_install', ROOT / 'target/sbc/radxa_dragon_q6a/install.py')
tft50 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tft50)


class EntryTests(unittest.TestCase):
    def test_entries_preserve_non_overlay_lines_and_accept_missing_newline(self):
        before = '# comment\ntitle normal\noptions root=UUID=example quiet'
        after = tft50.with_overlays(before, ['/camera.dtbo', '/display.dtbo'])
        self.assertEqual(after, before + '\ndevicetree-overlay /camera.dtbo /display.dtbo\n')
        self.assertEqual(after, tft50.with_overlays(after, tft50.overlay_paths(after)))

    def test_duplicate_lists_and_paths_are_rejected(self):
        for text in ['devicetree-overlay /a /a', 'devicetree-overlay /a\ndevicetree-overlay /b']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                tft50.overlay_paths(text)

    def test_confirmation_defaults_to_no_and_requires_terminal(self):
        for tty, answer in ((True, ''), (True, 'нет'), (False, 'да')):
            with mock.patch.object(tft50.sys.stdin, 'isatty', return_value=tty), \
                    mock.patch('builtins.input', return_value=answer), self.assertRaises(ValueError):
                tft50.confirm_replacement(['rpi7.dtbo'])
        with mock.patch.object(tft50.sys.stdin, 'isatty', return_value=True), \
                mock.patch('builtins.input', return_value='Да'):
            tft50.confirm_replacement(['rpi7.dtbo'])

    def test_efi_paths_cannot_escape_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            esp = Path(temp)
            for path in ['../outside', '/boot/../outside', 'relative', '/dir\\outside']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    tft50.efi_path(esp, path)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = self.root / 'efi/entry.conf'
        self.entry.parent.mkdir()
        self.entry.write_bytes(b'original entry\n')
        self.stock = self.root / 'efi/stock.dtbo'
        self.stock.write_bytes(b'original overlay')
        self.disabled = self.root / 'efi/stock.dtbo.disabled'
        self.module = self.root / 'modules/updates/edt-ft5x06.ko'
        self.base = self.root / 'efi/base.dtb'
        self.base.write_bytes(b'camera base DTB')
        self.backup = self.root / 'backup'
        self.preserved = {str(self.base): tft50.file_hash(self.base)}
        self.changes = [(self.module, b'new module'), (self.disabled, self.stock.read_bytes()),
                        (self.stock, None), (self.entry, b'updated entry\n')]
        self.expected = {str(path): tft50.file_hash(path) for path, _ in self.changes}

    def stage(self):
        tft50.stage_transaction(self.changes, self.backup, self.preserved, 'test', self.expected)

    def assert_restored(self):
        self.assertEqual(self.entry.read_bytes(), b'original entry\n')
        self.assertEqual(self.stock.read_bytes(), b'original overlay')
        self.assertEqual(self.base.read_bytes(), b'camera base DTB')
        self.assertFalse(self.module.exists())
        self.assertFalse(self.disabled.exists())

    def test_install_then_repeatable_rollback_restores_exact_bytes(self):
        self.stage()
        tft50.apply_transaction(self.backup)
        self.assertEqual(self.entry.read_bytes(), b'updated entry\n')
        self.assertFalse(self.stock.exists())
        self.assertEqual(self.disabled.read_bytes(), b'original overlay')
        self.assertEqual(self.module.read_bytes(), b'new module')
        tft50.rollback_transaction(self.backup)
        tft50.rollback_transaction(self.backup)
        self.assert_restored()

    def test_interrupted_install_can_be_rolled_back(self):
        self.stage()
        original = tft50.atomic_write

        def fail_entry(path, data, mode):
            if path == self.entry:
                raise OSError('simulated interrupted EFI update')
            original(path, data, mode)

        with mock.patch.object(tft50, 'atomic_write', side_effect=fail_entry):
            with self.assertRaises(OSError):
                tft50.apply_transaction(self.backup)
        self.assertFalse(self.stock.exists())
        tft50.rollback_transaction(self.backup)
        self.assert_restored()

    def test_user_edit_after_install_blocks_entire_rollback(self):
        self.stage()
        tft50.apply_transaction(self.backup)
        self.entry.write_bytes(b'new camera setup\n')
        with self.assertRaisesRegex(ValueError, 'changed after installation'):
            tft50.rollback_transaction(self.backup)
        self.assertEqual(self.entry.read_bytes(), b'new camera setup\n')
        self.assertTrue(self.module.exists())
        self.assertTrue(self.disabled.exists())

    def test_edit_between_plan_and_stage_is_not_overwritten(self):
        self.entry.write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, 'changed since planning'):
            self.stage()
        self.assertEqual(self.entry.read_bytes(), b'user edit')
        self.assertFalse(self.module.exists())

    def test_edit_after_stage_blocks_install_before_any_write(self):
        self.stage()
        self.entry.write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, 'changed since planning'):
            tft50.apply_transaction(self.backup)
        self.assertFalse(self.module.exists())

    def test_changed_base_blocks_install_before_any_write(self):
        self.stage()
        self.base.write_bytes(b'new camera base DTB')
        with self.assertRaisesRegex(ValueError, 'Preserved file changed'):
            tft50.apply_transaction(self.backup)
        self.assertFalse(self.module.exists())

    def test_corrupt_backup_blocks_rollback_before_any_write(self):
        self.stage()
        tft50.apply_transaction(self.backup)
        (self.backup / '3.before').write_bytes(b'corrupted entry backup')
        with self.assertRaisesRegex(ValueError, 'Corrupt rollback payload'):
            tft50.rollback_transaction(self.backup)
        self.assertEqual(self.entry.read_bytes(), b'updated entry\n')
        self.assertTrue(self.module.exists())

    def test_symlink_target_is_rejected(self):
        try:
            self.disabled.symlink_to(self.base)
        except OSError:
            self.skipTest('Host does not grant symlink creation')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.stage()
        self.assertEqual(self.base.read_bytes(), b'camera base DTB')


class InstallPlanTests(unittest.TestCase):
    """Exercise the complete plan and transaction against a synthetic mounted ESP."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.esp = self.root / 'efi'
        self.kernel = '7.0.11-7-qcom'
        self.prefix = '/RadxaOS/' + self.kernel
        self.entry = self.esp / 'loader/entries/current.conf'
        self.stock_rel = self.prefix + '/dtbo/rpi7.dtbo'
        self.module_root = self.root / 'modules' / self.kernel
        self.selected = self.module_root / 'kernel/drivers/input/touchscreen/edt-ft5x06.ko.zst'
        self.build = self.root / 'build'
        self.dep = self.root / 'dependency'
        for path in (self.entry.parent, self.build, self.dep):
            path.mkdir(parents=True)
        self.original = ('title RadxaOS\nlinux ' + self.prefix + '/vmlinuz\n'
                         'initrd ' + self.prefix + '/initrd\n'
                         'devicetree ' + self.prefix + '/base.dtb\n'
                         'devicetree-overlay ' + self.prefix + '/dtbo/camera.dtbo ' + self.stock_rel + '\n'
                         'options root=UUID=example preserve_me=1\n')
        self.entry.write_text(self.original)
        for name, data in [('vmlinuz', b'kernel'), ('base.dtb', b'base'),
                           ('initrd', b'initrd'), ('dtbo/camera.dtbo', b'camera'), ('dtbo/rpi7.dtbo', b'stock')]:
            path = self.esp / self.prefix.lstrip('/') / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name, data in [('edt-ft5x06.ko', b'module'), (tft50.DISPLAY, b'display')]:
            (self.build / name).write_bytes(data)
        self.lock = {'kernel_release': self.kernel,
                     'stock_overlay': {'filename': 'rpi7.dtbo', 'sha256': tft50.sha(b'stock')},
                     'patches': [{'result_sha256': 'driver-hash'}],
                     'overlay_sha256': tft50.sha(b'display'),
                     'legacy_module_sha256': tft50.sha(b'old qualified module'),
                     'legacy_overlays': {name: tft50.sha(data) for name, data in
                                         [('rpi7.dtbo', b'stock'), ('old-display.dtbo', b'old display'),
                                          ('old-power.dtbo', b'power')] }}
        (self.dep / 'source.lock.json').write_text(json.dumps(self.lock))
        manifest = {'kernel_release': self.kernel,
                    'source_lock_sha256': tft50.file_hash(self.dep / 'source.lock.json'),
                    'driver_sha256': 'driver-hash',
                    'files': {p.name: tft50.file_hash(p) for p in self.build.iterdir()}}
        (self.build / 'build.json').write_text(json.dumps(manifest))
        compatible = self.root / 'compatible'
        compatible.write_bytes(b'radxa,dragon-q6a\0qcom,qcs6490\0')
        mapping = {'/lib/modules': self.root / 'modules', '/boot': self.root / 'boot',
                   '/sys/firmware/devicetree/base/compatible': compatible}

        def mapped_path(value):
            return mapping.get(str(value), Path(value))

        def command(*args):
            if args[:2] == ('modinfo', '-n'):
                return str(self.selected)
            if args[:3] == ('modinfo', '-F', 'vermagic'):
                return self.kernel + ' SMP preempt mod_unload aarch64'
            if args[0] in ('fdtoverlay', 'lsinitramfs'):
                return ''
            if args[0] == 'fdtget':
                if args[1] == '-p':
                    return 'compatible reg touchscreen-size-x touchscreen-size-y'
                if args[1] == '-l':
                    return ''
                return '/soc/i2c' if args[-1] == 'i2c13' else '/soc/tlmm'
            raise AssertionError(args)

        for patcher in [mock.patch.object(tft50, 'ESP', self.esp),
                        mock.patch.object(tft50, 'DEPENDENCY', self.dep),
                        mock.patch.object(tft50, 'Path', side_effect=mapped_path),
                        mock.patch.object(tft50.platform, 'release', return_value=self.kernel),
                        mock.patch.object(Path, 'is_mount', return_value=True),
                        mock.patch.object(tft50, 'command', side_effect=command),
                        mock.patch.object(tft50.subprocess, 'run', return_value=
                                          subprocess.CompletedProcess([], 0, stdout='', stderr=''))]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stock_active_and_inactive_plans_install_and_restore_exact_entry(self):
        for active in (True, False):
            with self.subTest(stock_active=active):
                original = self.original if active else self.original.replace(' ' + self.stock_rel, '')
                self.entry.write_text(original)
                changes, preserved, kernel, module, expected, retired = tft50.plan_install(self.build, self.entry)
                self.assertEqual(self.entry.read_text(), original)  # plan is read-only
                self.assertEqual(len(preserved), 2)  # base and camera overlay
                backup = self.root / ('backup-active' if active else 'backup-inactive')
                tft50.stage_transaction(changes, backup, preserved, kernel, expected)
                tft50.apply_transaction(backup)
                self.assertTrue(module.exists())
                self.assertIn('preserve_me=1', self.entry.read_text())
                self.assertIn(self.prefix + '/dtbo/camera.dtbo', self.entry.read_text())
                self.assertIn(tft50.DISPLAY, self.entry.read_text())
                tft50.rollback_transaction(backup)
                self.assertEqual(self.entry.read_text(), original)
                self.assertFalse(module.exists())

    def test_migrate_split_overlay_with_shared_recovery_entry_and_repeat_before_reboot(self):
        catalog = self.esp / self.prefix.lstrip('/') / 'dtbo'
        (catalog / 'rpi7.dtbo').unlink()
        display = self.prefix + '/dtbo/old-display.dtbo'
        power = self.prefix + '/dtbo/old-power.dtbo'
        (catalog / 'old-display.dtbo').write_bytes(b'old display')
        (catalog / 'old-power.dtbo').write_bytes(b'power')
        (catalog / 'other.dtbo').write_bytes(b'other')
        other = self.prefix + '/dtbo/other.dtbo'
        old = self.original.replace(self.stock_rel, ' '.join([display, other, power]))
        self.entry.write_text(old)
        shared = self.entry.with_name('image-only.conf')
        shared.write_text(self.original.replace(self.stock_rel, power))
        self.selected = self.module_root / 'updates/q6a-tft50/edt-ft5x06.ko'
        self.selected.parent.mkdir(parents=True)
        self.selected.write_bytes(b'old qualified module')
        changes, keep, kernel, module, expected, retired = tft50.plan_install(self.build, self.entry)
        self.assertEqual(set(retired), {display, power})
        backup = self.root / 'backup'
        tft50.stage_transaction(changes, backup, keep, kernel, expected)
        tft50.apply_transaction(backup)
        self.assertEqual(tft50.overlay_paths(self.entry.read_text()), [
            self.prefix + '/dtbo/camera.dtbo', self.prefix + '/dtbo/' + tft50.DISPLAY, other])
        self.assertEqual(tft50.overlay_paths(shared.read_text())[-1], power + '.disabled')
        self.assertEqual(self.selected.read_bytes(), b'old qualified module')
        self.assertEqual(tft50.plan_install(self.build, self.entry)[0], [])
        tft50.rollback_transaction(backup)
        self.assertEqual(self.entry.read_text(), old)
        self.assertEqual(tft50.overlay_paths(shared.read_text())[-1], power)
        self.assertEqual((catalog / 'old-display.dtbo').read_bytes(), b'old display')
        self.assertEqual((catalog / 'old-power.dtbo').read_bytes(), b'power')

    def test_refuse_modified_legacy_profile(self):
        (self.esp / self.stock_rel.lstrip('/')).write_bytes(b'custom')
        with self.assertRaisesRegex(ValueError, 'Unrecognized or missing legacy'):
            tft50.plan_install(self.build, self.entry)
        self.assertEqual(self.entry.read_text(), self.original)

    def test_refuse_other_display_even_with_replacement_enabled(self):
        with mock.patch.object(tft50.subprocess, 'run', return_value=
                               subprocess.CompletedProcess([], 0, stdout='mdss_dsi', stderr='')):
            with self.assertRaisesRegex(ValueError, 'Another overlay owns'):
                tft50.plan_install(self.build, self.entry)
        self.assertEqual(self.entry.read_text(), self.original)

    def test_changed_build_payload_is_rejected_before_any_system_write(self):
        (self.build / 'edt-ft5x06.ko').write_bytes(b'corrupt module')
        with self.assertRaisesRegex(ValueError, 'Build file changed'):
            tft50.plan_install(self.build, self.entry)
        self.assertEqual(self.entry.read_text(), self.original)

    def test_existing_module_override_is_preserved(self):
        self.selected = self.module_root / 'updates/other/edt-ft5x06.ko'
        with self.assertRaisesRegex(ValueError, 'override is already installed'):
            tft50.plan_install(self.build, self.entry)
        self.assertEqual(self.entry.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
