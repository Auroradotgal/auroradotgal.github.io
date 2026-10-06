# fbdump — volcado del framebuffer de 3DS por el USB del DSPico

Firmware personalizado para el cartucho DSPico (RP2040) que, junto con un
Luma3DS modificado, envía el framebuffer de la 3DS a un PC por USB. No
funciona como cartucho de DS.

Estado: **test de velocidad USB** (fase 1). Ver
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Compilar el firmware

Requisitos: `arm-none-eabi-gcc`, CMake ≥ 3.13 y
[pico-sdk](https://github.com/raspberrypi/pico-sdk) 2.x con el submódulo
`lib/tinyusb`.

```sh
export PICO_SDK_PATH=/ruta/a/pico-sdk
cmake -S firmware -B build
cmake --build build -j
```

Para flashear, conecta el DSPico en modo BOOTSEL y copia
`build/fbdump.uf2` a la unidad `RPI-RP2`.

## Test de velocidad

```sh
cd host
pip install -r requirements.txt
python speedtest.py                       # DSPico -> PC, 5 s
python speedtest.py --mode in --verify    # con comprobación de integridad
python speedtest.py --mode out --verify   # PC -> DSPico (verifica el firmware)
python speedtest.py --mode both -t 10     # ambos sentidos a la vez
```

- **Linux:** instala `host/udev/99-fbdump.rules` para no necesitar root.
- **Windows:** el firmware anuncia WinUSB (MS OS 2.0), así que no hace
  falta Zadig. `libusb-package` aporta la DLL de libusb.

El resultado se muestra en MB/s, MiB/s, Mbit/s y como porcentaje del
máximo teórico de bulk Full Speed (1 216 000 B/s). Se da la cifra medida
por el host y la medida por el propio firmware.
