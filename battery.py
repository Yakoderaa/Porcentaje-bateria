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
    try: return max(0, min(100, int(round(v))))
    except Exception: return None

class WindowsBluetoothProvider:
    """Reads Windows' Bluetooth battery property when the accessory exposes it."""
    SCRIPT = r'''
$items = @()
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
  $_.Class -in @('Bluetooth','AudioEndpoint','HIDClass','Keyboard','Mouse')
} | ForEach-Object {
  $d = $_
  $props = Get-PnpDeviceProperty -InstanceId $d.InstanceId -ErrorAction SilentlyContinue
  $bat = $props | Where-Object { $_.KeyName -eq 'DEVPKEY_Bluetooth_BatteryPercent' -or $_.KeyName -match '104ea319-6ee2-4701-bd47-8ddbf425bbe5' } | Select-Object -First 1
  if ($bat -and $null -ne $bat.Data) {
    $items += [pscustomobject]@{ name=$d.FriendlyName; id=$d.InstanceId; battery=[int]$bat.Data }
  }
}
$items | ConvertTo-Json -Compress
'''
    def read(self):
        try:
            p = subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",self.SCRIPT],capture_output=True,text=True,timeout=12,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if p.returncode != 0 or not p.stdout.strip(): return []
            raw = json.loads(p.stdout)
            if isinstance(raw, dict): raw=[raw]
            return [BatteryDevice("bt:"+str(x.get("id")),x.get("name") or "Dispositivo Bluetooth",_clamp(x.get("battery")),"Bluetooth","Conectado","Windows Battery Property") for x in raw]
        except Exception:
            return []

class RazerDeathAdderV2ProProvider:
    VID=0x1532; PID=0x007D
    def _command(self):
        report=bytearray(90)
        report[0]=0x00; report[1]=0x3F; report[5]=0x02; report[6]=0x07; report[7]=0x80
        crc=0
        for b in report[2:88]: crc ^= b
        report[88]=crc
        return bytes([0])+bytes(report)
    def read(self):
        if hid is None: return []
        candidates=[d for d in hid.enumerate(self.VID,self.PID) if d.get("interface_number") in (0,-1) and d.get("usage_page") in (1,None) and d.get("usage") in (2,None)]
        if not candidates: candidates=hid.enumerate(self.VID,self.PID)
        for info in candidates:
            dev=None
            try:
                dev=hid.device(); dev.open_path(info["path"]); dev.set_nonblocking(0)
                request=self._command()
                for _ in range(3):
                    dev.send_feature_report(request); time.sleep(.08)
                    resp=bytes(dev.get_feature_report(0,91))
                    if len(resp)>=91: data=resp[1:91]
                    elif len(resp)>=90: data=resp[:90]
                    else: continue
                    if data[0]==0x02 and data[6]==0x07 and data[7]==0x80:
                        pct=_clamp(data[9]*100/255)
                        return [BatteryDevice("razer:deathadder-v2-pro","Razer DeathAdder V2 Pro",pct,"Dongle 2.4 GHz","Conectado","Lectura HID nativa")]
            except Exception:
                pass
            finally:
                try:
                    if dev: dev.close()
                except Exception: pass
        if hid.enumerate(self.VID,self.PID):
            return [BatteryDevice("razer:deathadder-v2-pro","Razer DeathAdder V2 Pro",None,"Dongle 2.4 GHz","Detectado","No se pudo leer batería; cerrá Synapse/OpenRGB si bloquea HID")]
        return []

class LogitechG930Provider:
    VID=0x046D; PID=0x0A1F
    REQUEST=[0xff,0x09,0x00,0xfd,0xf4,0x10,0x05,0xb1,0xbf,0xa0,0x04]
    def read(self):
        if hid is None: return []
        found=hid.enumerate(self.VID,self.PID)
        for info in found:
            dev=None
            try:
                dev=hid.device(); dev.open_path(info["path"])
                req=self.REQUEST+[0]*(64-len(self.REQUEST))
                dev.send_feature_report(req)
                resp=None
                for _ in range(3):
                    resp=bytes(dev.get_feature_report(0xff,64)); time.sleep(.1)
                if resp and len(resp)>13:
                    raw=resp[13]
                    if raw>=44:
                        pct=_clamp((min(raw,91)-44)*100/(91-44))
                        return [BatteryDevice("logitech:g930","Logitech G930",pct,"Dongle USB","Conectado","Lectura HID nativa")]
            except Exception:
                pass
            finally:
                try:
                    if dev: dev.close()
                except Exception: pass
        if found:
            return [BatteryDevice("logitech:g930","Logitech G930",None,"Dongle USB","Detectado","El receptor responde, pero no entregó batería")]
        return []

class RedragonFizzProvider:
    VID=0x258A; PID=0x0049
    def read(self):
        if hid is None: return []
        if hid.enumerate(self.VID,self.PID):
            return [BatteryDevice("redragon:k616","Redragon Fizz Pro K616",None,"Dongle/Bluetooth","Detectado","ID reconocido; protocolo de porcentaje aún no documentado. Usá Diagnóstico para capturarlo.")]
        return []

class BatteryManager:
    def __init__(self):
        self.providers=[RazerDeathAdderV2ProProvider(),LogitechG930Provider(),WindowsBluetoothProvider(),RedragonFizzProvider()]
    def refresh(self):
        merged={}
        for provider in self.providers:
            try:
                for d in provider.read():
                    if d.key not in merged or (merged[d.key].percent is None and d.percent is not None): merged[d.key]=d
            except Exception:
                pass
        return list(merged.values())
    def diagnostic(self):
        result={"hid":[],"bluetooth_battery":[]}
        if hid:
            for d in hid.enumerate():
                result["hid"].append({k:(v.decode(errors="replace") if isinstance(v,bytes) else v) for k,v in d.items() if k in ("vendor_id","product_id","manufacturer_string","product_string","serial_number","interface_number","usage_page","usage","path")})
        result["bluetooth_battery"]=[d.__dict__ for d in WindowsBluetoothProvider().read()]
        return result
