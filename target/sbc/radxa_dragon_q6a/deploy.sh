#!/bin/sh
# Copy the self-contained package and run the same interactive setup over SSH.
set -eu
host=${1:-${Q6A:-radxa@radxa-dragon-q6a.local}}
if [ "$#" -gt 0 ]; then shift; fi
case "$*" in
    ''|--replace|--plan) ;;
    *) echo "Usage: sh deploy.sh [ssh-host] [--plan|--replace]" >&2; exit 2 ;;
esac
dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
stage=$(ssh -- "$host" 'mkdir -p ~/q6a-tft50 && cd ~/q6a-tft50 && mktemp -d kit.XXXXXXXX')
case "$stage" in kit.????????) ;; *) exit 1;; esac
case "$stage" in *[!a-zA-Z0-9.]*) exit 1;; esac
scp -r -- "$dir/driver" "$dir/build.py" "$dir/install.py" "$dir/setup.sh" "$dir"/*.dts "${host}:q6a-tft50/$stage/"
echo "Q6A package: ~/q6a-tft50/$stage"
ssh -t -- "$host" "cd ~/q6a-tft50/$stage && sh setup.sh $*"
