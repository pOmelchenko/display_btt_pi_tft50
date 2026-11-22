#!/usr/bin/env python3
"""Plan/install TFT50 files for one EFI entry, or restore its saved transaction."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
DEPENDENCY = HERE / 'driver'
ESP = Path('/boot/efi')
DISPLAY = 'q6a-btt-pi-tft50-v21.dtbo'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    require(not path.is_symlink(), f'Symlink is not accepted: {path}')
    return sha(path.read_bytes()) if path.exists() else None


def command(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def efi_path(esp, name):
    parts = PurePosixPath(name).parts
    require(name.startswith('/') and not name.startswith('//') and '..' not in parts and
            str(PurePosixPath(name)) == name and '\\' not in name,
            f'Invalid EFI path: {name}')
    path = esp / name.lstrip('/')
    require(path.resolve().is_relative_to(esp.resolve()), f'Path escapes EFI: {name}')
    return path


def entry_fields(text):
    fields = {}
    for index, line in enumerate(text.splitlines()):
        words = line.split()
        if not words or words[0].startswith('#'):
            continue
        fields.setdefault(words[0], []).append((index, words[1:]))
    return fields


def one_field(fields, key):
    matches = fields.get(key, [])
    require(len(matches) == 1 and len(matches[0][1]) == 1,
            f'Expected one {key} path in EFI entry')
    return matches[0][1][0]


def overlay_paths(text):
    rows = entry_fields(text).get('devicetree-overlay', [])
    require(len(rows) <= 1, 'Expected at most one devicetree-overlay line')
    paths = rows[0][1] if rows else []
    require(len(paths) == len(set(paths)), 'Duplicate overlays in EFI entry')
    return paths


def with_overlays(text, paths):
    if overlay_paths(text) == paths:
        return text
    lines = text.splitlines(keepends=True)
    rows = entry_fields(text).get('devicetree-overlay', [])
    line = 'devicetree-overlay ' + ' '.join(paths) + '\n'
    if rows:
        lines[rows[0][0]] = line
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(line)
    return ''.join(lines)


def default_entry():
    entries = ESP / 'loader/entries'
    loader = ESP / 'loader/loader.conf'
    rows = entry_fields(loader.read_text()).get('default', []) if loader.exists() else []
    if rows:
        name = one_field({'default': rows}, 'default').removesuffix('.conf')
        require(name and PurePosixPath(name).name == name, 'Select the EFI entry with --entry')
        entry = entries / (name + '.conf')
    else:
        token = Path('/etc/kernel/entry-token').read_text().strip()
        require(re.fullmatch(r'[A-Za-z0-9_.-]+', token), 'Invalid EFI entry token')
        entry = entries / f'{token}-{platform.release()}.conf'
        stock = re.compile(re.escape(token) + r'-\d[\w.+-]*\.conf')
        others = [p for p in entries.glob('*.conf') if not stock.fullmatch(p.name)
                  and p.name != 'Q6A-before-tft50-v21-' + entry.name]
        require(not others, 'Multiple EFI entries. Select the active entry with --entry')
    require(entry.is_file(), 'Cannot resolve boot default. Select the EFI entry with --entry')
    return entry


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stage_transaction(changes, backup, preserved, kernel, expected=None):
    """Save every before/after payload before the first system-file change."""
    require(len(changes) == len({str(path) for path, _ in changes}), 'Duplicate target')
    backup.mkdir(mode=0o700)
    records = []
    for index, (path, after) in enumerate(changes):
        require(not path.is_symlink(), f'Symlink is not accepted: {path}')
        before = path.read_bytes() if path.exists() else None
        before_hash = sha(before) if before is not None else None
        if expected is not None:
            require(before_hash == expected[str(path)], f'Target changed since planning: {path}')
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        # VFAT permissions come from mount options; chmod may be unsupported.
        if path.is_relative_to(ESP):
            mode = None
        before_name = f'{index}.before' if before_hash is not None else None
        after_name = f'{index}.after' if after is not None else None
        if before_name:
            (backup / before_name).write_bytes(before)
        if after_name:
            (backup / after_name).write_bytes(after)
        records.append({'path': str(path), 'before': before_name, 'after': after_name,
                        'before_sha256': before_hash,
                        'after_sha256': sha(after) if after is not None else None,
                        'mode': mode})
    manifest = {'schema_version': 1, 'kernel_release': kernel, 'files': records,
                'preserved': preserved}
    (backup / 'transaction.json').write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copyfile(Path(__file__), backup / 'install.py')
    return manifest


def verify_preserved(manifest):
    for name, expected in manifest['preserved'].items():
        require(file_hash(Path(name)) == expected, f'Preserved file changed: {name}')


def apply_transaction(backup):
    manifest = json.loads((backup / 'transaction.json').read_text())
    verify_preserved(manifest)
    for record in manifest['files']:
        require(file_hash(Path(record['path'])) == record['before_sha256'],
                f'Target changed since planning: {record["path"]}')
        if record['after']:
            require(sha((backup / record['after']).read_bytes()) == record['after_sha256'],
                    'Corrupt staged payload')
    for record in manifest['files']:
        path = Path(record['path'])
        if record['after'] is None:
            path.unlink()
        else:
            atomic_write(path, (backup / record['after']).read_bytes(), record['mode'])
    verify_preserved(manifest)


def rollback_transaction(backup):
    manifest = json.loads((backup / 'transaction.json').read_text())
    # Check all targets before touching any: never overwrite a later user edit.
    for record in manifest['files']:
        require(file_hash(Path(record['path'])) in
                (record['before_sha256'], record['after_sha256']),
                f'File changed after installation; inspect manually: {record["path"]}')
        if record['before']:
            require(sha((backup / record['before']).read_bytes()) == record['before_sha256'],
                    'Corrupt rollback payload')
    for record in reversed(manifest['files']):
        path = Path(record['path'])
        if record['before'] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, (backup / record['before']).read_bytes(), record['mode'])
    return manifest['kernel_release']


def plan_install(build, entry):
    lock = json.loads((DEPENDENCY / 'source.lock.json').read_text())
    kernel = lock['kernel_release']
    require(platform.release() == kernel, f'Qualified kernel required: {kernel}')
    require(b'radxa,dragon-q6a' in Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0'),
            'This installer requires a Radxa Dragon Q6A')
    require(ESP.is_mount(), 'The EFI system partition must be mounted at /boot/efi')
    require(entry.parent == ESP / 'loader/entries' and entry.suffix == '.conf',
            'Select an entry directly under /boot/efi/loader/entries')
    require(not entry.name.startswith('Q6A-before-'), 'A recovery entry cannot be the install target')
    require(not entry.is_symlink(), 'EFI entry must not be a symlink')
    old_bytes = entry.read_bytes()
    old = old_bytes.decode()
    fields = entry_fields(old)
    linux = one_field(fields, 'linux')
    base_rel = one_field(fields, 'devicetree')
    require(kernel in PurePosixPath(linux).parts and kernel in PurePosixPath(base_rel).parts,
            'Entry must reference the running kernel and its own DTB directory')
    require(efi_path(ESP, linux).is_file(), 'EFI kernel image is missing')
    base = efi_path(ESP, base_rel)
    catalog = PurePosixPath(linux).parent / 'dtbo'
    display_rel = str(catalog / DISPLAY)
    legacy = {str(catalog / name): digest for name, digest in lock['legacy_overlays'].items()}
    paths = overlay_paths(old)
    saved_profiles = {name + '.disabled': digest for name, digest in legacy.items()}
    retired = [p for p in paths if p in legacy or p in saved_profiles]
    unrelated = [p for p in paths if p not in retired and p != display_rel]
    updated = list(unrelated)
    insert = next((i for i, p in enumerate(paths) if p in retired or p == display_rel), len(paths))
    updated.insert(insert, display_rel)
    new = with_overlays(old, updated)
    preserved = {str(base): file_hash(base)}
    for name in retired:
        if name in saved_profiles:
            saved = efi_path(ESP, name)
            require(file_hash(saved) == saved_profiles[name],
                    f'Unrecognized or missing preserved legacy overlay: {name}')
            preserved[str(saved)] = saved_profiles[name]
    for name in unrelated:
        other = efi_path(ESP, name)
        preserved[str(other)] = file_hash(other)
        exclusive = subprocess.run(['fdtget', '-t', 's', str(other), '/metadata', 'exclusive'],
                                   capture_output=True, text=True)
        require(not {'mdss_dsi', 'gpio80'}.intersection(exclusive.stdout.split()),
                f'Another overlay owns DSI or GPIO80: {name}')
    require(all(preserved.values()), 'Missing base DTB or other active overlay')
    build_manifest = json.loads((build / 'build.json').read_text())
    require(build_manifest['kernel_release'] == kernel and
            build_manifest['source_lock_sha256'] == file_hash(DEPENDENCY / 'source.lock.json') and
            build_manifest['driver_sha256'] == lock['patches'][-1]['result_sha256'],
            'Build manifest does not match this pinned source/kernel')
    names = ('edt-ft5x06.ko', DISPLAY)
    payloads = {name: (build / name).read_bytes() for name in names}
    for name, data in payloads.items():
        require(sha(data) == build_manifest['files'][name], f'Build file changed: {name}')
    require(sha(payloads[DISPLAY]) == lock['overlay_sha256'], 'Unexpected TFT50 overlay')
    require(command('modinfo', '-F', 'vermagic', build / names[0]).split()[0] == kernel,
            'Module vermagic does not match kernel')
    module_root = Path('/lib/modules') / kernel
    selected = Path(command('modinfo', '-n', 'edt_ft5x06'))
    module = module_root / 'updates/q6a-tft50/edt-ft5x06.ko'
    module_digest = file_hash(module)
    known_modules = {sha(payloads[names[0]]), lock['legacy_module_sha256']}
    require(module_digest is None or module_digest in known_modules,
            f'Unrecognized existing module: {module}')
    require(selected.parent == module_root / 'kernel/drivers/input/touchscreen' or
            (selected == module and module_digest in known_modules),
            f'A module override is already installed: {selected}')
    if module_digest is not None:
        require(selected == module, 'Existing TFT50 override is not selected by depmod')
    initrd = efi_path(ESP, one_field(fields, 'initrd'))
    initrd_listing = command('lsinitramfs', initrd)
    require(not any('edt-ft5x06.ko' in p or 'edt_ft5x06.ko' in p
                    for p in initrd_listing.splitlines()),
            'edt-ft5x06 is embedded in initramfs; this recipe does not cover that case')

    with tempfile.TemporaryDirectory(prefix='q6a-tft50-check-') as temp:
        merged = Path(temp) / 'merged.dtb'
        rest = [efi_path(ESP, p) for p in unrelated]
        baseline = base
        if rest:
            baseline = Path(temp) / 'without-tft50.dtb'
            command('fdtoverlay', '-i', base, '-o', baseline, *rest)
        check_free_port(baseline)
        ordered = [build / DISPLAY if p == display_rel else efi_path(ESP, p) for p in updated]
        command('fdtoverlay', '-i', base, '-o', merged, *ordered)
        bus = command('fdtget', '-t', 's', merged, '/__symbols__', 'i2c13')
        props = command('fdtget', '-p', merged, bus + '/touch-controller@38').split()
        require('interrupts' not in props and 'interrupt-parent' not in props,
                'Touch IRQ remains in the merged tree')
        tlmm = command('fdtget', '-t', 's', merged, '/__symbols__', 'tlmm')
        for child in command('fdtget', '-l', merged, tlmm).split():
            node = tlmm + '/' + child
            props = command('fdtget', '-p', merged, node).split()
            if 'gpio-hog' in props and child != 'btt-tft50-lcd-power-hog':
                gpios = command('fdtget', '-t', 'u', merged, node, 'gpios').split()
                require('80' not in gpios[::2], 'Another GPIO hog already owns GPIO80')

    changes = []
    expected = {}

    def update(path, data, before):
        require(file_hash(path) == before, f'Target changed since planning: {path}')
        if before == (sha(data) if data is not None else None):
            if before is not None:
                preserved[str(path)] = before
            return
        changes.append((path, data))
        expected[str(path)] = before

    target = efi_path(ESP, display_rel)
    target_hash = file_hash(target)
    require(target_hash in (None, lock['overlay_sha256']), f'Unrecognized overlay: {target}')
    update(target, payloads[DISPLAY], target_hash)
    if module_digest in known_modules and selected == module:
        preserved[str(module)] = module_digest
    else:
        update(module, payloads[names[0]], module_digest)

    # rsetup enables every *.dtbo in the catalog. Preserve shared boot entries
    # by pointing them at identical .disabled copies before retiring old names.
    moved = {}
    for name, digest in legacy.items():
        active = efi_path(ESP, name)
        if active.exists() or name in retired:
            require(file_hash(active) == digest, f'Unrecognized or missing legacy overlay: {name}')
            saved = efi_path(ESP, name + '.disabled')
            before = file_hash(saved)
            require(before in (None, digest), f'Existing backup differs: {saved}')
            update(saved, active.read_bytes(), before)
            moved[name] = name + '.disabled'

    def redirected(text):
        return with_overlays(text, [moved.get(p, p) for p in overlay_paths(text)])

    if moved:
        for other in sorted(entry.parent.glob('*.conf')):
            if other == entry:
                continue
            original = other.read_bytes()
            if any(name in original.decode() for name in moved):
                update(other, redirected(original.decode()).encode(), sha(original))
    if new != old:
        recovery = entry.with_name('Q6A-before-tft50-v21-' + entry.name)
        recovery_lines = redirected(old).splitlines(keepends=True)
        titles = fields.get('title', [])
        require(len(titles) == 1, 'Expected one title in EFI entry')
        recovery_lines[titles[0][0]] = 'title Q6A before BTT TFT50 unified overlay\n'
        require(not recovery.exists(), f'Recovery entry already exists: {recovery}')
        update(recovery, ''.join(recovery_lines).encode(), None)
    update(entry, new.encode(), sha(old_bytes))
    for name in moved:
        update(efi_path(ESP, name), None, legacy[name])
    replacements = retired + [name for name in moved if name not in retired]
    return changes, preserved, kernel, module, expected, replacements


def check_free_port(tree):
    symbols = {s: command('fdtget', '-t', 's', tree, '/__symbols__', s)
               for s in ('mdss_dsi0_out', 'i2c13', 'mdss_dsi', 'tlmm')}
    props = command('fdtget', '-p', tree, symbols['mdss_dsi0_out']).split()
    require('remote-endpoint' not in props, 'Another panel is connected to the DSI endpoint')
    for node, reserved in [(symbols['i2c13'], {'38', '45'}), (symbols['mdss_dsi'], {'0'})]:
        for child in command('fdtget', '-l', tree, node).split():
            require(child.rsplit('@', 1)[-1] not in reserved, f'Display address occupied: {node}/{child}')
    require('btt-tft50-lcd-power-hog' not in command('fdtget', '-l', tree, symbols['tlmm']).split(),
            'LCD power is already configured in the base DTB or another profile')


def confirm_replacement(retired):
    print('Заменить профили дисплея: ' + ', '.join(retired), flush=True)
    require(sys.stdin.isatty(), 'Use --replace to confirm non-interactively')
    answer = input('Заменить? [д/Н] ').strip().casefold()
    require(answer in ('да', 'д', 'y', 'yes'), 'Замена отменена')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path)
    parser.add_argument('--entry', type=Path, help='Explicit current-kernel EFI entry')
    parser.add_argument('--apply', action='store_true', help='Install; otherwise only validate and print')
    parser.add_argument('--rollback', type=Path, help='Restore a saved transaction directory')
    parser.add_argument('--replace', action='store_true', help='Confirm replacement of verified legacy profiles')
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, 'Run with sudo on the Q6A (EFI files require root even for a plan)')
        lockfile = open('/run/lock/q6a-btt-tft50.lock', 'a')
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        if args.rollback:
            require(not (args.build or args.entry or args.apply or args.replace), 'Use --rollback by itself')
            kernel = rollback_transaction(args.rollback.resolve())
            command('depmod', '-a', kernel)
            os.sync()
            print('Saved files restored. Reboot to apply; the loaded module was not changed.')
            return
        require(args.build, '--build is required')
        entry = args.entry.absolute() if args.entry else default_entry()
        changes, preserved, kernel, module, expected, retired = plan_install(args.build.resolve(), entry)
        print(json.dumps({'kernel': kernel, 'changes': [str(p) for p, _ in changes],
                          'preserved_sha256': preserved, 'apply': args.apply}, indent=2))
        if not changes:
            print(f'{entry.name}: the unified TFT50 profile is already installed.')
            return
        if not args.apply:
            return
        if retired and not args.replace:
            confirm_replacement(retired)
        # mkdtemp reserves a unique name; staging requires an empty, new directory.
        backup = Path(tempfile.mkdtemp(prefix='q6a-tft50-', dir='/var/backups'))
        backup.rmdir()
        stage_transaction(changes, backup, preserved, kernel, expected)
        print(f'Backup: {backup}', flush=True)
        os.sync()
        try:
            apply_transaction(backup)
            command('depmod', '-a', kernel)
            require(Path(command('modinfo', '-n', 'edt_ft5x06')) == module,
                    'depmod did not select the installed module')
        except Exception:
            rollback_transaction(backup)
            command('depmod', '-a', kernel)
            os.sync()
            raise
        os.sync()
        print(f'Installed for next boot. Rollback: sudo python3 {backup}/install.py --rollback {backup}')
        print('The running module and live device tree were not changed. Reboot when ready.')
    except (OSError, EOFError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
