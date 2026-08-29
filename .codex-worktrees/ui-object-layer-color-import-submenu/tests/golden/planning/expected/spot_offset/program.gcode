; E3 Positioning System project job
; Project: Golden spot offset
; Generated: <TIMESTAMP>
; Bounds: X36.405..63.595 Y33.661..46.339
; Laser spot offset (spot = controller + offset): X2 Y-3
; Controller bounds: X34.405..61.595 Y36.661..49.339
; @E3_JOB {"planner":"source order","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Spot Offset · 1200 mm/min · 30% · 1 pass(es) · vector correction +0 · raster correction +0
; @E3_LAYER {"id":"layer-golden-spot","name":"Golden Spot Offset","color":"#89B85C","power_percent":30.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"line","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden spot line"}
G0 X34.405 Y36.661 F3000
M4 S300
G1 X61.595 Y49.339 F1200
M5
; @E3_PLANNER {"source_order_travel_mm":50.27662706488694,"planned_order_travel_mm":50.27662706488694,"savings_mm":0.0}
M5
; End of E3 project job
