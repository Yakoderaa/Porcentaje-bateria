from __future__ import annotations
import json, os, sys
from PySide6.QtCore import Qt,QTimer,QRunnable,QThreadPool,Signal,QObject
from PySide6.QtGui import QAction,QColor,QIcon,QPainter,QPixmap
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFrame,QProgressBar,QSystemTrayIcon,QMenu,QMessageBox,QCheckBox,QFileDialog
from battery import BatteryManager
from updater import check_update,download_and_install
from version import APP_VERSION

APP_NAME="Porcentaje de batería"
RUN_KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"

class Signals(QObject):
    done=Signal(object); error=Signal(str)

class Job(QRunnable):
    def __init__(self,fn):
        super().__init__(); self.fn=fn; self.s=Signals()
    def run(self):
        try:self.s.done.emit(self.fn())
        except Exception as e:self.s.error.emit(str(e))

def app_icon():
    p=QPixmap(64,64); p.fill(Qt.transparent)
    q=QPainter(p); q.setRenderHint(QPainter.Antialiasing)
    q.setBrush(QColor("#30d158")); q.setPen(Qt.NoPen); q.drawRoundedRect(8,14,44,36,8,8); q.drawRect(52,25,5,14)
    q.setBrush(QColor("#0b1117")); q.drawRoundedRect(13,19,34,26,5,5)
    q.setBrush(QColor("#30d158")); q.drawRoundedRect(17,23,23,18,4,4); q.end()
    return QIcon(p)

def startup_enabled():
    if os.name!="nt": return False
    import winreg
    try:
        k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY); winreg.QueryValueEx(k,"PorcentajeBateria"); return True
    except OSError:return False

def set_startup(enabled):
    if os.name!="nt": return
    import winreg
    k=winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY)
    if getattr(sys,"frozen",False): exe=f'"{sys.executable}"'
    else: exe=f'"{sys.executable}" "{os.path.abspath(__file__)}"'
    if enabled: winreg.SetValueEx(k,"PorcentajeBateria",0,winreg.REG_SZ,f'{exe} --minimized')
    else:
        try: winreg.DeleteValue(k,"PorcentajeBateria")
        except OSError: pass

class DeviceCard(QFrame):
    def __init__(self,d):
        super().__init__(); self.setObjectName("card")
        lay=QVBoxLayout(self); top=QHBoxLayout()
        name=QLabel(d.name); name.setObjectName("deviceName"); top.addWidget(name); top.addStretch()
        pct=QLabel("—" if d.percent is None else f"{d.percent}%"); pct.setObjectName("percent"); top.addWidget(pct); lay.addLayout(top)
        meta=QLabel(f"{d.connection} · {d.status}"); meta.setObjectName("muted"); lay.addWidget(meta)
        bar=QProgressBar(); bar.setTextVisible(False); bar.setRange(0,100); bar.setValue(d.percent or 0); lay.addWidget(bar)
        if d.detail:
            detail=QLabel(d.detail); detail.setWordWrap(True); detail.setObjectName("detail"); lay.addWidget(detail)

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.manager=BatteryManager(); self.pool=QThreadPool.globalInstance()
        self.setWindowTitle(APP_NAME); self.setWindowIcon(app_icon()); self.resize(720,640)
        root=QWidget(); self.setCentralWidget(root)
        self.v=QVBoxLayout(root); self.v.setContentsMargins(28,24,28,24); self.v.setSpacing(16)
        h=QHBoxLayout(); title=QLabel(APP_NAME); title.setObjectName("title"); h.addWidget(title); h.addStretch(); ver=QLabel("v"+APP_VERSION); ver.setObjectName("muted"); h.addWidget(ver); self.v.addLayout(h)
        sub=QLabel("Batería de periféricos Bluetooth y receptores USB"); sub.setObjectName("muted"); self.v.addWidget(sub)
        actions=QHBoxLayout()
        self.refresh_btn=QPushButton("Actualizar ahora"); self.refresh_btn.clicked.connect(self.refresh); actions.addWidget(self.refresh_btn)
        self.update_btn=QPushButton("Buscar actualizaciones"); self.update_btn.clicked.connect(self.update_app); actions.addWidget(self.update_btn)
        actions.addStretch(); self.v.addLayout(actions)
        settings=QFrame(); settings.setObjectName("card"); sl=QVBoxLayout(settings)
        self.start_cb=QCheckBox("Iniciar con Windows y abrir minimizado en la bandeja"); self.start_cb.setChecked(startup_enabled()); self.start_cb.toggled.connect(set_startup); sl.addWidget(self.start_cb)
        diag=QPushButton("Exportar diagnóstico de dispositivos"); diag.clicked.connect(self.export_diag); sl.addWidget(diag); self.v.addWidget(settings)
        self.status=QLabel("Buscando dispositivos…"); self.status.setObjectName("muted"); self.v.addWidget(self.status)
        self.cards=QVBoxLayout(); self.v.addLayout(self.cards); self.v.addStretch()
        self.setStyleSheet("""QWidget{background:#0b1117;color:#edf4fa;font-family:Segoe UI;font-size:14px} QLabel#title{font-size:28px;font-weight:700} QLabel#deviceName{font-size:17px;font-weight:600} QLabel#percent{font-size:24px;font-weight:700} QLabel#muted,QLabel#detail{color:#8fa3b5} QFrame#card{background:#121b24;border:1px solid #22303c;border-radius:14px} QPushButton{background:#1b2935;border:1px solid #314454;border-radius:9px;padding:9px 14px} QPushButton:hover{background:#243746} QProgressBar{height:10px;border:0;background:#25313b;border-radius:5px} QProgressBar::chunk{background:#30d158;border-radius:5px} QCheckBox{padding:4px}""")
        self.tray=QSystemTrayIcon(app_icon(),self); menu=QMenu()
        show=QAction("Abrir",self); show.triggered.connect(self.show_normal); menu.addAction(show)
        refresh=QAction("Actualizar baterías",self); refresh.triggered.connect(self.refresh); menu.addAction(refresh)
        menu.addSeparator(); quit_a=QAction("Salir",self); quit_a.triggered.connect(QApplication.quit); menu.addAction(quit_a)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda r:self.show_normal() if r==QSystemTrayIcon.Trigger else None); self.tray.show()
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(30000); self.refresh()

    def show_normal(self):
        self.show(); self.raise_(); self.activateWindow()

    def closeEvent(self,e):
        e.ignore(); self.hide(); self.tray.showMessage(APP_NAME,"La aplicación sigue activa en la bandeja.",QSystemTrayIcon.Information,1800)

    def clear_cards(self):
        while self.cards.count():
            item=self.cards.takeAt(0); w=item.widget()
            if w: w.deleteLater()

    def refresh(self):
        self.refresh_btn.setEnabled(False); self.status.setText("Actualizando…")
        j=Job(self.manager.refresh)
        j.s.done.connect(self.render); j.s.error.connect(lambda e:self.status.setText("Error: "+e))
        j.s.done.connect(lambda _:self.refresh_btn.setEnabled(True)); j.s.error.connect(lambda _:self.refresh_btn.setEnabled(True))
        self.pool.start(j)

    def render(self,devices):
        self.clear_cards()
        for d in sorted(devices,key=lambda x:(x.percent is None,x.name.lower())): self.cards.addWidget(DeviceCard(d))
        self.status.setText(f"{len(devices)} dispositivo(s) detectado(s)" if devices else "No encontré dispositivos con batería visible. Probá Exportar diagnóstico.")

    def export_diag(self):
        path,_=QFileDialog.getSaveFileName(self,"Guardar diagnóstico","diagnostico-bateria.json","JSON (*.json)")
        if not path:return
        try:
            with open(path,"w",encoding="utf-8") as f: json.dump(self.manager.diagnostic(),f,ensure_ascii=False,indent=2)
            QMessageBox.information(self,"Diagnóstico","Archivo guardado. No contiene contraseñas; sí incluye IDs y nombres de hardware.")
        except Exception as e: QMessageBox.critical(self,"Error",str(e))

    def update_app(self):
        self.update_btn.setEnabled(False); self.update_btn.setText("Buscando…")
        j=Job(check_update)
        j.s.done.connect(self._update_result); j.s.error.connect(lambda e:QMessageBox.warning(self,"Actualización",f"No pude comprobar actualizaciones.\n\n{e}"))
        j.s.done.connect(lambda _:self._reset_update_btn()); j.s.error.connect(lambda _:self._reset_update_btn()); self.pool.start(j)

    def _reset_update_btn(self):
        self.update_btn.setEnabled(True); self.update_btn.setText("Buscar actualizaciones")

    def _update_result(self,info):
        if not info["available"]:
            QMessageBox.information(self,"Actualización",f"Ya tenés la versión más reciente ({APP_VERSION})."); return
        if QMessageBox.question(self,"Actualización disponible",f"Está disponible la versión {info['version']}.\n\n¿Descargar, instalar y reiniciar ahora?")==QMessageBox.Yes:
            try:
                download_and_install(info["asset"]); QApplication.quit()
            except Exception as e: QMessageBox.critical(self,"Actualización",str(e))

def main():
    app=QApplication(sys.argv); app.setQuitOnLastWindowClosed(False); app.setApplicationName(APP_NAME)
    w=Window()
    if "--minimized" not in sys.argv: w.show()
    sys.exit(app.exec())

if __name__=="__main__": main()
