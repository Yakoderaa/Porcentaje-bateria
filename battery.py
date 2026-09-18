from __future__ import annotations
import json, re, statistics, subprocess, time
from dataclasses import dataclass, replace
from collections import deque
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

def _hid_live(vid, pid):
    if hid is None:
        return False
    for info in hid.enumerate(vid,pid):
        dev=None
        try:
            dev=hid.device()
            dev.open_path(info["path"])
            return True
        except Exception:
            continue
        finally:
            try:
                if dev:
                    dev.close()
            except Exception:
                pass
    return False

def _pnp_present(vid, pid):
    if subprocess is None:
        return False
    needle=f"VID_{vid:04X}&PID_{pid:04X}"
    script=(
        "$x=Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        f"Where-Object {{$_.InstanceId -match '{needle}'}} | Select-Object -First 1; "
        "if ($x) { '1' } else { '0' }"
    )
    try:
        p=subprocess.run(
            ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",script],
            capture_output=True,text=True,timeout=4,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)
        )
        return p.returncode==0 and p.stdout.strip()=="1"
    except Exception:
        return False

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
        "servicio de informaci",
        "servicio de atributo gen",
        "perfil de acceso gen",
        "bluetooth device (personal area network",
        "dispositivo bluetooth de bajo consumo hid",
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

        redragon_receiver=bool(hid and hid.enumerate(0x25A7,0xFA70))
        redragon_wired=_hid_live(0x258A,0x0049)
        devices=[]
        for entry in grouped.values():
            raw_name=entry["name"]
            pct=entry["battery"]
            is_redragon_ble=raw_name.lower()=="bt5.0 kb" and redragon_receiver
            name="Redragon Fizz Pro K616 (Bluetooth)" if is_redragon_ble else raw_name
            dtype="Teclado Bluetooth" if is_redragon_ble else _bluetooth_type(name,entry["class"])
            detail=dtype
            status="Conectado"
            if is_redragon_ble:
                detail+=" · Windows lo anuncia como BT5.0 KB y publica su batería mediante Bluetooth LE."
                if redragon_wired:
                    status="Cargando"
                    detail+=" · Cable USB del K616 detectado (258A:0049)."
            elif name.lower()=="jbl go essential":
                detail+=" · El JBL GO Essential no expone a Windows un perfil/propiedad de batería utilizable; no hay porcentaje fiable disponible."
            elif pct is not None:
                detail+=" · Porcentaje publicado por Windows."
            else:
                detail+=" · Windows no publica porcentaje de batería para este dispositivo."
            key="redragon:k616:bluetooth" if is_redragon_ble else "bluetooth:"+_slug(name)
            devices.append(BatteryDevice(
                key,
                name,
                pct,
                "Bluetooth",
                status,
                detail,
                dtype,
            ))
        return devices

class RazerDeathAdderV2ProProvider:
    VID=0x1532
    PID=0x007D
    WIRED_PID=0x007C

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
        wired_present=_hid_live(self.VID,self.WIRED_PID) or _pnp_present(self.VID,self.WIRED_PID)
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
                protocol_charging=bool(charging_data and len(charging_data)>9 and charging_data[9]!=0)
                charging=wired_present or protocol_charging
                charge_source=(
                    "cable USB 1532:007C detectado"
                    if wired_present else
                    "comando HID 0x84 (argumento de carga)" if charging_data else
                    "estado de carga no reportado por el receptor"
                )
                return [BatteryDevice(
                    "razer:deathadder-v2-pro",
                    "Razer DeathAdder V2 Pro",
                    pct,
                    "Dongle 2.4 GHz",
                    "Cargando" if charging else "Conectado",
                    f"Lectura HID nativa · {charge_source}.",
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
        if wired_present:
            return [BatteryDevice(
                "razer:deathadder-v2-pro","Razer DeathAdder V2 Pro",None,
                "USB","Cargando",
                "Mouse conectado por cable USB (1532:007C); el porcentaje inalámbrico no respondió.",
                "Mouse inalámbrico",
            )]
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
    CHARGER_PID=0x0A88

    # Stock G935 LiPo resting-voltage curve. The headset does not report a true
    # coulomb-counted percentage; it reports battery ADC voltage + charge flags.
    CURVE=[
        (3500,0),(3670,10),(3730,20),(3760,30),(3790,40),
        (3820,50),(3870,60),(3920,70),(3980,80),(4060,90),(4200,100)
    ]

    def __init__(self):
        self._rest_samples=deque(maxlen=5)
        self._last_rest_mv=None
        self._last_pct=None
        self._last_charging=False

    def _rest_percent(self, mv):
        self._rest_samples.append(int(mv))
        stable_mv=int(statistics.median(self._rest_samples))
        pct=_interpolate_curve(stable_mv,self.CURVE)
        if pct is not None:
            self._last_rest_mv=stable_mv
            self._last_pct=pct
        return pct,stable_mv

    def _charging_percent(self, raw_mv):
        # On USB charge the ADC can jump by hundreds of millivolts because it
        # sees the charger path. Never remap that spike directly to SoC.
        if self._last_rest_mv is not None:
            pct=_interpolate_curve(self._last_rest_mv,self.CURVE)
            if pct is not None:
                self._last_pct=pct
                return pct,self._last_rest_mv

        # If the app started while already charging and has no rest baseline,
        # only trust values that still look like plausible cell voltage.
        if raw_mv<=4240:
            clamped=min(int(raw_mv),4200)
            pct=_interpolate_curve(clamped,self.CURVE)
            if pct is not None:
                self._last_pct=pct
                return pct,clamped

        # No trustworthy SoC yet. Preserve the last known displayed value if any.
        return self._last_pct,None

    def read(self):
        if hid is None:
            return []
        found=hid.enumerate(self.VID,self.PID)
        charger_present=_hid_live(self.VID,self.CHARGER_PID) or _pnp_present(self.VID,self.CHARGER_PID)
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

                    raw_mv=(resp[4]<<8)|resp[5]
                    flags=resp[6]
                    measurement_valid=bool(flags & 0x01)
                    protocol_charging=bool(flags & 0x02)
                    if not measurement_valid or raw_mv<2000:
                        continue

                    charging=protocol_charging or charger_present
                    if charging:
                        pct,cell_mv=self._charging_percent(raw_mv)
                    else:
                        pct,cell_mv=self._rest_percent(raw_mv)

                    self._last_charging=charging
                    status="Carga completa" if charging and pct is not None and pct>=99 else ("Cargando" if charging else "Conectado")

                    sources=[]
                    if protocol_charging:
                        sources.append("flag HID++")
                    if charger_present:
                        sources.append("USB 046D:0A88")
                    source_text=" + ".join(sources) if sources else "sin carga"

                    if pct is None:
                        detail=(
                            f"Lectura HID nativa · ADC {raw_mv} mV · {source_text}. "
                            "Esperando una lectura en reposo para calcular un porcentaje fiable."
                        )
                    elif charging and cell_mv is not None and cell_mv!=raw_mv:
                        detail=(
                            f"Lectura HID nativa · carga detectada · ADC {raw_mv} mV · "
                            f"SoC anclado al último voltaje en reposo {cell_mv} mV."
                        )
                    else:
                        detail=f"Lectura HID nativa · {raw_mv} mV · {source_text}."

                    return [BatteryDevice(
                        "logitech:g935","Logitech G935 Gaming Headset",pct,
                        "Dongle USB",status,detail,
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

        if found or charger_present:
            status="Cargando" if charger_present else "Detectado"
            detail=(
                "USB de carga 046D:0A88 detectado; el receptor no respondió con una lectura de batería."
                if charger_present else
                "Receptor 046D:0A87 detectado, pero no respondió a la consulta de batería."
            )
            return [BatteryDevice(
                "logitech:g935","Logitech G935 Gaming Headset",self._last_pct,
                "Dongle USB",status,detail,
                "Auriculares inalámbricos",
            )]
        return []

class RedragonFizzProvider:
    VID=0x25A7
    PID=0xFA70
    WIRED_VID=0x258A
    WIRED_PID=0x0049

    def wired_present(self):
        return _hid_live(self.WIRED_VID,self.WIRED_PID)

    def read(self):
        if hid is None:
            return []
        found=hid.enumerate(self.VID,self.PID)
        wired=self.wired_present()
        if found or wired:
            vendor_pages=sorted({
                d.get("usage_page") for d in found
                if isinstance(d.get("usage_page"),int) and d.get("usage_page")>=0xFF00
            })
            pages=", ".join(f"0x{x:04X}" for x in vendor_pages) or "sin página propietaria"
            if wired:
                connection="USB + Dongle 2.4 GHz" if found else "USB"
                status="Cargando"
                detail="Cable USB del K616 detectado como 258A:0049. El teclado está conectado por cable y puede cargar; el porcentaje por dongle no está disponible."
            else:
                connection="Dongle 2.4 GHz"
                status="Detectado"
                detail=f"Receptor 25A7:FA70 detectado ({pages}). El dongle no expone un porcentaje de batería conocido."
            return [BatteryDevice(
                "redragon:k616","Redragon Fizz Pro K616 (dongle)",None,
                connection,status,detail,
                "Teclado inalámbrico",
            )]
        return []

class BatteryManager:
    def __init__(self):
        self.bluetooth=WindowsBluetoothProvider()
        self.redragon=RedragonFizzProvider()
        self.native=[
            RazerDeathAdderV2ProProvider(),
            LogitechG935Provider(),
            self.redragon,
        ]
        self._bluetooth_cache=[]

    def _native_devices(self):
        merged={}
        for provider in self.native:
            try:
                for d in provider.read():
                    merged[d.key]=d
            except Exception:
                pass
        return merged

    def _cached_bluetooth_with_live_status(self):
        wired=self.redragon.wired_present()
        out=[]
        for d in self._bluetooth_cache:
            if d.key=="redragon:k616:bluetooth":
                if wired:
                    out.append(replace(
                        d,
                        status="Cargando",
                        detail="Teclado Bluetooth · Windows publica su batería BLE · cable USB 258A:0049 detectado."
                    ))
                elif d.status=="Cargando":
                    out.append(replace(
                        d,
                        status="Conectado",
                        detail="Teclado Bluetooth · Windows publica su batería mediante Bluetooth LE."
                    ))
                else:
                    out.append(d)
            else:
                out.append(d)
        return out

    def refresh_fast(self):
        merged=self._native_devices()
        for d in self._cached_bluetooth_with_live_status():
            if d.key not in merged or (merged[d.key].percent is None and d.percent is not None):
                merged[d.key]=d
        return list(merged.values())

    def refresh(self):
        merged=self._native_devices()
        try:
            self._bluetooth_cache=self.bluetooth.catalog()
        except Exception:
            pass
        for d in self._cached_bluetooth_with_live_status():
            if d.key not in merged or (merged[d.key].percent is None and d.percent is not None):
                merged[d.key]=d
        return list(merged.values())

    def diagnostic(self):
        result={
            "hid":[],
            "bluetooth_devices":[],
            "bluetooth_catalog":[],
            "redragon_fizz_receiver":[],
            "logitech_g935_charger":[],
            "razer_wired":[],
            "redragon_wired":[],
            "version":6,
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
                if item.get("vendor_id")==0x046D and item.get("product_id")==0x0A88:
                    result["logitech_g935_charger"].append(item)
                if item.get("vendor_id")==0x1532 and item.get("product_id")==0x007C:
                    result["razer_wired"].append(item)
                if item.get("vendor_id")==0x258A and item.get("product_id")==0x0049:
                    result["redragon_wired"].append(item)
        result["bluetooth_devices"]=self.bluetooth.raw()
        result["bluetooth_catalog"]=[d.__dict__ for d in self.bluetooth.catalog()]
        return result
