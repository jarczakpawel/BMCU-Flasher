#!/usr/bin/env python3
import argparse
import os
import struct
import time
import hashlib
import zlib
import urllib.request
import urllib.parse
import serial
from serial.tools import list_ports

MAGIC_REQ = b"\x57\xAB"
MAGIC_RSP = b"\x55\xAA"

CMD_IDENTIFY   = 0xA1
CMD_ISP_END    = 0xA2
CMD_ISP_KEY    = 0xA3
CMD_ERASE      = 0xA4
CMD_PROGRAM    = 0xA5
CMD_VERIFY     = 0xA6
CMD_READ_CFG   = 0xA7
CMD_WRITE_CFG  = 0xA8
CMD_SET_BAUD   = 0xC5

BMCU_DEVICE_ID = 0x31
BMCU_DEVICE_TYPE = 0x19
BMCU_CFG_MASK = 0x1F

BMCU_SECTOR_SIZE = 1024
BMCU_CHUNK = 56

DEFAULT_VID = 0x1A86
DEFAULT_PID = 0x7523

REMOTE_VERSION_URL = "https://raw.githubusercontent.com/jarczakpawel/BMCU-C-PJARCZAK/refs/heads/main/version"
REMOTE_MANIFEST_URL = "https://raw.githubusercontent.com/jarczakpawel/BMCU-C-PJARCZAK/refs/heads/main/firmwares/manifest.txt"
REMOTE_FIRMWARE_BASE = "https://raw.githubusercontent.com/jarczakpawel/BMCU-C-PJARCZAK/refs/heads/main/firmwares/"

def u16_le(x: int) -> bytes:
    return struct.pack("<H", x & 0xFFFF)

def u32_le(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)

def checksum(payload: bytes) -> int:
    return sum(payload) & 0xFF

def pack_req(payload: bytes) -> bytes:
    return MAGIC_REQ + payload + bytes([checksum(payload)])

def build_identify(device_id: int, device_type: int) -> bytes:
    data = bytes([device_id & 0xFF, device_type & 0xFF]) + b"MCU ISP & WCH.CN"
    payload = bytes([CMD_IDENTIFY]) + u16_le(len(data)) + data
    return pack_req(payload)

def build_read_cfg(bit_mask: int) -> bytes:
    data = bytes([bit_mask & 0xFF, 0x00])
    payload = bytes([CMD_READ_CFG]) + u16_le(len(data)) + data
    return pack_req(payload)

CFG_MASK_RDPR_USER_DATA_WPR = 0x07
CH32V203C8_FLASH_KB = 64

def build_write_cfg(bit_mask: int, data: bytes) -> bytes:
    payload = bytes([CMD_WRITE_CFG]) + u16_le(2 + len(data)) + bytes([bit_mask & 0xFF, 0x00]) + data
    return pack_req(payload)

def build_isp_key(seed: bytes) -> bytes:
    payload = bytes([CMD_ISP_KEY]) + u16_le(len(seed)) + seed
    return pack_req(payload)

def build_erase(sectors: int) -> bytes:
    payload = bytes([CMD_ERASE]) + u16_le(4) + u32_le(sectors)
    return pack_req(payload)

def build_set_baud(baud: int) -> bytes:
    payload = bytes([CMD_SET_BAUD]) + u16_le(4) + u32_le(baud)
    return pack_req(payload)

def build_program(address: int, padding: int, data: bytes) -> bytes:
    ln = 4 + 1 + len(data)
    payload = bytes([CMD_PROGRAM]) + u16_le(ln) + u32_le(address) + bytes([padding & 0xFF]) + data
    return pack_req(payload)

def build_verify(address: int, padding: int, data: bytes) -> bytes:
    ln = 4 + 1 + len(data)
    payload = bytes([CMD_VERIFY]) + u16_le(ln) + u32_le(address) + bytes([padding & 0xFF]) + data
    return pack_req(payload)

def build_isp_end(reason: int) -> bytes:
    payload = bytes([CMD_ISP_END]) + u16_le(1) + bytes([reason & 0xFF])
    return pack_req(payload)

def calc_xor_key_seed(seed: bytes, uid_chk: int, chip_id: int) -> bytes:
    if len(seed) < 8:
        raise ValueError("seed too short")
    a = len(seed) // 5
    b = len(seed) // 7
    k0 = seed[b * 4] ^ uid_chk
    k1 = seed[a] ^ uid_chk
    k2 = seed[b] ^ uid_chk
    k3 = seed[b * 6] ^ uid_chk
    k4 = seed[b * 3] ^ uid_chk
    k5 = seed[a * 3] ^ uid_chk
    k6 = seed[b * 5] ^ uid_chk
    k7 = (k0 + (chip_id & 0xFF)) & 0xFF
    return bytes([k0, k1, k2, k3, k4, k5, k6, k7])

def calc_xor_key_uid(uid8: bytes, chip_id: int) -> bytes:
    s = sum(uid8) & 0xFF
    k = [s] * 8
    k[7] = (k[7] + (chip_id & 0xFF)) & 0xFF
    return bytes(k)

def xor_crypt(data: bytes, key8: bytes) -> bytes:
    return bytes((b ^ key8[i & 7]) for i, b in enumerate(data))

class WchIsp:
    def __init__(self, port: str, baud: int, parity: str, trace: bool):
        self.port = port
        self.baud = baud
        self.parity = parity
        self.trace = trace
        self.ser = None
        self._rx = bytearray()

    def open(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            timeout=0,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
            bytesize=serial.EIGHTBITS,
            parity=self.parity,
            stopbits=serial.STOPBITS_ONE,
        )

    def close(self):
        if self.ser:
            try:
                self.ser.dtr = True
                self.ser.rts = True
            except Exception:
                pass
            self.ser.close()
        self.ser = None
        self._rx.clear()

    def set_baud(self, baud: int):
        self.baud = baud
        self.ser.baudrate = baud

    def flush(self):
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._rx.clear()

    def _read_available(self):
        n = self.ser.in_waiting
        if n:
            self._rx += self.ser.read(n)

    def recv(self, expect_cmd: int, timeout_s: float):
        end = time.monotonic() + timeout_s
        while True:
            if time.monotonic() >= end:
                raise TimeoutError(f"timeout waiting for cmd=0x{expect_cmd:02x}")

            self._read_available()

            i = self._rx.find(MAGIC_RSP)
            if i >= 0:
                if i > 0:
                    del self._rx[:i]
                if len(self._rx) >= 2 + 4 + 1:
                    cmd = self._rx[2]
                    ln = self._rx[4] | (self._rx[5] << 8)
                    total = 2 + 4 + ln + 1
                    if len(self._rx) >= total:
                        frame = bytes(self._rx[:total])
                        del self._rx[:total]
                        pay = frame[2:-1]
                        if (sum(pay) & 0xFF) != frame[-1]:
                            continue
                        if cmd != expect_cmd:
                            continue
                        code = frame[3]
                        data = frame[6:-1]
                        if self.trace:
                            print(f"RX cmd=0x{cmd:02x} code=0x{code:02x} ln={ln} data={data.hex()}")
                        return code, data

            time.sleep(0.002)

    def txrx(self, pkt: bytes, expect_cmd: int, timeout_s: float):
        if self.trace:
            print(f"TX {pkt.hex()}")
        self.ser.write(pkt)
        return self.recv(expect_cmd, timeout_s)

def set_lines(isp: WchIsp, boot_is_dtr: bool, boot_val: bool, reset_val: bool):
    if boot_is_dtr:
        isp.ser.dtr = boot_val
        isp.ser.rts = reset_val
    else:
        isp.ser.rts = boot_val
        isp.ser.dtr = reset_val

def pulse_reset(isp: WchIsp, boot_is_dtr: bool, reset_assert: bool):
    if boot_is_dtr:
        isp.ser.rts = (not reset_assert)
        time.sleep(0.02)
        isp.ser.rts = reset_assert
    else:
        isp.ser.dtr = (not reset_assert)
        time.sleep(0.02)
        isp.ser.dtr = reset_assert

def autodi_try(isp: WchIsp, identify_pkt: bytes):
    combos = []
    for boot_is_dtr in (True, False):
        for boot_assert in (True, False):
            for reset_assert in (True, False):
                combos.append((boot_is_dtr, boot_assert, reset_assert))

    for boot_is_dtr, boot_assert, reset_assert in combos:
        try:
            isp.flush()
            set_lines(isp, boot_is_dtr, boot_assert, reset_assert)
            time.sleep(0.02)
            pulse_reset(isp, boot_is_dtr, reset_assert)
            time.sleep(0.06)
            isp.flush()
            code, data = isp.txrx(identify_pkt, CMD_IDENTIFY, 0.6)
            if code == 0x00 and len(data) >= 2:
                return (boot_is_dtr, boot_assert, reset_assert)
        except Exception:
            continue
    return None

def list_matching_ports(vid: int, pid: int):
    out = []
    for p in list_ports.comports():
        if p.vid is not None and p.pid is not None and int(p.vid) == vid and int(p.pid) == pid:
            out.append(p)
    return out

def list_all_ports():
    return list(list_ports.comports())

def auto_pick_port(vid: int, pid: int):
    ports = list_matching_ports(vid, pid)
    if not ports:
        return None
    return ports[0].device

def _log(log_cb, level: str, msg: str):
    if log_cb:
        log_cb(level, msg)

def flash_firmware(
    firmware_path: str,
    mode: str = "usb",
    port: str = "",
    vid: int = DEFAULT_VID,
    pid: int = DEFAULT_PID,
    flash_kb: int = 64,
    baud: int = 115200,
    fast_baud: int = 1000000,
    no_fast: bool = False,
    verify: bool = True,
    verify_every: int = 1,
    verify_last: bool = False,
    seed_len: int = 0x1E,
    seed_random: bool = False,
    parity: str = "N",
    trace: bool = False,
    log_cb=None,
    progress_cb=None,
):
    if mode not in ("usb", "ttl"):
        raise ValueError("bad mode")

    if not os.path.isfile(firmware_path):
        raise FileNotFoundError(firmware_path)

    if not port:
        if mode == "usb":
            port = auto_pick_port(vid, pid)
            if not port:
                raise RuntimeError(f"no port for vid=0x{vid:04x} pid=0x{pid:04x}")
        else:
            raise RuntimeError("ttl mode requires --port (no vid/pid filtering)")

    fw = open(firmware_path, "rb").read()
    blocks = (len(fw) + BMCU_CHUNK - 1) // BMCU_CHUNK
    fw_pad = fw + (b"\xFF" * (blocks * BMCU_CHUNK - len(fw)))

    verify_every = max(1, int(verify_every))

    parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
    if parity not in parity_map:
        raise RuntimeError("bad parity")
    parity_v = parity_map[parity]

    t0 = time.monotonic()
    isp = WchIsp(port, baud, parity_v, trace)
    isp.open()

    identify_pkt = build_identify(BMCU_DEVICE_ID, BMCU_DEVICE_TYPE)

    autodi = None
    if mode == "usb":
        _log(log_cb, "INFO", "autodi...")
        autodi = autodi_try(isp, identify_pkt)
        if not autodi:
            isp.close()
            raise RuntimeError("autodi failed (usb mode)")
        _log(log_cb, "INFO", f"autodi ok boot_is_dtr={int(autodi[0])} boot_assert={int(autodi[1])} reset_assert={int(autodi[2])}")
    else:
        _log(log_cb, "ACTION", "ttl mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...")

    _log(log_cb, "INFO", f"stage identify @ host_baud={isp.baud}")

    if mode == "ttl":
        end = time.monotonic() + 12.0
        last_err = None
        while True:
            try:
                isp.flush()
                code, data = isp.txrx(identify_pkt, CMD_IDENTIFY, 0.8)
                if code == 0x00 and len(data) >= 2:
                    _log(log_cb, "INFO", "bootloader detected (ISP active)")
                    break
                last_err = RuntimeError("identify bad response")
            except Exception as e:
                last_err = e
            if time.monotonic() >= end:
                isp.close()
                raise RuntimeError(f"identify failed (ttl). enter bootloader first. last={last_err}")
            time.sleep(0.15)
    else:
        code, data = isp.txrx(identify_pkt, CMD_IDENTIFY, 1.0)

    if code != 0x00 or len(data) < 2:
        isp.close()
        raise RuntimeError("identify failed")
    chip_id = data[0]
    chip_type = data[1]
    if chip_id != BMCU_DEVICE_ID or chip_type != BMCU_DEVICE_TYPE:
        isp.close()
        raise RuntimeError(f"unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}")
    _log(log_cb, "INFO", f"identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}")

    if int(flash_kb) != CH32V203C8_FLASH_KB:
        _log(log_cb, "WARN", f"flash_kb forced to {CH32V203C8_FLASH_KB} (CH32V203C8T6)")
    flash_kb = CH32V203C8_FLASH_KB
    flash_bytes = flash_kb * 1024

    _log(log_cb, "INFO", "stage read_cfg (A7)")
    code, cfg = isp.txrx(build_read_cfg(BMCU_CFG_MASK), CMD_READ_CFG, 1.2)
    if code != 0x00 or len(cfg) < 14:
        isp.close()
        raise RuntimeError("read_cfg failed")

    cfg12 = bytearray(cfg[2:14])
    wpr = bytes(cfg12[8:12])

    uid = cfg[-8:] if len(cfg) >= 8 else b""
    if len(uid) == 8:
        _log(log_cb, "INFO", f"uid={uid.hex('-')}")
    _log(log_cb, "INFO", f"cfg_rdpr_user={cfg12[0:4].hex()} cfg_data={cfg12[4:8].hex()} cfg_wpr={wpr.hex()}")

    # WCHTool preamble (USB+TTL):
    # A8(step1) -> A7 -> A2(01) -> re-enter ISP -> (A1+A7)x2 -> A8(step2) -> A7
    _log(log_cb, "INFO", "wchtool: stage write_cfg step1 (A8)")
    cfg12_a = bytearray(cfg12)
    cfg12_a[0:4] = b"\xA5\x5A\x3F\xC0"
    cfg12_a[4:8] = b"\x00\xFF\x00\xFF"
    cfg12_a[8:12] = b"\xFF\xFF\xFF\xFF"

    code, _ = isp.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12_a)), CMD_WRITE_CFG, 2.0)
    if code != 0x00:
        isp.close()
        raise RuntimeError("write_cfg (wchtool step1) failed")

    code, cfg = isp.txrx(build_read_cfg(BMCU_CFG_MASK), CMD_READ_CFG, 1.2)
    if code != 0x00 or len(cfg) < 14:
        isp.close()
        raise RuntimeError("read_cfg after write_cfg (wchtool step1) failed")

    cfg12 = bytearray(cfg[2:14])
    wpr = bytes(cfg12[8:12])
    uid = cfg[-8:] if len(cfg) >= 8 else b""
    if len(uid) == 8:
        _log(log_cb, "INFO", f"uid={uid.hex('-')}")
    _log(log_cb, "INFO", f"cfg_after_step1 rdpr_user={cfg12[0:4].hex()} cfg_data={cfg12[4:8].hex()} cfg_wpr={wpr.hex()}")

    _log(log_cb, "INFO", "wchtool: stage isp_end reason=0x01 (apply option bytes)")
    try:
        isp.txrx(build_isp_end(1), CMD_ISP_END, 1.2)
    except Exception:
        pass

    if mode == "usb":
        if not autodi:
            isp.close()
            raise RuntimeError("autodi missing (usb)")
        _log(log_cb, "INFO", "usb: re-enter bootloader (autodi)")
        end = time.monotonic() + 2.5
        last_err = None
        while True:
            try:
                isp.flush()
                set_lines(isp, autodi[0], autodi[1], autodi[2])
                time.sleep(0.02)
                pulse_reset(isp, autodi[0], autodi[2])
                time.sleep(0.08)
                isp.flush()
                code2, data2 = isp.txrx(identify_pkt, CMD_IDENTIFY, 0.8)
                if code2 == 0x00 and len(data2) >= 2:
                    break
                last_err = RuntimeError("identify bad response")
            except Exception as e:
                last_err = e
            if time.monotonic() >= end:
                isp.close()
                raise RuntimeError(f"identify failed (usb) after isp_end(01). last={last_err}")
            time.sleep(0.10)
    else:
        _log(log_cb, "ACTION", "ttl: re-enter bootloader now (hold BOOT, tap RESET). waiting for ISP...")
        end = time.monotonic() + 12.0
        last_err = None
        while True:
            try:
                isp.flush()
                code2, data2 = isp.txrx(identify_pkt, CMD_IDENTIFY, 0.8)
                if code2 == 0x00 and len(data2) >= 2:
                    break
                last_err = RuntimeError("identify bad response")
            except Exception as e:
                last_err = e
            if time.monotonic() >= end:
                isp.close()
                raise RuntimeError(f"identify failed (ttl) after isp_end(01). last={last_err}")
            time.sleep(0.15)

    _log(log_cb, "INFO", "bootloader detected again (after isp_end(01))")

    for _ in range(2):
        code2, data2 = isp.txrx(identify_pkt, CMD_IDENTIFY, 1.0)
        if code2 != 0x00 or len(data2) < 2:
            isp.close()
            raise RuntimeError("identify failed after re-enter (wchtool)")
        code, cfg = isp.txrx(build_read_cfg(BMCU_CFG_MASK), CMD_READ_CFG, 1.2)
        if code != 0x00 or len(cfg) < 14:
            isp.close()
            raise RuntimeError("read_cfg failed after re-enter (wchtool)")

    cfg12 = bytearray(cfg[2:14])
    wpr = bytes(cfg12[8:12])
    uid = cfg[-8:] if len(cfg) >= 8 else b""
    if len(uid) == 8:
        _log(log_cb, "INFO", f"uid={uid.hex('-')}")
    _log(log_cb, "INFO", f"cfg_after_reenter rdpr_user={cfg12[0:4].hex()} cfg_data={cfg12[4:8].hex()} cfg_wpr={wpr.hex()}")

    _log(log_cb, "INFO", "wchtool: stage write_cfg step2 (A8)")
    cfg12_b = bytearray(cfg12)
    cfg12_b[0:4] = b"\xFF\xFF\x3F\xC0"
    cfg12_b[4:8] = b"\x00\x00\x00\x00"
    cfg12_b[8:12] = b"\xFF\xFF\xFF\xFF"

    code, _ = isp.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12_b)), CMD_WRITE_CFG, 2.0)
    if code != 0x00:
        isp.close()
        raise RuntimeError("write_cfg (wchtool step2) failed")

    code, cfg = isp.txrx(build_read_cfg(BMCU_CFG_MASK), CMD_READ_CFG, 1.2)
    if code != 0x00 or len(cfg) < 14:
        isp.close()
        raise RuntimeError("read_cfg after write_cfg (wchtool step2) failed")

    cfg12 = bytearray(cfg[2:14])
    wpr = bytes(cfg12[8:12])
    uid = cfg[-8:] if len(cfg) >= 8 else b""
    if len(uid) == 8:
        _log(log_cb, "INFO", f"uid={uid.hex('-')}")
    _log(log_cb, "INFO", f"cfg_after_step2 rdpr_user={cfg12[0:4].hex()} cfg_data={cfg12[4:8].hex()} cfg_wpr={wpr.hex()}")

    _log(log_cb, "INFO", "stage isp_key (A3)")
    if seed_random:
        seed = os.urandom(int(seed_len))
    else:
        seed = b"\x00" * int(seed_len)

    code, kresp = isp.txrx(build_isp_key(seed), CMD_ISP_KEY, 1.2)
    if code != 0x00 or len(kresp) < 1:
        isp.close()
        raise RuntimeError("isp_key failed")
    boot_sum = kresp[0] & 0xFF

    uid_chk = cfg[2]
    candidates = []
    if len(uid) == 8:
        candidates.append(("uid", calc_xor_key_uid(uid, chip_id)))
    try:
        candidates.append(("seed", calc_xor_key_seed(seed, uid_chk, chip_id)))
    except Exception:
        pass

    picked = [c for c in candidates if ((sum(c[1]) & 0xFF) == boot_sum)]
    if not picked:
        msg = f"isp_key checksum mismatch: boot=0x{boot_sum:02x} "
        for name, key in candidates:
            msg += f"{name}=0x{(sum(key)&0xFF):02x} "
        isp.close()
        raise RuntimeError(msg.strip())

    picked.sort(key=lambda x: 0 if x[0] == "uid" else 1)
    key_src, xor_key = picked[0]
    _log(log_cb, "INFO", f"isp_key ok (src={key_src} key_sum=0x{boot_sum:02x})")
    _log(log_cb, "INFO", "unlock ok")

    if wpr != b"\xFF\xFF\xFF\xFF":
        _log(log_cb, "WARN", f"code flash protected (WPR={wpr.hex()}) -> clearing WPR + RDPR")
        cfg12[0] = 0xA5
        cfg12[1] = 0x5A
        cfg12[8:12] = b"\xFF\xFF\xFF\xFF"

        code, _ = isp.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12)), CMD_WRITE_CFG, 2.0)
        if code != 0x00:
            isp.close()
            raise RuntimeError("write_cfg (unprotect) failed")

        time.sleep(0.08)
        code, cfg2 = isp.txrx(build_read_cfg(BMCU_CFG_MASK), CMD_READ_CFG, 1.2)
        if code != 0x00 or len(cfg2) < 14:
            isp.close()
            raise RuntimeError("read_cfg after unprotect failed")

        wpr2 = bytes(cfg2[2+8:2+12])
        _log(log_cb, "INFO", f"cfg_wpr(after)={wpr2.hex()}")

        if wpr2 != b"\xFF\xFF\xFF\xFF":
            isp.close()
            raise RuntimeError("WPR still not cleared (needs reset/power-cycle to apply option bytes). Re-enter bootloader and retry.")

        _log(log_cb, "INFO", "unprotect ok")

    _log(log_cb, "INFO", f"stage erase sectors={flash_kb} (full erase)")
    code, _ = isp.txrx(build_erase(flash_kb), CMD_ERASE, 12.0)
    if code != 0x00:
        isp.close()
        raise RuntimeError("erase failed")
    _log(log_cb, "INFO", "erase ok")

    tail_addr = ((flash_bytes - BMCU_CHUNK) // BMCU_CHUNK) * BMCU_CHUNK
    ff_enc = xor_crypt(b"\xFF" * BMCU_CHUNK, xor_key)
    code, _ = isp.txrx(build_verify(tail_addr, 0x00, ff_enc), CMD_VERIFY, 1.8)
    if code != 0x00:
        isp.close()
        raise RuntimeError(f"erase incomplete (tail not erased) addr=0x{tail_addr:08x}")

    if not no_fast:
        _log(log_cb, "INFO", f"stage set_baud mcu={fast_baud}")
        code, _ = isp.txrx(build_set_baud(int(fast_baud)), CMD_SET_BAUD, 1.2)
        if code != 0x00:
            isp.close()
            raise RuntimeError("set_baud failed")
        time.sleep(0.03)
        isp.set_baud(int(fast_baud))
        isp.flush()
        _log(log_cb, "INFO", f"set_baud ok host_baud={isp.baud}")

    pad_byte = 0x00
    fw_end = blocks * BMCU_CHUNK
    _log(log_cb, "INFO", f"stage program addr=0x00000000..0x{fw_end:08x} blocks={blocks} chunk={BMCU_CHUNK} verify={'on' if verify else 'off'} every={verify_every}{' +last' if verify_last else ''}")
    tprog = time.monotonic()

    if progress_cb:
        progress_cb(0, 0, blocks)

    last_ui_pct = -1
    last_log_pct = -1
    for i in range(blocks):
        addr = i * BMCU_CHUNK
        plain = fw_pad[addr:addr+BMCU_CHUNK]
        enc = xor_crypt(plain, xor_key)

        code, _ = isp.txrx(build_program(addr, pad_byte, enc), CMD_PROGRAM, 1.8)
        if code != 0x00:
            isp.close()
            raise RuntimeError(f"program failed at 0x{addr:08x}")

        stage_pct = (i + 1) * 100 // blocks
        ui_pct = (i + 1) * 50 // blocks

        if ui_pct != last_ui_pct:
            last_ui_pct = ui_pct
            if progress_cb:
                progress_cb(ui_pct, i + 1, blocks)

        if log_cb and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
            last_log_pct = stage_pct
            _log(log_cb, "INFO", f"program {stage_pct}% addr=0x{(i+1)*BMCU_CHUNK:08x}")

    flush_addr = blocks * BMCU_CHUNK
    _log(log_cb, "INFO", f"stage program_flush addr=0x{flush_addr:08x} (A5 empty)")
    code, _ = isp.txrx(build_program(flush_addr, pad_byte, b""), CMD_PROGRAM, 2.2)
    if code != 0x00:
        isp.close()
        raise RuntimeError("program_flush failed")

    if progress_cb:
        progress_cb(50, blocks, blocks)

    dt = time.monotonic() - tprog
    kb = len(fw) / 1024.0
    _log(log_cb, "INFO", f"program done in {dt:.3f}s ({kb/dt:.1f} KiB/s)")

    if verify:
        _log(log_cb, "INFO", "stage isp_key (A3) before verify")
        code, kresp2 = isp.txrx(build_isp_key(seed), CMD_ISP_KEY, 1.2)
        if code != 0x00 or len(kresp2) < 1 or ((kresp2[0] & 0xFF) != boot_sum):
            isp.close()
            raise RuntimeError("isp_key before verify failed")

        verify_indices = []
        for i in range(blocks):
            is_last = (i == blocks - 1)
            do_this = ((i % verify_every) == 0 and not is_last) or (verify_last and is_last)
            if do_this:
                verify_indices.append(i)

        if not verify_indices:
            _log(log_cb, "INFO", "stage verify skipped (no blocks selected)")
            if progress_cb:
                progress_cb(100, blocks, blocks)
        else:
            _log(log_cb, "INFO", f"stage verify blocks={len(verify_indices)}/{blocks}")
            last_ui_pct = -1
            last_log_pct = -1
            vtotal = len(verify_indices)

            for j, i in enumerate(verify_indices):
                addr = i * BMCU_CHUNK
                plain = fw_pad[addr:addr+BMCU_CHUNK]
                enc = xor_crypt(plain, xor_key)

                code, _ = isp.txrx(build_verify(addr, pad_byte, enc), CMD_VERIFY, 1.8)
                if code != 0x00:
                    isp.close()
                    raise RuntimeError(f"verify failed at 0x{addr:08x}")

                stage_pct = (j + 1) * 100 // vtotal
                ui_pct = 50 + ((j + 1) * 50 // vtotal)

                if ui_pct != last_ui_pct:
                    last_ui_pct = ui_pct
                    if progress_cb:
                        progress_cb(ui_pct, j + 1, vtotal)

                if log_cb and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    _log(log_cb, "INFO", f"verify {stage_pct}% addr=0x{addr:08x}")

            _log(log_cb, "INFO", "verify ok")

    _log(log_cb, "INFO", "stage isp_end")
    try:
        isp.txrx(build_isp_end(0), CMD_ISP_END, 1.2)
    except Exception:
        pass

    if mode == "usb" and autodi:
        set_lines(isp, autodi[0], (not autodi[1]), autodi[2])
        time.sleep(0.02)
        pulse_reset(isp, autodi[0], autodi[2])

    isp.close()
    if progress_cb:
        progress_cb(100, blocks, blocks)
    _log(log_cb, "INFO", f"OK total={time.monotonic()-t0:.3f}s")

def _http_get(url: str, timeout_s: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "BMCUFlasher/1.2"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return r.read()

def remote_get_version(version_url: str = REMOTE_VERSION_URL, timeout_s: float = 12.0) -> str:
    b = _http_get(version_url, timeout_s=timeout_s)
    s = b.decode("utf-8", "replace").strip()
    if not s:
        raise RuntimeError("empty version")

    parts = s.split(".")
    if len(parts) < 2:
        raise RuntimeError(f"bad version: {s!r}")

    a = parts[0].strip()
    b2 = parts[1].strip()
    if not a.isdigit() or not b2.isdigit():
        raise RuntimeError(f"bad version: {s!r}")

    major = int(a)
    minor_raw = int(b2)

    if minor_raw == 0:
        return f"V{major}"
    if (minor_raw % 10) == 0:
        return f"V{major}.{minor_raw // 10}"
    return f"V{major}.{minor_raw}"

def remote_parse_manifest(text: str) -> dict:
    m = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(None, 3)
        if len(parts) != 4:
            continue
        sha, crc, size_s, rel = parts
        sha = sha.strip().lower()
        crc = crc.strip().upper()
        try:
            size = int(size_s.strip())
        except Exception:
            continue
        if len(sha) != 64:
            continue
        if len(crc) != 8:
            continue
        m[rel.strip()] = (sha, crc, size)
    if not m:
        raise RuntimeError("manifest empty")
    return m

def remote_get_manifest(manifest_url: str = REMOTE_MANIFEST_URL, timeout_s: float = 20.0) -> dict:
    b = _http_get(manifest_url, timeout_s=timeout_s)
    s = b.decode("utf-8", "replace")
    return remote_parse_manifest(s)

def _file_digest(path: str):
    h = hashlib.sha256()
    crc = 0
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            size += len(b)
            crc = zlib.crc32(b, crc)
            h.update(b)
    return h.hexdigest(), f"{crc & 0xffffffff:08X}", size

def remote_download_firmware(
    rel_path: str,
    cache_dir: str,
    manifest_url: str = REMOTE_MANIFEST_URL,
    firmware_base_url: str = REMOTE_FIRMWARE_BASE,
    version_url: str = REMOTE_VERSION_URL,
    timeout_s: float = 30.0,
    log_cb=None,
    progress_cb=None,
):
    if not rel_path or ".." in rel_path or rel_path.startswith("/") or rel_path.startswith("\\"):
        raise RuntimeError("bad rel_path")

    ver = remote_get_version(version_url=version_url, timeout_s=12.0)
    _log(log_cb, "INFO", f"online: version={ver}")

    man = remote_get_manifest(manifest_url=manifest_url, timeout_s=20.0)
    if rel_path not in man:
        raise RuntimeError(f"rel_path not in manifest: {rel_path}")
    exp_sha, exp_crc, exp_size = man[rel_path]

    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, f"{exp_sha}.bin")

    if os.path.isfile(out_path):
        sha, crc, size = _file_digest(out_path)
        if sha.lower() == exp_sha and crc.upper() == exp_crc and int(size) == int(exp_size):
            _log(log_cb, "INFO", f"online: cache hit ({os.path.basename(out_path)})")
            if progress_cb:
                progress_cb(100, 0, 0)
            return out_path, ver

    url = firmware_base_url + urllib.parse.quote(rel_path, safe="/")
    _log(log_cb, "INFO", f"online: download {rel_path}")
    _log(log_cb, "INFO", f"online: url {url}")

    tmp_path = out_path + ".part"

    h = hashlib.sha256()
    crc = 0
    size = 0
    last_pct = -1

    req = urllib.request.Request(url, headers={"User-Agent": "BMCUFlasher/1.2"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r, open(tmp_path, "wb") as f:
            clen = r.headers.get("Content-Length")
            total = None
            if clen:
                try:
                    total = int(clen)
                except Exception:
                    total = None
            if total is None and exp_size > 0:
                total = int(exp_size)

            while True:
                b = r.read(64 * 1024)
                if not b:
                    break
                f.write(b)
                size += len(b)
                crc = zlib.crc32(b, crc)
                h.update(b)

                if progress_cb and total:
                    pct = int(size * 100 / total) if total > 0 else 0
                    if pct > 100:
                        pct = 100
                    if pct != last_pct:
                        last_pct = pct
                        progress_cb(pct, 0, 0)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise

    sha_hex = h.hexdigest().lower()
    crc_hex = f"{crc & 0xffffffff:08X}"

    if int(size) != int(exp_size) or crc_hex.upper() != exp_crc.upper() or sha_hex != exp_sha:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise RuntimeError(f"online: verify failed size={size}/{exp_size} crc={crc_hex}/{exp_crc} sha={sha_hex[:12]}.../{exp_sha[:12]}...")

    os.replace(tmp_path, out_path)
    _log(log_cb, "INFO", f"online: ok size={size} crc={crc_hex} sha={sha_hex}")
    if progress_cb:
        progress_cb(100, 0, 0)
    return out_path, ver

def main():
    ap = argparse.ArgumentParser(prog="bmcu_flasher.py")
    ap.add_argument("firmware")
    ap.add_argument("--mode", choices=["usb", "ttl"], default="usb")
    ap.add_argument("--port", default="")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=DEFAULT_VID)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=DEFAULT_PID)
    ap.add_argument("--list", action="store_true")

    ap.add_argument("--flash-kb", type=int, default=64)

    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--fast-baud", type=int, default=1000000)
    ap.add_argument("--no-fast", action="store_true")

    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--verify-every", type=int, default=1)
    ap.add_argument("--verify-last", action="store_true")

    ap.add_argument("--seed-len", type=lambda x: int(x, 0), default=0x1E)
    ap.add_argument("--seed-random", action="store_true")

    ap.add_argument("--parity", choices=["N", "E", "O"], default="N")
    ap.add_argument("--trace", action="store_true")

    ap.add_argument("--erase", choices=["all"], default="all")

    args = ap.parse_args()

    if args.list:
        if args.mode == "usb":
            ports = list_matching_ports(args.vid, args.pid)
        else:
            ports = list_all_ports()
        if not ports:
            print("no ports")
            return
        for p in ports:
            vid = f"0x{p.vid:04x}" if p.vid is not None else "----"
            pid = f"0x{p.pid:04x}" if p.pid is not None else "----"
            print(f"{p.device} - {p.description} (vid={vid} pid={pid})")
        return

    def log_cb(level, msg):
        print(msg)

    def prog_cb(pct, done, total):
        pass

    flash_firmware(
        firmware_path=args.firmware,
        mode=args.mode,
        port=args.port.strip(),
        vid=args.vid,
        pid=args.pid,
        flash_kb=args.flash_kb,
        baud=args.baud,
        fast_baud=args.fast_baud,
        no_fast=bool(args.no_fast),
        verify=(not args.no_verify),
        verify_every=max(1, int(args.verify_every)),
        verify_last=bool(args.verify_last),
        seed_len=int(args.seed_len),
        seed_random=bool(args.seed_random),
        parity=args.parity,
        trace=bool(args.trace),
        log_cb=log_cb,
        progress_cb=prog_cb,
    )

if __name__ == "__main__":
    main()
