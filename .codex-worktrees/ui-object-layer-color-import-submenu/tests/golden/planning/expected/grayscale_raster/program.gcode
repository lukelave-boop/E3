; E3 Positioning System project job
; Project: Golden grayscale raster
; Generated: <TIMESTAMP>
; Bounds: X40..60 Y42.5..57.5
; @E3_JOB {"planner":"nearest path + fixed raster rows","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Grayscale · 900 mm/min · 25% · 1 pass(es) · vector correction +0 · raster correction +0
; Raster tone: deterministic 8x8 ordered grayscale dither
; Raster rows remain serpentine; overscan and white gaps are laser-off
; @E3_LAYER {"id":"layer-golden-grayscale","name":"Golden Grayscale","color":"#89B85C","power_percent":25.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"raster","raster_tone":"ordered-dither-8x8"}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden grayscale image"}
G0 X40 Y42.5 F3000
G1 X50 Y42.5 F900
M4 S250
G1 X60 Y42.5 F900
M5
; @E3_PATH {"name":"Golden grayscale image"}
G0 X60 Y47.5 F3000
M4 S250
G1 X55 Y47.5 F900
M5
G1 X45 Y47.5 F900
M4 S250
G1 X40 Y47.5 F900
M5
; @E3_PATH {"name":"Golden grayscale image"}
G0 X40 Y52.5 F3000
G1 X50 Y52.5 F900
M4 S250
G1 X60 Y52.5 F900
M5
; @E3_PATH {"name":"Golden grayscale image"}
G0 X60 Y57.5 F3000
G1 X50 Y57.5 F900
M4 S250
G1 X40 Y57.5 F900
M5
; @E3_PLANNER {"source_order_travel_mm":73.36308764964376,"planned_order_travel_mm":73.36308764964376,"savings_mm":0.0}
M5
; End of E3 project job
