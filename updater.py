from __future__ import annotations
import json, os, subprocess, sys, tempfile, urllib.request
from packaging.version import Version
from version import APP_VERSION

REPO_API="https://api.github.com/repos/Yakoderaa/Porcentaje-bateria/releases/latest"

class UpdateError(RuntimeError):
    pass

def check_update(timeout=8):
    req=urllib.request.Request(
        REPO_API,
        headers={
            "User-Agent":"PorcentajeBateria-Updater",
            "Accept":"application/vnd.github+json",
            "Cache-Control":"no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            data=json.load(r)
    except Exception as e:
        raise UpdateError(f"No se pudo consultar GitHub: {e}") from e

    tag=str(data.get("tag_name","")).lstrip("v")
    if not tag:
        raise UpdateError("El release no tiene una versión válida.")
    asset=next((a for a in data.get("assets",[]) if a.get("name")=="PorcentajeBateria-Setup.exe"),None)
    return {
        "available":Version(tag)>Version(APP_VERSION),
        "version":tag,
        "asset":asset,
        "notes":data.get("body") or "",
    }

def download_and_install(asset):
    if not asset:
        raise UpdateError("El release no contiene PorcentajeBateria-Setup.exe.")
    target=os.path.join(tempfile.gettempdir(),"PorcentajeBateria-Setup.exe")
    req=urllib.request.Request(
        asset["browser_download_url"],
        headers={"User-Agent":"PorcentajeBateria-Updater","Cache-Control":"no-cache"},
    )
    try:
        with urllib.request.urlopen(req,timeout=30) as src, open(target,"wb") as dst:
            while True:
                chunk=src.read(1024*1024)
                if not chunk:
                    break
                dst.write(chunk)
    except Exception as e:
        raise UpdateError(f"No se pudo descargar la actualización: {e}") from e

    # Use a tiny detached helper so the updater can exit, let Inno replace the
    # running executable, then guarantee the installed app is launched again.
    current_exe=sys.executable if getattr(sys,"frozen",False) else os.path.abspath(sys.argv[0])
    helper=os.path.join(tempfile.gettempdir(),"PorcentajeBateria-update-helper.ps1")
    ps_target=target.replace("'","''")
    ps_exe=current_exe.replace("'","''")
    script=f"""$ErrorActionPreference = 'SilentlyContinue'
$installer = '{ps_target}'
$currentApp = '{ps_exe}'
$args = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS')
$p = Start-Process -FilePath $installer -ArgumentList $args -PassThru -Wait
Start-Sleep -Milliseconds 1500

$candidates = @(
    $currentApp,
    (Join-Path $env:LOCALAPPDATA 'Programs\\PorcentajeBateria\\PorcentajeBateria.exe'),
    (Join-Path $env:ProgramFiles 'PorcentajeBateria\\PorcentajeBateria.exe'),
    (Join-Path $env:ProgramFiles(x86) 'PorcentajeBateria\\PorcentajeBateria.exe')
) | Where-Object {{ $_ -and (Test-Path -LiteralPath $_) }} | Select-Object -Unique

if (-not (Get-Process -Name 'PorcentajeBateria' -ErrorAction SilentlyContinue)) {{
    foreach ($app in $candidates) {{
        try {{
            Start-Process -FilePath $app
            break
        }} catch {{}}
    }}
}}
Start-Sleep -Milliseconds 300
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force
"""
    with open(helper,"w",encoding="utf-8-sig") as f:
        f.write(script)

    flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NO_WINDOW",0)
    subprocess.Popen(
        ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",helper],
        close_fds=True,
        creationflags=flags,
    )
    return target
