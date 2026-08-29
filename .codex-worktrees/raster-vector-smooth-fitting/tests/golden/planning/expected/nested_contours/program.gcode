; E3 Positioning System project job
; Project: Golden nested contours
; Generated: <TIMESTAMP>
; Bounds: X20..80 Y20..80
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Nested · 1000 mm/min · 30% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-nested","name":"Golden Nested","color":"#5CA9E7","power_percent":30.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden nested contours"}
G0 X38 Y38 F3000
M4 S300
G1 X62 Y38 F1000
G1 X62 Y62 F1000
G1 X38 Y62 F1000
G1 X38 Y38 F1000
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden nested contours"}
G0 X20 Y20 F3000
M4 S300
G1 X80 Y20 F1000
G1 X80 Y80 F1000
G1 X20 Y80 F1000
G1 X20 Y20 F1000
M5
; @E3_PLANNER {"source_order_travel_mm":53.74011537017761,"planned_order_travel_mm":79.19595949289332,"savings_mm":0.0}
M5
; End of E3 project job
