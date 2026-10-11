# -*- coding: utf-8 -*-
"""Modul Geospasial Jalan: indeks GPS foto, pencocokan ruas, STA, dan ekspor.

Implementasi menggunakan pustaka standar Python. ExifTool dibaca dari
<direktori-aplikasi>/bin/exiftool.exe agar instalasi portabel tidak bergantung
pada ExifTool global. Input geometri yang didukung: GeoJSON dan KML (LineString).
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import subprocess
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".bmp", ".webp"}
GRID_DEGREES = 0.005
EARTH_RADIUS_M = 6_371_008.8


def _cell(value: float) -> int:
    return math.floor(value / GRID_DEGREES)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(min(1.0, math.sqrt(a)))


def _interpolate(a, b, fraction):
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def _line_length_m(coords):
    return sum(_distance_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords, coords[1:]))


def _station_points(coords, interval_m):
    """Yield (STA meter, latitude, longitude) along a lon/lat polyline."""
    if len(coords) < 2:
        return
    segments = []
    cumulative = 0.0
    for a, b in zip(coords, coords[1:]):
        length = _distance_m(a[1], a[0], b[1], b[0])
        segments.append((a, b, length, cumulative))
        cumulative += length
    if cumulative <= 0:
        return
    distances = list(range(0, int(cumulative), interval_m))
    if not distances or distances[-1] != int(cumulative):
        distances.append(int(round(cumulative)))
    for sta in distances:
        target = min(float(sta), cumulative)
        for a, b, length, offset in segments:
            if target <= offset + length or (a, b, length, offset) == segments[-1]:
                fraction = 0.0 if length <= 0 else max(0.0, min(1.0, (target - offset) / length))
                lon, lat = _interpolate((a[0], a[1]), (b[0], b[1]), fraction)
                yield sta, lat, lon
                break


def _geojson_lines(data):
    features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    for index, feature in enumerate(features, start=1):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") if feature.get("type") == "Feature" else feature
        if not isinstance(geometry, dict):
            continue
        kind, coords = geometry.get("type"), geometry.get("coordinates")
        lines = [coords] if kind == "LineString" else coords if kind == "MultiLineString" else []
        for part, line in enumerate(lines or [], start=1):
            normalized = []
            for point in line:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    lon, lat = float(point[0]), float(point[1])
                    if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
                        normalized.append((lon, lat))
            if len(normalized) >= 2:
                road_id = str(props.get("road_code") or props.get("segment_id") or props.get("id") or f"ruas-{index}")
                name = str(props.get("road_name") or props.get("name") or road_id)
                yield f"{road_id}-{part}" if len(lines) > 1 else road_id, name, normalized


def _kml_lines(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for index, element in enumerate(root.iter(), start=1):
        if element.tag.rsplit("}", 1)[-1] != "LineString":
            continue
        coord_el = next((child for child in element.iter() if child.tag.rsplit("}", 1)[-1] == "coordinates"), None)
        if coord_el is None or not coord_el.text:
            continue
        coords = []
        for item in coord_el.text.replace("\n", " ").split():
            values = item.split(",")
            if len(values) >= 2:
                try:
                    lon, lat = float(values[0]), float(values[1])
                except ValueError:
                    continue
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    coords.append((lon, lat))
        if len(coords) >= 2:
            parent_name = ""
            # Find the nearest Placemark name without requiring a KML namespace.
            for placemark in root.iter():
                if placemark.tag.rsplit("}", 1)[-1] != "Placemark" or element not in list(placemark.iter()):
                    continue
                name_el = next((child for child in placemark.iter() if child.tag.rsplit("}", 1)[-1] == "name"), None)
                parent_name = (name_el.text or "").strip() if name_el is not None else ""
                break
            name = parent_name or f"Ruas-{index}"
            yield name, name, coords


def load_road_segments(path):
    """Return road geometries from KML or GeoJSON; reject unsupported formats."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".json", ".geojson"}:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        segments = list(_geojson_lines(data))
    elif suffix == ".kml":
        segments = list(_kml_lines(path))
    else:
        raise ValueError("Format geometri belum didukung. Gunakan file .kml, .geojson, atau .json.")
    if not segments:
        raise ValueError("Tidak ditemukan geometri LineString yang valid pada file ruas.")
    return segments


class GeospatialStore:
    """Database terpisah; tidak mengubah database penilaian survei yang sudah ada."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS photo_index (
                    path TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    taken_at TEXT,
                    cell_y INTEGER,
                    cell_x INTEGER,
                    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_photo_grid ON photo_index(cell_y, cell_x);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    road_file TEXT NOT NULL,
                    photo_root TEXT NOT NULL,
                    interval_m INTEGER NOT NULL,
                    radius_m REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS matches (
                    job_id TEXT NOT NULL,
                    road_id TEXT NOT NULL,
                    road_name TEXT NOT NULL,
                    sta_m INTEGER NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    photo_path TEXT,
                    photo_latitude REAL,
                    photo_longitude REAL,
                    distance_m REAL,
                    validation_status TEXT NOT NULL,
                    PRIMARY KEY(job_id, road_id, sta_m)
                );
                CREATE INDEX IF NOT EXISTS idx_matches_photo ON matches(photo_path);
            """)

    def connect(self):
        return sqlite3.connect(str(self.db_path), timeout=60)

    def create_job(self, road_file, photo_root, interval_m, radius_m):
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO jobs(job_id, road_file, photo_root, interval_m, radius_m, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, str(road_file), str(photo_root), interval_m, radius_m, "queued", now, now),
            )
        return job_id

    def set_job_status(self, job_id, status, error=""):
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                (status, str(error)[:4000], datetime.now().isoformat(timespec="seconds"), job_id),
            )

    def photo_count(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM photo_index WHERE latitude IS NOT NULL AND longitude IS NOT NULL").fetchone()[0]

    def existing_fingerprints(self, paths):
        """Ambil fingerprint satu batch agar indeks inkremental tidak membaca ulang metadata."""
        resolved = [str(path.resolve()) for path in paths]
        if not resolved:
            return {}
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in resolved)
            rows = conn.execute(
                f"SELECT path, size_bytes, mtime_ns FROM photo_index WHERE path IN ({placeholders})",
                resolved,
            ).fetchall()
        return {path: (size, mtime) for path, size, mtime in rows}

    def latest_job_id(self):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT job_id FROM jobs WHERE status='completed' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def index_photo_batch(self, paths, metadata):
        rows = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            item = metadata.get(str(path.resolve()), {})
            lat, lon = item.get("GPSLatitude"), item.get("GPSLongitude")
            try:
                lat, lon = float(lat), float(lon)
                if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    lat = lon = None
            except (TypeError, ValueError):
                lat = lon = None
            taken = item.get("DateTimeOriginal") or item.get("CreateDate") or ""
            rows.append((
                str(path.resolve()), stat.st_size, stat.st_mtime_ns, lat, lon,
                str(taken), _cell(lat) if lat is not None else None, _cell(lon) if lon is not None else None,
            ))
        with self.connect() as conn:
            conn.executemany("""
                INSERT INTO photo_index(path,size_bytes,mtime_ns,latitude,longitude,taken_at,cell_y,cell_x)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                  latitude=excluded.latitude, longitude=excluded.longitude,
                  taken_at=excluded.taken_at, cell_y=excluded.cell_y, cell_x=excluded.cell_x,
                  indexed_at=CURRENT_TIMESTAMP
                WHERE photo_index.size_bytes != excluded.size_bytes
                   OR photo_index.mtime_ns != excluded.mtime_ns
                   OR photo_index.latitude IS NOT excluded.latitude
                   OR photo_index.longitude IS NOT excluded.longitude
            """, rows)

    def nearby_photos(self, lat, lon, radius_m):
        lat_delta = radius_m / 110_574.0
        lon_delta = radius_m / max(1.0, 111_320.0 * abs(math.cos(math.radians(lat))))
        min_y, max_y = _cell(lat - lat_delta), _cell(lat + lat_delta)
        min_x, max_x = _cell(max(-180, lon - lon_delta)), _cell(min(180, lon + lon_delta))
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT path, latitude, longitude, taken_at FROM photo_index
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                  AND cell_y BETWEEN ? AND ? AND cell_x BETWEEN ? AND ?
            """, (min_y, max_y, min_x, max_x)).fetchall()
        matches = []
        for path, photo_lat, photo_lon, taken in rows:
            distance = _distance_m(lat, lon, photo_lat, photo_lon)
            if distance <= radius_m:
                matches.append((distance, path, photo_lat, photo_lon, taken))
        matches.sort(key=lambda row: row[0])
        return matches

    def save_matches(self, job_id, records):
        with self.connect() as conn:
            conn.execute("DELETE FROM matches WHERE job_id=?", (job_id,))
            conn.executemany("""
                INSERT INTO matches(job_id,road_id,road_name,sta_m,latitude,longitude,
                  photo_path,photo_latitude,photo_longitude,distance_m,validation_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, records)

    def get_matches(self, job_id):
        with self.connect() as conn:
            return conn.execute("""
                SELECT road_id, road_name, sta_m, latitude, longitude, photo_path,
                       photo_latitude, photo_longitude, distance_m, validation_status
                FROM matches WHERE job_id=? ORDER BY road_id, sta_m
            """, (job_id,)).fetchall()

    def get_match_for_photo(self, path):
        with self.connect() as conn:
            return conn.execute("""
                SELECT road_id, road_name, sta_m, distance_m, validation_status
                FROM matches WHERE photo_path=? ORDER BY sta_m
            """, (str(Path(path).resolve()),)).fetchall()

    def export_csv(self, job_id, destination):
        rows = self.get_matches(job_id)
        with open(destination, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["road_id", "road_name", "sta_m", "sta_km", "sta_latitude", "sta_longitude",
                             "photo_path", "photo_latitude", "photo_longitude", "distance_m", "validation_status"])
            for road_id, name, sta, lat, lon, photo, plat, plon, distance, status in rows:
                writer.writerow([road_id, name, sta, sta / 1000.0, lat, lon, photo or "", plat, plon, distance, status])
        return len(rows)


class GeospatialEngine:
    def __init__(self, app_root):
        self.app_root = Path(app_root)
        self.store = GeospatialStore(self.app_root / "data" / "geospatial.sqlite3")
        self.cache_root = self.app_root / "geospatial" / "cache"

    def exiftool_path(self):
        candidates = [
            self.app_root / "bin" / "exiftool.exe",
            self.app_root / "bin" / "exiftool",
            self.app_root / "bin" / "exiftool_files" / "exiftool.exe",
            # PyInstaller 6 default onedir layout, retained as a defensive fallback.
            self.app_root / "_internal" / "bin" / "exiftool.exe",
            self.app_root / "_internal" / "bin" / "exiftool",
            self.app_root / "_internal" / "bin" / "exiftool_files" / "exiftool.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(
            "ExifTool tidak ditemukan. Tempatkan exiftool.exe di folder aplikasi bin\\exiftool.exe."
        )

    def _run_exiftool(self, executable, paths):
        # Batch terbatas menjaga command line Windows tetap di bawah batas panjangnya.
        command = [str(executable), "-json", "-n", "-GPSLatitude", "-GPSLongitude",
                   "-DateTimeOriginal", "-CreateDate", "--"] + [str(path) for path in paths]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=180, check=False)
        if completed.returncode not in (0, 1):
            raise RuntimeError(f"ExifTool gagal (kode {completed.returncode}): {completed.stderr[:1000]}")
        try:
            items = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Respons ExifTool bukan JSON valid: {error}") from error
        result = {}
        for item in items if isinstance(items, list) else []:
            source = item.get("SourceFile")
            if source:
                try:
                    result[str(Path(source).resolve())] = item
                except OSError:
                    result[str(Path(source).absolute())] = item
        return result

    def index_photos(self, photo_root, progress=None, cancel_event=None, batch_size=200):
        photo_root = Path(photo_root)
        executable = self.exiftool_path()
        if not photo_root.is_dir():
            raise NotADirectoryError(f"Folder foto tidak ditemukan: {photo_root}")
        total = indexed = 0
        batch = []
        # Iterator ini tidak membuat daftar jutaan path sekaligus.
        def process_batch(paths):
            nonlocal total, indexed
            fingerprints = self.store.existing_fingerprints(paths)
            changed = []
            for path in paths:
                try:
                    stat = path.stat()
                    resolved = str(path.resolve())
                except OSError:
                    continue
                if fingerprints.get(resolved) != (stat.st_size, stat.st_mtime_ns):
                    changed.append(path)
            metadata = self._run_exiftool(executable, changed) if changed else {}
            self.store.index_photo_batch(changed, metadata)
            total += len(paths)
            indexed += sum(1 for item in metadata.values() if item.get("GPSLatitude") is not None and item.get("GPSLongitude") is not None)
            if progress:
                progress("index", total, indexed, f"Memindai {total} foto; {len(changed)} foto baru/berubah pada batch ini.")
        for path in photo_root.rglob("*"):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Pekerjaan dibatalkan pengguna.")
            try:
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
            except OSError:
                continue
            batch.append(path)
            if len(batch) >= batch_size:
                process_batch(batch)
                batch = []
        if batch:
            process_batch(batch)
        return total, indexed

    def run_job(self, job_id, photo_root, road_file, interval_m=100, radius_m=100,
                progress=None, cancel_event=None):
        if not isinstance(interval_m, int) or interval_m < 1:
            raise ValueError("Interval STA harus bilangan bulat minimal 1 meter.")
        if not math.isfinite(float(radius_m)) or not 1 <= float(radius_m) <= 5000:
            raise ValueError("Radius pencarian harus antara 1 dan 5000 meter.")
        self.store.set_job_status(job_id, "indexing")
        try:
            self.index_photos(photo_root, progress=progress, cancel_event=cancel_event)
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Pekerjaan dibatalkan pengguna.")
            roads = load_road_segments(road_file)
            records = []
            used_photos = set()
            station_count = 0
            for road_id, road_name, coords in roads:
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Pekerjaan dibatalkan pengguna.")
                for sta, lat, lon in _station_points(coords, interval_m):
                    station_count += 1
                    candidates = self.store.nearby_photos(lat, lon, float(radius_m))
                    chosen = next((item for item in candidates if item[1] not in used_photos), None)
                    if chosen:
                        distance, photo, plat, plon, taken = chosen
                        used_photos.add(photo)
                        status = "Perlu verifikasi"
                    else:
                        distance = photo = plat = plon = None
                        status = "Tidak ada kandidat dalam radius"
                    records.append((job_id, road_id, road_name, int(sta), lat, lon,
                                    photo, plat, plon, distance, status))
                    if progress and station_count % 25 == 0:
                        progress("match", station_count, len(records), f"Mencocokkan STA: {station_count}.")
            self.store.save_matches(job_id, records)
            self.store.set_job_status(job_id, "completed")
            if progress:
                progress("done", station_count, len(records), f"Selesai: {station_count} STA, {sum(1 for row in records if row[6])} foto terpilih.")
            return records
        except InterruptedError as error:
            self.store.set_job_status(job_id, "cancelled", str(error))
            raise
        except Exception as error:
            self.store.set_job_status(job_id, "failed", str(error))
            raise
