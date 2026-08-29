; E3 Positioning System project job
; Project: Golden vector fill
; Generated: <TIMESTAMP>
; Bounds: X35..65 Y40..55
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Fill · 1300 mm/min · 18% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-fill","name":"Golden Fill","color":"#89B85C","power_percent":18.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"fill","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden filled rectangle"}
G0 X35 Y40 F3000
M4 S180
G1 X65 Y40 F1300
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden filled rectangle"}
G0 X65 Y45 F3000
M4 S180
G1 X35 Y45 F1300
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden filled rectangle"}
G0 X35 Y50 F3000
M4 S180
G1 X65 Y50 F1300
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden filled rectangle"}
G0 X65 Y55 F3000
M4 S180
G1 X35 Y55 F1300
M5
; @E3_PLANNER {"source_order_travel_mm":68.15072906367325,"planned_order_travel_mm":68.15072906367325,"savings_mm":0.0}
M5
; End of E3 project job
