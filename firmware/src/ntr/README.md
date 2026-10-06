# ntr/ — bus del slot DS (pendiente)

Aquí irá la capa física del bus NTR (programas PIO + DMA) y el
intérprete de comandos personalizados. Se ejecutará en el **core 1**, de
modo que el core 0 queda dedicado a `tud_task()`.

Plan:

- `ntr_bus.pio` / `ntr_bus.c`: captura del comando de 8 bytes, servir
  lecturas (cartucho → consola) y aceptar escrituras (consola → cartucho,
  `ROMCTRL` bit 30). Partir de la PIO del firmware DSPico revisando su
  licencia.
- `ntr_cmd.c`: tabla de comandos (`0xC0` identificación, `0xC1` bloque de
  framebuffer, `0xC2` estado/backpressure). Sin KEY1/KEY2.
- Cola entre cores (`pico/util/queue.h` o un ring buffer en SRAM) hacia el
  streamer USB, que sustituirá a `speedtest/` como `fbdump_usb_handler_t`.

Los pines exactos del DSPico están por confirmar con su esquemático.
