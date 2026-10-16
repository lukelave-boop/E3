# Historical project format fixtures

These frozen synthetic projects were serialized and reloaded using the unmodified
historical `ProjectDocument` implementation from the following Git commits. They
are not operator projects or captured hardware data. The timestamps, identifiers,
material provenance, text and missing image path are deliberately synthetic.

| File | Schema | Historical serializer commit |
| --- | --- | --- |
| `schema-1.e3laser` | 1 | `35025bee5906f62e14738b07379e1b6b651ff9d1` |
| `schema-2.e3laser` | 2 | `8c50f568989bd8b83f3f08b2427193e9a8ca367b` |
| `schema-3.e3laser` | 3 | `35d4c27280be4d6888d2c7d7932dad5b1159daff` |
| `schema-4.e3laser` | 4 | `1cbbdb864da928316bedefe0b8a6ea6efb9ca4e1` |
| `schema-5.e3laser` | 5 | `dcaa217a019bfab986af2bb28506264c830e7a24` |

Fixture preparation loaded `model.py` and its required `path_geometry.py`,
`material_placement.py` and `raster_cleanup.py` modules directly from each listed
commit in an isolated temporary module namespace. Each hand-authored sample
passed that version's `from_dict`, `to_dict` and second read/write round trip.
No current serializer was used to generate these files. They remain static test
inputs; do not regenerate them with current code when the schema changes.

Every file contains all seven object types, three operation modes, explicit
output/visibility/lock states, nondefault operation settings, grouping, mirrored
and rotated transforms, Unicode text and nested custom metadata. Schema 1 lacks
`coordinate_space` and power-correction fields, exercising their historical
defaults. Schemas 1 and 2 contain legacy closed and open polylines. Schemas 3–5
contain exact cubic controls and compound native paths. Schemas 2 and 4 use
honeycomb-local coordinates; the others use machine coordinates. Schema 4 adds
historical material-placement metadata, and schema 5 includes reversible image
background cleanup. The nonexistent asset remains a reference when opening;
these tests perform no raster decode, planning, machine connection or motion.

`test_project_historical_compatibility.py` checks each frozen schema independently:
data preservation and read-only opening, geometric fidelity and safe absent
feature defaults, explicit current-schema save with byte-exact original backup,
editable autosave recovery without touching the source, and rejection of
schema-6 editor data mislabeled as an older schema. Existing project/model tests
retain malformed-input and future-schema rejection coverage.

The contract is forward reading: newer E3 versions open valid older project
formats. Explicit saves and autosaves may use the current schema; these tests do
not require old binaries to read newer files, or claim exhaustive coverage of
every possible project from every historical binary.
