#!/bin/sh
# Run as a normal user on the board; sudo is used for packages and installation.
set -eu
cd -- "$(dirname -- "$0")"
kernel=$(uname -r)
if [ "$kernel" != 7.0.11-7-qcom ] || [ "$(id -u)" = 0 ]; then
    echo "Run as a normal user on the Q6A with kernel 7.0.11-7-qcom." >&2
    exit 1
fi
if ! tr '\000' '\n' </sys/firmware/devicetree/base/compatible | grep -Fxq 'radxa,dragon-q6a'; then
    echo 'This package requires a Radxa Dragon Q6A.' >&2
    exit 1
fi
missing=
for dep in python3 build-essential patch device-tree-compiler kmod initramfs-tools-core "linux-headers-$kernel"; do
    if [ "$(dpkg-query -W -f='${Status}' "$dep" 2>/dev/null || :)" != 'install ok installed' ]; then
        missing="$missing $dep"
    fi
done
if [ -n "$missing" ]; then
    if [ "${1:-}" = --plan ]; then
        echo "Install prerequisites for validation: sudo apt install$missing" >&2
        exit 1
    fi
    sudo apt-get install --no-install-recommends $missing
fi
build_dir=${XDG_CACHE_HOME:-"$HOME/.cache"}/btt-pi-tft50/"$kernel"
mkdir -p -- "$build_dir"
exec 9>"$build_dir/setup.lock"
flock 9
package=$(sha256sum driver/source.lock.json | cut -d' ' -f1)
output=$build_dir/$package
if [ ! -f "$output/build.json" ]; then
    stage=$(mktemp -d "$build_dir/build.XXXXXXXX")
    trap 'rm -rf -- "$stage"' EXIT
    python3 build.py --output "$stage/result"
    mv -T -- "$stage/result" "$output"
fi
apply=--apply
if [ "${1:-}" = --plan ]; then
    shift
    apply=
fi
sudo python3 install.py --build "$output" $apply "$@"
