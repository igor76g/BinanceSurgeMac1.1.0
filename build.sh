#!/bin/bash
# Local build on a Mac. Requires Python 3.10+ with tkinter (the installer from python.org includes it).
# Usage:  bash build.sh
set -e
cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --windowed --name BinanceSurgeMonitor \
  --osx-bundle-identifier com.example.binancesurgemonitor run.py

codesign --force --deep --sign - dist/BinanceSurgeMonitor.app

rm -rf dmg_src BinanceSurgeMonitor.dmg
mkdir dmg_src
cp -R dist/BinanceSurgeMonitor.app dmg_src/
ln -s /Applications dmg_src/Applications
hdiutil create -volname "Binance Surge Monitor" -srcfolder dmg_src -ov -format UDZO BinanceSurgeMonitor.dmg
rm -rf dmg_src

echo
echo "Done: BinanceSurgeMonitor.dmg (and dist/BinanceSurgeMonitor.app)"
