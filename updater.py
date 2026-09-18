from __future__ import annotations
import json, os, subprocess, tempfile, urllib.request
from packaging.version import Version
from version import APP_VERSION

REPO_API="https://api.github.com/repos/Yakoderaa/Porcentaje-bateria/releases/latest"

class UpdateError(RuntimeError): pass

def check_update():
    req=urllib.request.Request(REPO_API,headers={"User-Agent":"PorcentajeBateria-Updater","Accept":"application/vnd.github+json"})
    with urllib.request.urlopen(req,timeout=15) as r: data=json.load(r)
    tag=str(data.get("tag_name","")).lstrip("v")
    if not tag: raise UpdateError("El release no tiene versión")
    asset=next((a for a in data.get("assets",[]) if a.get("name")=="PorcentajeBateria-Setup.exe"),None)
    return {"available":Version(tag)>Version(APP_VERSION),"version":tag,"asset":asset,"notes":data.get("body") or ""}

def download_and_install(asset):
    if not asset: raise UpdateError("No encontré el instalador en el release")
    target=os.path.join(tempfile.gettempdir(),"PorcentajeBateria-Setup.exe")
    req=urllib.request.Request(asset["browser_download_url"],headers={"User-Agent":"PorcentajeBateria-Updater"})
    with urllib.request.urlopen(req,timeout=60) as src, open(target,"wb") as dst:
        while True:
            chunk=src.read(1024*1024)
            if not chunk: break
            dst.write(chunk)
    flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    subprocess.Popen([target,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/CLOSEAPPLICATIONS"],close_fds=True,creationflags=flags)
