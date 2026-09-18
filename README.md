# Porcentaje de batería

Aplicación de Windows en Python para ver el nivel de batería de periféricos conectados por Bluetooth o receptores USB propietarios.

## Soporte inicial

- **Razer DeathAdder V2 Pro (dongle 2.4 GHz)**: lectura HID nativa (`1532:007D`).
- **Logitech G930 (receptor USB)**: lectura HID nativa (`046D:0A1F`).
- **Bluetooth**: usa la propiedad de batería publicada por Windows cuando el dispositivo la expone (por ejemplo, JBL compatibles).
- **Redragon Fizz Pro K616**: detecta `258A:0049` y exporta diagnóstico; el porcentaje por dongle queda listo para completar una vez capturado el reporte real del hardware.

## Funciones

- Interfaz moderna con tarjetas y actualización automática cada 30 segundos.
- Bandeja del sistema: abrir, refrescar y salir.
- Inicio con Windows, minimizado en bandeja.
- Botón **Buscar actualizaciones** con descarga, instalación silenciosa y reapertura automática.
- Exportación de diagnóstico HID/Bluetooth para agregar soporte a hardware no documentado.
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
