from __future__ import annotations
import json, subprocess, time
from dataclasses import dataclass
from typing import Optional

try:
    import hid
except Exception:
    hid = None

@dataclass
class BatteryDevice:
    key: str
    name: str
    percent: Optional[int]
    connection: str
    status: str
    detail: str = ""

def _clamp(v):
    try:
        return max(0, min(100, int(round(v))))
    except Exception:
        return None

class WindowsBluetoothProvider:
    """Reads Windows Bluetooth battery data and keeps JBL devices visible."""
    SCRIPT = r"""
$items = @()
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
  $d = $_
  $id = [string]$d.InstanceId
  $name = [string]$d.FriendlyName
  $isBt = ($d.Class -eq 'Bluetooth') -or ($id -like 'BTH*')
  $isJbl = $name -match '(?i)\bJBL\b'
  if (-not $isBt -and -not $isJbl) { return }

  $props = Get-PnpDeviceProperty -InstanceId $d.InstanceId -ErrorAction SilentlyContinue
  $bat = $props | Where-Object {
    $_.KeyName -eq 'DEVPKEY_Bluetooth_BatteryPercent' -or
    $_.KeyName -match '104ea319-6ee2-4701-bd47-8ddbf425bbe5'
  } | Select-Object -First 1

  $battery = $null
  if ($bat -and $null -ne $bat.Data) { $battery = [int]$bat.Data }

  $items += [pscustomobject]@{
    name = $name
    id = $id
    class = [string]$d.Class
    status = [string]$d.Status
    battery = $battery
    is_jbl = [bool]$isJbl
  }
}
$items | ConvertTo-Json -Compress -Depth 4
"""
    def raw(self):
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", self.SCRIPT],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            if p.returncode != 0 or not p.stdout.strip():
                return []
            raw = json.loads(p.stdout)
            if isinstance(raw, dict):
                raw = [raw]
            return raw
        except Exception:
            return []

    def read(self):
        devices = []
        jbl_seen = set()
        for x in self.raw():
            name = (x.get("name") or "").strip()
            pct = _clamp(x.get("battery")) if x.get("battery") is not None else None
            if pct is not None:
                devices.append(BatteryDevice(
                    "bt:" + str(x.get("id")), name or "Dispositivo Bluetooth", pct,
                    "Bluetooth", "Conectado", "Porcentaje publicado por Windows"
                ))
                continue
            if x.get("is_jbl") and name and name.lower() not in jbl_seen:
                jbl_seen.add(name.lower())
                devices.append(BatteryDevice(
                    "jbl:" + name.lower(), name, None, "Bluetooth", "Detectado",
                    "Windows ve el JBL, pero no publica su porcentaje de batería por la propiedad estándar."
                ))
        return devices

class RazerDeathAdderV2ProProvider:
    VID = 0x1532
    PID = 0x007D

    def _command(self):
        report = bytearray(90)
        report[0] = 0x00
        report[1] = 0x3F
        report[5] = 0x02
        report[6] = 0x07
        report[7] = 0x80
        crc = 0
        for b in report[2:88]:
            crc ^= b
        report[88] = crc
        return bytes([0]) + bytes(report)

    def read(self):
        if hid is None:
            return []
        candidates = [
            d for d in hid.enumerate(self.VID, self.PID)
            if d.get("interface_number") in (0, -1)
            and d.get("usage_page") in (1, None)
            and d.get("usage") in (2, None)
        ]
        if not candidates:
            candidates = hid.enumerate(self.VID, self.PID)
        for info in candidates:
            dev = None
            try:
                dev = hid.device()
                dev.open_path(info["path"])
                dev.set_nonblocking(0)
                request = self._command()
                for _ in range(3):
                    dev.send_feature_report(request)
                    time.sleep(.08)
                    resp = bytes(dev.get_feature_report(0, 91))
                    if len(resp) >= 91:
                        data = resp[1:91]
                    elif len(resp) >= 90:
                        data = resp[:90]
                    else:
                        continue
                    if data[0] == 0x02 and data[6] == 0x07 and data[7] == 0x80:
                        pct = _clamp(data[9] * 100 / 255)
                        return [BatteryDevice(
                            "razer:deathadder-v2-pro", "Razer DeathAdder V2 Pro", pct,
                            "Dongle 2.4 GHz", "Conectado", "Lectura HID nativa"
                        )]
            except Exception:
                pass
            finally:
                try:
                    if dev:
                        dev.close()
                except Exception:
                    pass
        if hid.enumerate(self.VID, self.PID):
            return [BatteryDevice(
                "razer:deathadder-v2-pro", "Razer DeathAdder V2 Pro", None,
                "Dongle 2.4 GHz", "Detectado",
                "No se pudo leer batería; cerrá Synapse/OpenRGB si bloquea HID."
            )]
        return []

def _interpolate_curve(voltage, curve):
    if voltage < curve[0][0]:
        return None
    if voltage >= curve[-1][0]:
        return curve[-1][1]
    for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
        if v0 <= voltage <= v1:
            span = v1 - v0
            return _clamp(p0 + (voltage - v0) * (p1 - p0) / span)
    return None

class LogitechG935Provider:
    VID = 0x046D
    PID = 0x0A87
    CURVE = [
        (3150, 0), (3300, 5), (3500, 10), (3650, 20),
        (3750, 40), (3850, 60), (3950, 80), (4100, 100)
    ]

    def read(self):
        if hid is None:
            return []
        found = hid.enumerate(self.VID, self.PID)
        preferred = [d for d in found if d.get("usage_page") in (0xFF43, 0xFF00)]
        candidates = preferred + [d for d in found if d not in preferred]
        for info in candidates:
            dev = None
            try:
                dev = hid.device()
                dev.open_path(info["path"])
                dev.set_nonblocking(0)
                try:
                    while dev.read(64, 5):
                        pass
                except Exception:
                    pass

                req = [0x11, 0xFF, 0x08, 0x0A] + [0] * 16
                dev.write(req)
                for _ in range(5):
                    resp = bytes(dev.read(20, 400))
                    if len(resp) < 7:
                        continue
                    if resp[2] == 0xFF:
                        continue
                    if resp[2] != 0x08 or resp[3] != 0x0A:
                        continue
                    voltage = (resp[4] << 8) | resp[5]
                    pct = _interpolate_curve(voltage, self.CURVE)
                    if pct is None:
                        continue
                    charging = resp[6] == 0x03
                    status = "Cargando" if charging else "Conectado"
                    return [BatteryDevice(
                        "logitech:g935", "Logitech G935 Gaming Headset", pct,
                        "Dongle USB", status,
                        f"Lectura HID nativa · {voltage} mV"
                    )]
            except Exception:
                pass
            finally:
                try:
                    if dev:
                        dev.close()
                except Exception:
                    pass

        if found:
            return [BatteryDevice(
                "logitech:g935", "Logitech G935 Gaming Headset", None,
                "Dongle USB", "Detectado",
                "Receptor 046D:0A87 detectado, pero no respondió a la consulta de batería."
            )]
        return []

class RedragonFizzProvider:
    VID = 0x25A7
    PID = 0xFA70

    def read(self):
        if hid is None:
            return []
        found = hid.enumerate(self.VID, self.PID)
        if found:
            vendor_pages = sorted({
                d.get("usage_page") for d in found
                if isinstance(d.get("usage_page"), int) and d.get("usage_page") >= 0xFF00
            })
            pages = ", ".join(f"0x{x:04X}" for x in vendor_pages) or "sin página propietaria"
            return [BatteryDevice(
                "redragon:k616", "Redragon Fizz Pro K616", None,
                "Dongle 2.4 GHz", "Detectado",
                f"Receptor real 25A7:FA70 detectado ({pages}). Falta identificar el reporte propietario de batería."
            )]
        return []

class BatteryManager:
    def __init__(self):
        self.bluetooth = WindowsBluetoothProvider()
        self.providers = [
            RazerDeathAdderV2ProProvider(),
            LogitechG935Provider(),
            self.bluetooth,
            RedragonFizzProvider(),
        ]

    def refresh(self):
        merged = {}
        for provider in self.providers:
            try:
                for d in provider.read():
                    if d.key not in merged or (merged[d.key].percent is None and d.percent is not None):
                        merged[d.key] = d
            except Exception:
                pass
        return list(merged.values())

    def diagnostic(self):
        result = {
            "hid": [],
            "bluetooth_battery": [],
            "bluetooth_devices": [],
            "redragon_fizz_receiver": [],
            "version": 2,
        }
        if hid:
            for d in hid.enumerate():
                item = {
                    k: (v.decode(errors="replace") if isinstance(v, bytes) else v)
                    for k, v in d.items()
                    if k in (
                        "vendor_id", "product_id", "manufacturer_string", "product_string",
                        "serial_number", "interface_number", "usage_page", "usage", "path"
                    )
                }
                result["hid"].append(item)
                if item.get("vendor_id") == 0x25A7 and item.get("product_id") == 0xFA70:
                    result["redragon_fizz_receiver"].append(item)

        bt_raw = self.bluetooth.raw()
        result["bluetooth_devices"] = bt_raw
        result["bluetooth_battery"] = [
            x for x in bt_raw if x.get("battery") is not None
        ]
        return result
