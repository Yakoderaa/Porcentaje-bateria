# Porcentaje de batería

Aplicación de Windows en Python para ver el nivel de batería de periféricos conectados por Bluetooth o receptores USB propietarios.

## Soporte actual

- **Razer DeathAdder V2 Pro (dongle 2.4 GHz)**: lectura HID nativa (`1532:007D`).
- **Logitech G935 Gaming Headset (receptor USB)**: lectura HID nativa por voltaje (`046D:0A87`).
- **Redragon Fizz Pro K616**: detección del receptor real `25A7:FA70`; el porcentaje sigue pendiente de identificar dentro del protocolo HID propietario.
- **JBL por Bluetooth**: lee el porcentaje si Windows lo publica y, si no, igualmente muestra el dispositivo como detectado para poder diagnosticarlo.
- **Bluetooth genérico**: usa la propiedad de batería publicada por Windows cuando el dispositivo la expone.

## Funciones

- Interfaz moderna con tarjetas y actualización automática cada 30 segundos.
- Bandeja del sistema: abrir, refrescar y salir.
- Inicio con Windows, minimizado en bandeja.
- Botón **Buscar actualizaciones** con descarga, instalación silenciosa y reapertura automática.
- Exportación de diagnóstico HID/Bluetooth ampliada.
- GitHub Actions compila `PorcentajeBateria.exe`, crea instalador Inno Setup y publica un Release al subir una versión nueva.

## Desarrollo

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Crear una nueva versión

1. Cambiar `APP_VERSION` en `version.py`.
2. Hacer push a `main`.
3. GitHub Actions compila y publica el nuevo release. La app instalada lo detectará desde **Buscar actualizaciones**.

## Nota sobre batería

Bluetooth estándar puede exponer el porcentaje directamente a Windows. Los receptores 2.4 GHz no tienen un estándar universal: cada fabricante puede requerir un protocolo HID propio.
