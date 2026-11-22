"""Exercise the actual device tree tools and verify the offline driver inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TARGET = Path(__file__).resolve().parents[1] / 'target/sbc/radxa_dragon_q6a'
DRIVER = TARGET / 'driver'
LOCK = json.loads((DRIVER / 'source.lock.json').read_text())


def call(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PackageTests(unittest.TestCase):
    def test_driver_matches_every_pinned_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'edt-ft5x06.c'
            shutil.copyfile(DRIVER / 'edt-ft5x06-stock.c', source)
            self.assertEqual(digest(source), LOCK['driver']['sha256'])
            self.assertEqual(digest(DRIVER / LOCK['license']['path']), LOCK['license']['sha256'])
            for patch in LOCK['patches']:
                path = DRIVER / patch['path']
                self.assertEqual(digest(path), patch['sha256'])
                subprocess.run(['patch', '-s', '--batch', '--forward', '--fuzz=0',
                                f"-p{patch['strip']}", '-i', path], cwd=temp, check=True)
                self.assertEqual(digest(source), patch['result_sha256'])

    def test_one_overlay_supplies_panel_touch_power_and_keeps_camera(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'base.dts'
            source.write_text('''/dts-v1/;
/ {
    compatible = "radxa,dragon-q6a";
    mdss: display { mdss_dsi: dsi { mdss_dsi0_out: endpoint {}; }; mdss_dsi_phy: phy {}; };
    vreg_l6b_1p2: regulator-l6b {};
    vreg_l10c_0p88: regulator-l10c {};
    qupv3_id_1: qup { i2c13: i2c {}; };
    tlmm: pinctrl { gpio-controller; #gpio-cells = <2>; };
    cci { cam@1a { compatible = "sony,imx296"; }; };
};''')
            base, overlay, merged = (root / n for n in ('base.dtb', 'combined.dtbo', 'merged.dtb'))
            call('dtc', '-q', '-@', '-O', 'dtb', '-o', base, source)
            call('dtc', '-q', '-@', '-O', 'dtb', '-o', overlay, TARGET / 'q6a-btt-pi-tft50-v21.dts')
            self.assertEqual(digest(overlay), LOCK['overlay_sha256'])
            call('fdtoverlay', '-i', base, '-o', merged, overlay)

            def get(node, prop, kind='s'):
                return call('fdtget', '-t', kind, merged, node, prop)

            self.assertEqual(get('/panel@0', 'compatible'), 'raspberrypi,7inch-dsi')
            self.assertEqual(get('/display/dsi/bridge@0', 'compatible'), 'toshiba,tc358762')
            hog = '/pinctrl/btt-tft50-lcd-power-hog'
            self.assertEqual(get(hog, 'gpios', 'u'), '80 0')
            self.assertIn('output-high', call('fdtget', '-p', merged, hog).split())
            touch = '/qup/i2c/touch-controller@38'
            self.assertEqual(get(touch, 'compatible'), 'edt,edt-ft5406')
            self.assertEqual(get(touch, 'touchscreen-size-x', 'u'), '800')
            self.assertEqual(get(touch, 'touchscreen-size-y', 'u'), '480')
            self.assertEqual(get(touch, 'vcc-supply', 'x'), get('/qup/i2c/regulator@45', 'phandle', 'x'))
            self.assertTrue({'interrupts', 'interrupt-parent'}.isdisjoint(call('fdtget', '-p', merged, touch).split()))
            self.assertEqual(get('/cci/cam@1a', 'compatible'), 'sony,imx296')
