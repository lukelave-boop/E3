; E3 Positioning System project job
; Project: Golden simple rectangle
; Generated: <TIMESTAMP>
; Bounds: X20..60 Y17.5..42.5
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Line · 1200 mm/min · 25% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-line","name":"Golden Line","color":"#E35D6A","power_percent":25.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden rectangle"}
G0 X20 Y17.5 F3000
M4 S250
G1 X60 Y17.5 F1200
G1 X60 Y42.5 F1200
G1 X20 Y42.5 F1200
G1 X20 Y17.5 F1200
M5
; @E3_PLANNER {"source_order_travel_mm":26.575364531836623,"planned_order_travel_mm":26.575364531836623,"savings_mm":0.0}
M5
; End of E3 project job
