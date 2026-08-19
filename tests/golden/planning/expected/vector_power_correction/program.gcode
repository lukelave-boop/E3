; E3 Positioning System project job
; Project: Golden vector power correction
; Generated: <TIMESTAMP>
; Bounds: X25..55 Y30..50
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Correction · 1800 mm/min · 35% · 1 pass(es) · vector correction +60 · raster correction +0
; @E3_LAYER {"id":"layer-golden-correction","name":"Golden Correction","color":"#5CA9E7","power_percent":35.0,"vector_power_correction":60.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden corrected rectangle"}
G0 X25 Y30 F3000
M4 S350
G1 X25.15 Y30 F1800 S394
G1 X25.3 Y30 F1800 S376
G1 X25.45 Y30 F1800 S359
G1 X54.55 Y30 F1800 S350
G1 X54.7 Y30 F1800 S359
G1 X54.85 Y30 F1800 S376
G1 X55 Y30 F1800 S394
G1 X55 Y30.15 F1800
G1 X55 Y30.3 F1800 S376
G1 X55 Y30.45 F1800 S359
G1 X55 Y49.55 F1800 S350
G1 X55 Y49.7 F1800 S359
G1 X55 Y49.85 F1800 S376
G1 X55 Y50 F1800 S394
G1 X54.85 Y50 F1800
G1 X54.7 Y50 F1800 S376
G1 X54.55 Y50 F1800 S359
G1 X25.45 Y50 F1800 S350
G1 X25.3 Y50 F1800 S359
G1 X25.15 Y50 F1800 S376
G1 X25 Y50 F1800 S394
G1 X25 Y49.85 F1800
G1 X25 Y49.7 F1800 S376
G1 X25 Y49.55 F1800 S359
G1 X25 Y30.45 F1800 S350
G1 X25 Y30.3 F1800 S359
G1 X25 Y30.15 F1800 S376
G1 X25 Y30 F1800 S394
M5
; @E3_PLANNER {"source_order_travel_mm":39.05124837953327,"planned_order_travel_mm":39.05124837953327,"savings_mm":0.0}
M5
; End of E3 project job
