#!/usr/bin/env python3
"""
Copia de la CTRNAND de un dump de NAND de 3DS las carpetas con los datos de
guardado de Plaza Mii StreetPass, tal cual están en la consola:

  savedata/000202x8   (de CTRNAND:/data/<ID0>/sysdata/000202x8)
  extdata/f000000b    (de CTRNAND:/data/<ID0>/extdata/00048000/f000000b)

y escribe donde_iban.txt con la ruta original de cada una.

    python extraer_plaza_mii.py nand.bin --otp otp.bin --boot9 boot9.bin
"""

import argparse
import struct
import sys
from pathlib import Path

try:
    from pyctr.crypto import BootromNotFoundError, CorruptBootromError, CryptoEngine, CryptoError
    from pyctr.type.nand import NAND, NANDError
except ImportError:
    sys.exit('Falta la librería pyctr. Instálala con:  pip install "pyctr>=0.7,<0.8"')

# Plaza Mii StreetPass es el título 00040010 000XX800; su savedata de sistema
# usa el ID 0x00020000 | XX8, que cambia según la región de la consola.
REGIONES_PLAZA = {0x208: 'JPN', 0x218: 'USA', 0x228: 'EUR', 0x268: 'CHN', 0x278: 'KOR', 0x288: 'TWN'}

# Extdata compartido de la NAND con los Miis (CFL_DB.dat) y los iconos de StreetPass (idb.dat, idbt.dat).
EXTDATA_ALTO = 0x00048000
EXTDATA_BAJO = 0xF000000B


class ErrorExtraccion(Exception):
    pass


# ---------------------------------------------------------------------------
# Lector FAT mínimo (solo lectura) para la partición CTRNAND
# ---------------------------------------------------------------------------

class EntradaFat:
    __slots__ = ('nombre', 'es_dir', 'cluster', 'tam')

    def __init__(self, nombre, es_dir, cluster, tam):
        self.nombre = nombre
        self.es_dir = es_dir
        self.cluster = cluster
        self.tam = tam


def _checksum_nombre_corto(nombre83):
    suma = 0
    for b in nombre83:
        suma = (((suma & 1) << 7) + (suma >> 1) + b) & 0xFF
    return suma


class VolumenFat:
    """Lector de FAT12/16/32. La CTRNAND es FAT16, pero no cuesta nada soportar el resto."""

    def __init__(self, f):
        self.f = f
        bs = self._leer(0, 512)
        if bs[510:512] != b'\x55\xaa':
            raise ErrorExtraccion('La CTRNAND descifrada no tiene un sector de arranque FAT válido. '
                                  '¿El OTP es de esta misma consola?')
        (self.bytes_sector, self.sectores_cluster, reservados, n_fats,
         entradas_raiz, total16, _, fat16) = struct.unpack_from('<HBHBHHBH', bs, 0x0B)
        total32, fat32 = struct.unpack_from('<II', bs, 0x20)
        if not self.bytes_sector or not self.sectores_cluster:
            raise ErrorExtraccion('Sector de arranque FAT corrupto en la CTRNAND.')

        sectores_fat = fat16 or fat32
        total = total16 or total32
        sectores_raiz = (entradas_raiz * 32 + self.bytes_sector - 1) // self.bytes_sector
        inicio_raiz = reservados + n_fats * sectores_fat
        self.inicio_datos = (inicio_raiz + sectores_raiz) * self.bytes_sector
        self.tam_cluster = self.bytes_sector * self.sectores_cluster
        self.n_clusters = (total - inicio_raiz - sectores_raiz) // self.sectores_cluster

        if self.n_clusters < 4085:
            self.tipo = 12
        elif self.n_clusters < 65525:
            self.tipo = 16
        else:
            self.tipo = 32

        self.fat = self._leer(reservados * self.bytes_sector, sectores_fat * self.bytes_sector)

        if self.tipo == 32:
            self.raiz = struct.unpack_from('<I', bs, 0x2C)[0]
        else:
            self.raiz = None
            self.datos_raiz = self._leer(inicio_raiz * self.bytes_sector, entradas_raiz * 32)

    def _leer(self, pos, tam):
        self.f.seek(pos)
        return self.f.read(tam)

    def _siguiente(self, c):
        if self.tipo == 16:
            v = struct.unpack_from('<H', self.fat, c * 2)[0]
            return None if v >= 0xFFF7 else v
        if self.tipo == 32:
            v = struct.unpack_from('<I', self.fat, c * 4)[0] & 0x0FFFFFFF
            return None if v >= 0x0FFFFFF7 else v
        off = c + c // 2
        v = self.fat[off] | (self.fat[off + 1] << 8)
        v = v >> 4 if c & 1 else v & 0xFFF
        return None if v >= 0xFF7 else v

    def _cadena(self, primero):
        clusters = []
        c = primero
        while c is not None and 2 <= c < self.n_clusters + 2:
            if len(clusters) > self.n_clusters:
                raise ErrorExtraccion('Cadena de clusters circular en la CTRNAND.')
            clusters.append(c)
            c = self._siguiente(c)
        return clusters

    def _leer_clusters(self, primero, tam=None):
        partes = []
        restante = tam
        for c in self._cadena(primero):
            n = self.tam_cluster if restante is None else min(self.tam_cluster, restante)
            partes.append(self._leer(self.inicio_datos + (c - 2) * self.tam_cluster, n))
            if restante is not None:
                restante -= n
                if restante <= 0:
                    break
        datos = b''.join(partes)
        if tam is not None and len(datos) < tam:
            raise ErrorExtraccion('Archivo truncado en la CTRNAND (cadena de clusters demasiado corta).')
        return datos

    def _entradas(self, datos):
        trozos_lfn = []
        checksum_lfn = None
        for i in range(0, len(datos) - 31, 32):
            e = datos[i:i + 32]
            if e[0] == 0x00:
                break
            if e[0] == 0xE5:
                trozos_lfn = []
                continue
            atributos = e[11]
            if atributos == 0x0F:
                # Nombre largo (LFN): los trozos aparecen del último al primero
                if e[0] & 0x40:
                    trozos_lfn = []
                    checksum_lfn = e[13]
                trozos_lfn.insert(0, e[1:11] + e[14:26] + e[28:32])
                continue
            if atributos & 0x08:
                trozos_lfn = []
                continue

            nombre = None
            if trozos_lfn and checksum_lfn == _checksum_nombre_corto(e[0:11]):
                nombre = b''.join(trozos_lfn).decode('utf-16-le', 'replace').split('\x00')[0]
            trozos_lfn = []
            if not nombre:
                base = bytearray(e[0:8])
                if base[0] == 0x05:
                    base[0] = 0xE5
                base = base.decode('latin-1').rstrip()
                ext = e[8:11].decode('latin-1').rstrip()
                if e[12] & 0x08:
                    base = base.lower()
                if e[12] & 0x10:
                    ext = ext.lower()
                nombre = f'{base}.{ext}' if ext else base
            if nombre in ('.', '..'):
                continue

            cluster = struct.unpack_from('<H', e, 26)[0]
            if self.tipo == 32:
                cluster |= struct.unpack_from('<H', e, 20)[0] << 16
            tam = struct.unpack_from('<I', e, 28)[0]
            yield EntradaFat(nombre, bool(atributos & 0x10), cluster, tam)

    def listar(self, directorio=None):
        """Lista un directorio (None = raíz)."""
        if directorio is None:
            datos = self.datos_raiz if self.raiz is None else self._leer_clusters(self.raiz)
        else:
            datos = self._leer_clusters(directorio.cluster)
        return list(self._entradas(datos))

    def buscar(self, ruta):
        """Busca una ruta tipo '/data/xxxx' sin distinguir mayúsculas. Devuelve None si no existe."""
        actual = None
        for parte in [p for p in ruta.split('/') if p]:
            if actual is not None and not actual.es_dir:
                return None
            for entrada in self.listar(actual):
                if entrada.nombre.lower() == parte.lower():
                    actual = entrada
                    break
            else:
                return None
        return actual

    def leer_archivo(self, entrada):
        if entrada.tam == 0:
            return b''
        return self._leer_clusters(entrada.cluster, entrada.tam)

    def copiar(self, entrada, destino):
        """Copia un archivo o un directorio entero de la CTRNAND a disco."""
        if entrada.es_dir:
            destino.mkdir(parents=True, exist_ok=True)
            for hijo in self.listar(entrada):
                self.copiar(hijo, destino / hijo.nombre)
        else:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(self.leer_archivo(entrada))


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description='Copia de la CTRNAND la savedata y el extdata de Plaza Mii StreetPass.')
    p.add_argument('nand', type=Path, help='dump de la NAND (p. ej. nand.bin o <serie>_sysnand_000.bin de GodMode9)')
    p.add_argument('--otp', type=Path, help='otp.bin de ESA consola (no hace falta si la NAND es de GodMode9 '
                                           'y lleva essential.exefs dentro)')
    p.add_argument('--boot9', type=Path, help='boot9.bin (vale el de cualquier 3DS; si no, se busca en '
                                             '~/.3ds/boot9.bin o en la variable BOOT9_PATH)')
    p.add_argument('--movable', type=Path, help='movable.sed (opcional: se lee de la propia NAND)')
    p.add_argument('--cid', type=Path, help='nand_cid.bin (opcional, casi nunca hace falta)')
    p.add_argument('-o', '--salida', type=Path, default=Path('plaza_mii_extraida'),
                   help='carpeta de salida (por defecto: plaza_mii_extraida)')
    args = p.parse_args()

    for nombre, ruta in (('la NAND', args.nand), ('el OTP', args.otp), ('el boot9', args.boot9),
                         ('el movable.sed', args.movable), ('el nand_cid', args.cid)):
        if ruta is not None and not ruta.is_file():
            bins = sorted(f'{f.name}  ({f.stat().st_size:,} bytes)' for f in Path('.').glob('*.bin'))
            sys.exit(f'No encuentro {nombre}: "{ruta}" no existe en esta carpeta.\n'
                     'Archivos .bin que sí hay aquí:\n  ' + ('\n  '.join(bins) or '(ninguno)') +
                     '\nUsa el nombre exacto (entre comillas si tiene espacios).')
    if args.boot9 is not None and args.boot9.stat().st_size not in (0x8000, 0x10000):
        sys.exit(f'"{args.boot9}" no parece un boot9.bin: mide {args.boot9.stat().st_size:,} bytes '
                 'y debería medir 65,536 (o 32,768).')

    try:
        crypto = CryptoEngine(boot9=str(args.boot9) if args.boot9 else None)
    except (BootromNotFoundError, CorruptBootromError, FileNotFoundError) as e:
        sys.exit(f'No se pudo cargar boot9.bin ({e}).\n'
                 'Hace falta un boot9.bin volcado de una 3DS (cualquiera vale, no es único por consola). '
                 'Pásalo con --boot9 ruta/boot9.bin')

    otp = args.otp.read_bytes() if args.otp else None
    cid = args.cid.read_bytes()[:0x10] if args.cid else None
    movable = args.movable.read_bytes() if args.movable else None

    print(f'Abriendo {args.nand} ...')
    try:
        nand = NAND(str(args.nand), crypto=crypto, otp=otp, cid=cid, auto_raise_exceptions=False)
        nand.raise_if_ctr_failed()
        vol = VolumenFat(nand.open_ctr_partition())
    except CryptoError as e:
        sys.exit(f'Error con el OTP: {e}\n¿Seguro que el otp.bin es de esta consola y está completo (256 bytes)?')
    except NANDError as e:
        if not otp and 'OTP' in str(e):
            sys.exit('Esta NAND no lleva essential.exefs dentro, así que hay que pasar el OTP con --otp otp.bin')
        sys.exit(f'No se pudo abrir la NAND: {e}')
    except ErrorExtraccion as e:
        sys.exit(str(e))

    if movable is None:
        entrada = vol.buscar('/private/movable.sed')
        if entrada is not None:
            movable = vol.leer_archivo(entrada)

    carpeta_data = vol.buscar('/data')
    dirs_data = [e.nombre for e in vol.listar(carpeta_data) if e.es_dir] if carpeta_data else []
    if not dirs_data:
        sys.exit('La NAND no tiene carpeta /data/<ID0>: no hay datos de usuario que extraer.')

    # El nombre de /data/<ID0> sale del movable.sed; si no cuadra y solo hay una carpeta, se usa esa
    id0 = None
    if movable is not None:
        crypto.setup_sd_key(movable)
        id0 = crypto.id0.hex()
        if id0 not in [d.lower() for d in dirs_data]:
            print(f'Aviso: el movable.sed corresponde a {id0}, que no está en la NAND {dirs_data}.')
            id0 = None
    if id0 is None:
        if len(dirs_data) != 1:
            sys.exit(f'Hay varias carpetas en /data ({dirs_data}) y no sé cuál usar. Pasa el movable.sed correcto.')
        id0 = dirs_data[0]

    salida = args.salida
    notas = [
        'Datos de guardado de Plaza Mii StreetPass sacados de la CTRNAND.',
        f'ID0 de la consola: {id0}',
        '',
        'Carpeta copiada  ->  dónde iba en la CTRNAND',
    ]
    copiadas = 0

    for unico, region in REGIONES_PLAZA.items():
        nombre = f'{0x00020000 | unico:08x}'
        ruta = f'/data/{id0}/sysdata/{nombre}'
        entrada = vol.buscar(ruta)
        if entrada is None:
            continue
        vol.copiar(entrada, salida / 'savedata' / nombre)
        notas.append(f'savedata/{nombre}  ->  CTRNAND:{ruta}   (región {region})')
        print(f'savedata/{nombre}  <-  {ruta}')
        copiadas += 1

    nombre = f'{EXTDATA_BAJO:08x}'
    ruta = f'/data/{id0}/extdata/{EXTDATA_ALTO:08x}/{nombre}'
    entrada = vol.buscar(ruta)
    if entrada is not None:
        vol.copiar(entrada, salida / 'extdata' / nombre)
        notas.append(f'extdata/{nombre}  ->  CTRNAND:{ruta}')
        print(f'extdata/{nombre}  <-  {ruta}')
        copiadas += 1

    if not copiadas:
        sys.exit('No se encontró ni la savedata ni el extdata de la Plaza Mii en esta NAND.')

    notas += [
        '',
        'En GodMode9 la CTRNAND de la consola es la unidad "1:" (SYSNAND CTRNAND).',
        'Si los pones en otra 3DS: la carpeta <ID0> de esa consola tendrá otro nombre, y como estos',
        'datos van firmados con el movable.sed de la consola original, después hay que usar la',
        'opción "Fix CMACs" de GodMode9 sobre ellos.',
    ]
    salida.mkdir(parents=True, exist_ok=True)
    with open(salida / 'donde_iban.txt', 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(notas) + '\n')

    print(f'\nListo: {salida}')


if __name__ == '__main__':
    main()
