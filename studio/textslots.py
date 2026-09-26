"""DP104 text slots: five 30-character ASCII strings on the safe raw-HID channel.

The firmware keeps one buffer of 30 bytes per slot; writes must pad with NUL
so stale characters from a longer previous text do not survive.
"""

from __future__ import annotations

SLOT_COUNT = 5
SLOT_LEN = 30
PACK_LIMIT = 25


def encode_slot(text: str) -> bytes:
    """ASCII bytes NUL-padded to exactly 30 bytes (write semantics)."""
    raw = text.encode("ascii", errors="replace")[:SLOT_LEN]
    return raw + b"\0" * (SLOT_LEN - len(raw))


def segments(text: str) -> list[tuple[int, bytes]]:
    """Split into HID-sized (offset, chunk) writes; 25 chars fit one report."""
    data = encode_slot(text)
    if len(data) <= PACK_LIMIT:
        return [(0, data)]
    return [(0, data[:PACK_LIMIT]), (PACK_LIMIT, data[PACK_LIMIT:])]


def write_slot(device, slot: int, text: str, hid_command) -> bool:
    for offset, chunk in segments(text):
        payload = [0x07, 26, 5, slot, offset, len(chunk), *chunk]
        reply = hid_command(device, payload)
        if reply is None or (reply and reply[0] == 0xFF):
            return False
    return True


def read_slot(device, slot: str, hid_command) -> str | None:
    parts: list[bytes] = []
    for offset, length in ((0, PACK_LIMIT), (PACK_LIMIT, SLOT_LEN - PACK_LIMIT)):
        reply = hid_command(device, [0x08, 26, 5, slot, offset, length])
        if reply is None or (reply and reply[0] == 0xFF):
            return None
        parts.append(bytes(reply[6:6 + length]))
    raw = b"".join(parts)[:SLOT_LEN]
    if len(raw) < SLOT_LEN:
        raw += b"\0" * (SLOT_LEN - len(raw))
    return raw.split(b"\0")[0].decode("ascii", "replace")


def read_all(device, hid_command) -> list[str]:
    return [read_slot(device, slot, hid_command) or "" for slot in range(SLOT_COUNT)]
