from __future__ import annotations
import ctypes, json, os, sys

from PySide6.QtCore import Qt, QTimer, QRunnable, QThreadPool, Signal, QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QProgressBar, QSystemTrayIcon, QMenu, QMessageBox,
    QCheckBox, QFileDialog, QDialog, QTabWidget, QScrollArea, QInputDialog
)

from battery import BatteryManager
from updater import check_update, download_and_install
from version import APP_VERSION

APP_NAME="Porcentaje de batería"
APP_USER_MODEL_ID="Yakoderaa.PorcentajeBateria"
RUN_KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"
CONFIG_DIR=os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),"PorcentajeBateria")
CONFIG_PATH=os.path.join(CONFIG_DIR,"config.json")

class Signals(QObject):
    done=Signal(object)
    error=Signal(str)

class Job(QRunnable):
    def __init__(self,fn):
        super().__init__()
        self.fn=fn
        self.s=Signals()
    def run(self):
        try:
            self.s.done.emit(self.fn())
        except Exception as e:
            self.s.error.emit(str(e))

def resource_path(relative):
    base=getattr(sys,"_MEIPASS",os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base,relative)

def _load_icon(primary,fallback_svg):
    for rel in (primary,fallback_svg):
        path=resource_path(rel)
        if os.path.exists(path):
            icon=QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon()

def app_icon():
    return _load_icon(os.path.join("assets","app.ico"),os.path.join("assets","app.svg"))

def tray_icon():
    return _load_icon(os.path.join("assets","tray.ico"),os.path.join("assets","tray.svg"))

def _load_config():
    try:
        with open(CONFIG_PATH,"r",encoding="utf-8") as f:
            data=json.load(f)
        aliases=data.get("aliases",{})
        if not isinstance(aliases,dict):
            aliases={}
        return set(data.get("selected_keys",[])),dict(aliases),True
    except Exception:
        return set(),{},False

def _save_config(keys,aliases):
    os.makedirs(CONFIG_DIR,exist_ok=True)
    tmp=CONFIG_PATH+".tmp"
    payload={
        "selected_keys":sorted(keys),
        "aliases":dict(sorted(aliases.items())),
    }
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
    os.replace(tmp,CONFIG_PATH)

def startup_enabled():
    if os.name!="nt":
        return False
    import winreg
    try:
        k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY)
        winreg.QueryValueEx(k,"PorcentajeBateria")
        return True
    except OSError:
        return False

def set_startup(enabled):
    if os.name!="nt":
        return
    import winreg
    k=winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY)
    if getattr(sys,"frozen",False):
        exe=f'"{sys.executable}"'
    else:
        exe=f'"{sys.executable}" "{os.path.abspath(__file__)}"'
    if enabled:
        winreg.SetValueEx(k,"PorcentajeBateria",0,winreg.REG_SZ,f'{exe} --minimized')
    else:
        try:
            winreg.DeleteValue(k,"PorcentajeBateria")
        except OSError:
            pass

def is_admin():
    if os.name!="nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def restart_as_admin():
    if os.name!="nt":
        raise RuntimeError("Esta función solo está disponible en Windows.")
    if getattr(sys,"frozen",False):
        exe=sys.executable
        params=""
    else:
        exe=sys.executable
        params=f'"{os.path.abspath(__file__)}"'
    result=ctypes.windll.shell32.ShellExecuteW(None,"runas",exe,params,None,1)
    if result<=32:
        raise RuntimeError("Windows no pudo iniciar la aplicación como administrador.")

class DeviceCard(QFrame):
    def __init__(self,d,display_name=None,selectable=False,checked=False,on_toggle=None,on_rename=None):
        super().__init__()
        self.setObjectName("card")
        lay=QVBoxLayout(self)
        lay.setSpacing(7)

        top=QHBoxLayout()
        shown_name=display_name or d.name
        name=QLabel(shown_name)
        name.setObjectName("deviceName")
        top.addWidget(name)
        top.addStretch()
        pct=QLabel("—" if d.percent is None else f"{d.percent}%")
        pct.setObjectName("percent")
        top.addWidget(pct)
        lay.addLayout(top)

        meta_bits=[d.connection]
        if d.device_type:
            meta_bits.append(d.device_type)
        meta_bits.append(d.status)
        meta=QLabel(" · ".join(meta_bits))
        meta.setObjectName("muted")
        lay.addWidget(meta)

        bar=QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0,100)
        bar.setValue(d.percent or 0)
        if d.status=="Cargando":
            bar.setProperty("charging",True)
        lay.addWidget(bar)

        if d.detail:
            detail=QLabel(d.detail)
            detail.setWordWrap(True)
            detail.setObjectName("detail")
            lay.addWidget(detail)

        if shown_name!=d.name:
            original=QLabel(f"Nombre original: {d.name}")
            original.setObjectName("detail")
            lay.addWidget(original)

        options=QHBoxLayout()
        if selectable:
            cb=QCheckBox("Mostrar en Mis dispositivos y en la bandeja del sistema")
            cb.setChecked(checked)
            if on_toggle:
                cb.toggled.connect(lambda state,key=d.key:on_toggle(key,state))
            options.addWidget(cb)
        options.addStretch()
        if on_rename:
            rename=QPushButton("Cambiar nombre")
            rename.clicked.connect(lambda _=False,key=d.key:on_rename(key))
            options.addWidget(rename)
        lay.addLayout(options)

class SettingsDialog(QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.pool=window.pool
        self._update_busy=False
        self._update_seq=0
        self._update_job=None
        self._install_job=None

        self.setWindowTitle("Configuración")
        self.setWindowIcon(app_icon())
        self.resize(560,500)
        self.setStyleSheet(window.styleSheet())

        root=QVBoxLayout(self)
        root.setContentsMargins(26,24,26,24)
        root.setSpacing(16)

        title=QLabel("Configuración")
        title.setObjectName("title")
        root.addWidget(title)
        subtitle=QLabel(f"{APP_NAME} · v{APP_VERSION}")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        update_card=QFrame()
        update_card.setObjectName("card")
        ul=QVBoxLayout(update_card)
        utitle=QLabel("Actualización de la aplicación")
        utitle.setObjectName("sectionTitle")
        ul.addWidget(utitle)
        self.update_status=QLabel("Comprueba nuevas versiones publicadas en GitHub.")
        self.update_status.setWordWrap(True)
        self.update_status.setObjectName("muted")
        ul.addWidget(self.update_status)
        self.update_btn=QPushButton("Buscar actualización de la aplicación")
        self.update_btn.clicked.connect(self.check_for_updates)
        ul.addWidget(self.update_btn)
        root.addWidget(update_card)

        start_card=QFrame()
        start_card.setObjectName("card")
        sl=QVBoxLayout(start_card)
        stitle=QLabel("Inicio y permisos")
        stitle.setObjectName("sectionTitle")
        sl.addWidget(stitle)
        self.start_cb=QCheckBox("Iniciar con Windows y abrir minimizado en la bandeja")
        self.start_cb.setChecked(startup_enabled())
        self.start_cb.toggled.connect(set_startup)
        sl.addWidget(self.start_cb)
        self.admin_label=QLabel(
            "La aplicación está ejecutándose como administrador."
            if is_admin() else
            "La aplicación está ejecutándose con permisos normales."
        )
        self.admin_label.setObjectName("muted")
        sl.addWidget(self.admin_label)
        self.admin_btn=QPushButton(
            "Ya se está ejecutando como administrador"
            if is_admin() else
            "Reiniciar como administrador"
        )
        self.admin_btn.setEnabled(not is_admin())
        self.admin_btn.clicked.connect(self.restart_admin)
        sl.addWidget(self.admin_btn)
        root.addWidget(start_card)

        diag_card=QFrame()
        diag_card.setObjectName("card")
        dl=QVBoxLayout(diag_card)
        dtitle=QLabel("Diagnóstico")
        dtitle.setObjectName("sectionTitle")
        dl.addWidget(dtitle)
        dd=QLabel("Exporta IDs HID y datos Bluetooth para agregar o corregir soporte de dispositivos.")
        dd.setWordWrap(True)
        dd.setObjectName("muted")
        dl.addWidget(dd)
        diag=QPushButton("Exportar diagnóstico de dispositivos")
        diag.clicked.connect(self.window.export_diag)
        dl.addWidget(diag)
        root.addWidget(diag_card)

        root.addStretch()
        close=QPushButton("Cerrar configuración")
        close.clicked.connect(self.close)
        root.addWidget(close)

        self.update_watchdog=QTimer(self)
        self.update_watchdog.setSingleShot(True)
        self.update_watchdog.timeout.connect(self._update_timeout)

    def restart_admin(self):
        try:
            restart_as_admin()
            QTimer.singleShot(250,QApplication.quit)
        except Exception as e:
            QMessageBox.critical(self,"Administrador",str(e))

    def check_for_updates(self):
        if self._update_busy:
            return
        self._update_busy=True
        self._update_seq+=1
        seq=self._update_seq
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Buscando actualización…")
        self.update_status.setText("Consultando GitHub…")
        self.update_watchdog.start(15000)

        j=Job(lambda:check_update(timeout=8))
        self._update_job=j
        j.s.done.connect(lambda info,s=seq:self._check_done(s,info))
        j.s.error.connect(lambda err,s=seq:self._check_error(s,err))
        self.pool.start(j)

    def _finish_check_ui(self):
        self._update_busy=False
        self.update_watchdog.stop()
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Buscar actualización de la aplicación")

    def _update_timeout(self):
        if not self._update_busy:
            return
        self._update_seq+=1
        self._finish_check_ui()
        self.update_status.setText("La consulta tardó demasiado. Podés volver a intentarlo.")
        QMessageBox.warning(
            self,"Actualización",
            "GitHub no respondió a tiempo. La búsqueda fue liberada y el botón ya puede usarse de nuevo."
        )

    def _check_error(self,seq,error):
        if seq!=self._update_seq:
            return
        self._finish_check_ui()
        self.update_status.setText("No se pudo comprobar la versión.")
        QMessageBox.warning(self,"Actualización",f"No pude comprobar actualizaciones.\n\n{error}")

    def _check_done(self,seq,info):
        if seq!=self._update_seq:
            return
        self.update_watchdog.stop()
        if not info["available"]:
            self._finish_check_ui()
            self.update_status.setText(f"Ya tenés la versión más reciente: v{APP_VERSION}.")
            return

        self.update_status.setText(
            f"Versión v{info['version']} encontrada. Descargando e instalando automáticamente…"
        )
        self.update_btn.setEnabled(False)
        self.update_btn.setText(f"Instalando v{info['version']}…")
        j=Job(lambda:download_and_install(info["asset"]))
        self._install_job=j
        j.s.done.connect(lambda _path:self._install_started(info["version"]))
        j.s.error.connect(self._install_error)
        self.pool.start(j)

    def _install_started(self,version):
        self.update_status.setText(f"Instalador v{version} iniciado. Cerrando la aplicación…")
        QTimer.singleShot(500,QApplication.quit)

    def _install_error(self,error):
        self._finish_check_ui()
        self.update_status.setText("No se pudo instalar la actualización.")
        QMessageBox.critical(self,"Actualización",error)

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.manager=BatteryManager()
        self.pool=QThreadPool.globalInstance()
        self._refresh_job=None
        self._refresh_busy=False
        self.devices={}
        self.selected_keys,self.aliases,self._selection_loaded=_load_config()

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(850,720)

        root=QWidget()
        self.setCentralWidget(root)
        self.v=QVBoxLayout(root)
        self.v.setContentsMargins(28,24,28,24)
        self.v.setSpacing(14)

        h=QHBoxLayout()
        title=QLabel(APP_NAME)
        title.setObjectName("title")
        h.addWidget(title)
        h.addStretch()
        ver=QLabel("v"+APP_VERSION)
        ver.setObjectName("muted")
        h.addWidget(ver)
        self.v.addLayout(h)

        sub=QLabel("Batería de periféricos Bluetooth y receptores USB")
        sub.setObjectName("muted")
        self.v.addWidget(sub)

        actions=QHBoxLayout()
        self.refresh_btn=QPushButton("Actualizar dispositivos")
        self.refresh_btn.setToolTip("Vuelve a consultar porcentaje, conexión y estado de carga.")
        self.refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_btn)
        self.settings_btn=QPushButton("Configuración")
        self.settings_btn.clicked.connect(self.open_settings)
        actions.addWidget(self.settings_btn)
        actions.addStretch()
        self.v.addLayout(actions)

        self.status=QLabel("Buscando dispositivos…")
        self.status.setObjectName("muted")
        self.v.addWidget(self.status)

        self.tabs=QTabWidget()
        self.v.addWidget(self.tabs,1)

        self.selected_container,self.selected_layout=self._make_scroll_tab()
        self.all_container,self.all_layout=self._make_scroll_tab()
        self.tabs.addTab(self.selected_container,"Mis dispositivos")
        self.tabs.addTab(self.all_container,"Todos los dispositivos")

        self.setStyleSheet("""QWidget{background:#0b1117;color:#edf4fa;font-family:Segoe UI;font-size:14px}
QLabel#title{font-size:28px;font-weight:700}
QLabel#sectionTitle{font-size:17px;font-weight:650}
QLabel#deviceName{font-size:17px;font-weight:600}
QLabel#percent{font-size:24px;font-weight:700}
QLabel#muted,QLabel#detail{color:#8fa3b5}
QFrame#card{background:#121b24;border:1px solid #22303c;border-radius:14px}
QPushButton{background:#1b2935;border:1px solid #314454;border-radius:9px;padding:9px 14px}
QPushButton:hover{background:#243746}
QPushButton:disabled{color:#667786;background:#101820}
QProgressBar{height:10px;border:0;background:#25313b;border-radius:5px}
QProgressBar::chunk{background:#30d158;border-radius:5px}
QCheckBox{padding:5px}
QTabWidget::pane{border:1px solid #22303c;border-radius:10px;top:-1px}
QTabBar::tab{background:#101820;color:#8fa3b5;padding:10px 18px;border:1px solid #22303c}
QTabBar::tab:selected{background:#1b2935;color:#edf4fa}
QScrollArea{border:0;background:transparent}""")

        self.settings_dialog=SettingsDialog(self)
        self.tray=QSystemTrayIcon(tray_icon(),self)
        self.tray.activated.connect(
            lambda r:self.show_normal() if r==QSystemTrayIcon.Trigger else None
        )
        self.tray.show()
        self.rebuild_tray_menu()

        self.fast_timer=QTimer(self)
        self.fast_timer.timeout.connect(self.refresh_fast)
        self.fast_timer.start(4000)

        self.full_timer=QTimer(self)
        self.full_timer.timeout.connect(self.refresh_background)
        self.full_timer.start(15000)

        self.refresh()

    def _make_scroll_tab(self):
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        body=QWidget()
        layout=QVBoxLayout(body)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        return scroll,layout

    def _clear_layout(self,layout):
        while layout.count():
            item=layout.takeAt(0)
            w=item.widget()
            if w:
                w.deleteLater()

    def open_settings(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self,e):
        e.ignore()
        self.hide()
        self.tray.showMessage(
            APP_NAME,
            "La aplicación sigue activa en la bandeja.",
            QSystemTrayIcon.Information,
            1800,
        )

    def _start_refresh(self,fn,interactive=False):
        if self._refresh_busy:
            return
        self._refresh_busy=True
        if interactive:
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("Actualizando dispositivos…")
            self.status.setText("Consultando periféricos…")
        j=Job(fn)
        self._refresh_job=j
        j.s.done.connect(lambda devices,flag=interactive:self._refresh_done(devices,flag))
        j.s.error.connect(lambda error,flag=interactive:self._refresh_error(error,flag))
        self.pool.start(j)

    def refresh(self):
        self._start_refresh(self.manager.refresh,True)

    def refresh_background(self):
        self._start_refresh(self.manager.refresh,False)

    def refresh_fast(self):
        self._start_refresh(self.manager.refresh_fast,False)

    def _refresh_done(self,devices,interactive):
        self._refresh_busy=False
        self.render(devices)
        if interactive:
            self._reset_refresh()

    def _refresh_error(self,error,interactive):
        self._refresh_busy=False
        if interactive:
            self.status.setText("Error al actualizar dispositivos: "+error)
            self._reset_refresh()

    def _reset_refresh(self):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("Actualizar dispositivos")

    def render(self,devices):
        self.devices={d.key:d for d in devices}
        if not self._selection_loaded and devices:
            self.selected_keys={d.key for d in devices}
            _save_config(self.selected_keys,self.aliases)
            self._selection_loaded=True
        self._render_all()
        self._render_selected()
        self.rebuild_tray_menu()
        bt_count=sum(1 for d in devices if d.connection=="Bluetooth")
        self.status.setText(
            f"{len(devices)} dispositivo(s) detectado(s) · {bt_count} Bluetooth"
            if devices else
            "No encontré periféricos compatibles o visibles."
        )

    def display_name(self,d):
        alias=(self.aliases.get(d.key) or "").strip()
        return alias or d.name

    def rename_device(self,key):
        d=self.devices.get(key)
        if not d:
            return
        current=self.display_name(d)
        text,ok=QInputDialog.getText(
            self,
            "Cambiar nombre",
            f"Nombre para {d.name}:\n(Dejalo vacío para restaurar el nombre original)",
            text=current,
        )
        if not ok:
            return
        value=text.strip()
        if not value or value==d.name:
            self.aliases.pop(key,None)
        else:
            self.aliases[key]=value
        _save_config(self.selected_keys,self.aliases)
        self._render_selected()
        self._render_all()
        self.rebuild_tray_menu()

    def _render_selected(self):
        self._clear_layout(self.selected_layout)
        chosen=[
            d for d in self.devices.values()
            if d.key in self.selected_keys
        ]
        chosen.sort(key=lambda d:(d.percent is None,self.display_name(d).lower()))
        if not chosen:
            hint=QLabel(
                "No seleccionaste ningún dispositivo. Abrí “Todos los dispositivos” y marcá los que querés ver acá y en la bandeja."
            )
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            self.selected_layout.addWidget(hint)
            return
        for d in chosen:
            self.selected_layout.addWidget(DeviceCard(
                d,
                display_name=self.display_name(d),
                on_rename=self.rename_device,
            ))

    def _render_all(self):
        self._clear_layout(self.all_layout)
        devices=sorted(self.devices.values(),key=lambda d:(d.connection!="Bluetooth",self.display_name(d).lower()))
        if not devices:
            hint=QLabel("No hay dispositivos detectados.")
            hint.setObjectName("muted")
            self.all_layout.addWidget(hint)
            return
        for d in devices:
            self.all_layout.addWidget(
                DeviceCard(
                    d,
                    display_name=self.display_name(d),
                    selectable=True,
                    checked=d.key in self.selected_keys,
                    on_toggle=self.set_device_selected,
                    on_rename=self.rename_device,
                )
            )

    def set_device_selected(self,key,enabled):
        if enabled:
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)
        _save_config(self.selected_keys,self.aliases)
        self._selection_loaded=True
        self._render_selected()
        self.rebuild_tray_menu()

    def rebuild_tray_menu(self):
        menu=QMenu()
        open_action=QAction("Abrir Porcentaje de batería",self)
        open_action.triggered.connect(self.show_normal)
        menu.addAction(open_action)

        selected=[
            d for d in self.devices.values()
            if d.key in self.selected_keys
        ]
        selected.sort(key=lambda d:self.display_name(d).lower())
        if selected:
            menu.addSeparator()
            header=QAction("Mis dispositivos",self)
            header.setEnabled(False)
            menu.addAction(header)
            tooltip=[]
            for d in selected:
                level="—" if d.percent is None else f"{d.percent}%"
                charge=f" · {d.status}" if d.status else ""
                shown=self.display_name(d)
                action=QAction(f"{shown}  ·  {level}{charge}",self)
                action.triggered.connect(self.show_normal)
                menu.addAction(action)
                tooltip.append(f"{shown}: {level}")
            self.tray.setToolTip(" | ".join(tooltip)[:125])
        else:
            self.tray.setToolTip(APP_NAME)

        menu.addSeparator()
        refresh=QAction("Actualizar dispositivos",self)
        refresh.triggered.connect(self.refresh)
        menu.addAction(refresh)
        settings=QAction("Configuración",self)
        settings.triggered.connect(self.open_settings)
        menu.addAction(settings)
        menu.addSeparator()
        quit_a=QAction("Salir",self)
        quit_a.triggered.connect(QApplication.quit)
        menu.addAction(quit_a)
        self.tray.setContextMenu(menu)
        self._tray_menu=menu

    def export_diag(self):
        path,_=QFileDialog.getSaveFileName(
            self,"Guardar diagnóstico","diagnostico-bateria.json","JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path,"w",encoding="utf-8") as f:
                json.dump(self.manager.diagnostic(),f,ensure_ascii=False,indent=2)
            QMessageBox.information(
                self,"Diagnóstico",
                "Archivo guardado. Incluye nombres e IDs de hardware, pero no contraseñas."
            )
        except Exception as e:
            QMessageBox.critical(self,"Error",str(e))

def main():
    if os.name=="nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass
    app=QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    w=Window()
    if "--minimized" not in sys.argv:
        w.show()
    sys.exit(app.exec())

if __name__=="__main__":
    main()
