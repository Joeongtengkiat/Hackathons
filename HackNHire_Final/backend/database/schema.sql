-- schema.sql - every table the project stores in Neon, in one place.
--
-- Two levels:
--   * shared, created ONCE for the database:   python database/maps.py init
--       public.maps (the list of maps) and PostGIS (needed only for the outlines)
--   * per map, stamped out from this file at every upload, in the map's own schema map_<name>
--     (e.g. map_jambi): one schema per map, so deleting or replacing a map is one DROP SCHEMA,
--     instant, with its storage freed at once.
--
-- The code reads this file (common.schema_sql): each section starts with a line "-- @name" and
-- runs as one batch. {schema} becomes the map's schema and {srid} its EPSG code. Tables that hold
-- pages or shapes get their keys and indexes in a separate *_keys section, run after the rows are
-- loaded (much faster than updating an index row by row).
--
-- Production note: with many maps (all of Indonesia in tiles, a new map every year), the same
-- tables would be created once with a map_id column and PARTITION BY LIST (map_id): an upload
-- adds a partition, a delete drops one.


-- @shared
-- The list of maps (one row per map). Created by "maps.py init"; uploads make sure it exists.
CREATE TABLE IF NOT EXISTS public.maps (
    name         TEXT PRIMARY KEY,         -- what you called the map, e.g. 'jambi-v1'
    schema_name  TEXT NOT NULL UNIQUE,     -- the schema holding its tables, e.g. 'map_jambi_v1'
    source_file  TEXT,                     -- the TIFF it was built from
    fingerprint  TEXT,                     -- identifies the exact map (common.Fingerprint)
    width        INT,                      -- pixels
    height       INT,
    added_at     TIMESTAMPTZ DEFAULT now()
);


-- @postgis
-- PostGIS, in public so that deleting a map's schema can never remove it. Only the outlines use it.
CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public;


-- @map
-- Every map has these, whichever approaches are built: its address system, and the farms and
-- the 5-colour checks' verdicts saved for it.
CREATE TABLE IF NOT EXISTS {schema}.grid_info (
    map_version    TEXT PRIMARY KEY,
    x0             DOUBLE PRECISION NOT NULL,  -- x of the map's top-left corner
    y0             DOUBLE PRECISION NOT NULL,  -- y of the map's top-left corner
    pixel_w        DOUBLE PRECISION NOT NULL,  -- pixel width  (map units)
    pixel_h        DOUBLE PRECISION NOT NULL,  -- pixel height (map units)
    width          INT NOT NULL,               -- pixels
    height         INT NOT NULL,
    srid           INT NOT NULL,               -- EPSG code, e.g. 4326
    geographic     BOOLEAN NOT NULL,           -- true if the units are degrees
    default_class  SMALLINT,                   -- the colour NOT stored (the map's most common)
    fingerprint    TEXT,                       -- identifies the exact map that was uploaded
    source_file    TEXT,
    built_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.plots (
    plot_id      TEXT PRIMARY KEY,
    supplier_id  TEXT,
    declared_ha  REAL,
    geojson      JSONB NOT NULL      -- the farm exactly as the supplier sent it
);

CREATE TABLE IF NOT EXISTS {schema}.plot_results (
    plot_id        TEXT NOT NULL REFERENCES {schema}.plots(plot_id) ON DELETE CASCADE,
    map_version    TEXT NOT NULL,
    method         TEXT NOT NULL,      -- 'polygons', 'stencils', 'tiff-pixel', 'tiff-exact'
    verdict        TEXT NOT NULL,      -- its outcome (compliant / non-compliant / needs review) is
                                       -- common.outcome_of(verdict), so it isn't stored
    colours        TEXT,               -- every colour the farm touches, e.g. 'land, forest'
    point_colour   TEXT,               -- point farms: the colour under the point
    forest_ha      DOUBLE PRECISION,
    land_ha        DOUBLE PRECISION,
    deforested_ha  DOUBLE PRECISION,
    review_ha      DOUBLE PRECISION,
    water_ha       DOUBLE PRECISION,
    no_data_ha     DOUBLE PRECISION,
    note           TEXT,
    checked_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (plot_id, map_version, method)
);


-- @map_upgrade
-- Brings tables made by earlier versions of these programs up to date (does nothing on new ones).
ALTER TABLE {schema}.grid_info ADD COLUMN IF NOT EXISTS default_class SMALLINT;
ALTER TABLE {schema}.grid_info ADD COLUMN IF NOT EXISTS fingerprint TEXT;
ALTER TABLE {schema}.plot_results DROP COLUMN IF EXISTS answered_by_label;
ALTER TABLE {schema}.plot_results ADD COLUMN IF NOT EXISTS colours TEXT;
ALTER TABLE {schema}.plot_results ADD COLUMN IF NOT EXISTS point_colour TEXT;
ALTER TABLE {schema}.plot_results ADD COLUMN IF NOT EXISTS forest_ha DOUBLE PRECISION;
ALTER TABLE {schema}.plot_results ADD COLUMN IF NOT EXISTS land_ha DOUBLE PRECISION;


-- @stencils
-- The stencils (stencil_approach.py): the 5 colours in 256 x 256-pixel pages. Answers the two key
-- questions. Pages that are all the map's most common colour get no row.
CREATE TABLE {schema}.stencil_pages (
    page_col  INT NOT NULL,     -- which 256 x 256 page (column)
    page_row  INT NOT NULL,     -- which 256 x 256 page (row)
    colours   BYTEA NOT NULL    -- the page's colour codes 0-5, one byte per pixel, lzma-compressed
                                -- (raw LZMA2, stencil_approach.LZMA_FILTERS)
);
-- already compressed: keep the pages inside the table rows, not in a side table
ALTER TABLE {schema}.stencil_pages ALTER COLUMN colours SET STORAGE MAIN;


-- @stencils_keys
-- The primary key IS the index: a farm's location says exactly which pages to fetch.
ALTER TABLE {schema}.stencil_pages ADD PRIMARY KEY (page_row, page_col);


-- @outlines
-- The outlines (polygon_approach.py), optional (maps.py upload --no-outlines leaves them out):
-- every colour but the most common traced into shapes, plus labels for squares of one colour.
CREATE TABLE {schema}.class_polys (
    class_id  SMALLINT NOT NULL,          -- 0 land, 1 water, 2 forest, 3 deforestation,
                                          -- 4 needs review, 5 no data
    geom      geometry(Polygon, {srid}) NOT NULL
);

CREATE TABLE {schema}.square_labels (
    page_col  INT NOT NULL,
    page_row  INT NOT NULL,
    labels    BYTEA NOT NULL              -- colour of each 16 x 16 square (255 = mixed), zipped
);


-- @outlines_keys
CREATE INDEX class_polys_geom_idx ON {schema}.class_polys USING GIST (geom);
ALTER TABLE {schema}.square_labels ADD PRIMARY KEY (page_row, page_col);


-- @evidence
-- The evidence (evidence_approach.py), for a map uploaded from a results folder: evidence.tif,
-- every EUDR decision fact per pixel in 32 bits, in 256 x 256-pixel pages.
CREATE TABLE {schema}.evidence_info (
    producer       TEXT,            -- the eudr_layers.py version that built evidence.tif
    bit_layout     JSONB NOT NULL,  -- where each fact sits in the 32 bits: [{field, bits, meaning}]
    rule_params    JSONB NOT NULL,  -- the rule's settings (hansen_only, settled_until, ...)
    default_value  BIGINT NOT NULL, -- the map's most common value: pages all of it have no row
    fingerprint    TEXT NOT NULL,   -- identifies the exact evidence.tif
    source_file    TEXT,            -- the evidence.tif (its path inside layers/)
    tags           JSONB            -- everything evidence.tif says about itself (rule, sources, ...)
);

CREATE TABLE {schema}.evidence_pages (
    page_col  INT NOT NULL,         -- which 256 x 256 page (column)
    page_row  INT NOT NULL,         -- which 256 x 256 page (row)
    evidence  BYTEA NOT NULL        -- the page's distinct values + one index per pixel, zipped
);
ALTER TABLE {schema}.evidence_pages ALTER COLUMN evidence SET STORAGE MAIN;


-- @evidence_keys
ALTER TABLE {schema}.evidence_pages ADD PRIMARY KEY (page_row, page_col);


-- @protected
-- Protected areas (protected_areas.py), optional: the polygons of the wdpa*.geojson files that
-- came with the map (the World Database on Protected Areas), cut to the map's extent. The app
-- draws them over the map as "protected areas". Needs PostGIS.
CREATE TABLE {schema}.protected_areas (
    wdpa_id      TEXT,                    -- WDPAID (or WDPA_PID), if the file has it
    name         TEXT,                    -- NAME, e.g. 'Berbak'
    designation  TEXT,                    -- DESIG_ENG, e.g. 'National Park'
    iucn_cat     TEXT,                    -- IUCN_CAT, e.g. 'II'
    properties   JSONB NOT NULL,          -- every property of the feature, as the file had it
    geom         geometry(MultiPolygon, {srid}) NOT NULL   -- in the map's coordinate system
);


-- @protected_keys
CREATE INDEX protected_areas_geom_idx ON {schema}.protected_areas USING GIST (geom);


-- @evidence_results
-- The evidence check's verdicts, made the first time plots are checked and saved.
CREATE TABLE IF NOT EXISTS {schema}.evidence_results (
    plot_id     TEXT PRIMARY KEY REFERENCES {schema}.plots(plot_id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL,      -- non-compliant, probable deforestation, needs review,
                                    -- deforestation-free or not assessed (eudr_layers.py)
    reason      TEXT,
    facts       JSONB NOT NULL,     -- every figure (hectares by kind of clearing, the years, ...)
                                    -- and the outcome: compliant / non-compliant / needs review
    checked_at  TIMESTAMPTZ DEFAULT now()
);
