"""Compare the old and new profiles on a Q6A DTB, optionally with camera overlays."""
import argparse
import hashlib
import json
from pathlib import Path
from subprocess import run
import tempfile

TARGET = Path(__file__).resolve().parents[1] / 'target/sbc/radxa_dragon_q6a'


def compare(base, display, power, cameras):
    lock = json.loads((TARGET / 'driver/source.lock.json').read_text())
    for path, name in [(display, 'q6a-btt-tft50-display.dtbo'), (power, 'q6a-btt-tft50-power.dtbo')]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != lock['legacy_overlays'][name]:
            raise ValueError(f'Input differs from hardware validation record: {path}')
    with tempfile.TemporaryDirectory() as temp:
        unified = Path(temp) / 'unified.dtbo'
        run(['dtc', '-q', '-@', '-O', 'dtb', '-o', unified, TARGET / 'q6a-btt-pi-tft50-v21.dts'], check=True)
        trees = []
        for overlays in ([display, power], [unified]):
            merged = Path(temp) / 'merged.dtb'
            run(['fdtoverlay', '-i', base, '-o', merged, *cameras, *overlays], check=True)
            trees.append(run(['dtc', '-q', '-s', '-I', 'dtb', '-O', 'dts', merged],
                             check=True, capture_output=True).stdout)
        if trees[0] != trees[1]:
            raise ValueError('The normalized device trees differ')
        print('Identical normalized DTS. SHA-256', hashlib.sha256(trees[0]).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('display', type=Path)
    parser.add_argument('power', type=Path)
    parser.add_argument('camera', type=Path, nargs='*')
    args = parser.parse_args()
    compare(args.base, args.display, args.power, args.camera)
