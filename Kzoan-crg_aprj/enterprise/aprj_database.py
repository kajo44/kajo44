# -*- coding: utf-8 -*-
"""Integrasi PostgreSQL untuk sinkronisasi catatan survei jalan.

SQLite tetap menjadi penyimpanan lokal utama; PostgreSQL adalah target sinkronisasi.
"""
import json
import os


class DatabaseManager:
    def __init__(self, host="localhost", port=5432, database="aprj_db", user="postgres", password=""):
        self.config = {
            "host": host,
            "port": int(port),
            "database": database,
            "user": user,
            "password": password,
            "connect_timeout": 8,
        }

    def _connect(self):
        import psycopg2
        return psycopg2.connect(**self.config)

    def test_connection(self):
        """Uji koneksi tanpa mengubah data."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return True, "Koneksi PostgreSQL berhasil."
        except Exception as error:
            return False, str(error)

    def sync_to_postgres(self, survey_records):
        """Sinkronkan catatan, memperbarui pasangan foto+folder bila sudah ada."""
        conn = None
        try:
            conn = self._connect()
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS survey_results (
                            id BIGSERIAL PRIMARY KEY,
                            photo_name TEXT NOT NULL,
                            folder_source TEXT NOT NULL DEFAULT '',
                            survey_data JSONB NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    inserted = 0
                    updated = 0
                    for record_key, raw_data in survey_records.items():
                        data = raw_data if isinstance(raw_data, dict) else {}
                        key_text = str(record_key)
                        photo_name = str(
                            data.get("filename")
                            or os.path.basename(key_text)
                            or key_text
                        )
                        folder_source = str(
                            data.get("folder")
                            or os.path.dirname(key_text)
                            or ""
                        )
                        payload = json.dumps(data, ensure_ascii=False, default=str)
                        cursor.execute("""
                            SELECT id FROM survey_results
                            WHERE photo_name = %s AND folder_source = %s
                            ORDER BY id LIMIT 1
                        """, (photo_name, folder_source))
                        existing = cursor.fetchone()
                        if existing:
                            cursor.execute("""
                                UPDATE survey_results
                                SET survey_data = %s::jsonb,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                            """, (payload, existing[0]))
                            updated += 1
                        else:
                            cursor.execute("""
                                INSERT INTO survey_results
                                    (photo_name, folder_source, survey_data)
                                VALUES (%s, %s, %s::jsonb)
                            """, (photo_name, folder_source, payload))
                            inserted += 1
            return True, (
                f"Sinkronisasi selesai. Catatan baru: {inserted}; "
                f"catatan diperbarui: {updated}."
            )
        except Exception as error:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False, str(error)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
