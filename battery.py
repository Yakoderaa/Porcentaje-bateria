from __future__ import annotations
import json, re, subprocess, time
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
    device_type: str = ""

def _clamp(v):
    try:
        return max(0, min(100, int(round(v))))
    except Exception:
        return None

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")

def _canonical_bt_name(name):
    name=(name or "").strip()
    if not name:
        return ""
    if re.search(r"(?i)(transporte\s+avrcp|avrcp\s+transport)",name):
        return ""
    wrappers=[
        r"(?i)^Altavoces\s*\((?:\d+\s*[-–]\s*)?(.+)\)$",
        r"(?i)^Auriculares\s*\((?:\d+\s*[-–]\s*)?(.+)\)$",
        r"(?i)^Headphones\s*\((?:\d+\s*[-–]\s*)?(.+)\)$",
        r"(?i)^Speakers\s*\((?:\d+\s*[-–]\s*)?(.+)\)$",
    ]
    for pattern in wrappers:
        m=re.match(pattern,name)
        if m:
            return m.group(1).strip()
    return name

def _bluetooth_type(name, pnp_class):
    n=(name or "").lower()
    c=(pnp_class or "").lower()
    if "keyboard" in c or re.search(r"(^|\W)kb($|\W)",n):
        return "Teclado Bluetooth"
    if "mouse" in c or "mouse" in n:
        return "Mouse Bluetooth"
    if any(x in n for x in ("jbl","speaker","altavoz","parlante","soundbar")):
        return "Audio Bluetooth"
    if any(x in n for x in ("headset","headphone","auricular","buds","earbuds")):
        return "Auriculares Bluetooth"
    if "phone" in n or "teléfono" in n or "telefono" in n:
        return "Teléfono Bluetooth"
    return "Dispositivo Bluetooth"

def _system_bt_name(name):
    n=(name or "").lower()
    noise=(
        "microsoft bluetooth enumerator",
        "enumerador bluetooth de microsoft",
        "bluetooth device (rfcomm",
        "dispositivo bluetooth (rfcomm",
        "generic bluetooth adapter",
        "adaptador bluetooth genérico",
        "intel(r) wireless bluetooth",
        "mediatek bluetooth adapter",
        "realtek bluetooth adapter",
    )
    return any(x in n for x in noise)

class WindowsBluetoothProvider:
    """Enumerates real Bluetooth devices and reads Windows battery percent when exposed."""
    SCRIPT = r"""
$items = @()
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
  $d = $_
  $id = [string]$d.InstanceId
  $name = [string]$d.FriendlyName
  $isBt = ($d.Class -eq 'Bluetooth') -or ($id -match '^(BTH|BTHENUM|BTHLE)')
  if (-not $isBt) { return }

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
  }
}
$items | ConvertTo-Json -Compress -Depth 4
"""
    def raw(self):
        try:
            p=subprocess.run(
                ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",self.SCRIPT],
                capture_output=True,text=True,timeout=15,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)
            )
            if p.returncode!=0 or not p.stdout.strip():
                return []
            raw=json.loads(p.stdout)
            return [raw] if isinstance(raw,dict) else raw
        except Exception:
            return []

    def catalog(self):
        grouped={}
        for x in self.raw():
            raw_name=(x.get("name") or "").strip()
            if not raw_name or _system_bt_name(raw_name):
                continue
            name=_canonical_bt_name(raw_name)
            if not name:
                continue
            key_name=name.lower()
            pct=_clamp(x.get("battery")) if x.get("battery") is not None else None
            pnp_class=x.get("class") or ""
            entry=grouped.get(key_name)
            if entry is None:
                entry={
                    "name":name,
                    "battery":pct,
                    "class":pnp_class,
                    "ids":[str(x.get("id") or "")],
                }
                grouped[key_name]=entry
            else:
                if entry["battery"] is None and pct is not None:
                    entry["battery"]=pct
                if not entry["class"] and pnp_class:
                    entry["class"]=pnp_class
                entry["ids"].append(str(x.get("id") or ""))

        devices=[]
        for entry in grouped.values():
            name=entry["name"]
            pct=entry["battery"]
            dtype=_bluetooth_type(name,entry["class"])
            detail=dtype
            if name.lower()=="bt5.0 kb":
                detail+=" · Nombre genérico anunciado por el propio dispositivo; probablemente es un teclado Bluetooth."
            elif pct is not None:
                detail+=" · Porcentaje publicado por Windows."
            else:
                detail+=" · Windows no publica porcentaje de batería para este dispositivo."
            devices.append(BatteryDevice(
                "bluetooth:"+_slug(name),
                name,
                pct,
                "Bluetooth",
                "Conectado",
                detail,
                dtype,
            ))
        return devices

class RazerDeathAdderV2ProProvider:
    VID=0x1532
    PID=0x007D

    def _command(self, command_id):
        report=bytearray(90)
        report[0]=0x00
        report[1]=0x3F
        report[5]=0x02
        report[6]=0x07
        report[7]=command_id
        crc=0
        for b in report[2:88]:
            crc^=b
        report[88]=crc
        return bytes([0])+bytes(report)

    def _query(self,dev,command_id):
        request=self._command(command_id)
        for _ in range(3):
            dev.send_feature_report(request)
            time.sleep(.08)
            resp=bytes(dev.get_feature_report(0,91))
            if len(resp)>=91:
                data=resp[1:91]
            elif len(resp)>=90:
                data=resp[:90]
            else:
                continue
            if data[0]==0x02 and data[6]==0x07 and data[7]==command_id:
                return data
        return None

    def read(self):
        if hid is None:
            return []
        candidates=[
            d for d in hid.enumerate(self.VID,self.PID)
            if d.get("interface_number") in (0,-1)
            and d.get("usage_page") in (1,None)
            and d.get("usage") in (2,None)
        ]
        if not candidates:
            candidates=hid.enumerate(self.VID,self.PID)
        for info in candidates:
            dev=None
            try:
                dev=hid.device()
                dev.open_path(info["path"])
                dev.set_nonblocking(0)
                battery=self._query(dev,0x80)
                if not battery:
                    continue
                pct=_clamp(battery[9]*100/255)
                charging_data=self._query(dev,0x84)
                charging=bool(charging_data and charging_data[9]!=0)
                return [BatteryDevice(
                    "razer:deathadder-v2-pro",
                    "Razer DeathAdder V2 Pro",
                    pct,
                    "Dongle 2.4 GHz",
                    "Cargando" if charging else "Conectado",
                    "Lectura HID nativa · estado de carga consultado al mouse.",
                    "Mouse inalámbrico",
                )]
            except Exception:
                pass
            finally:
                try:
                    if dev:
                        dev.close()
                except Exception:
                    pass
        if hid.enumerate(self.VID,self.PID):
            return [BatteryDevice(
                "razer:deathadder-v2-pro","Razer DeathAdder V2 Pro",None,
                "Dongle 2.4 GHz","Detectado",
                "No se pudo leer batería; cerrá Synapse/OpenRGB si bloquea HID.",
                "Mouse inalámbrico",
            )]
        return []

def _interpolate_curve(voltage,curve):
    if voltage<curve[0][0]:
        return None
    if voltage>=curve[-1][0]:
        return curve[-1][1]
    for (v0,p0),(v1,p1) in zip(curve,curve[1:]):
        if v0<=voltage<=v1:
            return _clamp(p0+(voltage-v0)*(p1-p0)/(v1-v0))
    return None

class LogitechG935Provider:
    VID=0x046D
    PID=0x0A87
    CURVE=[
        (3150,0),(3300,5),(3500,10),(3650,20),
        (3750,40),(3850,60),(3950,80),(4100,100)
    ]

    def read(self):
        if hid is None:
            return []
        found=hid.enumerate(self.VID,self.PID)
        preferred=[d for d in found if d.get("usage_page") in (0xFF43,0xFF00)]
        candidates=preferred+[d for d in found if d not in preferred]
        for info in candidates:
            dev=None
            try:
                dev=hid.device()
                dev.open_path(info["path"])
                dev.set_nonblocking(0)
                try:
                    while dev.read(64,5):
                        pass
                except Exception:
                    pass
                dev.write([0x11,0xFF,0x08,0x0A]+[0]*16)
                for _ in range(5):
                    resp=bytes(dev.read(20,400))
                    if len(resp)<7 or resp[2]==0xFF:
                        continue
                    if resp[2]!=0x08 or resp[3]!=0x0A:
                        continue
                    voltage=(resp[4]<<8)|resp[5]
                    pct=_interpolate_curve(voltage,self.CURVE)
                    if pct is None:
                        continue
                    charge_code=resp[6]
                    if charge_code==0x03:
                        status="Cargando"
                    elif charge_code==0x07:
                        status="Carga completa"
                    else:
                        status="Conectado"
                    return [BatteryDevice(
                        "logitech:g935","Logitech G935 Gaming Headset",pct,
                        "Dongle USB",status,
                        f"Lectura HID nativa · {voltage} mV · estado de carga leído del auricular.",
                        "Auriculares inalámbricos",
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
                "logitech:g935","Logitech G935 Gaming Headset",None,
                "Dongle USB","Detectado",
                "Receptor 046D:0A87 detectado, pero no respondió a la consulta de batería.",
                "Auriculares inalámbricos",
            )]
        return []

class RedragonFizzProvider:
    VID=0x25A7
    PID=0xFA70

    def read(self):
        if hid is None:
            return []
        found=hid.enumerate(self.VID,self.PID)
        if found:
            vendor_pages=sorted({
                d.get("usage_page") for d in found
                if isinstance(d.get("usage_page"),int) and d.get("usage_page")>=0xFF00
            })
            pages=", ".join(f"0x{x:04X}" for x in vendor_pages) or "sin página propietaria"
            return [BatteryDevice(
                "redragon:k616","Redragon Fizz Pro K616",None,
                "Dongle 2.4 GHz","Detectado",
                f"Receptor 25A7:FA70 detectado ({pages}). El protocolo propietario de batería/carga todavía no está identificado.",
                "Teclado inalámbrico",
            )]
        return []

class BatteryManager:
    def __init__(self):
        self.bluetooth=WindowsBluetoothProvider()
        self.native=[
            RazerDeathAdderV2ProProvider(),
            LogitechG935Provider(),
            RedragonFizzProvider(),
        ]

    def refresh(self):
        merged={}
        for provider in self.native:
            try:
                for d in provider.read():
                    merged[d.key]=d
            except Exception:
                pass
        try:
            for d in self.bluetooth.catalog():
                if d.key not in merged or (merged[d.key].percent is None and d.percent is not None):
                    merged[d.key]=d
        except Exception:
            pass
        return list(merged.values())

    def diagnostic(self):
        result={
            "hid":[],
            "bluetooth_devices":[],
            "bluetooth_catalog":[],
            "redragon_fizz_receiver":[],
            "version":3,
        }
        if hid:
            for d in hid.enumerate():
                item={
                    k:(v.decode(errors="replace") if isinstance(v,bytes) else v)
                    for k,v in d.items()
                    if k in (
                        "vendor_id","product_id","manufacturer_string","product_string",
                        "serial_number","interface_number","usage_page","usage","path"
                    )
                }
                result["hid"].append(item)
                if item.get("vendor_id")==0x25A7 and item.get("product_id")==0xFA70:
                    result["redragon_fizz_receiver"].append(item)
        result["bluetooth_devices"]=self.bluetooth.raw()
        result["bluetooth_catalog"]=[d.__dict__ for d in self.bluetooth.catalog()]
        return result
