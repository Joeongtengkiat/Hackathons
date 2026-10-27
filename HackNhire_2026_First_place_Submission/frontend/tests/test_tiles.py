import struct
import unittest
import zlib

import numpy as np

import tiles
from backend import common


def make_grid(width=600, height=600, default=0):
    """The stored map's address system, smaller: top-left 103.0, -1.8, pixels of 1/8192 degree (exact in binary, so the map edge is exact)."""
    return common.Grid("test", 103.0, -1.8, 1 / 8192, 1 / 8192, width, height, 4326, True,
                       default_class=default, fingerprint="test")


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * n
    return int(x), int(y)


def decode_png(data):
    """Our palette PNG -> (indices, rgba list), checking signature, CRCs, filter bytes."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = body
        pos += 12 + length
    w, h, depth, colour_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, colour_type) == (8, 3)
    raw = np.frombuffer(zlib.decompress(chunks[b"IDAT"]), np.uint8).reshape(h, w + 1)
    assert not raw[:, 0].any()
    plte, trns = chunks[b"PLTE"], chunks[b"tRNS"]
    rgba = [(plte[3 * i], plte[3 * i + 1], plte[3 * i + 2], trns[i]) for i in range(len(plte) // 3)]
    return raw[:, 1:], rgba


class TileMathTest(unittest.TestCase):
    def setUp(self):
        self.grid = make_grid()
        self.classes = np.random.default_rng(1).integers(0, 5, (600, 600)).astype(np.uint8)

    def test_tile_pixels_take_the_colour_point_to_pixel_gives(self):
        z = 14
        x, y = lonlat_to_tile(103.02, -1.81, z)
        tile = tiles.render_tile(self.classes, self.grid, z, x, y)
        lon, lat = tiles.tile_lonlat(z, x, y)
        for i in (0, 37, 128, 255):
            for j in (0, 91, 200, 255):
                row, col = self.grid.point_to_pixel(float(lon[j]), float(lat[i]))
                expected = self.classes[row, col] if self.grid.inside(row, col) else tiles.TRANSPARENT
                self.assertEqual(tile[i, j], expected)

    def test_off_the_map_is_transparent(self):
        x, y = lonlat_to_tile(102.5, -1.5, 12)
        tile = tiles.render_tile(self.classes, self.grid, 12, x, y)
        self.assertTrue((tile == tiles.TRANSPARENT).all())

    def test_map_edges_follow_the_floor_rule(self):
        """Review focus 4: the east edge itself is outside the map, just west of it is inside."""
        east = 103.0 + self.grid.width / 8192
        self.assertFalse(self.grid.inside(*self.grid.point_to_pixel(east, -1.81)))
        x, y = lonlat_to_tile(east, -1.81, 18)
        tile = tiles.render_tile(self.classes, self.grid, 18, x, y)
        lon, _ = tiles.tile_lonlat(18, x, y)
        self.assertTrue((tile[:, lon >= east] == tiles.TRANSPARENT).all())
        self.assertTrue((tile[:, lon < east] != tiles.TRANSPARENT).all())


class PngTest(unittest.TestCase):
    def test_round_trip(self):
        indices = np.array([[0, 1, 2], [3, 4, tiles.TRANSPARENT]], np.uint8)
        got, rgba = decode_png(tiles.encode_png(indices, tiles.palette()))
        np.testing.assert_array_equal(got, indices)
        self.assertEqual(rgba[3], (0xE3, 0x1A, 0x1C, 255))
        self.assertEqual(rgba[tiles.TRANSPARENT][3], 0)

    def test_hidden_classes_are_transparent(self):
        rgba = tiles.palette(hidden={0, 4})
        self.assertEqual((rgba[0][3], rgba[4][3], rgba[2][3]), (0, 0, 255))

    def test_qt_reads_the_png(self):
        from PyQt5.QtGui import QImage
        image = QImage.fromData(tiles.encode_png(np.full((4, 5), 2, np.uint8), tiles.palette()), "PNG")
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (5, 4))
        self.assertEqual(image.pixelColor(1, 1).name(), "#1b7837")

    def test_parse_tile_path(self):
        self.assertEqual(tiles.parse_tile_path("/12/3245/2045.png", "hide=0,4"), (12, 3245, 2045, {0, 4}))
        self.assertEqual(tiles.parse_tile_path("/12/3245/2045.png", "map=map_riau&hide=0,4"),
                         (12, 3245, 2045, {0, 4}))
        self.assertEqual(tiles.parse_tile_path("/12/3245/2045.png", ""), (12, 3245, 2045, set()))
        self.assertIsNone(tiles.parse_tile_path("/12/x/2045.png", ""))
        self.assertIsNone(tiles.parse_tile_path("/12/2045", ""))
