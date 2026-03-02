# BMCU Flasher

Cross-platform flasher for BMCU (WCH ISP protocol).

- OS: Linux / Windows / macOS
- Modes:
  - USB (BMCU with CH340 on board, AutoDI)
  - TTL (pin header / external USB-Serial, manual BOOT+RESET)

## Download
Download prebuilt binaries from Releases:
- Windows: BMCU-Flasher-windows-x64.zip
- macOS: BMCU-Flasher-macos.zip
- Linux: BMCU-Flasher-linux-x64.tar.gz

## Firmware
Latest BMCU firmware:
https://github.com/jarczakpawel/BMCU-C-PJARCZAK

## Drivers (CH340)
If your system does not show a serial port for CH340:
- Drivers are available in the drivers/ folder.

Linux usually works out-of-the-box (kernel ch341).

## Usage
GUI:
- Run the app and click Help inside the program.

CLI (optional):
- USB (auto port by VID/PID):
```bash
  python3 bmcu_flasher.py firmware.bin --mode usb
```
- TTL (manual BOOT+RESET, port required):
```bash
  python3 bmcu_flasher.py firmware.bin --mode ttl --port /dev/ttyUSB0
```

## License
MIT - see LICENSE.
