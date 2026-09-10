"""Synthetic fixtures used only by the explicitly requested --preview mode."""
import struct


def demo_payloads(updated=False):
    dll = bytearray(256)
    dll[:2] = b"MZ"
    struct.pack_into("<I", dll, 0x3C, 0x80)
    dll[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", dll, 0x84, 0x14C)
    struct.pack_into("<H", dll, 0x96, 0x2000)
    struct.pack_into("<H", dll, 0x98, 0x10B)
    return {"d3d9.dll": bytes(dll),
            "dxvk.conf": b"dxvk.hud = fps\n" if updated else b"dxvk.hud = 0\n",
            "eldorado.dxvk-cache": b"DXVK" + (b"new-preview-data" if updated else b"old-preview-data")}
