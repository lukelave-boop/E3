; E3 Positioning System project job
; Project: Golden honeycomb guarded placement
; Generated: <TIMESTAMP>
; Bounds: X50..60 Y40..60
; @E3_JOB {"planner":"source order","start_x":70.0,"start_y":30.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Honeycomb · 1100 mm/min · 28% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-honeycomb","name":"Golden Honeycomb","color":"#89B85C","power_percent":28.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden local rectangle"}
G0 X60 Y40 F3000
M4 S280
G1 X60 Y60 F1100
G1 X50 Y60 F1100
G1 X50 Y40 F1100
G1 X60 Y40 F1100
M5
; @E3_PLANNER {"source_order_travel_mm":14.142135623730951,"planned_order_travel_mm":14.142135623730951,"savings_mm":0.0}
M5
; End of E3 project job
