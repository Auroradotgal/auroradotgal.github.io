#!/usr/bin/env python3
"""
Extrae los datos de Plaza Mii StreetPass de un dump de NAND de 3DS.

Pensado para consolas que ya no arrancan (placa base muerta): solo hace falta
el dump de la NAND, el OTP de esa consola y un boot9.bin.

    python extraer_plaza_mii.py nand.bin --otp otp.bin --boot9 boot9.bin

Consulta README.md para más detalles.
"""

import argparse
import hashlib
import io
import struct
import sys
from pathlib import Path

try:
    from pyctr.crypto import BootromNotFoundError, CorruptBootromError, CryptoEngine, CryptoError
    from pyctr.type.exefs import ExeFSReader
    from pyctr.type.nand import NAND, NANDError
    from pyctr.type.save.diff import DIFF
    from pyctr.type.save.disa import DISA
    from pyctr.type.save.partdesc.ivfc import IVFCLevel4Reader
    from pyctr.type.sd import SDFilesystem
except ImportError:
    sys.exit('Falta la librería pyctr. Instálala con:  pip install "pyctr>=0.7,<0.8"')

# Plaza Mii StreetPass es el título 00040010 000XX800. Su "ID único" (XX8)
# depende de la región. La savedata del sistema usa el ID 0x00020000 | ID único
# y el extdata de la SD usa directamente el ID único.
REGIONES_PLAZA = {0x208: 'JPN', 0x218: 'USA', 0x228: 'EUR', 0x268: 'CHN', 0x278: 'KOR', 0x288: 'TWN'}

# Savedata del módulo CECD: los buzones de StreetPass (entradas y salidas).
CECD_SAVE_ID = 0x00010026

# Extdata compartido de la NAND con la base de datos de Miis (CFL_DB.dat),
# los iconos de juegos de StreetPass (idb.dat, idbt.dat) y la lista de bloqueados.
EXTDATA_COMPARTIDO = 0x00048000F000000B

BLOQUE_VACIO = 0x80000000


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
# Savedata (DISA + SAVE) y extdata (DIFF + VSXE)
# ---------------------------------------------------------------------------

FS_INFO = struct.Struct('<IIQIIQIIQIIQIIIIIIIIII')
ENTRADA_DIR = struct.Struct('<I16sIIIII')     # padre, nombre, siguiente, subdir, subarchivo, -, colisión
ENTRADA_ARCHIVO = struct.Struct('<I16sIIIQII')  # padre, nombre, siguiente, -, bloque, tamaño/uid, -, colisión

CARACTERES_PROHIBIDOS = set(b'/\\:*?"<>|')


def nombre_seguro(crudo):
    """Convierte un nombre de 16 bytes de la savedata en un nombre de archivo válido en cualquier SO."""
    crudo = crudo.split(b'\0', 1)[0]
    nombre = ''.join(chr(b) if 0x20 <= b < 0x7F and b not in CARACTERES_PROHIBIDOS else f'%{b:02X}'
                     for b in crudo)
    if nombre in ('', '.', '..'):
        nombre = ''.join(f'%{b:02X}' for b in crudo) or '%00'
    return nombre


def datos_particion(particion):
    """Contenido de una partición DISA/DIFF (nivel 4 de IVFC), sin descartar bloques con hash erróneo."""
    return IVFCLevel4Reader(particion.ivfc_hash_tree, verify=False).read()


class SistemaArchivosSave:
    """Sistema de archivos interno compartido por la savedata (SAVE) y el extdata (VSXE)."""

    def __init__(self, meta, offset_fs_info, datos=None, tablas_en_fat=True):
        (_, self.tam_bloque, _, _, _, _, _, _, offset_fat, tam_fat, _, offset_datos, n_bloques, _,
         tabla_dirs_lo, tabla_dirs_hi, max_dirs, _, tabla_arch_lo, tabla_arch_hi, max_archivos,
         _) = FS_INFO.unpack_from(meta, offset_fs_info)

        self.fat = meta[offset_fat:offset_fat + (tam_fat + 1) * 8]
        if datos is None:
            datos = meta[offset_datos:offset_datos + n_bloques * self.tam_bloque]
        self.datos = datos

        if tablas_en_fat:
            self.tabla_dirs = self._leer_cadena(tabla_dirs_lo)
            self.tabla_archivos = self._leer_cadena(tabla_arch_lo)
        else:
            off = tabla_dirs_lo | (tabla_dirs_hi << 32)
            self.tabla_dirs = meta[off:off + (max_dirs + 2) * ENTRADA_DIR.size]
            off = tabla_arch_lo | (tabla_arch_hi << 32)
            self.tabla_archivos = meta[off:off + (max_archivos + 1) * ENTRADA_ARCHIVO.size]

    def _bloques(self, primero):
        bloques = []
        actual = primero
        nodos = 0
        while actual is not None:
            nodos += 1
            if nodos > len(self.fat) // 8:
                raise ErrorExtraccion('FAT circular dentro de la savedata.')
            u, v = struct.unpack_from('<II', self.fat, (actual + 1) * 8)
            tam = 1
            if v & 0x80000000:
                # Nodo de varios bloques: la entrada siguiente indica dónde acaba
                u2, v2 = struct.unpack_from('<II', self.fat, (actual + 2) * 8)
                tam = (v2 & 0x7FFFFFFF) - (u2 & 0x7FFFFFFF) + 1
            bloques.extend(range(actual, actual + tam))
            siguiente = v & 0x7FFFFFFF
            actual = siguiente - 1 if siguiente else None
        return bloques

    def _leer_cadena(self, primero, tam=None):
        tb = self.tam_bloque
        datos = b''.join(self.datos[b * tb:(b + 1) * tb] for b in self._bloques(primero))
        if tam is not None:
            if len(datos) < tam:
                raise ErrorExtraccion('Archivo truncado dentro de la savedata.')
            datos = datos[:tam]
        return datos

    def recorrer(self):
        """Genera ('dir', ruta, None, None) y ('archivo', ruta, índice, (bloque, tamaño_o_uid))."""
        pendientes = [(1, '')]  # el directorio raíz siempre es la entrada 1
        visitados = set()
        while pendientes:
            indice, ruta = pendientes.pop()
            if indice in visitados:
                raise ErrorExtraccion('Directorio repetido dentro de la savedata.')
            visitados.add(indice)
            if ruta:
                yield 'dir', ruta, None, None
            _, _, _, sub_dir, sub_archivo, _, _ = ENTRADA_DIR.unpack_from(self.tabla_dirs, indice * ENTRADA_DIR.size)

            a = sub_archivo
            vistos = set()
            while a and a not in vistos:
                vistos.add(a)
                _, nombre, siguiente, _, bloque, tam, _, _ = ENTRADA_ARCHIVO.unpack_from(
                    self.tabla_archivos, a * ENTRADA_ARCHIVO.size)
                yield 'archivo', ruta + nombre_seguro(nombre), a, (bloque, tam)
                a = siguiente

            d = sub_dir
            while d and d not in visitados:
                _, nombre, siguiente, _, _, _, _ = ENTRADA_DIR.unpack_from(self.tabla_dirs, d * ENTRADA_DIR.size)
                pendientes.append((d, ruta + nombre_seguro(nombre) + '/'))
                d = siguiente


def extraer_savedata(datos_disa, destino, crypto):
    """Desempaqueta una savedata de sistema (contenedor DISA) en una carpeta. Devuelve el nº de archivos."""
    disa = DISA(io.BytesIO(datos_disa), crypto=crypto)
    part_a = datos_particion(disa.partitions[0])
    if part_a[0:8] != b'SAVE\x00\x00\x04\x00':
        raise ErrorExtraccion('La partición de la savedata no empieza por SAVE.')
    offset_fs_info = struct.unpack_from('<Q', part_a, 8)[0]

    if 1 in disa.partitions:
        fs = SistemaArchivosSave(part_a, offset_fs_info, datos=datos_particion(disa.partitions[1]),
                                 tablas_en_fat=False)
    else:
        fs = SistemaArchivosSave(part_a, offset_fs_info)

    destino.mkdir(parents=True, exist_ok=True)
    n = 0
    for tipo, ruta, _, info in fs.recorrer():
        if tipo == 'dir':
            (destino / ruta).mkdir(parents=True, exist_ok=True)
            continue
        bloque, tam = info
        contenido = b'' if bloque == BLOQUE_VACIO else fs._leer_cadena(bloque, tam)
        (destino / ruta).write_bytes(contenido)
        n += 1
    return n


def extraer_extdata(leer, destino, crypto):
    """
    Desempaqueta un extdata. `leer(sub_ruta)` devuelve los bytes de un archivo del
    extdata (p. ej. '00000000/00000001') o None si no existe. Devuelve el nº de archivos.
    """
    meta = leer('00000000/00000001')
    if meta is None:
        raise ErrorExtraccion('Falta el archivo de metadatos 00000000/00000001 del extdata.')
    vsxe = datos_particion(DIFF(io.BytesIO(meta), crypto=crypto).partitions[0])
    if vsxe[0:8] != b'VSXE\x00\x00\x03\x00':
        raise ErrorExtraccion('Los metadatos del extdata no empiezan por VSXE.')
    offset_fs_info = struct.unpack_from('<Q', vsxe, 8)[0]
    fs = SistemaArchivosSave(vsxe, offset_fs_info)

    destino.mkdir(parents=True, exist_ok=True)
    n = 0
    for tipo, ruta, indice, _ in fs.recorrer():
        if tipo == 'dir':
            (destino / ruta).mkdir(parents=True, exist_ok=True)
            continue
        # Cada archivo del extdata es un DIFF aparte; su nombre sale del índice en la tabla
        numero = indice + 1
        crudo = leer(f'{numero // 126:08x}/{numero % 126:08x}')
        if crudo is None:
            print(f'    ! falta el archivo físico de {ruta}, se omite')
            continue
        (destino / ruta).write_bytes(datos_particion(DIFF(io.BytesIO(crudo), crypto=crypto).partitions[0]))
        n += 1
    return n


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def id0_de_movable(crypto, movable):
    crypto.setup_sd_key(movable)
    return crypto.id0.hex()


def leer_essential(ruta):
    with ExeFSReader(ruta) as exefs:
        datos = {}
        for nombre in ('otp', 'nand_cid', 'movable'):
            if nombre in exefs.entries:
                with exefs.open(nombre) as f:
                    datos[nombre] = f.read()
        return datos


def main():
    p = argparse.ArgumentParser(
        description='Extrae los datos de Plaza Mii StreetPass de un dump de NAND de 3DS.')
    p.add_argument('nand', type=Path, help='dump de la NAND (p. ej. nand.bin o <serie>_sysnand_000.bin de GodMode9)')
    p.add_argument('--otp', type=Path, help='otp.bin de ESA consola (no hace falta si la NAND es de GodMode9 '
                                           'y lleva essential.exefs dentro)')
    p.add_argument('--boot9', type=Path, help='boot9.bin (vale el de cualquier 3DS; si no, se busca en '
                                             '~/.3ds/boot9.bin o en la variable BOOT9_PATH)')
    p.add_argument('--movable', type=Path, help='movable.sed (opcional: se lee de la propia NAND)')
    p.add_argument('--cid', type=Path, help='nand_cid.bin (opcional, casi nunca hace falta)')
    p.add_argument('--essential', type=Path, help='essential.exefs suelto de GodMode9 (opcional)')
    p.add_argument('--sd', type=Path, help='carpeta "Nintendo 3DS" de la SD de esa consola, para extraer '
                                          'también el extdata de la Plaza Mii que vive en la SD (opcional)')
    p.add_argument('-o', '--salida', type=Path, default=Path('plaza_mii_extraida'),
                   help='carpeta de salida (por defecto: plaza_mii_extraida)')
    p.add_argument('--copia-cruda', action='store_true',
                   help='guarda también los contenedores sin desempaquetar (solo para casos raros)')
    args = p.parse_args()

    try:
        crypto = CryptoEngine(boot9=str(args.boot9) if args.boot9 else None)
    except (BootromNotFoundError, CorruptBootromError, FileNotFoundError) as e:
        sys.exit(f'No se pudo cargar boot9.bin ({e}).\n'
                 'Hace falta un boot9.bin volcado de una 3DS (cualquiera vale, no es único por consola). '
                 'Pásalo con --boot9 ruta/boot9.bin')

    essential = leer_essential(args.essential) if args.essential else {}
    otp = args.otp.read_bytes() if args.otp else essential.get('otp')
    cid = args.cid.read_bytes()[:0x10] if args.cid else essential.get('nand_cid')
    movable = args.movable.read_bytes() if args.movable else essential.get('movable')

    print(f'Abriendo {args.nand} ...')
    try:
        nand = NAND(str(args.nand), crypto=crypto, otp=otp, cid=cid, auto_raise_exceptions=False)
        nand.raise_if_ctr_failed()
    except CryptoError as e:
        sys.exit(f'Error con el OTP: {e}\n¿Seguro que el otp.bin es de esta consola y está completo (256 bytes)?')
    except NANDError as e:
        if not otp and 'OTP' in str(e):
            sys.exit('Esta NAND no lleva essential.exefs dentro, así que hay que pasar el OTP con --otp otp.bin')
        sys.exit(f'No se pudo abrir la NAND: {e}')

    vol = VolumenFat(nand.open_ctr_partition())

    if movable is None:
        entrada = vol.buscar('/private/movable.sed')
        if entrada is not None:
            movable = vol.leer_archivo(entrada)
            print('movable.sed leído de la propia NAND.')

    carpeta_data = vol.buscar('/data')
    dirs_data = [e.nombre for e in vol.listar(carpeta_data) if e.es_dir] if carpeta_data else []
    if not dirs_data:
        sys.exit('La NAND no tiene carpeta /data/<ID0>: no hay datos de usuario que extraer.')

    id0 = None
    if movable is not None:
        id0 = id0_de_movable(crypto, movable)
        if id0 not in [d.lower() for d in dirs_data]:
            print(f'Aviso: el movable.sed corresponde a {id0}, que no está en la NAND {dirs_data}.')
            id0 = None
    if id0 is None:
        if len(dirs_data) != 1:
            sys.exit(f'Hay varias carpetas en /data ({dirs_data}) y no sé cuál usar. Pasa el movable.sed correcto.')
        id0 = dirs_data[0]
    print(f'ID0: {id0}')

    extraido = args.salida
    crudo = extraido / '_copia_cruda'
    base_data = f'/data/{id0}'

    def copiar_crudo(ruta_nand):
        entrada = vol.buscar(ruta_nand)
        if entrada is not None and args.copia_cruda:
            vol.copiar(entrada, crudo / ruta_nand.strip('/'))
        return entrada

    aviso_fallo = 'vuelve a ejecutar con --copia-cruda para guardar el contenedor sin desempaquetar'

    encontrado = False

    # 1) Savedata de la Plaza Mii (meet.dat: la plaza, los Miis conocidos, progreso...)
    for unico, region in REGIONES_PLAZA.items():
        save_id = 0x00020000 | unico
        ruta = f'{base_data}/sysdata/{save_id:08x}/00000000'
        entrada = copiar_crudo(ruta)
        if entrada is None:
            continue
        encontrado = True
        destino = extraido / f'plaza_mii_savedata_{region}_{save_id:08x}'
        print(f'\n[Plaza Mii {region}] savedata del sistema {save_id:08x}')
        try:
            n = extraer_savedata(vol.leer_archivo(entrada), destino, crypto)
            print(f'  {n} archivo(s) -> {destino}')
        except Exception as e:
            print(f'  ! no se pudo desempaquetar ({e}); {aviso_fallo}')

    if not encontrado:
        print('\nAviso: no se encontró la savedata de la Plaza Mii (00020208/00020218/00020228/...).')

    # 2) Buzones de StreetPass del módulo CECD
    ruta = f'{base_data}/sysdata/{CECD_SAVE_ID:08x}/00000000'
    entrada = copiar_crudo(ruta)
    if entrada is not None:
        destino = extraido / f'streetpass_cecd_{CECD_SAVE_ID:08x}'
        print(f'\n[StreetPass] buzones de CECD {CECD_SAVE_ID:08x}')
        try:
            n = extraer_savedata(vol.leer_archivo(entrada), destino, crypto)
            print(f'  {n} archivo(s) -> {destino}')
        except Exception as e:
            print(f'  ! no se pudo desempaquetar ({e}); {aviso_fallo}')

    # 3) Extdata compartido f000000b (Miis, iconos de StreetPass, lista de bloqueados)
    alto, bajo = EXTDATA_COMPARTIDO >> 32, EXTDATA_COMPARTIDO & 0xFFFFFFFF
    ruta_ext = f'{base_data}/extdata/{alto:08x}/{bajo:08x}'
    entrada_ext = copiar_crudo(ruta_ext)
    if entrada_ext is not None:
        destino = extraido / f'extdata_compartido_{bajo:08x}'
        print(f'\n[Extdata compartido {bajo:08x}] Miis e iconos de StreetPass')

        def leer_nand(sub):
            e = vol.buscar(f'{ruta_ext}/{sub}')
            return vol.leer_archivo(e) if e is not None else None

        try:
            n = extraer_extdata(leer_nand, destino, crypto)
            print(f'  {n} archivo(s) -> {destino}')
        except Exception as e:
            print(f'  ! no se pudo desempaquetar ({e}); {aviso_fallo}')
    else:
        print(f'\nAviso: no se encontró el extdata compartido {bajo:08x}.')

    # 4) Opcional: extdata de la Plaza Mii en la SD (cifrado con el movable.sed)
    if args.sd:
        if movable is None:
            print('\nNo hay movable.sed, así que no se puede descifrar la SD.')
        else:
            try:
                sd = SDFilesystem(str(args.sd), crypto=crypto)
            except Exception as e:
                print(f'\nNo se pudo abrir la SD ({e}). Pasa la carpeta "Nintendo 3DS" con --sd.')
                sd = None
            for id1 in (sd.id1s if sd else []):
                for unico, region in REGIONES_PLAZA.items():
                    base = f'extdata/00000000/{unico:08x}'
                    if not sd.isdir(base, id1=id1):
                        continue
                    destino = extraido / f'plaza_mii_extdata_sd_{region}_{unico:08x}'
                    print(f'\n[Plaza Mii {region}] extdata de la SD {unico:08x} (ID1 {id1})')

                    def leer_sd(sub, base=base, id1=id1):
                        if not sd.isfile(f'{base}/{sub}', id1=id1):
                            return None
                        with sd.open(f'{base}/{sub}', id1=id1) as f:
                            return f.read()

                    try:
                        n = extraer_extdata(leer_sd, destino, crypto)
                        print(f'  {n} archivo(s) -> {destino}')
                    except Exception as e:
                        print(f'  ! no se pudo desempaquetar ({e})')

    print(f'\nListo. Archivos de la partida en: {extraido}')


if __name__ == '__main__':
    main()
