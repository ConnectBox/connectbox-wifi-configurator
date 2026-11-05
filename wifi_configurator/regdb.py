"""Read the Linux wireless regulatory database (regulatory.db) without crda.

crda's /sbin/regdbdump decoded the old regulatory.bin into text.  crda is not
in Debian 13 (trixie): there the kernel reads wireless-regdb's
/lib/firmware/regulatory.db itself.  This module decodes that file into the
same text regdbdump printed, so scan.py's parser works unchanged:

    country US: DFS-FCC
    	(2402.000 - 2472.000 @ 40.000), (N/A, 30.00), (N/A)
    	...
    <blank line>

File format (as read by the kernel, net/wireless/reg.c "fwdb"), big endian:
  header:     u32 magic 'RGDB', u32 version 20
  countries:  {char alpha2[2]; u16 coll_ptr}... ending with alpha2 = 0,0
  collection: {u8 len; u8 n_rules; u8 dfs_region} then, at len rounded up
              to 2, n_rules u16 rule pointers
  rule:       {u8 len; u8 flags; u16 max_eirp; u32 start, end, max_bw (kHz)}
  Pointers are byte offsets divided by 4.
"""
import struct

REGULATORY_DB = "/lib/firmware/regulatory.db"
MAGIC = 0x52474442
VERSION = 20

FLAGS = (  # rule flag bits, in regdbdump's order
    (1 << 0, "NO-OFDM"),
    (1 << 1, "NO-OUTDOOR"),
    (1 << 2, "DFS"),
    (1 << 3, "NO-IR"),
    (1 << 4, "AUTO-BW"),
)
DFS_REGIONS = {0: "DFS-UNSET", 1: "DFS-FCC", 2: "DFS-ETSI", 3: "DFS-JP"}


def _rule_line(data, ptr):
    _rlen, flags, max_eirp, start, end, max_bw = struct.unpack_from(">BBHIII", data, ptr << 2)
    line = "\t(%.3f - %.3f @ %.3f), (N/A, %.2f), (N/A)" % (
        start / 1000.0, end / 1000.0, max_bw / 1000.0, max_eirp / 100.0)
    for bit, name in FLAGS:
        if flags & bit:
            line += ", " + name
    return line


def dump_lines(path=REGULATORY_DB):
    """Return the database as regdbdump-style text lines (one block per country)."""
    with open(path, "rb") as f:
        data = f.read()
    magic, version = struct.unpack_from(">II", data, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError("%s: not a version %d regulatory.db" % (path, VERSION))
    lines = []
    offset = 8
    while offset + 4 <= len(data):
        alpha2 = data[offset:offset + 2]
        (coll_ptr,) = struct.unpack_from(">H", data, offset + 2)
        offset += 4
        if alpha2 == b"\0\0":
            break
        coll = coll_ptr << 2
        clen, n_rules, dfs_region = struct.unpack_from(">BBB", data, coll)
        rules_at = coll + ((clen + 1) & ~1)
        lines.append("country %s: %s" % (alpha2.decode("ascii"),
                                         DFS_REGIONS.get(dfs_region & 0x3, "DFS-UNSET")))
        for i in range(n_rules):
            (rule_ptr,) = struct.unpack_from(">H", data, rules_at + 2 * i)
            lines.append(_rule_line(data, rule_ptr))
        lines.append("")
    return lines


if __name__ == "__main__":
    print("\n".join(dump_lines()))
