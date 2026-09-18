from __future__ import annotations
import ctypes, json, os, sys

from PySide6.QtCore import Qt, QTimer, QRunnable, QThreadPool, Signal, QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QProgressBar, QSystemTrayIcon, QMenu, QMessageBox,
    QCheckBox, QFileDialog, QDialog
)

from battery import BatteryManager
from updater import check_update, download_and_install
from version import APP_VERSION

APP_NAME="Porcentaje de batería"
RUN_KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"

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

def app_icon():
    p=QPixmap(64,64)
    p.fill(Qt.transparent)
    q=QPainter(p)
    q.setRenderHint(QPainter.Antialiasing)
    q.setBrush(QColor("#30d158"))
    q.setPen(Qt.NoPen)
    q.drawRoundedRect(8,14,44,36,8,8)
    q.drawRect(52,25,5,14)
    q.setBrush(QColor("#0b1117"))
    q.drawRoundedRect(13,19,34,26,5,5)
    q.setBrush(QColor("#30d158"))
    q.drawRoundedRect(17,23,23,18,4,4)
    q.end()
    return QIcon(p)

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
    def __init__(self,d):
        super().__init__()
        self.setObjectName("card")
        lay=QVBoxLayout(self)
        top=QHBoxLayout()
        name=QLabel(d.name)
        name.setObjectName("deviceName")
        top.addWidget(name)
        top.addStretch()
        pct=QLabel("—" if d.percent is None else f"{d.percent}%")
        pct.setObjectName("percent")
        top.addWidget(pct)
        lay.addLayout(top)

        meta=QLabel(f"{d.connection} · {d.status}")
        meta.setObjectName("muted")
        lay.addWidget(meta)

        bar=QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0,100)
        bar.setValue(d.percent or 0)
        lay.addWidget(bar)

        if d.detail:
            detail=QLabel(d.detail)
            detail.setWordWrap(True)
            detail.setObjectName("detail")
            lay.addWidget(detail)

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

        j=Job(lambda: check_update(timeout=8))
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
        j=Job(lambda: download_and_install(info["asset"]))
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

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(720,640)

        root=QWidget()
        self.setCentralWidget(root)
        self.v=QVBoxLayout(root)
        self.v.setContentsMargins(28,24,28,24)
        self.v.setSpacing(16)

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
        self.refresh_btn.setToolTip("Vuelve a consultar el porcentaje y estado de los periféricos.")
        self.refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_btn)

        self.settings_btn=QPushButton("Configuración")
        self.settings_btn.setToolTip("Actualizaciones de la app, inicio con Windows, permisos y diagnóstico.")
        self.settings_btn.clicked.connect(self.open_settings)
        actions.addWidget(self.settings_btn)
        actions.addStretch()
        self.v.addLayout(actions)

        self.status=QLabel("Buscando dispositivos…")
        self.status.setObjectName("muted")
        self.v.addWidget(self.status)

        self.cards=QVBoxLayout()
        self.v.addLayout(self.cards)
        self.v.addStretch()

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
QCheckBox{padding:4px}""")

        self.settings_dialog=SettingsDialog(self)

        self.tray=QSystemTrayIcon(app_icon(),self)
        menu=QMenu()
        show=QAction("Abrir",self)
        show.triggered.connect(self.show_normal)
        menu.addAction(show)
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
        self.tray.activated.connect(
            lambda r:self.show_normal() if r==QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

        self.timer=QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)
        self.refresh()

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

    def clear_cards(self):
        while self.cards.count():
            item=self.cards.takeAt(0)
            w=item.widget()
            if w:
                w.deleteLater()

    def refresh(self):
        if not self.refresh_btn.isEnabled():
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Actualizando dispositivos…")
        self.status.setText("Consultando periféricos…")
        j=Job(self.manager.refresh)
        self._refresh_job=j
        j.s.done.connect(self.render)
        j.s.error.connect(lambda e:self.status.setText("Error al actualizar dispositivos: "+e))
        j.s.done.connect(lambda _:self._reset_refresh())
        j.s.error.connect(lambda _:self._reset_refresh())
        self.pool.start(j)

    def _reset_refresh(self):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("Actualizar dispositivos")

    def render(self,devices):
        self.clear_cards()
        for d in sorted(devices,key=lambda x:(x.percent is None,x.name.lower())):
            self.cards.addWidget(DeviceCard(d))
        self.status.setText(
            f"{len(devices)} dispositivo(s) detectado(s)"
            if devices else
            "No encontré periféricos compatibles o visibles."
        )

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
    app=QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    w=Window()
    if "--minimized" not in sys.argv:
        w.show()
    sys.exit(app.exec())

if __name__=="__main__":
    main()
