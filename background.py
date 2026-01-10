import io
import sys
from pprint import pprint, pformat
import textwrap
import struct
from operator import itemgetter

from aseprite import parse_file
from aseprite import PaletteChunk, OldPaletteChunk, TilesetChunkInternal, CelChunk_CompressedTilemap

import os

PNB = os.environ.get("PNB", "2WORD")
assert PNB in {"2WORD", "1WORD"}

def pprinti(o, i):
    s = pformat(o)
    print(textwrap.indent(s, '    ' * i))

def pack_bgr555(red, green, blue):
    bgr = (
        ((red >> 3) << 0) |
        ((green >> 3) << 5) |
        ((blue >> 3) << 10)
    )
    return struct.pack(">H", bgr)

# 2x2

def pack_index_2word(character_size, y_flip, x_flip, id):
    tile_id = id * character_size
    assert tile_id < 0x7fff, tile_id
    pattern = (int(y_flip) << 31) | (int(x_flip) << 30) | tile_id
    return struct.pack(">I", pattern)

def pack_index_1word(character_size, y_flip, x_flip, id):
    if character_size == 8:
        # 2x2 cell
        tile_id = (id * character_size) >> 2
    elif character_size == 2:
        # 1x1 cell
        tile_id = (id * character_size) >> 0
    else:
        assert False, character_size

    assert tile_id < 0x3ff, tile_id
    pattern = (int(y_flip) << 11) | (int(x_flip) << 10) | tile_id
    return struct.pack(">H", pattern)

def pack_index(character_size, y_flip, x_flip, id):
    if PNB == "2WORD":
        return pack_index_2word(character_size, y_flip, x_flip, id)
    else:
        return pack_index_1word(character_size, y_flip, x_flip, id)

def pack_old_palette_chunk(old_palette_chunk):
    with open("palette.bin", "wb") as f:
        for color in old_palette_chunk.packets[0].colors:
            f.write(pack_bgr555(*color))

def pack_palette_chunk(f, palette_chunk):
    assert palette_chunk.first_color_index_to_change == 0

    for entry in palette_chunk.entries:
        color = (entry.red, entry.green, entry.blue)
        f.write(pack_bgr555(*color))

def pack_palette(f, palette):
    if type(palette) is PaletteChunk:
        pack_palette_chunk(f, palette)
    elif type(palette) is OldPaletteChunk:
        pack_old_palette_chunk(f, palette)
    else:
        assert False, type(palette)

def pack_character_2x2(tileset_chunk, offset):
    assert tileset_chunk.tile_width == 16
    assert tileset_chunk.tile_height == 16
    assert type(tileset_chunk.data) == TilesetChunkInternal

    buf = bytearray(16 * 16)

    for cell_ix in range(4):
        for y in range(8):
            for x in range(8):
                tileset_x = 8 * (cell_ix % 2) + x
                tileset_y = 8 * (cell_ix // 2) + y
                px = tileset_chunk.data.pixel[offset + tileset_y * 16 + tileset_x]
                buf[cell_ix * 8 * 8 + y * 8 + x] = px

    return bytes(buf)

def pack_character_1x1(tileset_chunk, offset):
    assert tileset_chunk.tile_width == 8
    assert tileset_chunk.tile_height == 8
    assert type(tileset_chunk.data) == TilesetChunkInternal

    buf = bytearray(8 * 8)

    for y in range(8):
        for x in range(8):
            tileset_x = x
            tileset_y = y
            px = tileset_chunk.data.pixel[offset + tileset_y * 8 + tileset_x]
            buf[y * 8 + x] = px

    return bytes(buf)

def pack_character_patterns(f, tileset_chunk):
    for i in range(tileset_chunk.number_of_tiles):
        offset = tileset_chunk.tile_width * tileset_chunk.tile_height * i

        if tileset_chunk.tile_width == 8 and tileset_chunk.tile_height == 8:
            buf = pack_character_1x1(tileset_chunk, offset)
        elif tileset_chunk.tile_width == 16 and tileset_chunk.tile_height == 16:
            buf = pack_character_2x2(tileset_chunk, offset)
        else:
            assert False, (tileset_chunk.tile_width, tileset_chunk.tile_height)

        f.write(buf)

def tileset_chunk_character_size(tileset_chunk):
    # assumes 256 color
    if tileset_chunk.tile_width == 8 and tileset_chunk.tile_height == 8:
        return 2
    elif tileset_chunk.tile_width == 16 and tileset_chunk.tile_height == 16:
        return 8
    else:
        assert False, (tileset_chunk.tile_width, tileset_chunk.tile_height)

def pack_pattern_name_table(f, cel_chunk, tileset_chunk):
    x_cells = 64 // (tileset_chunk.tile_width // 8)
    y_cells = 64 // (tileset_chunk.tile_height // 8)
    character_size = tileset_chunk_character_size(tileset_chunk)

    assert type(cel_chunk.data) == CelChunk_CompressedTilemap
    #assert cel_chunk.data.width_in_number_of_tiles <= 64
    #assert cel_chunk.data.height_in_number_of_tiles <= 64

    tile_width = cel_chunk.data.width_in_number_of_tiles
    tile_height = cel_chunk.data.height_in_number_of_tiles

    print(tile_width, tile_height)

    h_pages = ((tile_width + (x_cells - 1)) & (~(x_cells - 1))) // x_cells
    v_pages = ((tile_height + (y_cells - 1)) & (~(y_cells - 1))) // y_cells

    if h_pages > 2:
        h_pages = 2
    if v_pages > 2:
        v_pages = 2

    for v_page in range(v_pages):
        for h_page in range(h_pages):
            for y in range(y_cells):
                for x in range(x_cells):
                    tx = (h_page * x_cells) + x
                    ty = (v_page * y_cells) + y
                    if tx >= tile_width or ty >= tile_height:
                        f.write(pack_index(0, 0, 0, 0))
                    else:
                        cel_chunk_ix = ty * tile_width + tx
                        tile_data = cel_chunk.data.tile[cel_chunk_ix]

                        tile_id = tile_data & cel_chunk.data.bitmask_for_tile_id.value
                        x_flip = (tile_data & cel_chunk.data.bitmask_for_x_flip.value) != 0
                        y_flip = (tile_data & cel_chunk.data.bitmask_for_y_flip.value) != 0

                        f.write(pack_index(character_size, y_flip, x_flip, tile_id))

    return h_pages, v_pages

def generate_separate_files(mem):
    tilesets, layers, palette, cel_chunks = parse_file(mem)

    with open("palette.bin", "wb") as f:
        pack_palette(f, palette)

    for tileset_index, tileset_chunk in sorted(tilesets.items(), key=itemgetter(0)):
        filename = f"character_pattern__tileset_{tileset_index}.bin"
        print(filename)
        with open(filename, "wb") as f:
            pack_character_patterns(f, tileset_chunk)

    for layer_index, cel_chunk in sorted(cel_chunks.items(), key=itemgetter(0)):
        #layers[layer_index]
        print(f"layer={layer_index} layer_name={layers[layer_index].layer_name} tileset={layers[layer_index].tileset_index}");

        filename = f"pattern_name_table__layer_{layer_index}.bin"
        print(filename)
        with open(filename, "wb") as f:
            tileset_chunk = tilesets[layers[layer_index].tileset_index]
            pack_pattern_name_table(f, cel_chunk, tileset_chunk)

PNCN0__N0PNB__2WORD = (0 << 15) # PNB_2WORD
PNCN0__N0PNB__1WORD = (1 << 15) # PNB_1WORD
PNCN0__N0CNSM = (1 << 14) # CN_12BIT

PLSZ__N0PLSZ__1x1 = (0b00 << 0) # PL_SIZE_1x1
PLSZ__N0PLSZ__2x1 = (0b01 << 0) # PL_SIZE_2x1
PLSZ__N0PLSZ__2x2 = (0b11 << 0) # PL_SIZE_2x2

CHCTLA__N0CHCN__16_COLOR = (0b000 << 4) # COL_TYPE_16
CHCTLA__N0CHCN__256_COLOR = (0b001 << 4) # COL_TYPE_256
CHCTLA__N0CHCN__2048_COLOR = (0b010 << 4) # COL_TYPE_2048
CHCTLA__N0CHCN__32K_COLOR = (0b011 << 4) # COL_TYPE_32768
CHCTLA__N0CHCN__16M_COLOR = (0b100 << 4) # COL_TYPE_1M

CHCTLA__N0CHSZ__1x1_CELL = (0 << 0)
CHCTLA__N0CHSZ__2x2_CELL = (1 << 0)

def header_7shades(size_of_cel_data, # bytes
                   size_of_map_data, # bytes
                   tile_character_size, # CHCTLA__N0CHSZ
                   tile_color_mode, # CHCTLA__N0CHCN
                   plane_size, # PLSZ
                   map_data, # PNCN0
                   ): #
    file_type_id = 0x5
    width = 1
    height = 1

    print("7shades:\n"
          f"  file_type_id: {file_type_id}\n"
          f"  size_of_cel_data: {size_of_cel_data}\n"
          f"  size_of_map_data: {size_of_map_data}\n"
          f"  tile_character_size: {tile_character_size}\n"
          f"  tile_color_mode: {tile_color_mode}\n"
          f"  plane_size: {plane_size}\n"
          f"  map_data: {map_data}\n"
          f"  width: {width}\n"
          f"  height: {height}\n")

    return struct.pack(">IIIIIIIHH",
                       file_type_id,
                       size_of_cel_data,
                       size_of_map_data,
                       tile_character_size,
                       tile_color_mode,
                       plane_size,
                       map_data,
                       width,
                       height)

def palette_pad_7shades(f_tmp, palette_size):
    # - Pallet data block of 0, 32, or 512 bytes based on the color mode indicated in header
    for valid_size in [32, 512]:
        if palette_size > valid_size:
            continue
        while palette_size < valid_size:
            f_tmp.write(bytes([0]))
            palette_size += 1
        break
    else:
        assert False, palette_size
    return palette_size

def chctla_n0chsz(tileset_chunk):
    if tileset_chunk.tile_width == 8 and tileset_chunk.tile_height == 8:
        return CHCTLA__N0CHSZ__1x1_CELL
    elif tileset_chunk.tile_width == 16 and tileset_chunk.tile_height == 16:
        return CHCTLA__N0CHSZ__2x2_CELL
    else:
        assert False, (tileset_chunk.tile_width, tileset_chunk.tile_height)

def hv_pages_plsz(hv_pages):
    if hv_pages == (1, 1):
        return PLSZ__N0PLSZ__1x1
    elif hv_pages == (2, 1):
        return PLSZ__N0PLSZ__2x1
    elif hv_pages == (2, 2):
        return PLSZ__N0PLSZ__2x2
    else:
        assert False, hv_pages

def generate_7shades(mem, output_filename):
    tilesets, layers, palette, cel_chunks = parse_file(mem)

    f_tmp = io.BytesIO()

    pack_palette(f_tmp, palette)
    palette_size = f_tmp.getbuffer().nbytes
    palette_size = palette_pad_7shades(f_tmp, palette_size)
    print("palette_size", palette_size)

    pattern_name_tables_start = f_tmp.getbuffer().nbytes
    for layer_index, cel_chunk in sorted(cel_chunks.items(), key=itemgetter(0)):
        tileset_chunk = tilesets[layers[layer_index].tileset_index]
        hv_pages = pack_pattern_name_table(f_tmp, cel_chunk, tileset_chunk)
        break
    pattern_name_tables_end = f_tmp.getbuffer().nbytes
    size_of_map_data = pattern_name_tables_end - pattern_name_tables_start
    print("pattern_name_table_size", size_of_map_data)

    character_patterns_start = f_tmp.getbuffer().nbytes
    for tileset_index, tileset_chunk in sorted(tilesets.items(), key=itemgetter(0)):
        pack_character_patterns(f_tmp, tileset_chunk)
        break
    character_patterns_end = f_tmp.getbuffer().nbytes
    size_of_cel_data = character_patterns_end - character_patterns_start
    print("character_patterns_size", size_of_cel_data)

    tile_character_size = chctla_n0chsz(tileset_chunk)
    tile_color_mode = CHCTLA__N0CHCN__256_COLOR
    plane_size = hv_pages_plsz(hv_pages)
    map_data = PNCN0__N0PNB__2WORD if PNB == "2WORD" else PNCN0__N0PNB__1WORD

    header = header_7shades(size_of_cel_data,
                            size_of_map_data,
                            tile_character_size,
                            tile_color_mode,
                            plane_size,
                            map_data)

    with open(output_filename, "wb") as f:
        f.write(header)
        f.write(f_tmp.getvalue())

def read_input():
    with open(sys.argv[1], 'rb') as f:
        buf = f.read()
        mem = memoryview(buf)
    return mem

if len(sys.argv) == 3:
    mem = read_input()
    generate_7shades(mem, sys.argv[2])
elif len(sys.argv) == 2:
    mem = read_input()
    generate_separate_files(mem)
else:
    print(f"usage:")
    print()
    print("separated raw binaries")
    print(f"  python {sys.argv[0]} input.aseprite")
    print()
    print("7shades output:")
    print(f"  python {sys.argv[0]} input.aseprite output.bin")
