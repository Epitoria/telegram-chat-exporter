"""
Telegram Chat Exporter
Exports chats to HTML (Telegram Desktop style) or JSON.
Requires: pip install telethon python-dotenv pillow
"""

import asyncio
import html
import io
import json
# ujson is optional — install with: pip install ujson
# Pylance may show a "missing stub" warning; this is harmless.
try:
    import ujson as _json_mod  # type: ignore[import-untyped]  # noqa: F401
except ImportError:
    _json_mod = json  # type: ignore[assignment]
import mimetypes
import os
import struct
import threading
import time
import tkinter as tk
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from dotenv import load_dotenv

# ── FIX 1: Split imports so a missing sub-type doesn't kill the whole block ──
TELETHON_AVAILABLE = False
try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    TELETHON_AVAILABLE = True
except ImportError:
    pass

# ── Import tl.types one-by-one so a missing type never kills the whole app ──
# Some types were added or removed across Telethon versions.
# Every name below falls back to None if unavailable; callers use isinstance()
# guards that safely skip None, so the app still works on older/newer builds.

def _safe_import(module: str, name: str):
    """Return the named attribute from module, or None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module)
        return getattr(mod, name, None)
    except ImportError:
        return None

if TELETHON_AVAILABLE:
    _tl = "telethon.tl.types"
    # Core peer types
    Channel                    = _safe_import(_tl, "Channel")
    Chat                       = _safe_import(_tl, "Chat")
    User                       = _safe_import(_tl, "User")
    # Document attributes
    DocumentAttributeAnimated  = _safe_import(_tl, "DocumentAttributeAnimated")
    DocumentAttributeAudio     = _safe_import(_tl, "DocumentAttributeAudio")
    DocumentAttributeFilename  = _safe_import(_tl, "DocumentAttributeFilename")
    DocumentAttributeSticker   = _safe_import(_tl, "DocumentAttributeSticker")
    DocumentAttributeVideo     = _safe_import(_tl, "DocumentAttributeVideo")
    # Message entities
    MessageEntityBold          = _safe_import(_tl, "MessageEntityBold")
    MessageEntityBotCommand    = _safe_import(_tl, "MessageEntityBotCommand")
    MessageEntityCashtag       = _safe_import(_tl, "MessageEntityCashtag")
    MessageEntityCode          = _safe_import(_tl, "MessageEntityCode")
    MessageEntityEmail         = _safe_import(_tl, "MessageEntityEmail")
    MessageEntityHashtag       = _safe_import(_tl, "MessageEntityHashtag")
    MessageEntityItalic        = _safe_import(_tl, "MessageEntityItalic")
    MessageEntityMention       = _safe_import(_tl, "MessageEntityMention")
    MessageEntityMentionName   = _safe_import(_tl, "MessageEntityMentionName")
    MessageEntityPhone         = _safe_import(_tl, "MessageEntityPhone")
    MessageEntityPre           = _safe_import(_tl, "MessageEntityPre")
    MessageEntitySpoiler       = _safe_import(_tl, "MessageEntitySpoiler")
    MessageEntityStrike        = _safe_import(_tl, "MessageEntityStrike")
    MessageEntityTextUrl       = _safe_import(_tl, "MessageEntityTextUrl")
    MessageEntityUnderline     = _safe_import(_tl, "MessageEntityUnderline")
    MessageEntityUrl           = _safe_import(_tl, "MessageEntityUrl")
    # Media types
    MessageMediaContact        = _safe_import(_tl, "MessageMediaContact")
    MessageMediaDocument       = _safe_import(_tl, "MessageMediaDocument")
    MessageMediaGeo            = _safe_import(_tl, "MessageMediaGeo")
    MessageMediaGeoLive        = _safe_import(_tl, "MessageMediaGeoLive")
    MessageMediaInvoice        = _safe_import(_tl, "MessageMediaInvoice")
    MessageMediaPhoto          = _safe_import(_tl, "MessageMediaPhoto")
    MessageMediaVenue          = _safe_import(_tl, "MessageMediaVenue")
    MessageMediaWebPage        = _safe_import(_tl, "MessageMediaWebPage")
    # ── Call media: name changed across Telethon versions ──────────────────
    # ≤ 1.27  → MessageMediaCall  (media-based call info)
    # ≥ 1.28  → removed; call info lives in Message.action as
    #           MessageActionPhoneCall instead
    MessageMediaCall           = _safe_import(_tl, "MessageMediaCall")        # old
    MessageActionPhoneCall     = _safe_import(_tl, "MessageActionPhoneCall")  # new
else:
    # Provide stubs so the rest of the module doesn't NameError at import time
    (Channel, Chat, User,
     DocumentAttributeAnimated, DocumentAttributeAudio,
     DocumentAttributeFilename, DocumentAttributeSticker, DocumentAttributeVideo,
     MessageEntityBold, MessageEntityBotCommand, MessageEntityCashtag,
     MessageEntityCode, MessageEntityEmail, MessageEntityHashtag,
     MessageEntityItalic, MessageEntityMention, MessageEntityMentionName,
     MessageEntityPhone, MessageEntityPre, MessageEntitySpoiler,
     MessageEntityStrike, MessageEntityTextUrl, MessageEntityUnderline,
     MessageEntityUrl,
     MessageMediaContact, MessageMediaDocument, MessageMediaGeo,
     MessageMediaGeoLive, MessageMediaInvoice, MessageMediaPhoto,
     MessageMediaVenue, MessageMediaWebPage,
     MessageMediaCall, MessageActionPhoneCall) = (None,) * 35

# ── FIX 2: Import reaction types separately with fallbacks ──────────────────
_ReactionEmoji       = None
_ReactionCustomEmoji = None
_ReactionPaid        = None
if TELETHON_AVAILABLE:
    try:
        from telethon.tl.types import ReactionEmoji
        _ReactionEmoji = ReactionEmoji
    except ImportError:
        pass
    try:
        from telethon.tl.types import ReactionCustomEmoji
        _ReactionCustomEmoji = ReactionCustomEmoji
    except ImportError:
        pass
    try:
        from telethon.tl.types import ReactionPaid
        _ReactionPaid = ReactionPaid
    except ImportError:
        pass

load_dotenv()


# ══════════════════════════════════════════════════════════════════════════════
#  PNG / ICON GENERATION  (pure Python, no Pillow)
# ══════════════════════════════════════════════════════════════════════════════

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

def _build_png_rgb(w: int, h: int, pixels: list) -> bytes:
    """pixels = list of (r,g,b) tuples, row-major."""
    raw = b""
    for y in range(h):
        raw += b"\x00"  # filter byte
        for x in range(w):
            r, g, b = pixels[y * w + x]
            raw += bytes([r, g, b])
    idat = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat)
            + _png_chunk(b"IEND", b""))

def _circle_pixels(size: int, r: int, g: int, b: int, bg=(255, 255, 255)) -> list:
    """Filled circle on white background."""
    cx = cy = (size - 1) / 2.0
    radius = cx - 0.5
    pix = []
    for py in range(size):
        for px in range(size):
            dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if dist <= radius:
                pix.append((r, g, b))
            elif dist <= radius + 1.0:
                t = dist - radius
                pix.append((
                    int(r * (1 - t) + bg[0] * t),
                    int(g * (1 - t) + bg[1] * t),
                    int(b * (1 - t) + bg[2] * t),
                ))
            else:
                pix.append(bg)
    return pix

def _make_circle_png(size: int, color: tuple) -> bytes:
    return _build_png_rgb(size, size, _circle_pixels(size, *color))

def _make_play_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = cy = (size - 1) / 2.0
    for py in range(size):
        for px in range(size):
            idx = py * size + px
            tx = px - cx * 0.7
            ty = py - cy
            if tx >= -size * 0.15 and abs(ty) <= (tx + size * 0.15) * 0.75 + 1:
                r, g, b = pix[idx]
                t = 0.9
                pix[idx] = (
                    int(255 * t + r * (1 - t)),
                    int(255 * t + g * (1 - t)),
                    int(255 * t + b * (1 - t)),
                )
    return _build_png_rgb(size, size, pix)

def _make_mic_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = int((size - 1) / 2)
    bar_w = max(2, size // 8)
    bar_h = max(3, size // 3)
    x0 = cx - bar_w // 2
    y0 = cx - bar_h // 2
    for py in range(y0, y0 + bar_h):
        for px in range(x0, x0 + bar_w):
            if 0 <= px < size and 0 <= py < size:
                pix[py * size + px] = (255, 255, 255)
    return _build_png_rgb(size, size, pix)

def _make_file_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = int((size - 1) / 2)
    fw = max(3, size // 4)
    fh = max(4, size // 3)
    x0 = cx - fw // 2
    y0 = cx - fh // 2
    for py in range(y0, y0 + fh):
        for px in range(x0, x0 + fw):
            if 0 <= px < size and 0 <= py < size:
                pix[py * size + px] = (255, 255, 255)
    return _build_png_rgb(size, size, pix)

def _make_wave_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = cy = (size - 1) / 2.0
    lines_y = [int(cy - size * 0.12), int(cy), int(cy + size * 0.12)]
    for ly in lines_y:
        for px in range(int(cx * 0.4), int(cx * 1.6)):
            if 0 <= px < size and 0 <= ly < size:
                pix[ly * size + px] = (255, 255, 255)
    return _build_png_rgb(size, size, pix)

def _make_location_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = int((size - 1) / 2)
    r2 = max(2, size // 8)
    for py in range(size):
        for px in range(size):
            dx = px - cx
            dy = py - (cx - size // 6)
            if dx * dx + dy * dy <= r2 * r2:
                pix[py * size + px] = (255, 255, 255)
    stem_x = cx
    for py in range(cx - size // 6 + r2, cx + size // 4):
        if 0 <= py < size:
            pix[py * size + stem_x] = (255, 255, 255)
    return _build_png_rgb(size, size, pix)

def _make_contact_icon_png(size: int, bg: tuple) -> bytes:
    pix = _circle_pixels(size, *bg)
    cx = int((size - 1) / 2)
    hr = max(1, size // 8)
    hy = cx - size // 6
    for py in range(size):
        for px in range(size):
            if (px - cx) ** 2 + (py - hy) ** 2 <= hr * hr:
                pix[py * size + px] = (255, 255, 255)
    bw = max(2, size // 4)
    bh = max(2, size // 5)
    bx0, by0 = cx - bw // 2, cx + size // 10
    for py in range(by0, by0 + bh):
        for px in range(bx0, bx0 + bw):
            if 0 <= px < size and 0 <= py < size:
                pix[py * size + px] = (255, 255, 255)
    return _build_png_rgb(size, size, pix)

def _make_back_arrow_png(size: int) -> bytes:
    pix = [(255, 255, 255)] * (size * size)
    cx = int((size - 1) / 2)
    col = (100, 156, 217)
    y = cx
    for px in range(size // 4, size * 3 // 4):
        pix[y * size + px] = col
    for i in range(size // 4):
        py_up   = cx - i
        py_down = cx + i
        px      = size // 4 + i
        if 0 <= py_up < size:   pix[py_up   * size + px] = col
        if 0 <= py_down < size: pix[py_down * size + px] = col
    return _build_png_rgb(size, size, pix)

_C = {
    "red":    (255,  85,  85),
    "green":  (100, 191,  71),
    "blue":   ( 79, 156, 217),
    "purple": (152, 132, 232),
    "pink":   (230, 113, 165),
    "sea":    ( 71, 188, 209),
    "orange": (255, 140,  68),
}

def generate_all_icons(img_dir: Path):
    entries = [
        ("media_photo",        lambda s: _make_circle_png(s, _C["green"])),
        ("media_video",        lambda s: _make_play_icon_png(s, _C["sea"])),
        ("media_voice",        lambda s: _make_mic_icon_png(s, _C["blue"])),
        ("media_music",        lambda s: _make_wave_icon_png(s, _C["blue"])),
        ("media_file",         lambda s: _make_file_icon_png(s, _C["red"])),
        ("media_call",         lambda s: _make_circle_png(s, _C["red"])),
        ("media_contact",      lambda s: _make_contact_icon_png(s, _C["orange"])),
        ("media_location",     lambda s: _make_location_icon_png(s, _C["sea"])),
        ("media_game",         lambda s: _make_circle_png(s, _C["purple"])),
        ("media_shop",         lambda s: _make_circle_png(s, _C["pink"])),
        ("section_chats",      lambda s: _make_circle_png(s, _C["blue"])),
        ("section_calls",      lambda s: _make_circle_png(s, _C["red"])),
        ("section_contacts",   lambda s: _make_contact_icon_png(s, _C["blue"])),
        ("section_photos",     lambda s: _make_circle_png(s, _C["green"])),
        ("section_frequent",   lambda s: _make_circle_png(s, _C["orange"])),
        ("section_sessions",   lambda s: _make_circle_png(s, _C["blue"])),
        ("section_stories",    lambda s: _make_circle_png(s, _C["purple"])),
        ("section_music",      lambda s: _make_wave_icon_png(s, _C["blue"])),
        ("section_web",        lambda s: _make_circle_png(s, _C["sea"])),
        ("section_other",      lambda s: _make_circle_png(s, _C["orange"])),
    ]
    for base, fn in entries:
        for sz, sfx in [(24, ""), (48, "@2x")]:
            p = img_dir / f"{base}{sfx}.png"
            if not p.exists():
                p.write_bytes(fn(sz))
    bp = img_dir / "back.png"
    if not bp.exists():
        bp.write_bytes(_make_back_arrow_png(24))
    b2p = img_dir / "back@2x.png"
    if not b2p.exists():
        b2p.write_bytes(_make_back_arrow_png(48))


# ══════════════════════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════════════════════

CSS = """\
body { margin:0; font:12px/18px 'Open Sans',"Lucida Grande",Arial,Helvetica,sans-serif; }
strong { font-weight:700; }
code, kbd, pre, samp { font-family:Menlo,Monaco,Consolas,"Courier New",monospace; }
code { padding:2px 4px; font-size:90%; color:#c7254e; background:#f9f2f4; border-radius:4px; }
pre  { display:block; margin:0; word-break:break-all; word-wrap:break-word;
       color:#333; background:#f5f5f5; border-radius:4px; overflow:auto;
       padding:3px; border:1px solid #eee; font-size:inherit; }
.clearfix:after { content:" "; visibility:hidden; display:block; height:0; clear:both; }
.pull_left  { float:left; }
.pull_right { float:right; }
.page_wrap  { background:#fff; color:#000; }
.page_wrap a { color:#168acd; text-decoration:none; }
.page_wrap a:hover { text-decoration:underline; }
.page_header {
    position:fixed; z-index:10; background:#fff; width:100%;
    border-bottom:1px solid #e3e6e8;
}
.page_header .content { width:480px; margin:0 auto; }
.page_header a.content {
    display:block; background:no-repeat 24px 21px / 24px 24px url(../images/back.png);
}
.bold    { color:#212121; font-weight:700; }
.details { color:#70777b; }
.page_header .content .text {
    padding:24px 24px 22px 24px; font-size:22px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.page_header a.content .text { padding-left:64px; }
.page_body { padding-top:64px; width:480px; margin:0 auto; }
.userpic { display:block; border-radius:50%; overflow:hidden; }
.userpic .initials {
    display:block; color:#fff; text-align:center;
    text-transform:uppercase; user-select:none;
}
.userpic_photo { width:42px; height:42px; border-radius:50%; object-fit:cover; display:block; }
.userpic1 { background:#ff5555; } .userpic2 { background:#64bf47; }
.userpic3 { background:#ffab00; } .userpic4 { background:#4f9cd9; }
.userpic5 { background:#9884e8; } .userpic6 { background:#e671a5; }
.userpic7 { background:#47bcd1; } .userpic8 { background:#ff8c44; }
.history { padding:16px 0; }
.message { margin:0 -10px; transition:background-color 2.0s ease; }
div.selected { background:rgba(242,246,250,255); transition:background-color 0.5s ease; }
.service { padding:10px 24px; }
.service .body { text-align:center; }
.default { padding:10px; }
.default.joined { margin-top:-10px; }
.default .from_name { color:#3892db; font-weight:700; padding-bottom:5px; }
.default .body { margin-left:60px; }
.default .text { word-wrap:break-word; line-height:150%; unicode-bidi:plaintext; text-align:start; }
.default .reply_to, .default .media_wrap { padding-bottom:5px; }
.default .media { margin:0 -10px; padding:5px 10px; display:flex; align-items:center; }
.default .media .fill {
    flex-shrink:0; width:48px; height:48px; border-radius:50%;
    background:no-repeat center/24px 24px;
    margin-right:10px;
}
.default .media .body { overflow:hidden; }
.default .media .title { font-size:14px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.default .media .description { color:#444; font-size:13px; }
.default .media .status { padding-top:2px; font-size:12px; color:#70777b; }
.media_photo        .fill { background-color:#64bf47;  background-image:url(../images/media_photo.png); }
.media_video        .fill { background-color:#47bcd1;  background-image:url(../images/media_video.png); }
.media_voice_message .fill { background-color:#4f9cd9; background-image:url(../images/media_voice.png); }
.media_audio_file   .fill { background-color:#4f9cd9;  background-image:url(../images/media_music.png); }
.media_file         .fill { background-color:#ff5555;  background-image:url(../images/media_file.png); }
.media_call         .fill { background-color:#ff5555;  background-image:url(../images/media_call.png); }
.media_call.success .fill { background-color:#64bf47;  background-image:url(../images/media_call.png); }
.media_contact      .fill { background-color:#ff8c44;  background-image:url(../images/media_contact.png); }
.media_location     .fill,
.media_live_location .fill,
.media_venue        .fill { background-color:#47bcd1;   background-image:url(../images/media_location.png); }
.media_game         .fill { background-color:#9884e8;  background-image:url(../images/media_game.png); }
.media_invoice      .fill { background-color:#e671a5;  background-image:url(../images/media_shop.png); }
.default .photo { display:block; max-width:400px; border-radius:6px; }
.default .video_file, .default .animated { display:block; max-width:400px; border-radius:6px; }
.video_file_wrap, .animated_wrap { position:relative; display:inline-block; }
.video_duration {
    background:rgba(0,0,0,.5); padding:2px 6px; position:absolute;
    z-index:2; border-radius:3px; right:6px; bottom:6px;
    color:#fff; font-size:11px; pointer-events:none;
}
.video_play_bg {
    position:absolute; z-index:2;
    top:50%; left:50%; transform:translate(-50%,-50%);
    width:44px; height:44px; border-radius:50%;
    background:rgba(0,0,0,.45);
    display:flex; align-items:center; justify-content:center;
    pointer-events:none;
}
.gif_play { font-weight:700; color:#fff; font-size:13px; }
.audio_player { display:block; width:100%; max-width:380px; margin-top:4px; }
.sticker_wrap img { max-width:200px; max-height:200px; display:block; }
.pull_left.userpic_wrap { margin-right:10px; }
.forwarded.body { margin-left:10px; border-left:2px solid #168acd; padding-left:8px; }
.forwarded .from_name { color:#168acd; font-weight:700; font-size:11px; padding-bottom:2px; }
.reply_to { font-size:11px; padding-bottom:3px; }
a.block_link { display:block; text-decoration:none !important; border-radius:4px; }
a.block_link:hover { background:#f5f7f8; }
.spoiler { background:#e8e8e8; }
.spoiler.hidden { background:#a9a9a9; cursor:pointer; border-radius:3px; }
.spoiler.hidden span { opacity:0; user-select:none; }
.reactions { margin:5px 0; display:flex; flex-wrap:wrap; gap:4px; }
.reactions .reaction {
    display:inline-flex; align-items:center;
    height:22px; border-radius:11px; padding:0 8px 0 4px;
    background:#e8f5fc; color:#168acd; font-weight:700;
}
.reactions .reaction.active { background:#40a6e2; color:#fff; }
.reactions .reaction.paid   { background:#fdf6e1; color:#c58523; }
.reactions .reaction.active.paid { background:#ecae0a; color:#fdf6e1; }
.reactions .reaction .emoji { font-size:15px; margin-right:4px; line-height:22px; }
.reactions .reaction .count { font-size:12px; line-height:22px; }
@media (prefers-color-scheme: dark) {
  html,body { background:#1a2026; }
  .page_wrap { background:#1a2026; color:#fff; min-height:100vh; }
  .page_wrap a { color:#4db8ff; }
  .page_header { background:#1a2026; border-bottom:1px solid #2c333d; }
  .bold { color:#fff; } .details { color:#91979e; }
  code { color:#ff8aac; background:#2c333d; }
  pre  { color:#fff;  background:#2c333d; border:1px solid #323a45; }
  .message { color:#fff; } div.selected { background:#323a45; }
  .default .from_name { color:#4db8ff; }
  .default .media .description { color:#ccc; }
  .spoiler { background:#323a45; } .spoiler.hidden { background:#61c0ff; }
  a.block_link:hover { background:#323a45; }
  .reactions .reaction { background:#2c333d; color:#4db8ff; }
  .reactions .reaction.active { background:#4db8ff; color:#1a2026; }
}
"""

# ══════════════════════════════════════════════════════════════════════════════
#  JS
# ══════════════════════════════════════════════════════════════════════════════

JS = r"""
"use strict";
function CheckLocation() {
    var h = location.hash;
    if (h.indexOf("#go_to_message") === 0) {
        var id = parseInt(h.slice(14));
        if (id) GoToMessage(id);
    }
}
function ShowToast(text) {
    var c = document.createElement("div");
    c.style.cssText = "position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:9999;opacity:0;transition:opacity .3s";
    var b = c.appendChild(document.createElement("div"));
    b.style.cssText = "background:rgba(0,0,0,.75);color:#fff;padding:10px 22px;border-radius:14px;font-size:13px;white-space:nowrap";
    b.textContent = text;
    document.body.appendChild(c);
    setTimeout(function(){ c.style.opacity="1"; },10);
    setTimeout(function(){ c.style.opacity="0"; setTimeout(function(){ c.remove(); },400); },2800);
}
function ShowSpoiler(el){ el.classList.remove("hidden"); }
function GoToMessage(id) {
    var el = document.getElementById("message" + id);
    if (!el) { ShowToast("Message not in export."); return false; }
    if (location.hash !== "#go_to_message"+id) location.hash = "#go_to_message"+id;
    var hdr = document.querySelector(".page_header");
    var hh = hdr ? hdr.offsetHeight : 0;
    var top = el.offsetTop - Math.max((window.innerHeight-hh-el.offsetHeight)/2,10) - hh;
    window.scrollTo({top:Math.max(0,top),behavior:"smooth"});
    el.style.transition = "background-color .3s";
    el.style.backgroundColor = "rgba(100,180,255,.25)";
    setTimeout(function(){ el.style.backgroundColor=""; },1400);
    return false;
}
"""


# ══════════════════════════════════════════════════════════════════════════════
#  HTML HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_user_color_map: dict = {}
_color_ctr = [1]

def get_user_color(uid: int) -> int:
    if uid not in _user_color_map:
        _user_color_map[uid] = (_color_ctr[0] % 8) + 1
        _color_ctr[0] += 1
    return _user_color_map[uid]

def initials(name: str) -> str:
    p = name.strip().split()
    return (p[0][0] + p[-1][0]).upper() if len(p) >= 2 else name[:2].upper() if name else "?"

# Cache the last date-header computation — in a normal chat, hundreds of
# messages share the same date so strftime is only called once per day.
_last_dh_key: tuple = (0, 0, 0)
_last_dh_val: str   = ""

def format_date_header(dt: datetime) -> str:
    global _last_dh_key, _last_dh_val
    key = (dt.year, dt.month, dt.day)
    if key == _last_dh_key:
        return _last_dh_val
    _last_dh_key = key
    _last_dh_val = f"{dt.day} {dt.strftime('%B %Y')}"
    return _last_dh_val

_last_ft_key: tuple = (0, 0)
_last_ft_val: str   = ""

def format_time(dt: datetime) -> str:
    global _last_ft_key, _last_ft_val
    key = (dt.hour, dt.minute)
    if key == _last_ft_key:
        return _last_ft_val
    _last_ft_key = key
    _last_ft_val = f"{dt.hour:02d}:{dt.minute:02d}"
    return _last_ft_val

def format_full_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M:%S UTC+00:00")

def apply_entities(text: str, entities) -> str:
    # Fast path: the vast majority of messages have no entities
    if not text:
        return ""
    if not entities:
        return html.escape(text)   # single C-level call, no allocation
    chars = list(text)
    escaped = [html.escape(ch) for ch in chars]
    opens: dict = {}
    closes: dict = {}

    def tag(o, e, ot, ct):
        opens.setdefault(o, []).append(ot)
        closes.setdefault(e, []).insert(0, ct)

    for ent in (entities or []):
        o, l = ent.offset, ent.length
        e = o + l
        if not TELETHON_AVAILABLE:
            continue
        t = type(ent)
        if   t is MessageEntityBold:        tag(o,e,"<strong>","</strong>")
        elif t is MessageEntityItalic:      tag(o,e,"<em>","</em>")
        elif t is MessageEntityUnderline:   tag(o,e,"<u>","</u>")
        elif t is MessageEntityStrike:      tag(o,e,"<s>","</s>")
        elif t is MessageEntityCode:        tag(o,e,"<code>","</code>")
        elif t is MessageEntityPre:         tag(o,e,"<pre>","</pre>")
        elif t is MessageEntitySpoiler:
            tag(o,e,'<span class="spoiler hidden" onclick="ShowSpoiler(this)"><span>','</span></span>')
        elif t is MessageEntityUrl:
            u = html.escape("".join(chars[o:e]))
            tag(o,e,f'<a href="{u}">','</a>')
        elif t is MessageEntityTextUrl:
            tag(o,e,f'<a href="{html.escape(ent.url)}">','</a>')
        elif t is MessageEntityEmail:
            addr = html.escape("".join(chars[o:e]))
            tag(o,e,f'<a href="mailto:{addr}">','</a>')
        elif t is MessageEntityMention:
            m = html.escape("".join(chars[o:e]))
            tag(o,e,f'<a href="https://t.me/{m.lstrip("@")}">','</a>')
        elif t is MessageEntityPhone:
            ph = html.escape("".join(chars[o:e]))
            tag(o,e,f'<a href="tel:{ph}">','</a>')
        elif t is MessageEntityHashtag:
            ht = html.escape("".join(chars[o:e]))
            tag(o,e,f'<a href="javascript:void(0)" onclick="ShowToast(\'{ht}\')">','</a>')

    out = []
    for i in range(len(chars)):
        if i in closes: out.extend(closes[i])
        if i in opens:  out.extend(opens[i])
        out.append(escaped[i])
    n = len(chars)
    if n in closes: out.extend(closes[n])
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT OPTIONS
# ══════════════════════════════════════════════════════════════════════════════

class ExportOptions:
    __slots__ = ("photos", "videos", "voice", "video_messages", "stickers",
                 "gifs", "files", "file_size_limit_mb", "fmt",
                 "profile_pics", "cancel_event", "concurrent_downloads")
    def __init__(self):
        self.photos               = True
        self.videos               = True
        self.voice                = True
        self.video_messages       = True
        self.stickers             = True
        self.gifs                 = True
        self.files                = True
        self.file_size_limit_mb   = 50
        self.fmt                  = "html"
        self.profile_pics         = True
        self.cancel_event         = threading.Event()
        self.concurrent_downloads = 6


# ── Concurrency knobs ────────────────────────────────────────────────────────
# How many media files download simultaneously.  6 is safe under Telegram's
# flood-wait limits; raise to 10 if you have a fast line and don't hit limits.
CONCURRENT_DL   = 6
# How many PIL thumbnail operations run in parallel threads.
THUMB_WORKERS   = 4
# Minimum seconds between GUI progress updates (avoids tkinter flood).
PROGRESS_INTERVAL = 0.15

_thumb_pool = ThreadPoolExecutor(max_workers=THUMB_WORKERS, thread_name_prefix="thumb")

def _make_thumb_sync(data: bytes, out_path: str, max_px: int = 260):
    """CPU-bound thumbnail creation – runs in thread pool, not event loop."""
    try:
        from PIL import Image as PILImage
        img = PILImage.open(io.BytesIO(data))
        tw, th = img.size
        ratio = min(max_px / tw, max_px / th)
        tw, th = int(tw * ratio), int(th * ratio)
        img.thumbnail((tw, th))
        img.save(out_path, "JPEG")
        return tw, th
    except Exception:
        Path(out_path).write_bytes(data)
        return max_px, max_px

# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class Exporter:
    def __init__(self, client, output_dir: Path, log_fn,
                 limit: int = 0, progress_fn=None, options: ExportOptions = None):
        self.client      = client
        self.output_dir  = output_dir
        self.log         = log_fn
        self.progress    = progress_fn
        self.limit       = limit
        self.opts        = options or ExportOptions()
        self._date_ctr   = 0
        self._pic_cache: dict   = {}   # uid → relative path str or ""
        self._pic_futures: dict = {}   # uid → asyncio.Task (de-dup concurrent requests)
        self._dl_sem            = None # asyncio.Semaphore, created in export()
        self._last_progress     = 0.0  # throttle GUI updates
        self._name_cache: dict  = {}   # uid → display name string
        self._user_meta: dict   = {}   # uid → (color, initials_str)

    def _init_dirs(self):
        for d in ["css", "js", "images", "photos", "video_files", "files"]:
            (self.output_dir / d).mkdir(parents=True, exist_ok=True)
        generate_all_icons(self.output_dir / "images")

    def _write_assets(self):
        (self.output_dir / "css" / "style.css").write_text(CSS, encoding="utf-8")
        (self.output_dir / "js"  / "script.js").write_text(JS,  encoding="utf-8")

    async def _get_profile_pic(self, sender) -> str:
        """Download profile pic exactly once per user even under concurrent calls."""
        if sender is None or not self.opts.profile_pics:
            return ""
        uid = sender.id
        if uid in self._pic_cache:
            return self._pic_cache[uid]
        # If a download is already in-flight for this uid, await that same task
        if uid in self._pic_futures:
            return await self._pic_futures[uid]
        async def _do():
            try:
                buf = io.BytesIO()
                async with self._dl_sem:
                    res = await self.client.download_profile_photo(sender, file=buf)
                if res is None:
                    self._pic_cache[uid] = ""
                    return ""
                fname = f"userpic_{uid}.jpg"
                (self.output_dir / "photos" / fname).write_bytes(buf.getvalue())
                path = f"photos/{fname}"
                self._pic_cache[uid] = path
                return path
            except Exception:
                self._pic_cache[uid] = ""
                return ""
            finally:
                self._pic_futures.pop(uid, None)
        task = asyncio.ensure_future(_do())
        self._pic_futures[uid] = task
        return await task


    async def export(self, entity):
        self._init_dirs()
        self._write_assets()
        # Create the download semaphore on the running loop
        self._dl_sem = asyncio.Semaphore(self.opts.concurrent_downloads)

        if hasattr(entity, "title"):
            chat_name = entity.title
        elif hasattr(entity, "first_name"):
            chat_name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
        else:
            chat_name = str(entity.id)

        self.log(f"Exporting: {chat_name}")

        total = 0
        try:
            from telethon.tl.functions.messages import GetHistoryRequest
            fe = await self.client.get_entity(entity)
            r  = await self.client(GetHistoryRequest(
                peer=fe, offset_id=0, offset_date=None,
                add_offset=0, limit=1, max_id=0, min_id=0, hash=0))
            total = getattr(r, "count", 0)
            if self.limit > 0:
                total = min(total, self.limit)
            self.log(f"  Messages to export: {total}")
        except Exception:
            total = self.limit or 0

        if self.progress:
            self.progress(0, total)

        # ════════════════════════════════════════════════════════════════════
        # Two-phase producer/consumer pipeline
        #
        # PRODUCER: streams messages from Telegram into a bounded asyncio.Queue.
        #           Uses wait_time=0 to skip inter-page sleep.
        #           wait_time=0 to skip Telethon's inter-batch sleep.
        #
        # CONSUMER: pulls items from the queue, immediately fires a render Task
        #           for each message (downloads start at once, bounded by the
        #           semaphore), collects Tasks in order, and drains them in a
        #           sliding window so the event loop never stalls.
        #
        # Result: fetching and downloading overlap completely — while Telethon
        # is waiting for the next API page, previous downloads are already done.
        # ════════════════════════════════════════════════════════════════════

        kw = dict(reverse=True, wait_time=0)
        if self.limit > 0:
            kw["limit"] = self.limit

        _SENTINEL  = object()          # signals producer is done
        # For text-heavy chats: large queue so the producer (network-bound) is
        # never blocked by the consumer (CPU-bound string building).
        _Q_MAXSIZE = 2000
        # Window = how many async tasks we allow in-flight simultaneously.
        # Text messages bypass the task system entirely, so this only matters
        # for the minority of messages that actually have media.
        _WINDOW    = max(32, self.opts.concurrent_downloads * 8)

        queue: asyncio.Queue = asyncio.Queue(maxsize=_Q_MAXSIZE)

        # ── producer ─────────────────────────────────────────────────────────
        async def _producer():
            try:
                async for msg in self.client.iter_messages(entity, **kw):
                    if self.opts.cancel_event.is_set():
                        break
                    await queue.put(msg)
            finally:
                await queue.put(_SENTINEL)

        # ── consumer ─────────────────────────────────────────────────────────
        async def _consumer():
            nonlocal idx, last_date, prev_sid
            parts      = []
            pending    = []   # list of (date_str, Task)

            async def _drain_pending(pending, parts, last_date, up_to: int):
                """Drain completed tasks in order. Uses result() when already
                done to skip the coroutine-switch overhead of await."""
                while len(pending) >= up_to:
                    date_str, task = pending.pop(0)
                    try:
                        # Fast path: task already finished, no context switch
                        if task.done():
                            result = task.result()
                        else:
                            result = await task
                    except Exception as ex:
                        result = f'<!-- render error: {html.escape(str(ex))} -->'
                    if date_str is not None and date_str != last_date:
                        parts.append(self._date_divider(date_str))
                        last_date = date_str
                    parts.append(result)
                return last_date

            while True:
                msg = await queue.get()
                if msg is _SENTINEL:
                    break

                if msg.date is None:
                    continue

                idx += 1
                now = time.monotonic()
                if now - self._last_progress >= PROGRESS_INTERVAL:
                    self.log(f"  {idx}/{total or '?'}…")
                    if self.progress:
                        self.progress(idx, total)
                    self._last_progress = now

                dt   = msg.date.replace(tzinfo=timezone.utc)
                ds   = format_date_header(dt)
                snd  = msg.sender
                sid  = snd.id if snd else None
                if sid is not None and sid in self._name_cache:
                    sname = self._name_cache[sid]
                else:
                    sname = None
                    if snd:
                        if isinstance(snd, User):
                            sname = f"{snd.first_name or ''} {snd.last_name or ''}".strip() or "Unknown"
                        elif isinstance(snd, (Chat, Channel)):
                            sname = snd.title or "Unknown"
                    if sid is not None:
                        self._name_cache[sid] = sname
                        if sname:
                            self._user_meta[sid] = (
                                get_user_color(sid), initials(sname))

                joined   = sid is not None and sid == prev_sid
                prev_sid = sid

                # ── decide sync vs async ──────────────────────────────────
                # A message needs async I/O only if it has real downloadable
                # media OR if the sender's profile pic isn't cached yet.
                # Everything else (text, reactions, replies, web previews,
                # calls) renders synchronously with zero task overhead.
                _has_real_media = (
                    msg.media is not None
                    and not (MessageMediaWebPage and isinstance(msg.media, MessageMediaWebPage))
                )
                _pic_uncached = (
                    not joined
                    and sid is not None
                    and sname is not None
                    and sid not in self._pic_cache
                    and self.opts.profile_pics
                )
                _use_async = (self.opts.fmt == "json") or _has_real_media or _pic_uncached

                if _use_async:
                    if self.opts.fmt == "json":
                        coro = self._msg_to_dict(msg, sid, sname, dt)
                    else:
                        coro = self._render_msg(msg, sid, sname, snd, dt, joined)
                    task = asyncio.ensure_future(coro)
                    pending.append((ds, task))
                    last_date = await _drain_pending(pending, parts, last_date, _WINDOW)
                else:
                    # Pure sync — drain any in-flight async tasks first so
                    # ordering is preserved, then append directly.
                    last_date = await _drain_pending(pending, parts, last_date, _WINDOW)
                    if ds != last_date:
                        parts.append(self._date_divider(ds))
                        last_date = ds
                    pic = self._pic_cache.get(sid, "") if sid is not None else ""
                    parts.append(self._render_msg_sync(
                        msg, sid, sname, snd, dt, joined, pic))

            # Drain all remaining tasks
            last_date = await _drain_pending(pending, parts, last_date, 1)
            return parts

        # Run producer and consumer concurrently
        last_date = None
        prev_sid  = None
        idx       = 0
        producer_task = asyncio.ensure_future(_producer())
        parts = await _consumer()
        await producer_task   # propagate any producer exception

        self.log(f"  Processed {idx} messages.")
        if self.progress:
            self.progress(idx, max(idx, total))

        if self.opts.fmt == "json":
            obj  = {"chat": chat_name, "messages": [p for p in parts if isinstance(p, dict)]}
            path = self.output_dir / "messages.json"
            path.write_text(_json_mod.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"JSON → {path}")
        else:
            self.log("Writing HTML…")
            # Stream HTML to disk in chunks instead of one giant string join
            path = self.output_dir / "messages.html"
            header, footer = self._wrap_page_parts(chat_name)
            with path.open("w", encoding="utf-8") as fh:
                fh.write(header)
                for chunk in parts:
                    fh.write("\n")
                    fh.write(chunk)
                fh.write(footer)
            self.log(f"HTML → {path}")

        self.log("Done!")

    # ── JSON ──────────────────────────────────────────────────────────────────

    async def _msg_to_dict(self, msg, sid, sname, dt: datetime) -> dict:
        d: dict = {"id": msg.id, "date": dt.isoformat(),
                   "from": sname, "from_id": sid, "text": msg.message or ""}
        if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
            d["reply_to"] = msg.reply_to.reply_to_msg_id
        if msg.fwd_from:
            d["forwarded_from"] = getattr(msg.fwd_from, "from_name", None)
        if (MessageActionPhoneCall and hasattr(msg, "action")
                and msg.action is not None
                and isinstance(msg.action, MessageActionPhoneCall)):
            action = msg.action
            reason = type(action.reason).__name__ if getattr(action, "reason", None) else ""
            d["media"] = {
                "type": "call",
                "video": getattr(action, "video", False),
                "duration": getattr(action, "duration", 0) or 0,
                "reason": reason,
            }
        elif msg.media:
            d["media"] = await self._media_to_dict(msg)
        if msg.reactions and msg.reactions.results:
            d["reactions"] = [{"emoji": getattr(r.reaction, "emoticon", "?"),
                               "count": r.count}
                              for r in msg.reactions.results]
        return d

    async def _media_to_dict(self, msg) -> dict:
        m = msg.media
        if isinstance(m, MessageMediaPhoto):
            return {"type": "photo", "file": await self._dl_photo(msg)}
        if isinstance(m, MessageMediaDocument):
            return {"type": "document", "file": await self._dl_doc_generic(msg)}
        if isinstance(m, (MessageMediaGeo, MessageMediaGeoLive)):
            return {"type": "location", "lat": m.geo.lat, "long": m.geo.long}
        if isinstance(m, MessageMediaContact):
            return {"type": "contact",
                    "name": f"{m.first_name or ''} {m.last_name or ''}".strip(),
                    "phone": m.phone_number or ""}
        return {"type": type(m).__name__}

    # ── date divider ──────────────────────────────────────────────────────────

    def _date_divider(self, ds: str) -> str:
        # FIX 3: Only one increment here — the original also incremented at the
        # call site in export(), causing the counter to skip every other number.
        self._date_ctr += 1
        return (f'\n     <div class="message service" id="message-{self._date_ctr}">'
                f'\n      <div class="body details">{html.escape(ds)}</div>'
                f'\n     </div>')

    # ── message renderer ──────────────────────────────────────────────────────

    def _needs_io(self, msg) -> bool:
        """Return True only if rendering this message requires async I/O."""
        if msg.media:
            return True
        if MessageActionPhoneCall and getattr(msg, "action", None) is not None:
            if isinstance(msg.action, MessageActionPhoneCall):
                return False   # call is sync-rendered
        if msg.fwd_from:
            # msg.fwd_from.from_name is already on the object (no RPC needed)
            # msg.forward.sender would be an RPC — we avoid that below
            return False
        return False   # pure text, reply, reactions — all sync

    def _render_msg_sync(self, msg, sid, sname, snd_obj,
                         dt: datetime, joined: bool,
                         pic_path: str = "") -> str:
        """Synchronous render — used when no I/O is required."""
        mid       = msg.id
        time_str  = format_time(dt)
        full_date = format_full_date(dt)

        if joined and sid is not None:
            userpic_blk = name_blk = ""
        elif sid is not None and sname:
            _meta = self._user_meta.get(sid)
            color, ini = _meta if _meta else (get_user_color(sid), initials(sname))
            if pic_path:
                inner = f'<img src="{pic_path}" class="userpic_photo" alt="{html.escape(ini)}"/>'
            else:
                inner = (f'<div class="initials" style="line-height:42px">'
                         f'{html.escape(ini)}</div>')
            userpic_blk = (f'\n      <div class="pull_left userpic_wrap">'
                           f'<div class="userpic userpic{color}" style="width:42px;height:42px">'
                           f'{inner}</div></div>')
            name_blk = f'\n       <div class="from_name">{html.escape(sname)}</div>'
        else:
            userpic_blk = name_blk = ""

        date_blk = (f'\n       <div class="pull_right date details" title="{full_date}">'
                    f'{time_str}</div>')

        reply_blk = ""
        if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
            rid = msg.reply_to.reply_to_msg_id
            reply_blk = (f'\n       <div class="reply_to details">In reply to '
                         f'<a href="#go_to_message{rid}" onclick="return GoToMessage({rid})">'
                         f'this message</a></div>')

        fwd_blk = ""
        if msg.fwd_from:
            # Use from_name directly — never touch msg.forward.sender (RPC!)
            fn = (msg.fwd_from.from_name or "").strip() or "Unknown"
            fc = get_user_color(abs(hash(fn)) % 1_000_000)
            fwd_blk = (f'\n      <div class="pull_left forwarded userpic_wrap">'
                       f'<div class="userpic userpic{fc}" style="width:42px;height:42px">'
                       f'<div class="initials" style="line-height:42px">{html.escape(initials(fn))}</div>'
                       f'</div></div>'
                       f'\n      <div class="forwarded body">'
                       f'\n       <div class="from_name">{html.escape(fn)}'
                       f' <span class="date details" title="{full_date}">'
                       f'{dt.strftime("%d.%m.%Y %H:%M:%S")}</span></div>')

        # Calls are sync-rendered directly
        media_blk = ""
        if MessageActionPhoneCall and isinstance(getattr(msg, "action", None), MessageActionPhoneCall):
            media_blk = self._render_action_call(msg.action)

        raw      = apply_entities(msg.message, msg.entities) if msg.message else ""
        text_blk = f'\n       <div class="text">{raw}</div>' if raw else ""
        rx_blk   = self._render_reactions(msg)

        joined_cls = " joined" if joined else ""
        inner = date_blk + name_blk + reply_blk
        if fwd_blk:
            inner += fwd_blk + media_blk + text_blk + "\n      </div>"
        else:
            inner += media_blk + text_blk
        inner += rx_blk

        return (f'\n     <div class="message default clearfix{joined_cls}" id="message{mid}">'
                f'{userpic_blk}'
                f'\n      <div class="body">{inner}\n      </div>'
                f'\n     </div>')

    async def _render_msg(self, msg, sid, sname, snd_obj,
                          dt: datetime, joined: bool) -> str:
        """Async render — only called when the message actually needs I/O."""
        mid       = msg.id
        time_str  = format_time(dt)
        full_date = format_full_date(dt)

        if joined and sid is not None:
            userpic_blk = name_blk = ""
        elif sid is not None and sname:
            color   = get_user_color(sid)
            ini     = initials(sname)
            pic     = await self._get_profile_pic(snd_obj)
            if pic:
                inner = f'<img src="{pic}" class="userpic_photo" alt="{html.escape(ini)}"/>'
            else:
                inner = (f'<div class="initials" style="line-height:42px">'
                         f'{html.escape(ini)}</div>')
            userpic_blk = (f'\n      <div class="pull_left userpic_wrap">'
                           f'<div class="userpic userpic{color}" style="width:42px;height:42px">'
                           f'{inner}</div></div>')
            name_blk    = f'\n       <div class="from_name">{html.escape(sname)}</div>'
        else:
            userpic_blk = name_blk = ""

        date_blk = (f'\n       <div class="pull_right date details" title="{full_date}">'
                    f'{time_str}</div>')

        reply_blk = ""
        if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
            rid = msg.reply_to.reply_to_msg_id
            reply_blk = (f'\n       <div class="reply_to details">In reply to '
                         f'<a href="#go_to_message{rid}" onclick="return GoToMessage({rid})">'
                         f'this message</a></div>')

        fwd_blk = ""
        if msg.fwd_from:
            fn = (msg.fwd_from.from_name or "").strip() or "Unknown"
            fc = get_user_color(abs(hash(fn)) % 1_000_000)
            fwd_blk = (f'\n      <div class="pull_left forwarded userpic_wrap">'
                       f'<div class="userpic userpic{fc}" style="width:42px;height:42px">'
                       f'<div class="initials" style="line-height:42px">{html.escape(initials(fn))}</div>'
                       f'</div></div>'
                       f'\n      <div class="forwarded body">'
                       f'\n       <div class="from_name">{html.escape(fn)}'
                       f' <span class="date details" title="{full_date}">'
                       f'{dt.strftime("%d.%m.%Y %H:%M:%S")}</span></div>')

        media_blk = cap_txt = ""
        if (MessageActionPhoneCall and hasattr(msg, "action")
                and msg.action is not None
                and isinstance(msg.action, MessageActionPhoneCall)):
            media_blk = self._render_action_call(msg.action)
        elif msg.media:
            media_blk, cap_txt = await self._render_media(msg)

        raw      = cap_txt if cap_txt else (apply_entities(msg.message, msg.entities) if msg.message else "")
        text_blk = f'\n       <div class="text">{raw}</div>' if raw else ""
        rx_blk   = self._render_reactions(msg)

        joined_cls = " joined" if joined else ""
        inner = date_blk + name_blk + reply_blk
        if fwd_blk:
            inner += fwd_blk + media_blk + text_blk + "\n      </div>"
        else:
            inner += media_blk + text_blk
        inner += rx_blk

        return (f'\n     <div class="message default clearfix{joined_cls}" id="message{mid}">'
                f'{userpic_blk}'
                f'\n      <div class="body">{inner}\n      </div>'
                f'\n     </div>')

    # ── reactions ─────────────────────────────────────────────────────────────

    def _render_action_call(self, action) -> str:
        """Render a phone call from MessageActionPhoneCall (Telethon >= 1.28)."""
        try:
            reason   = type(action.reason).__name__ if getattr(action, "reason", None) else ""
            duration = getattr(action, "duration", 0) or 0
            status_map = {
                "PhoneCallDiscardReasonMissed":     "Missed",
                "PhoneCallDiscardReasonDisconnect": "Disconnected",
                "PhoneCallDiscardReasonHangup":     "Ended",
                "PhoneCallDiscardReasonBusy":       "Busy",
                "PhoneCallDiscardReasonEmpty":      "Cancelled",
                "":                                 "Cancelled",
            }
            status_str = status_map.get(
                reason, reason.replace("PhoneCallDiscardReason", "") or "Cancelled")
            if duration:
                mins, secs = divmod(duration, 60)
                status_str += f" ({mins}:{secs:02d})"
            is_video_call = getattr(action, "video", False)
            call_type  = "Video call" if is_video_call else "Voice call"
            extra_cls  = " success" if duration > 0 else ""
            return (f'\n       <div class="media_wrap clearfix">'
                    f'\n        <div class="media clearfix pull_left media_call{extra_cls}">'
                    f'\n         <div class="fill pull_left"></div>'
                    f'\n         <div class="body">'
                    f'\n          <div class="title bold">{call_type}</div>'
                    f'\n          <div class="status details">{html.escape(status_str)}</div>'
                    f'\n         </div>'
                    f'\n        </div>'
                    f'\n       </div>')
        except Exception as ex:
            return self._ph("media_call", "Call", f"error: {ex}")

    def _render_reactions(self, msg) -> str:
        # FIX 2: Use module-level pre-imported reaction types instead of
        # importing inside the method, which failed silently on older Telethon.
        try:
            if not msg.reactions or not msg.reactions.results:
                return ""
            parts = []
            for r in msg.reactions.results:
                try:
                    if _ReactionEmoji and isinstance(r.reaction, _ReactionEmoji):
                        em, paid = r.reaction.emoticon, False
                    elif _ReactionPaid and isinstance(r.reaction, _ReactionPaid):
                        em, paid = "⭐", True
                    else:
                        # Fallback: try to get emoticon attribute, else use bullet
                        em = getattr(r.reaction, "emoticon", None) or "●"
                        paid = False
                except Exception:
                    em, paid = "?", False
                chosen = bool(
                    getattr(r, "chosen_order", None) is not None
                    or getattr(r, "chosen", False)
                )
                cls = "reaction" + (" active" if chosen else "") + (" paid" if paid else "")
                parts.append(f'<span class="{cls}"><span class="emoji">{html.escape(em)}</span>'
                             f'<span class="count">{r.count}</span></span>')
            return ('\n       <span class="reactions">'
                    + "".join(parts) + '</span>') if parts else ""
        except Exception:
            return ""

    # ── media ─────────────────────────────────────────────────────────────────

    async def _render_media(self, msg) -> tuple:
        media = msg.media
        cap   = apply_entities(msg.message, msg.entities) if msg.message else ""

        if isinstance(media, MessageMediaPhoto):
            if not self.opts.photos:
                return self._ph("media_photo", "Photo", "not exported"), cap
            if not media.photo:
                return self._ph("media_photo", "Photo", "unavailable"), cap
            try:
                dts  = msg.date.strftime("%d-%m-%Y_%H-%M-%S")
                fn   = f"photo_{msg.id}@{dts}.jpg"
                tfn  = f"photo_{msg.id}@{dts}_thumb.jpg"
                photo_path = self.output_dir / "photos" / fn
                thumb_path = self.output_dir / "photos" / tfn
                if photo_path.exists() and thumb_path.exists():
                    # Already downloaded — read dimensions from existing thumb
                    try:
                        from PIL import Image as _PI
                        _img = _PI.open(thumb_path)
                        tw, th = _img.size
                    except Exception:
                        tw = th = 260
                else:
                    buf  = io.BytesIO()
                    async with self._dl_sem:
                        await self.client.download_media(media, file=buf)
                    data = buf.getvalue()
                    photo_path.write_bytes(data)
                    # Thumbnail in thread pool so PIL doesn't block the event loop
                    loop = asyncio.get_running_loop()
                    tw, th = await loop.run_in_executor(
                        _thumb_pool, _make_thumb_sync,
                        data, str(thumb_path), 260)
                blk = (f'\n       <div class="media_wrap clearfix">'
                       f'<a class="photo_wrap clearfix pull_left" href="photos/{fn}">'
                       f'<img class="photo" src="photos/{tfn}" style="width:{tw}px;height:{th}px"/>'
                       f'</a></div>')
                return blk, cap
            except Exception as ex:
                return self._ph("media_photo", "Photo", f"error: {ex}"), cap

        if isinstance(media, MessageMediaDocument):
            doc = media.document
            if not doc:
                return self._ph("media_file", "File", "unavailable"), cap
            try:
                mime  = doc.mime_type or "application/octet-stream"
                ext   = mimetypes.guess_extension(mime) or ""
                dts   = msg.date.strftime("%d-%m-%Y_%H-%M-%S")

                orig_name = None
                is_anim = is_video = is_voice = is_sticker = is_round = False
                is_audio = mime.startswith("audio/")
                w = h = dur = 0

                for a in (doc.attributes or []):
                    if isinstance(a, DocumentAttributeFilename):
                        orig_name = a.file_name
                    if isinstance(a, DocumentAttributeAnimated):
                        is_anim = True
                    if isinstance(a, DocumentAttributeVideo):
                        w, h = a.w, a.h
                        if getattr(a, "round_message", False):
                            is_round = True
                        else:
                            is_video = True
                    if isinstance(a, DocumentAttributeAudio):
                        is_audio = True
                        dur = int(a.duration or 0)
                        if getattr(a, "voice", False):
                            is_voice = True
                    if isinstance(a, DocumentAttributeSticker):
                        is_sticker = True

                if is_anim:
                    if not self.opts.gifs:
                        return self._ph("media_video", "GIF", "not exported"), cap
                    safe = f"animation_{msg.id}@{dts}.mp4"
                    sp   = self.output_dir / "video_files" / safe
                    ok   = False
                    try:
                        if sp.exists():
                            ok = True
                        else:
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                            ok = True
                    except Exception as ex:
                        self.log(f"    GIF dl failed {msg.id}: {ex}")
                    if ok:
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div class="animated_wrap clearfix pull_left">'
                               f'<video class="animated" autoplay loop muted playsinline'
                               f' style="display:block;max-width:400px;cursor:pointer"'
                               f' onclick="this.paused?this.play():this.pause()">'
                               f'<source src="video_files/{safe}" type="video/mp4"/>'
                               f'</video></div></div>')
                    else:
                        blk = self._ph("media_video", "GIF", "download failed")
                    return blk, cap

                if is_sticker:
                    if not self.opts.stickers:
                        return self._ph("media_file", "Sticker", "not exported"), cap
                    safe = orig_name or f"sticker_{msg.id}{ext or '.webp'}"
                    sp   = self.output_dir / "files" / safe
                    try:
                        if not sp.exists():
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div class="sticker_wrap">'
                               f'<img src="files/{html.escape(safe)}" style="max-width:200px;max-height:200px"/>'
                               f'</div></div>')
                    except Exception as ex:
                        blk = self._ph("media_file", "Sticker", f"error: {ex}")
                    return blk, cap

                if is_round:
                    if not self.opts.video_messages:
                        return self._ph("media_video", "Video message", "not exported"), cap
                    safe = f"video_message_{msg.id}@{dts}.mp4"
                    sp   = self.output_dir / "video_files" / safe
                    try:
                        if not sp.exists():
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div style="position:relative;display:inline-block">'
                               f'<video controls style="display:block;width:240px;height:240px;border-radius:50%;object-fit:cover">'
                               f'<source src="video_files/{safe}" type="video/mp4"/>'
                               f'</video></div></div>')
                    except Exception as ex:
                        blk = self._ph("media_video", "Video message", f"error: {ex}")
                    return blk, cap

                if is_video:
                    if not self.opts.videos:
                        return self._ph("media_video", "Video", "not exported"), cap
                    safe = orig_name or f"video_{msg.id}@{dts}.mp4"
                    sp   = self.output_dir / "video_files" / safe
                    sz_kb = round((doc.size or 0) / 1024, 1)
                    try:
                        if not sp.exists():
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                        st = html.escape(orig_name or "Video")
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div class="video_file_wrap" style="position:relative;display:inline-block">'
                               f'<video class="video_file" controls style="display:block;max-width:400px;max-height:400px">'
                               f'<source src="video_files/{safe}"/>'
                               f'</video>'
                               f'<div class="video_duration">'
                               f'<a href="video_files/{safe}" download="{safe}"'
                               f' style="color:#fff;text-decoration:none">{st} ({sz_kb} KB)</a>'
                               f'</div></div></div>')
                    except Exception as ex:
                        blk = self._ph("media_video", "Video", f"error: {ex}")
                    return blk, cap

                if is_voice:
                    if not self.opts.voice:
                        return self._ph("media_voice_message", "Voice message", "not exported"), cap
                    safe    = orig_name or f"voice_{msg.id}.ogg"
                    sp      = self.output_dir / "files" / safe
                    dur_str = f"{dur//60}:{dur%60:02d}" if dur else ""
                    try:
                        if not sp.exists():
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div class="media clearfix pull_left media_voice_message">'
                               f'<div class="fill pull_left"></div>'
                               f'<div class="body">'
                               f'<div class="title bold">Voice message</div>'
                               f'<div class="status details">{html.escape(dur_str)}</div>'
                               f'</div></div>'
                               f'<audio class="audio_player" controls>'
                               f'<source src="files/{html.escape(safe)}"/>'
                               f'</audio></div>')
                    except Exception as ex:
                        blk = self._ph("media_voice_message", "Voice message", f"error: {ex}")
                    return blk, cap

                if is_audio:
                    safe = orig_name or f"audio_{msg.id}{ext}"
                    sp   = self.output_dir / "files" / safe
                    try:
                        if not sp.exists():
                            async with self._dl_sem:
                                await self.client.download_media(doc, file=str(sp))
                        st  = html.escape(safe)
                        blk = (f'\n       <div class="media_wrap clearfix">'
                               f'<div class="media clearfix pull_left media_audio_file">'
                               f'<div class="fill pull_left"></div>'
                               f'<div class="body"><div class="title bold">{st}</div></div>'
                               f'</div>'
                               f'<audio class="audio_player" controls>'
                               f'<source src="files/{html.escape(safe)}"/>'
                               f'</audio></div>')
                    except Exception as ex:
                        blk = self._ph("media_audio_file", orig_name or "Audio", f"error: {ex}")
                    return blk, cap

                sz_bytes = doc.size or 0
                sz_kb    = round(sz_bytes / 1024, 1)
                sz_mb    = sz_bytes / (1024 * 1024)
                label    = orig_name or f"file{ext}"
                if not self.opts.files:
                    return self._ph("media_file", label, f"{sz_kb} KB – not exported"), cap
                if sz_mb > self.opts.file_size_limit_mb:
                    return self._ph("media_file", label,
                                    f"{sz_mb:.1f} MB – over {self.opts.file_size_limit_mb} MB limit"), cap
                safe = orig_name or f"file_{msg.id}{ext}"
                sp   = self.output_dir / "files" / safe
                href = ""
                try:
                    if not sp.exists():
                        async with self._dl_sem:
                            await self.client.download_media(doc, file=str(sp))
                    href = f"files/{safe}" if sp.exists() else ""
                except Exception as ex:
                    self.log(f"    File dl failed {msg.id}: {ex}")
                return self._file_link(label, f"{sz_kb} KB", href), cap

            except Exception as ex:
                return self._ph("media_file", "File", f"error: {ex}"), cap

        # ── Call: handle both Telethon ≤1.27 (MessageMediaCall on msg.media)
        #         and Telethon ≥1.28 (MessageActionPhoneCall on msg.action) ──
        call_obj = None
        if MessageMediaCall and isinstance(media, MessageMediaCall):
            call_obj = media          # old API: call info is the media itself
        if call_obj is not None:
            try:
                reason   = type(call_obj.reason).__name__ if getattr(call_obj, "reason", None) else ""
                duration = getattr(call_obj, "duration", 0) or 0
                status_map = {
                    "PhoneCallDiscardReasonMissed":     "Missed",
                    "PhoneCallDiscardReasonDisconnect": "Disconnected",
                    "PhoneCallDiscardReasonHangup":     "Ended",
                    "PhoneCallDiscardReasonBusy":       "Busy",
                    "PhoneCallDiscardReasonEmpty":      "Cancelled",
                    "":                                 "Cancelled",
                }
                status_str = status_map.get(reason, reason.replace("PhoneCallDiscardReason", "") or "Cancelled")
                if duration:
                    mins, secs = divmod(duration, 60)
                    status_str += f" ({mins}:{secs:02d})"
                is_video_call = getattr(call_obj, "video", False)
                call_type  = "Video call" if is_video_call else "Voice call"
                extra_cls  = " success" if duration > 0 else ""
                blk = (f'\n       <div class="media_wrap clearfix">'
                       f'\n        <div class="media clearfix pull_left media_call{extra_cls}">'
                       f'\n         <div class="fill pull_left"></div>'
                       f'\n         <div class="body">'
                       f'\n          <div class="title bold">{call_type}</div>'
                       f'\n          <div class="status details">{html.escape(status_str)}</div>'
                       f'\n         </div>'
                       f'\n        </div>'
                       f'\n       </div>')
                return blk, cap
            except Exception as ex:
                return self._ph("media_call", "Call", f"error: {ex}"), cap

        if isinstance(media, (MessageMediaGeo, MessageMediaGeoLive)):
            geo = media.geo
            if not geo:
                return self._ph("media_location", "Location", "unavailable"), cap
            lat, lon = geo.lat, geo.long
            url = f"https://maps.google.com/maps?q={lat},{lon}&amp;ll={lat},{lon}&amp;z=16"
            blk = (f'\n       <div class="media_wrap clearfix">'
                   f'\n        <a class="media clearfix pull_left block_link media_location" href="{url}">'
                   f'\n         <div class="fill pull_left"></div>'
                   f'\n         <div class="body">'
                   f'\n          <div class="title bold">Location</div>'
                   f'\n          <div class="status details">{lat:.6f}, {lon:.6f}</div>'
                   f'\n         </div>'
                   f'\n        </a>'
                   f'\n       </div>')
            return blk, cap

        if isinstance(media, MessageMediaVenue):
            lat, lon = media.geo.lat, media.geo.long
            url = f"https://maps.google.com/maps?q={lat},{lon}&amp;ll={lat},{lon}&amp;z=16"
            blk = (f'\n       <div class="media_wrap clearfix">'
                   f'\n        <a class="media clearfix pull_left block_link media_venue" href="{url}">'
                   f'\n         <div class="fill pull_left"></div>'
                   f'\n         <div class="body">'
                   f'\n          <div class="title bold">{html.escape(media.title or "Venue")}</div>'
                   f'\n          <div class="status details">{html.escape(media.address or "")}</div>'
                   f'\n         </div>'
                   f'\n        </a>'
                   f'\n       </div>')
            return blk, cap

        if isinstance(media, MessageMediaContact):
            name  = html.escape(f"{media.first_name or ''} {media.last_name or ''}".strip())
            phone = html.escape(media.phone_number or "")
            blk   = (f'\n       <div class="media_wrap clearfix">'
                     f'\n        <a class="media clearfix pull_left block_link media_contact" href="tel:{phone}">'
                     f'\n         <div class="fill pull_left"></div>'
                     f'\n         <div class="body">'
                     f'\n          <div class="title bold">{name}</div>'
                     f'\n          <div class="status details">{phone}</div>'
                     f'\n         </div>'
                     f'\n        </a>'
                     f'\n       </div>')
            return blk, cap

        if isinstance(media, MessageMediaWebPage):
            return "", cap

        return "", cap

    # ── helpers ───────────────────────────────────────────────────────────────

    def _ph(self, css: str, title: str, desc: str) -> str:
        return (f'\n       <div class="media_wrap clearfix">'
                f'<div class="media clearfix pull_left {css}">'
                f'<div class="fill pull_left"></div>'
                f'<div class="body">'
                f'<div class="title bold">{html.escape(title)}</div>'
                f'<div class="status details">{html.escape(desc)}</div>'
                f'</div></div></div>')

    def _file_link(self, title: str, desc: str, href: str) -> str:
        st = html.escape(title)
        if href:
            return (f'\n       <div class="media_wrap clearfix">'
                    f'\n        <a class="media clearfix pull_left block_link media_file"'
                    f' href="{html.escape(href)}">'
                    f'\n         <div class="fill pull_left"></div>'
                    f'\n         <div class="body">'
                    f'\n          <div class="title bold">{st}</div>'
                    f'\n          <div class="status details">{html.escape(desc)}</div>'
                    f'\n         </div>'
                    f'\n        </a>'
                    f'\n       </div>')
        return self._ph("media_file", title, desc)

    async def _dl_photo(self, msg) -> str:
        try:
            dts = msg.date.strftime("%d-%m-%Y_%H-%M-%S")
            fn  = f"photo_{msg.id}@{dts}.jpg"
            buf = io.BytesIO()
            _pp = self.output_dir / "photos" / fn
            if _pp.exists():
                return f"photos/{fn}"
            async with self._dl_sem:
                await self.client.download_media(msg.media, file=buf)
            _pp.write_bytes(buf.getvalue())
            return f"photos/{fn}"
        except Exception:
            return ""

    async def _dl_doc_generic(self, msg) -> str:
        try:
            doc = msg.media.document
            on  = next((a.file_name for a in doc.attributes
                        if isinstance(a, DocumentAttributeFilename)), None)
            ext = mimetypes.guess_extension(doc.mime_type or "") or ""
            fn  = on or f"file_{msg.id}{ext}"
            sp  = self.output_dir / "files" / fn
            if not sp.exists():
                async with self._dl_sem:
                    await self.client.download_media(doc, file=str(sp))
            return f"files/{fn}"
        except Exception:
            return ""

    def _wrap_page(self, title: str, body: str) -> str:
        h, f = self._wrap_page_parts(title)
        return h + body + f

    def _wrap_page_parts(self, title: str):
        """Return (header_html, footer_html) for streaming writes."""
        header = (f'<!DOCTYPE html>\n<html>\n <head>\n'
                  f'  <meta charset="utf-8"/>\n'
                  f'  <title>Exported Data</title>\n'
                  f'  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>\n'
                  f'  <link href="css/style.css" rel="stylesheet"/>\n'
                  f'  <script src="js/script.js"></script>\n'
                  f' </head>\n <body onload="CheckLocation();">\n'
                  f'  <div class="page_wrap">\n'
                  f'   <div class="page_header"><div class="content">'
                  f'<div class="text bold">{html.escape(title)}</div>'
                  f'</div></div>\n'
                  f'   <div class="page_body chat_page"><div class="history">\n')
        footer = '\n    </div></div>\n  </div>\n </body>\n</html>'
        return header, footer


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Chat Exporter")
        self.resizable(True, True)
        self.configure(bg="#2b2b2b")
        self._client    = None
        self._dialogs   = []
        self._loop      = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._cancel_ev = threading.Event()
        self._exporting = False
        self._confirm_cancel = False
        self._build_ui()
        self._load_env()

    def _load_env(self):
        self._api_id_var.set(os.getenv("API_ID", ""))
        self._api_hash_var.set(os.getenv("API_HASH", ""))
        self._phone_var.set(os.getenv("PHONE", ""))

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        self.columnconfigure(0, weight=3, minsize=360)
        self.columnconfigure(1, weight=4, minsize=400)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(99, weight=1)

        row = 0

        cf = ttk.LabelFrame(left, text="API Credentials", padding=8)
        cf.grid(row=row, column=0, sticky="ew", pady=(0, 8)); row += 1
        cf.columnconfigure(1, weight=1)
        self._api_id_var   = tk.StringVar()
        self._api_hash_var = tk.StringVar()
        self._phone_var    = tk.StringVar()
        for i, (lbl, var, show) in enumerate([
            ("API ID:",        self._api_id_var,   ""),
            ("API Hash:",      self._api_hash_var, "*"),
            ("Phone (+964…):", self._phone_var,    ""),
        ]):
            ttk.Label(cf, text=lbl, anchor="e", width=13).grid(row=i, column=0, sticky="e", pady=3)
            ttk.Entry(cf, textvariable=var, show=show).grid(row=i, column=1, sticky="ew", pady=3, padx=(6,0))

        sf = ttk.LabelFrame(left, text="Settings", padding=8)
        sf.grid(row=row, column=0, sticky="ew", pady=(0, 8)); row += 1
        sf.columnconfigure(1, weight=1)
        self._session_var = tk.StringVar(value="tg_exporter")
        self._output_var  = tk.StringVar(value=str(Path.home() / "TelegramExport"))
        self._limit_var   = tk.StringVar(value="0")

        ttk.Label(sf, text="Session:", anchor="e", width=13).grid(row=0, column=0, sticky="e", pady=3)
        ttk.Entry(sf, textvariable=self._session_var).grid(row=0, column=1, sticky="ew", pady=3, padx=(6,0))

        ttk.Label(sf, text="Output folder:", anchor="e", width=13).grid(row=1, column=0, sticky="e", pady=3)
        of = ttk.Frame(sf)
        of.grid(row=1, column=1, sticky="ew", pady=3, padx=(6,0))
        of.columnconfigure(0, weight=1)
        ttk.Entry(of, textvariable=self._output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(of, text="…", width=3, command=self._browse_output).grid(row=0, column=1, padx=(3,0))

        ttk.Label(sf, text="Msg limit:", anchor="e", width=13).grid(row=2, column=0, sticky="e", pady=3)
        lf = ttk.Frame(sf)
        lf.grid(row=2, column=1, sticky="ew", pady=3, padx=(6,0))
        ttk.Entry(lf, textvariable=self._limit_var, width=8).pack(side="left")
        ttk.Label(lf, text="  (0 = all)", foreground="#888").pack(side="left")

        ff = ttk.LabelFrame(left, text="Export Format", padding=8)
        ff.grid(row=row, column=0, sticky="ew", pady=(0, 8)); row += 1
        self._fmt_var = tk.StringVar(value="html")
        ttk.Radiobutton(ff, text="🌐  HTML  — Telegram Desktop style (open in browser)",
                        variable=self._fmt_var, value="html").pack(anchor="w", pady=2)
        ttk.Radiobutton(ff, text="📄  JSON  — Machine-readable data",
                        variable=self._fmt_var, value="json").pack(anchor="w", pady=2)

        mf = ttk.LabelFrame(left, text="Include in Export", padding=8)
        mf.grid(row=row, column=0, sticky="ew", pady=(0, 8)); row += 1
        mf.columnconfigure(0, weight=1)
        mf.columnconfigure(1, weight=1)

        self._cb_photos  = self._mkchk(mf, "📷  Photos",           0, 0)
        self._cb_videos  = self._mkchk(mf, "🎬  Videos",           0, 1)
        self._cb_voice   = self._mkchk(mf, "🎙  Voice messages",   1, 0)
        self._cb_vidmsg  = self._mkchk(mf, "⭕  Video messages",   1, 1)
        self._cb_stickers= self._mkchk(mf, "🎭  Stickers",         2, 0)
        self._cb_gifs    = self._mkchk(mf, "🎞  GIFs",             2, 1)
        self._cb_profpic = self._mkchk(mf, "👤  Profile pictures", 3, 0)

        self._cb_files_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mf, text="📎  Files", variable=self._cb_files_var).grid(
            row=3, column=1, sticky="w", pady=2)

        sf2 = ttk.Frame(mf)
        sf2.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4,0))
        sf2.columnconfigure(1, weight=1)
        ttk.Label(sf2, text="Max file size:", anchor="e", width=13).grid(row=0, column=0, sticky="e")
        self._file_mb_var = tk.IntVar(value=50)
        self._file_mb_lbl = tk.StringVar(value="50 MB")
        sc = ttk.Scale(sf2, from_=1, to=4000, orient="horizontal",
                       variable=self._file_mb_var, command=self._refresh_mb_label)
        sc.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(sf2, textvariable=self._file_mb_lbl, width=9, anchor="w").grid(row=0, column=2)

        # ── Concurrency slider ────────────────────────────────────────────
        sf3 = ttk.Frame(mf)
        sf3.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4,0))
        sf3.columnconfigure(1, weight=1)
        ttk.Label(sf3, text="Parallel DLs:", anchor="e", width=13).grid(row=0, column=0, sticky="e")
        self._concur_var = tk.IntVar(value=6)
        self._concur_lbl = tk.StringVar(value="6")
        sc2 = ttk.Scale(sf3, from_=1, to=20, orient="horizontal",
                        variable=self._concur_var, command=self._refresh_concur_label)
        sc2.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(sf3, textvariable=self._concur_lbl, width=9, anchor="w").grid(row=0, column=2)

        ttk.Frame(left).grid(row=99, column=0, sticky="nsew")

        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        chf = ttk.LabelFrame(right, text="Chat Selection", padding=8)
        chf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        chf.columnconfigure(0, weight=1)
        self._chat_var   = tk.StringVar()
        self._chat_combo = ttk.Combobox(chf, textvariable=self._chat_var, state="disabled")
        self._chat_combo.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(chf, text="🔗  Connect & Load Chats", command=self._on_connect).grid(
            row=1, column=0, columnspan=2, sticky="ew")

        bf = ttk.Frame(right)
        bf.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)
        bf.columnconfigure(2, weight=1)
        self._export_btn = ttk.Button(bf, text="▶  Export",       command=self._on_export, state="disabled")
        self._cancel_btn = ttk.Button(bf, text="⏹  Cancel",       command=self._on_cancel, state="disabled")
        self._folder_btn = ttk.Button(bf, text="📂  Open Folder",  command=self._open_folder)
        self._export_btn.grid(row=0, column=0, sticky="ew", padx=(0,4))
        self._cancel_btn.grid(row=0, column=1, sticky="ew", padx=4)
        self._folder_btn.grid(row=0, column=2, sticky="ew", padx=(4,0))

        pf = ttk.LabelFrame(right, text="Progress", padding=(8, 4))
        pf.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        pf.columnconfigure(0, weight=1)
        self._prog_var = tk.DoubleVar(value=0)
        self._prog_lbl = tk.StringVar(value="Idle")
        self._progressbar = ttk.Progressbar(pf, variable=self._prog_var,
                                            maximum=100, mode="determinate")
        self._progressbar.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(pf, textvariable=self._prog_lbl, anchor="center").grid(row=1, column=0, sticky="ew")

        lf3 = ttk.LabelFrame(right, text="Log", padding=8)
        lf3.grid(row=3, column=0, sticky="nsew")
        lf3.columnconfigure(0, weight=1)
        lf3.rowconfigure(0, weight=1)
        self._log = scrolledtext.ScrolledText(
            lf3, state="disabled", height=18,
            font=("Courier", 9), bg="#1a1a2e", fg="#e0e0e0",
            insertbackground="white", relief="flat", wrap="word")
        self._log.grid(row=0, column=0, sticky="nsew")
        ttk.Button(lf3, text="Clear log", command=self._clear_log).grid(
            row=1, column=0, sticky="w", pady=(4, 0))

        self._status_var = tk.StringVar(value="Ready — fill in credentials and click Connect.")
        sb = ttk.Label(self, textvariable=self._status_var, anchor="w",
                       relief="sunken", padding=(6, 2))
        sb.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.minsize(800, 560)
        self.geometry("1020x640")

    def _mkchk(self, parent, text, row, col) -> tk.BooleanVar:
        var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text=text, variable=var).grid(
            row=row, column=col, sticky="w", pady=2, padx=4)
        return var

    def _refresh_mb_label(self, _=None):
        self._file_mb_lbl.set(f"{int(self._file_mb_var.get())} MB")

    def _refresh_concur_label(self, _=None):
        self._concur_lbl.set(str(int(self._concur_var.get())))

    def _browse_output(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self._output_var.set(d)

    def _open_folder(self):
        import subprocess, sys
        path = self._output_var.get()
        if not os.path.isdir(path):
            self.log("⚠  Output folder does not exist yet.")
            return
        if sys.platform == "win32":    os.startfile(path)
        elif sys.platform == "darwin": subprocess.Popen(["open", path])
        else:                          subprocess.Popen(["xdg-open", path])

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def log(self, msg: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", msg + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
            self._status_var.set(msg[:140])
        self.after(0, _do)

    def set_progress(self, current: int, total: int):
        # FIX 6: All progressbar mutations go through self.after() — never
        # call ttk.Progressbar.step() directly from a worker thread.
        def _do():
            if total > 0:
                pct = min(100.0, current / total * 100)
                self._progressbar.configure(mode="determinate")
                self._prog_var.set(pct)
                self._prog_lbl.set(f"{current} / {total}  ({pct:.0f}%)")
            else:
                # Indeterminate: just show the count; avoid step() cross-thread crash
                self._progressbar.configure(mode="determinate")
                self._prog_var.set(0)
                self._prog_lbl.set(f"{current} messages processed…")
        self.after(0, _do)

    def reset_progress(self, label="Idle"):
        def _do():
            self._progressbar.configure(mode="determinate")
            self._prog_var.set(0)
            self._prog_lbl.set(label)
        self.after(0, _do)

    def _get_client(self):
        raw_id   = self._api_id_var.get().strip()
        api_hash = self._api_hash_var.get().strip()
        session  = self._session_var.get().strip() or "tg_exporter"
        if not raw_id:
            raise ValueError("API ID is empty — get it from https://my.telegram.org")
        if not api_hash:
            raise ValueError("API Hash is empty — get it from https://my.telegram.org")
        try:
            api_id = int(raw_id)
        except ValueError:
            raise ValueError(f"API ID must be a number, got: {raw_id!r}")
        return TelegramClient(session, api_id, api_hash,
                              connection_retries=10,
                              retry_delay=1,
                              auto_reconnect=True,
                              sequential_updates=False)

    def _on_connect(self):
        if not TELETHON_AVAILABLE:
            self.log("ERROR: Telethon not installed. Run:  pip install telethon python-dotenv pillow")
            return
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        try:
            self._client = self._get_client()
        except Exception as ex:
            self.log(f"Connection error (client init): {type(ex).__name__}: {ex}")
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)
            fut.result(timeout=120)
        except Exception as ex:
            self.log(f"Connection error: {type(ex).__name__}: {ex}")

    async def _connect_async(self):
        self.log("Connecting to Telegram…")
        try:
            await self._client.connect()
        except Exception as ex:
            self.log(f"  TCP connect failed: {type(ex).__name__}: {ex}")
            raise

        if not await self._client.is_user_authorized():
            phone = self._phone_var.get().strip()
            if not phone:
                phone = await self._ask_input("Phone number", "Enter your phone number (e.g. +9647801234567):")
            self.log(f"Sending code to {phone}…")
            await self._client.send_code_request(phone)
            code = await self._ask_input("Verification code", "Enter the code you received:")
            try:
                await self._client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pw = await self._ask_input("2FA Password", "Enter your 2FA password:", secret=True)
                await self._client.sign_in(password=pw)

        me = await self._client.get_me()
        self.log(f"✔  Logged in as: {me.first_name} (@{me.username})")
        self.log("Loading dialogs…")
        dialogs = await self._client.get_dialogs(limit=200)
        self._dialogs = dialogs
        names = [d.name or str(d.id) for d in dialogs]

        def _upd():
            self._chat_combo["values"] = names
            self._chat_combo["state"]  = "readonly"
            if names:
                self._chat_combo.current(0)
            self._export_btn["state"] = "normal"
            self.log(f"✔  Loaded {len(names)} dialogs. Select a chat and click Export.")
        self.after(0, _upd)

    async def _ask_input(self, label: str, prompt: str, secret: bool = False) -> str:
        """Show a small inline overlay frame for user input."""
        result = {"v": ""}
        evt    = threading.Event()

        def _show():
            # FIX 5: Call update_idletasks() before reading window dimensions
            self.update_idletasks()
            overlay = ttk.LabelFrame(self, text=label, padding=12)
            ow, oh = 320, 110
            ox = max(0, self.winfo_width()  // 2 - ow // 2)
            oy = max(0, self.winfo_height() // 2 - oh // 2)
            overlay.place(x=ox, y=oy, width=ow, height=oh)
            overlay.lift()
            ttk.Label(overlay, text=prompt, wraplength=290).pack(anchor="w")
            var = tk.StringVar()
            ent = ttk.Entry(overlay, textvariable=var, show="*" if secret else "")
            ent.pack(fill="x", pady=4)
            ent.focus_set()

            def _ok(e=None):
                result["v"] = var.get()
                overlay.destroy()
                evt.set()
            ent.bind("<Return>", _ok)
            ttk.Button(overlay, text="OK", command=_ok).pack(side="right")

        self.after(0, _show)
        # FIX 4: Use asyncio.get_running_loop() instead of get_event_loop()
        await asyncio.get_running_loop().run_in_executor(None, evt.wait)
        return result["v"]

    def _on_export(self):
        idx = self._chat_combo.current()
        if idx < 0:
            self.log("⚠  Please select a chat first.")
            return
        dialog = self._dialogs[idx]
        output = Path(self._output_var.get().strip())
        try:
            limit = int(self._limit_var.get().strip())
        except ValueError:
            limit = 0

        opts = ExportOptions()
        opts.photos             = self._cb_photos.get()
        opts.videos             = self._cb_videos.get()
        opts.voice              = self._cb_voice.get()
        opts.video_messages     = self._cb_vidmsg.get()
        opts.stickers           = self._cb_stickers.get()
        opts.gifs               = self._cb_gifs.get()
        opts.files              = self._cb_files_var.get()
        opts.file_size_limit_mb = int(self._file_mb_var.get())
        opts.fmt                = self._fmt_var.get()
        opts.profile_pics         = self._cb_profpic.get()
        opts.concurrent_downloads = int(self._concur_var.get())

        self._cancel_ev.clear()
        self._confirm_cancel = False
        opts.cancel_event    = self._cancel_ev
        self._exporting      = True
        self._export_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal", text="⏹  Cancel")
        self.reset_progress("Starting…")

        threading.Thread(target=self._export_thread,
                         args=(dialog.entity, output, limit, opts), daemon=True).start()

    def _on_cancel(self):
        if not self._exporting:
            return
        if not self._confirm_cancel:
            self._confirm_cancel = True
            self._cancel_btn.configure(text="⏹  Confirm Cancel")
            self.log("⚠  Click 'Confirm Cancel' again to stop the export.")
        else:
            self._cancel_ev.set()
            self._confirm_cancel = False
            self.log("Cancelling export…")
            self._cancel_btn.configure(state="disabled", text="⏹  Cancel")

    def _export_thread(self, entity, output: Path, limit: int, opts: ExportOptions):
        try:
            exp = Exporter(self._client, output, self.log,
                           limit=limit, progress_fn=self.set_progress, options=opts)
            fut = asyncio.run_coroutine_threadsafe(exp.export(entity), self._loop)
            fut.result(timeout=7200)
            if opts.cancel_event.is_set():
                self.after(0, lambda: self.reset_progress("Cancelled"))
            else:
                self.log(f"✔  Export complete → {output}")
                self.after(0, lambda: self._prog_lbl.set("Done ✔"))
        except Exception as ex:
            self.log(f"✘  Export error: {ex}")
            self.after(0, lambda: self.reset_progress("Error"))
        finally:
            self._exporting = False
            self.after(0, lambda: self._export_btn.configure(state="normal"))
            self.after(0, lambda: self._cancel_btn.configure(
                state="disabled", text="⏹  Cancel"))


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not TELETHON_AVAILABLE:
        print("ERROR: Telethon not installed.\nRun:  pip install telethon python-dotenv pillow")
    App().mainloop()