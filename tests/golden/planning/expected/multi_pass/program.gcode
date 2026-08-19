; E3 Positioning System project job
; Project: Golden multi pass
; Generated: <TIMESTAMP>
; Bounds: X23..47 Y32..48
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Multi Pass · 900 mm/min · 40% · 3 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-multipass","name":"Golden Multi Pass","color":"#5CA9E7","power_percent":40.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/3
; @E3_PASS {"index":1,"count":3}
; @E3_PATH {"name":"Golden three pass rectangle"}
G0 X23 Y32 F3000
M4 S400
G1 X47 Y32 F900
G1 X47 Y48 F900
G1 X23 Y48 F900
G1 X23 Y32 F900
M5
; Pass 2/3
; @E3_PASS {"index":2,"count":3}
; @E3_PATH {"name":"Golden three pass rectangle"}
G0 X23 Y32 F3000
M4 S400
G1 X47 Y32 F900
G1 X47 Y48 F900
G1 X23 Y48 F900
G1 X23 Y32 F900
M5
; Pass 3/3
; @E3_PASS {"index":3,"count":3}
; @E3_PATH {"name":"Golden three pass rectangle"}
G0 X23 Y32 F3000
M4 S400
G1 X47 Y32 F900
G1 X47 Y48 F900
G1 X23 Y48 F900
G1 X23 Y32 F900
M5
; @E3_PLANNER {"source_order_travel_mm":39.408120990476064,"planned_order_travel_mm":39.408120990476064,"savings_mm":0.0}
M5
; End of E3 project job
