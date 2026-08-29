; E3 Positioning System project job
; Project: Golden raster power correction
; Generated: <TIMESTAMP>
; Bounds: X30..50 Y30..40
; @E3_JOB {"planner":"nearest path + fixed raster rows","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Raster Correction · 1000 mm/min · 20% · 1 pass(es) · vector correction +0 · raster correction +60
; Raster rows remain serpentine; overscan and white gaps are laser-off
; @E3_LAYER {"id":"layer-golden-raster-correction","name":"Golden Raster Correction","color":"#89B85C","power_percent":20.0,"vector_power_correction":0.0,"raster_power_correction":60.0,"mode":"raster","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden corrected raster rectangle"}
G0 X30 Y30 F3000
M4 S200
G1 X30.093 Y30 F1000 S250
G1 X30.185 Y30 F1000 S230
G1 X30.278 Y30 F1000 S210
G1 X49.722 Y30 F1000 S200
G1 X49.815 Y30 F1000 S210
G1 X49.907 Y30 F1000 S230
G1 X50 Y30 F1000 S250
M5
; @E3_PATH {"name":"Golden corrected raster rectangle"}
G0 X50 Y40 F3000
M4 S200
G1 X49.907 Y40 F1000 S250
G1 X49.815 Y40 F1000 S230
G1 X49.722 Y40 F1000 S210
G1 X30.278 Y40 F1000 S200
G1 X30.185 Y40 F1000 S210
G1 X30.093 Y40 F1000 S230
G1 X30 Y40 F1000 S250
M5
; @E3_PLANNER {"source_order_travel_mm":52.42640687119285,"planned_order_travel_mm":52.42640687119285,"savings_mm":0.0}
M5
; End of E3 project job
