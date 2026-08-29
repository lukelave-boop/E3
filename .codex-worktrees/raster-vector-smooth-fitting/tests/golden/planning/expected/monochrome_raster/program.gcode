; E3 Positioning System project job
; Project: Golden monochrome raster
; Generated: <TIMESTAMP>
; Bounds: X30..50 Y30..45
; @E3_JOB {"planner":"nearest path + fixed raster rows","start_x":0.0,"start_y":0.0}
G21 ; millimetres
G90 ; absolute positioning
M5 ; laser off before any motion
; Layer Golden Raster · 1000 mm/min · 20% · 1 pass(es) · vector correction +0 · raster correction +0
; Raster rows remain serpentine; overscan and white gaps are laser-off
; @E3_LAYER {"id":"layer-golden-raster","name":"Golden Raster","color":"#89B85C","power_percent":20.0,"vector_power_correction":0.0,"raster_power_correction":0.0,"mode":"raster","raster_tone":""}
; Pass 1/1
; @E3_PASS {"index":1,"count":1}
; @E3_PATH {"name":"Golden raster rectangle"}
G0 X28 Y30 F3000
G1 X30 Y30 F1000
M4 S200
G1 X50 Y30 F1000
M5
G1 X52 Y30 F1000
; @E3_PATH {"name":"Golden raster rectangle"}
G0 X52 Y35 F3000
G1 X50 Y35 F1000
M4 S200
G1 X30 Y35 F1000
M5
G1 X28 Y35 F1000
; @E3_PATH {"name":"Golden raster rectangle"}
G0 X28 Y40 F3000
G1 X30 Y40 F1000
M4 S200
G1 X50 Y40 F1000
M5
G1 X52 Y40 F1000
; @E3_PATH {"name":"Golden raster rectangle"}
G0 X52 Y45 F3000
G1 X50 Y45 F1000
M4 S200
G1 X30 Y45 F1000
M5
G1 X28 Y45 F1000
; @E3_PLANNER {"source_order_travel_mm":56.036569057366385,"planned_order_travel_mm":56.036569057366385,"savings_mm":0.0}
M5
; End of E3 project job
