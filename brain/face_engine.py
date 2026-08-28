# -*- coding: utf-8 -*-
"""Pixel-faithful Python port of the ESP32 "莲心" OLED face engine (eyes only).
Ports the drawing part of src/face_engine.h plus the u8g2 primitives it uses
(drawBox / drawTriangle / drawHLine) so the simulated 128x64 OLED shows exactly
what the SSD1306 on the device will render. No Qt dependency here."""
from __future__ import annotations

FIELDS = ["OffsetX","OffsetY","Height","Width","Slope_Top","Slope_Bottom",
          "Radius_Top","Radius_Bottom","Inverse_Radius_Top","Inverse_Radius_Bottom",
          "Inverse_Offset_Top","Inverse_Offset_Bottom"]
INT_FIELDS = [f for f in FIELDS if not f.startswith("Slope")]
OLED_W = 128
OLED_H = 64

import struct as _struct

def _trunc(value):
    return int(value)

def _f32(value):
    """Round to C 'float' (IEEE-754 single) precision."""
    return _struct.unpack("f", _struct.pack("f", value))[0]

class EyeConfig:
    __slots__ = FIELDS
    def __init__(self, **kw):
        defaults = dict(OffsetX=0, OffsetY=0, Height=40, Width=40, Slope_Top=0.0,
                        Slope_Bottom=0.0, Radius_Top=8, Radius_Bottom=8,
                        Inverse_Radius_Top=0, Inverse_Radius_Bottom=0,
                        Inverse_Offset_Top=0, Inverse_Offset_Bottom=0)
        defaults.update(kw)
        for f in FIELDS:
            setattr(self, f, defaults[f])
    def copy(self):
        return EyeConfig(**{f: getattr(self, f) for f in FIELDS})
    def lerp_to(self, dst, t):
        for f in FIELDS:
            v = getattr(self, f) * (1.0 - t) + getattr(dst, f) * t
            if f in INT_FIELDS:
                v = _trunc(v)
            setattr(self, f, v)
# Presets, exactly as parsed from src/face_engine.h
_P = {}
_P["Preset_Normal"]     = {"OffsetX":0,"OffsetY":0,"Height":40,"Width":40,"Slope_Top":0,"Slope_Bottom":0,"Radius_Top":8,"Radius_Bottom":8}
_P["Preset_Happy"]      = {"Height":10,"Width":40,"Radius_Top":10,"Radius_Bottom":0}
_P["Preset_Glee"]       = {"Height":8,"Width":40,"Radius_Top":8,"Radius_Bottom":0,"Inverse_Radius_Bottom":5}
_P["Preset_Sad"]        = {"Height":15,"Width":40,"Slope_Top":-0.5,"Radius_Top":1,"Radius_Bottom":10}
_P["Preset_Worried"]    = {"Height":25,"Width":40,"Slope_Top":-0.1,"Radius_Top":6,"Radius_Bottom":10}
_P["Preset_Worried_Alt"]= {"Height":35,"Width":40,"Slope_Top":-0.2,"Radius_Top":6,"Radius_Bottom":10}
_P["Preset_Focused"]    = {"Height":14,"Width":40,"Slope_Top":0.2,"Radius_Top":3,"Radius_Bottom":1}
_P["Preset_Annoyed"]    = {"Height":12,"Width":40,"Radius_Top":0,"Radius_Bottom":10}
_P["Preset_Annoyed_Alt"]= {"Height":5,"Width":40,"Radius_Top":0,"Radius_Bottom":4}
_P["Preset_Surprised"]  = {"OffsetX":-2,"Height":45,"Width":45,"Radius_Top":16,"Radius_Bottom":16}
_P["Preset_Skeptic"]    = {"Height":40,"Width":40,"Radius_Top":10,"Radius_Bottom":10}
_P["Preset_Skeptic_Alt"]= {"OffsetY":-6,"Height":26,"Width":40,"Slope_Top":0.3,"Radius_Top":1,"Radius_Bottom":10}
_P["Preset_Frustrated"]   = {"OffsetX":3,"OffsetY":-5,"Height":12,"Width":40,"Radius_Top":0,"Radius_Bottom":10}
_P["Preset_Unimpressed"]  = {"OffsetX":3,"Height":12,"Width":40,"Radius_Top":1,"Radius_Bottom":10}
_P["Preset_Unimpressed_Alt"]={"OffsetX":3,"OffsetY":-3,"Height":22,"Width":40,"Radius_Top":1,"Radius_Bottom":16}
_P["Preset_Sleepy"]       = {"OffsetY":-2,"Height":14,"Width":40,"Slope_Top":-0.5,"Slope_Bottom":-0.5,"Radius_Top":3,"Radius_Bottom":3}
_P["Preset_Sleepy_Alt"]   = {"OffsetY":-2,"Height":8,"Width":40,"Slope_Top":-0.5,"Slope_Bottom":-0.5,"Radius_Top":3,"Radius_Bottom":3}
_P["Preset_Suspicious"]   = {"Height":22,"Width":40,"Radius_Top":8,"Radius_Bottom":3}
_P["Preset_Suspicious_Alt"]={"OffsetY":-3,"Height":16,"Width":40,"Slope_Top":0.2,"Radius_Top":6,"Radius_Bottom":3}
_P["Preset_Squint"]       = {"OffsetX":-10,"OffsetY":-3,"Height":35,"Width":35,"Radius_Top":8,"Radius_Bottom":8}
_P["Preset_Squint_Alt"]   = {"OffsetX":5,"Height":20,"Width":20,"Radius_Top":5,"Radius_Bottom":5}
_P["Preset_Angry"]        = {"OffsetX":-3,"Height":20,"Width":40,"Slope_Top":0.3,"Radius_Top":2,"Radius_Bottom":12}
_P["Preset_Furious"]      = {"OffsetX":-2,"Height":30,"Width":40,"Slope_Top":0.4,"Radius_Top":2,"Radius_Bottom":8}
_P["Preset_Scared"]       = {"OffsetX":-3,"Height":40,"Width":40,"Slope_Top":-0.1,"Radius_Top":12,"Radius_Bottom":8}
_P["Preset_Awe"]          = {"OffsetX":2,"Height":35,"Width":45,"Slope_Top":-0.1,"Slope_Bottom":0.1,"Radius_Top":12,"Radius_Bottom":12}

def _cfg(d):
    full = dict(OffsetX=0,OffsetY=0,Height=40,Width=40,Slope_Top=0.0,Slope_Bottom=0.0,
                Radius_Top=8,Radius_Bottom=8,Inverse_Radius_Top=0,Inverse_Radius_Bottom=0,
                Inverse_Offset_Top=0,Inverse_Offset_Bottom=0)
    full.update(d)
    return EyeConfig(**full)

PRESETS = {name: _cfg(d) for name, d in _P.items()}
# Emotion list: number (1..18) matches the firmware emotion-loop index.
EMOTIONS = [
    (1,"Normal","平静","Preset_Normal","Preset_Normal",{"Rv1":{"Height":3},"Rv2":{"Width":1},"Lv1":{"Height":2},"Lv2":{"Width":2}},{"Rv1":("tri",1000,0),"Lv1":("tri",1000,0)}),
    (2,"Angry","生气","Preset_Angry","Preset_Angry",{"Rv1":{"OffsetY":2},"Lv1":{"OffsetY":2}},{"Rv1":("tri",300,0),"Lv1":("tri",300,0)}),
    (3,"Glee","窃喜","Preset_Glee","Preset_Glee",{"Rv1":{"OffsetY":5},"Lv1":{"OffsetY":5}},{"Rv1":("tri",300,0),"Lv1":("tri",300,0)}),
    (4,"Happy","开心","Preset_Happy","Preset_Happy",{},{}),
    (5,"Sad","难过","Preset_Sad","Preset_Sad",{},{}),
    (6,"Worried","担忧","Preset_Worried","Preset_Worried_Alt",{},{}),
    (7,"Focused","专注","Preset_Focused","Preset_Focused",{},{}),
    (8,"Annoyed","烦闷","Preset_Annoyed","Preset_Annoyed_Alt",{},{}),
    (9,"Surprised","惊讶","Preset_Surprised","Preset_Surprised",{},{}),
    (10,"Skeptic","怀疑","Preset_Skeptic","Preset_Skeptic_Alt",{},{}),
    (11,"Frustrated","沮丧","Preset_Frustrated","Preset_Frustrated",{},{}),
    (12,"Unimpressed","无语","Preset_Unimpressed","Preset_Unimpressed_Alt",{},{}),
    (13,"Sleepy","困倦","Preset_Sleepy","Preset_Sleepy_Alt",{},{}),
    (14,"Suspicious","狐疑","Preset_Suspicious","Preset_Suspicious_Alt",{},{}),
    (15,"Squint","眯眼","Preset_Squint","Preset_Squint_Alt",{"Lv1":{"OffsetX":6},"Lv2":{"OffsetY":6}},{}),
    (16,"Furious","暴怒","Preset_Furious","Preset_Furious",{},{}),
    (17,"Scared","害怕","Preset_Scared","Preset_Scared",{},{}),
    (18,"Awe","惊叹","Preset_Awe","Preset_Awe",{},{}),
]
EMOTION_BY_NUM = {e[0]: e for e in EMOTIONS}
# 128x64 framebuffer replicating u8g2 buffer semantics (color 1=set, 0=xor)
class FrameBuffer:
    def __init__(self, width=OLED_W, height=OLED_H):
        self.width = width
        self.height = height
        self.pixels = [[0] * width for _ in range(height)]
    def clear(self):
        for row in self.pixels:
            for i in range(self.width):
                row[i] = 0
    def _hline(self, x, y, length, mode):
        if y < 0 or y >= self.height or length <= 0:
            return
        x0 = max(x, 0)
        x1 = min(x + length, self.width)
        row = self.pixels[y]
        if mode == "set":
            for i in range(x0, x1):
                row[i] = 1
        else:
            for i in range(x0, x1):
                row[i] ^= 1
    def box(self, x0, y0, x1, y1, color):
        l, r = min(x0, x1), max(x0, x1)
        t, b = min(y0, y1), max(y0, y1)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return
        mode = "set" if color == 1 else "xor"
        for y in range(t, t + h):
            self._hline(l, y, w, mode)
    def triangle(self, x0, y0, x1, y1, x2, y2, color):
        mode = "set" if color == 1 else "xor"
        self._draw_polygon([(x0, y0), (x1, y1), (x2, y2)], mode)
    # Faithful port of u8g2_polygon.c (convex polygon rasterizer)
    def _draw_polygon(self, points, mode):
        cnt = len(points)
        max_y = min_y = points[0][1]
        left_idx = 0
        for i in range(1, cnt):
            if max_y < points[i][1]:
                max_y = points[i][1]
            if min_y > points[i][1]:
                left_idx = i
                min_y = points[i][1]
        total_scan = max_y - min_y
        if total_scan == 0:
            return
        right_idx = left_idx
        while True:
            i = (right_idx + 1) % cnt
            if points[i][1] != min_y:
                break
            right_idx = i
        while True:
            i = (left_idx - 1) % cnt
            if points[i][1] != min_y:
                break
            left_idx = i
        if points[left_idx][0] == points[right_idx][0]:
            total_scan -= 1
            if total_scan == 0:
                return
            is_flat = True
        else:
            is_flat = False
        def pge_init(x1, y1, x2, y2):
            dx = x2 - x1
            height = y2 - y1
            if height == 0:
                return {"max_y": y2, "current_y": y1, "current_x": x1,
                        "current_x_offset": 0, "error_offset": 0, "error": 0,
                        "x_direction": 1, "height": 0}
            if dx >= 0:
                x_direction, width, error = 1, dx, 0
            else:
                x_direction, width, error = -1, -dx, 1 - height
            return {"max_y": y2, "current_y": y1, "current_x": x1,
                    "current_x_offset": int(dx / height),
                    "error_offset": width % height, "error": error,
                    "x_direction": x_direction, "height": height}
        def pge_next(e):
            if e["current_y"] >= e["max_y"]:
                return False
            e["current_x"] += e["current_x_offset"]
            e["error"] += e["error_offset"]
            if e["error"] > 0:
                e["current_x"] += e["x_direction"]
                e["error"] -= e["height"]
            e["current_y"] += 1
            return True
        def edge_from(edge, start_idx, direction):
            idx = start_idx
            y1, x1 = points[idx][1], points[idx][0]
            nxt = (idx + direction) % cnt
            y2, x2 = points[nxt][1], points[nxt][0]
            edge["curr_idx"] = nxt
            edge["pge"] = pge_init(x1, y1, x2, y2)
        left = {"curr_idx": left_idx, "pge": None}
        right = {"curr_idx": right_idx, "pge": None}
        edge_from(left, left_idx, -1)
        edge_from(right, right_idx, 1)
        if is_flat:
            pge_next(left["pge"])
            pge_next(right["pge"])
        i = total_scan
        while i > 0:
            lx = left["pge"]["current_x"]
            rx = right["pge"]["current_x"]
            y = right["pge"]["current_y"]
            if 0 <= y < self.height:
                x1, x2 = min(lx, rx), max(lx, rx)
                if x1 < self.width and x2 > 0:
                    self._hline(x1, y, x2 - x1, mode)
            while not pge_next(left["pge"]):
                edge_from(left, left["curr_idx"], -1)
            while not pge_next(right["pge"]):
                edge_from(right, right["curr_idx"], 1)
            i -= 1
# Eye drawing primitives (port of EyeDrawer.h)
def _fill_rect(fb, x0, y0, x1, y1, color):
    fb.box(x0, y0, x1, y1, color)

def _fill_rect_triangle(fb, x0, y0, x1, y1, color):
    fb.triangle(x0, y0, x1, y1, x1, y0, color)

def _fill_ellipse_corner(fb, corner, x0, y0, rx, ry):
    if rx < 2 or ry < 2:
        return
    rx2, ry2 = rx * rx, ry * ry
    fx2, fy2 = 4 * rx2, 4 * ry2
    if corner == "T_R":
        x, y = 0, ry
        s = 2 * ry2 + rx2 * (1 - 2 * ry)
        while ry2 * x <= rx2 * y:
            fb._hline(x0, y0 - y, x, "set")
            if s >= 0:
                s += fx2 * (1 - y); y -= 1
            s += ry2 * (4 * x + 6); x += 1
        x, y = rx, 0
        s = 2 * rx2 + ry2 * (1 - 2 * rx)
        while rx2 * y <= ry2 * x:
            fb._hline(x0, y0 - y, x, "set")
            if s >= 0:
                s += fy2 * (1 - x); x -= 1
            s += rx2 * (4 * y + 6); y += 1
    elif corner == "B_R":
        x, y = 0, ry
        s = 2 * ry2 + rx2 * (1 - 2 * ry)
        while ry2 * x <= rx2 * y:
            fb._hline(x0, y0 + y - 1, x, "set")
            if s >= 0:
                s += fx2 * (1 - y); y -= 1
            s += ry2 * (4 * x + 6); x += 1
        x, y = rx, 0
        s = 2 * rx2 + ry2 * (1 - 2 * rx)
        while rx2 * y <= ry2 * x:
            fb._hline(x0, y0 + y - 1, x, "set")
            if s >= 0:
                s += fy2 * (1 - x); x -= 1
            s += rx2 * (4 * y + 6); y += 1
    elif corner == "T_L":
        x, y = 0, ry
        s = 2 * ry2 + rx2 * (1 - 2 * ry)
        while ry2 * x <= rx2 * y:
            fb._hline(x0 - x, y0 - y, x, "set")
            if s >= 0:
                s += fx2 * (1 - y); y -= 1
            s += ry2 * (4 * x + 6); x += 1
        x, y = rx, 0
        s = 2 * rx2 + ry2 * (1 - 2 * rx)
        while rx2 * y <= ry2 * x:
            fb._hline(x0 - x, y0 - y, x, "set")
            if s >= 0:
                s += fy2 * (1 - x); x -= 1
            s += rx2 * (4 * y + 6); y += 1
    elif corner == "B_L":
        x, y = 0, ry
        s = 2 * ry2 + rx2 * (1 - 2 * ry)
        while ry2 * x <= rx2 * y:
            fb._hline(x0 - x, y0 + y - 1, x, "set")
            if s >= 0:
                s += fx2 * (1 - y); y -= 1
            s += ry2 * (4 * x + 6); x += 1
        x, y = rx, 0
        s = 2 * rx2 + ry2 * (1 - 2 * rx)
        while rx2 * y <= ry2 * x:
            fb._hline(x0 - x, y0 + y, x, "set")
            if s >= 0:
                s += fy2 * (1 - x); x -= 1
            s += rx2 * (4 * y + 6); y += 1
def draw_eye(fb, center_x, center_y, cfg):
    height = cfg.Height
    delta_y_top = int(height * cfg.Slope_Top / 2.0)
    delta_y_bottom = int(height * cfg.Slope_Bottom / 2.0)
    total_height = height + delta_y_top - delta_y_bottom
    radius_top = cfg.Radius_Top
    radius_bottom = cfg.Radius_Bottom
    if radius_bottom > 0 and radius_top > 0 and total_height - 1 < radius_bottom + radius_top:
        radius_top = _trunc(radius_top * (total_height - 1) / (radius_bottom + radius_top))
        radius_bottom = _trunc(radius_bottom * (total_height - 1) / (radius_bottom + radius_top))
    h2 = int(height / 2)
    w2 = int(cfg.Width / 2)
    tl_y = _trunc(center_y + cfg.OffsetY - h2 + radius_top - delta_y_top)
    tl_x = _trunc(center_x + cfg.OffsetX - w2 + radius_top)
    tr_y = _trunc(center_y + cfg.OffsetY - h2 + radius_top + delta_y_top)
    tr_x = _trunc(center_x + cfg.OffsetX + w2 - radius_top)
    bl_y = _trunc(center_y + cfg.OffsetY + h2 - radius_bottom - delta_y_bottom)
    bl_x = _trunc(center_x + cfg.OffsetX - w2 + radius_bottom)
    br_y = _trunc(center_y + cfg.OffsetY + h2 - radius_bottom + delta_y_bottom)
    br_x = _trunc(center_x + cfg.OffsetX + w2 - radius_bottom)
    min_c_x = min(tl_x, bl_x); max_c_x = max(tr_x, br_x)
    min_c_y = min(tl_y, tr_y); max_c_y = max(bl_y, br_y)
    _fill_rect(fb, min_c_x, min_c_y, max_c_x, max_c_y, 1)
    _fill_rect(fb, tr_x, tr_y, br_x + radius_bottom, br_y, 1)
    _fill_rect(fb, tl_x - radius_top, tl_y, bl_x, bl_y, 1)
    _fill_rect(fb, tl_x, tl_y - radius_top, tr_x, tr_y, 1)
    _fill_rect(fb, bl_x, bl_y, br_x, br_y + radius_bottom, 1)
    if cfg.Slope_Top > 0:
        _fill_rect_triangle(fb, tl_x, tl_y - radius_top, tr_x, tr_y - radius_top, 0)
        _fill_rect_triangle(fb, tr_x, tr_y - radius_top, tl_x, tl_y - radius_top, 1)
    elif cfg.Slope_Top < 0:
        _fill_rect_triangle(fb, tr_x, tr_y - radius_top, tl_x, tl_y - radius_top, 0)
        _fill_rect_triangle(fb, tl_x, tl_y - radius_top, tr_x, tr_y - radius_top, 1)
    if cfg.Slope_Bottom > 0:
        _fill_rect_triangle(fb, br_x + radius_bottom, br_y + radius_bottom, bl_x - radius_bottom, bl_y + radius_bottom, 0)
        _fill_rect_triangle(fb, bl_x - radius_bottom, bl_y + radius_bottom, br_x + radius_bottom, br_y + radius_bottom, 1)
    elif cfg.Slope_Bottom < 0:
        _fill_rect_triangle(fb, bl_x - radius_bottom, bl_y + radius_bottom, br_x + radius_bottom, br_y + radius_bottom, 0)
        _fill_rect_triangle(fb, br_x + radius_bottom, br_y + radius_bottom, bl_x - radius_bottom, bl_y + radius_bottom, 1)
    if radius_top > 0:
        _fill_ellipse_corner(fb, "T_L", tl_x, tl_y, radius_top, radius_top)
        _fill_ellipse_corner(fb, "T_R", tr_x, tr_y, radius_top, radius_top)
    if radius_bottom > 0:
        _fill_ellipse_corner(fb, "B_L", bl_x, bl_y, radius_bottom, radius_bottom)
        _fill_ellipse_corner(fb, "B_R", br_x, br_y, radius_bottom, radius_bottom)
# Animation classes (ports of Ramp / Trapezium / TrapeziumPulse)
class Ramp:
    def __init__(self, interval):
        self.interval = interval
        self.start = 0
    def restart(self, now):
        self.start = now
    def value(self, now):
        e = now - self.start
        if e >= self.interval or self.interval == 0:
            return 1.0
        return e / self.interval

class TrapeziumPulse:
    def __init__(self, t0=0, t1=0, t2=0, t3=0, t4=0):
        self.t0, self.t1, self.t2, self.t3, self.t4 = t0, t1, t2, t3, t4
        self.interval = t0 + t1 + t2 + t3 + t4
        self.start = 0
    def restart(self, now):
        self.start = now
    def set_triangle(self, t, delay):
        self.t0 = 0
        self.t1 = t // 2
        self.t2 = 0
        self.t3 = self.t1
        self.t4 = delay
        self.interval = self.t0 + self.t1 + self.t2 + self.t3 + self.t4
    def set_pulse(self, t, delay):
        self.t0 = 0
        self.t1 = t // 3
        self.t2 = t - self.t0 - self.t0
        self.t3 = self.t1
        self.t4 = delay
        self.interval = self.t0 + self.t1 + self.t2 + self.t3 + self.t4
    def value(self, now):
        if self.interval == 0:
            return 0.0
        elapsed = (now - self.start) % self.interval
        if elapsed < self.t0:
            return 0.0
        if elapsed < self.t0 + self.t1:
            return (elapsed - self.t0) / self.t1 if self.t1 else 0.0
        if elapsed < self.t0 + self.t1 + self.t2:
            return 1.0
        if elapsed < self.t0 + self.t1 + self.t2 + self.t3:
            return 1.0 - (elapsed - self.t2 - self.t1 - self.t0) / self.t3 if self.t3 else 0.0
        return 0.0

class Trapezium:
    def __init__(self, t0, t1, t2):
        self.t0, self.t1, self.t2 = t0, t1, t2
        self.interval = t0 + t1 + t2
        self.start = 0
    def restart(self, now):
        self.start = now
    def value(self, now):
        elapsed = now - self.start
        if elapsed > self.interval:
            return 0.0
        if elapsed < self.t0:
            return elapsed / self.t0 if self.t0 else 1.0
        if elapsed < self.t0 + self.t1:
            return 1.0
        return 1.0 - (elapsed - self.t1 - self.t0) / self.t2 if self.t2 else 0.0
# Eye operator chain: transition -> transformation -> variation1 -> variation2 -> blink
class Eye:
    def __init__(self, mirrored):
        self.mirrored = mirrored
        self.center_x = 0
        self.center_y = 0
        self.config = PRESETS["Preset_Normal"].copy()
        self.destin = PRESETS["Preset_Normal"].copy()
        self.transition_ramp = Ramp(500)
        self.tf = dict(MoveX=0.0, MoveY=0.0, ScaleX=1.0, ScaleY=1.0)
        self.tf_origin = dict(MoveX=0.0, MoveY=0.0, ScaleX=1.0, ScaleY=1.0)
        self.tf_destin = dict(MoveX=0.0, MoveY=0.0, ScaleX=1.0, ScaleY=1.0)
        self.tf_ramp = Ramp(200)
        self.v1 = {"values": {f: 0 for f in FIELDS}, "anim": TrapeziumPulse(200, 200, 200, 200, 0)}
        self.v2 = {"values": {f: 0 for f in FIELDS}, "anim": TrapeziumPulse(0, 200, 200, 200, 200)}
        self.blink_anim = Trapezium(40, 100, 40)
        self.blink_width = 60
        self.blink_height = 2

    def _mirror_cfg(self, cfg):
        out = cfg.copy()
        if self.mirrored:
            out.OffsetX = -cfg.OffsetX
            out.Slope_Top = cfg.Slope_Top
            out.Slope_Bottom = cfg.Slope_Bottom
        else:
            out.Slope_Top = -cfg.Slope_Top
            out.Slope_Bottom = -cfg.Slope_Bottom
        out.OffsetY = -cfg.OffsetY
        return out

    def transition_to(self, preset, now):
        self.destin = self._mirror_cfg(preset)
        self.transition_ramp.restart(now)

    def apply_preset(self, preset, now):
        self.config = self._mirror_cfg(preset)
        self.destin = self.config.copy()
        self.transition_ramp.restart(now)

    def clear_variations(self, now):
        for v in (self.v1, self.v2):
            for f in FIELDS:
                v["values"][f] = 0
        self.v1["anim"].restart(now)

    def set_variation(self, stage, field, value):
        (self.v1 if stage == "v1" else self.v2)["values"][field] = value

    def restart_blink(self, now):
        self.blink_anim.restart(now)

    def _variation_apply(self, src, values, t):
        out = src.copy()
        for f in FIELDS:
            v = getattr(src, f) + values[f] * t
            if f in INT_FIELDS:
                v = _trunc(v)
            setattr(out, f, v)
        return out

    def _blink_apply(self, src, t):
        out = src.copy()
        out.OffsetX = src.OffsetX
        out.OffsetY = src.OffsetY
        out.Width = _trunc((self.blink_width - src.Width) * t + src.Width)
        out.Height = _trunc((self.blink_height - src.Height) * t + src.Height)
        out.Slope_Top = src.Slope_Top * (1.0 - t)
        out.Slope_Bottom = src.Slope_Bottom * (1.0 - t)
        for f in ["Radius_Top", "Radius_Bottom", "Inverse_Radius_Top",
                  "Inverse_Radius_Bottom", "Inverse_Offset_Top", "Inverse_Offset_Bottom"]:
            setattr(out, f, _trunc(getattr(src, f) * (1.0 - t)))
        return out

    def _final_config(self, now):
        self.config.lerp_to(self.destin, self.transition_ramp.value(now))
        t = self.tf_ramp.value(now)
        cur = {k: (self.tf_destin[k] - self.tf_origin[k]) * t + self.tf_origin[k] for k in self.tf}
        out = self.config.copy()
        out.OffsetX = _trunc(self.config.OffsetX + cur["MoveX"])
        out.OffsetY = _trunc(self.config.OffsetY - cur["MoveY"])
        out.Width = _trunc(self.config.Width * cur["ScaleX"])
        out.Height = _trunc(self.config.Height * cur["ScaleY"])
        v1t = 2.0 * self.v1["anim"].value(now) - 1.0
        out = self._variation_apply(out, self.v1["values"], v1t)
        v2t = 2.0 * self.v2["anim"].value(now) - 1.0
        out = self._variation_apply(out, self.v2["values"], v2t)
        t = self.blink_anim.value(now)
        if now - self.blink_anim.start > self.blink_anim.interval:
            t = 0.0
        t = t * t
        out = self._blink_apply(out, t)
        return out

    def draw(self, fb, now):
        draw_eye(fb, self.center_x, self.center_y, self._final_config(now))
# Face (port of Face.cpp) + firmware emotion-loop emulation
class FaceSim:
    def __init__(self, width=OLED_W, height=OLED_H, eye_size=40, init_normal=True):
        self.width = width
        self.height = height
        self.eye_size = eye_size
        self.center_x = width // 2
        self.center_y = height // 2
        self.eye_inter_distance = 4
        self.left = Eye(mirrored=True)
        self.right = Eye(mirrored=False)
        self.fb = FrameBuffer(width, height)
        self.current_num = 1
        self.draw_index = True
        self.last_look = 0
        self.last_blink = 0
        self.random_look = False
        self.random_blink = False
        self.look_interval = 4000
        self.blink_interval = 3500
        self._place_eyes()
        if init_normal:
            self.goto(1, 0)

    def _place_eyes(self):
        self.left.center_x = self.center_x - self.eye_size // 2 - self.eye_inter_distance
        self.left.center_y = self.center_y
        self.right.center_x = self.center_x + self.eye_size // 2 + self.eye_inter_distance
        self.right.center_y = self.center_y

    def goto(self, num, now):
        if 1 <= num <= len(EMOTIONS):
            self.current_num = num
            info = EMOTION_BY_NUM[num]
            right_preset = PRESETS[info[3]]
            left_preset = PRESETS[info[4]]
            for eye in (self.left, self.right):
                eye.clear_variations(now)
            for key, vals in info[5].items():
                obj = self.right if key.startswith("R") else self.left
                stage = "v1" if key.endswith("1") else "v2"
                for field, value in vals.items():
                    obj.set_variation(stage, field, value)
            for key, (kind, t, delay) in info[6].items():
                obj = self.right if key.startswith("R") else self.left
                stage = "v1" if key.endswith("1") else "v2"
                anim = (obj.v1 if stage == "v1" else obj.v2)["anim"]
                anim.set_triangle(t, delay)
                anim.restart(now)
            self.right.transition_to(right_preset, now)
            self.left.transition_to(left_preset, now)

    def _look_at(self, x, y):
        move_x = -25 * x
        move_y = 20 * y
        s_x = 1.0 - x * 0.2
        s_y = 1.0 - abs(y) * 0.4
        t = {"MoveX": move_x, "MoveY": move_y, "ScaleX": 1.0, "ScaleY": s_x * s_y}
        self.right.tf_origin = dict(self.right.tf)
        self.right.tf_destin = t
        self.right.tf_ramp.restart(self._now)
        s_x2 = 1.0 + x * 0.2
        t2 = {"MoveX": move_x, "MoveY": move_y, "ScaleX": 1.0, "ScaleY": s_x2 * s_y}
        self.left.tf_origin = dict(self.left.tf)
        self.left.tf_destin = t2
        self.left.tf_ramp.restart(self._now)

    def tick(self, now):
        self._now = now
        if self.random_look and now - self.last_look >= self.look_interval:
            self.last_look = now
            import random as _r
            self._look_at(_r.randint(-50, 50) / 100.0, _r.randint(-50, 50) / 100.0)
        if self.random_blink and now - self.last_blink >= self.blink_interval:
            self.last_blink = now
            self.left.restart_blink(now)
            self.right.restart_blink(now)
        self.fb.clear()
        self.left.draw(self.fb, now)
        self.right.draw(self.fb, now)
        if self.draw_index:
            draw_index_number(self.fb, self.current_num)
        return self.fb
# Emotion index drawn bottom-left, like firmware's u8g2_font_5x7 number
_DIGITS_5X7 = {
    "0": [0b01110,0b10001,0b10011,0b10101,0b11001,0b10001,0b01110],
    "1": [0b00100,0b01100,0b00100,0b00100,0b00100,0b00100,0b01110],
    "2": [0b01110,0b10001,0b00001,0b00010,0b00100,0b01000,0b11111],
    "3": [0b11111,0b00010,0b00100,0b00010,0b00001,0b10001,0b01110],
    "4": [0b00010,0b00110,0b01010,0b10010,0b11111,0b00010,0b00010],
    "5": [0b11111,0b10000,0b11110,0b00001,0b00001,0b10001,0b01110],
    "6": [0b00110,0b01000,0b10000,0b11110,0b10001,0b10001,0b01110],
    "7": [0b11111,0b00001,0b00010,0b00100,0b01000,0b01000,0b01000],
    "8": [0b01110,0b10001,0b10001,0b01110,0b10001,0b10001,0b01110],
    "9": [0b01110,0b10001,0b10001,0b01111,0b00001,0b00010,0b01100],
}
def draw_index_number(fb, num, x0=0, baseline_y=62):
    top = baseline_y - 7
    for ch in str(num):
        glyph = _DIGITS_5X7.get(ch)
        if glyph is None:
            continue
        for row in range(7):
            bits = glyph[row]
            for col in range(5):
                if bits & (1 << (4 - col)):
                    y = top + row
                    xx = x0 + col
                    if 0 <= y < fb.height and 0 <= xx < fb.width:
                        fb.pixels[y][xx] = 1
        x0 += 6

def ascii_preview(fb, lit="#", off=" "):
    return "\n".join("".join(lit if p else off for p in row) for row in fb.pixels)
