; E3 Positioning System project job
; Project: Golden multiple disjoint
; Generated: <TIMESTAMP>
; Bounds: X10..86 Y10..76
; @E3_JOB {"planner":"nearest path","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Disjoint · 1600 mm/min · 15% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-disjoint","name":"Golden Disjoint","color":"#5CA9E7","power_percent":15.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Disjoint A"}
G0 X10 Y10 F3000
M4 S150
G1 X20 Y10 F1600
G1 X20 Y20 F1600
G1 X10 Y20 F1600
G1 X10 Y10 F1600
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Disjoint C"}
G0 X38 Y64 F3000
M4 S150
G1 X52 Y64 F1600
G1 X52 Y76 F1600
G1 X38 Y76 F1600
G1 X38 Y64 F1600
M5
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Disjoint B"}
G0 X74 Y16 F3000
M4 S150
G1 X86 Y16 F1600
G1 X86 Y24 F1600
G1 X74 Y24 F1600
G1 X74 Y16 F1600
M5
; @E3_PLANNER {"source_order_travel_mm":138.42277034368374,"planned_order_travel_mm":134.96976092671315,"savings_mm":3.4530094169705876}
M5
; End of E3 project job
