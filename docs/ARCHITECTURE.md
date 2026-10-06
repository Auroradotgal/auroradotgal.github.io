# Arquitectura

```
3DS ARM11 (Rosalina)  ──►  3DS ARM9 (puente)  ──bus NTR──►  DSPico RP2040  ──USB FS──►  PC
 dirección/formato FB       lee FB, NTRCARD              core1: PIO + cola           host/
                            [lo hace el usuario]          core0: TinyUSB
```

## Estructura del repositorio

| Ruta | Contenido |
|---|---|
| `common/include/fbdump_usb.h` | Protocolo USB (VID/PID, endpoints, peticiones, structs). Fuente de verdad. |
| `firmware/` | Firmware RP2040 (pico-sdk 2.x + TinyUSB). |
| `firmware/src/usb/` | Driver de clase propio, descriptores (incl. WinUSB) y peticiones de control. |
| `firmware/src/speedtest/` | Fuente/sumidero de datos para medir el USB. |
| `firmware/src/ntr/` | Bus del slot DS (pendiente). |
| `host/` | Cliente Python (pyusb): `fbdump/` biblioteca, `speedtest.py`. |
| `docs/` | Esta documentación. |

Previsto: `common/include/fbdump_ntr.h` con los comandos del bus NTR,
compartido con el código ARM9 del lado 3DS.

## USB

- Una interfaz vendor (clase 0xFF) con EP OUT `0x01` y EP IN `0x81`, bulk
  de 64 B (Full Speed).
- Control por EP0 con peticiones vendor/device: `GET_INFO`, `TEST_START`,
  `TEST_STOP`, `GET_STATS`. Ver `fbdump_usb.h`.
- BOS + descriptor MS OS 2.0: en Windows se asigna WinUSB automáticamente.
  En Linux hace falta la regla udev de `host/udev/` para no usar root.

### Por qué no se usa la clase vendor de TinyUSB

Esa clase pasa por un FIFO y arma transferencias de un solo paquete de
64 B, que se reponen en cada iteración de `tud_task()`. Para exprimir el
bus, `fbdump_class.c` registra un driver propio (`usbd_app_driver_get_cb`)
que arma transferencias de 16 KiB directamente sobre el endpoint. El DCD
del RP2040 encadena los paquetes desde la ISR (con doble buffer en los
endpoints IN), así que el bucle principal solo interviene una vez cada
16 KiB.

### Límite teórico

USB 2.0 Full Speed permite como máximo 19 transacciones bulk de 64 B por
frame de 1 ms: **1 216 000 B/s** (≈1,16 MiB/s, 9,73 Mbit/s útiles de los
12 Mbit/s del bus). Ningún dispositivo FS puede superarlo. `speedtest.py`
muestra el porcentaje alcanzado sobre esa cifra. El resultado real
depende también del controlador del host y de si hay hubs de por medio.

## Test de velocidad

`TEST_START` recibe flags (`TX`, `RX`, `PATTERN`). Con `PATTERN`, los
datos son un contador u32 little-endian que empieza en 0: en TX lo genera
el firmware y lo verifica el host, y en RX al revés (los errores se leen
con `GET_STATS`).

Secuencia del host: `TEST_STOP` → drenar EP IN → `TEST_START` → leer y/o
escribir N segundos → `TEST_STOP` → drenar EP IN → `GET_STATS`. El
firmware rechaza (STALL) un `TEST_START` con TX si queda una transferencia
IN sin drenar.
