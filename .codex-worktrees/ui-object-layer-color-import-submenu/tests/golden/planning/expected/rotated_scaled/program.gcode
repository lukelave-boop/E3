; E3 Positioning System project job
; Project: Golden rotated scaled
; Generated: <TIMESTAMP>
; Bounds: X29.923..80.077 Y24.514..65.486
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Transform · 1400 mm/min · 22% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-transform","name":"Golden Transform","color":"#5CA9E7","power_percent":22.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden transformed rectangle"}
G0 X39.594 Y24.514 F3000
M4 S220
G1 X80.077 Y50.305 F1400
G1 X70.406 Y65.486 F1400
G1 X29.923 Y39.695 F1400
G1 X39.594 Y24.514 F1400
M5
; @E3_PLANNER {"source_order_travel_mm":46.568862667878854,"planned_order_travel_mm":46.568862667878854,"savings_mm":0.0}
M5
; End of E3 project job
