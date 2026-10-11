# APRJ Next — Panduan Penggunaan dan Pemeliharaan

Dokumen ini adalah panduan kerja untuk aplikasi di folder `enterprise`. Nama entry point resmi adalah `aprj_main.py`; jangan menjalankan file compatibility lama.

## 1. Ringkasan aplikasi

APRJ Next menggabungkan empat area kerja:

- **Survei Jalan** — memuat foto, mengisi/meninjau atribut survei, menyimpan catatan lokal, dan mengekspor Excel.
- **Rekayasa AI** — mengimpor dataset berlabel, melatih model lokal, memeriksa checkpoint, dan menjalankan prediksi untuk ditinjau petugas.
- **PKRMS** — alat bantu audit/ekspor serta panel **Modul Geospasial Jalan** untuk indeks GPS, pencocokan foto ke STA, dan ekspor hasil.
- **Integrasi Online** — fitur terkait Google Colab dan sinkronisasi PostgreSQL jika server serta kredensial tersedia.

SQLite lokal tetap menjadi penyimpanan lokal. PostgreSQL merupakan integrasi online terpisah. Prediksi AI dan kandidat geospasial harus diverifikasi manusia sebelum dijadikan data resmi.

## 2. Persyaratan sistem

### Untuk membangun aplikasi di Windows

- Windows 10/11 64-bit.
- Python 3.11 64-bit dengan Python Launcher (`py`) dan Tcl/Tk.
- Koneksi internet saat menginstal dependency Python.
- Ruang disk yang memadai; PyTorch CPU membuat paket hasil build cukup besar.
- ExifTool Windows tersedia di `enterprise\\bin\\exiftool.exe`. Jika distribusinya memerlukan `exiftool_files`, sertakan folder tersebut.

### Untuk menjalankan hasil portable

- Salin **seluruh** folder `dist\\APRJ_Next`, bukan hanya EXE.
- Pastikan folder aplikasi dapat ditulisi oleh pengguna karena database, log, model, dan hasil disimpan di folder aplikasi.
- Modul Geospasial Jalan memerlukan ExifTool di folder `bin` pada lokasi aplikasi yang sesuai.
- PostgreSQL hanya diperlukan untuk fungsi online; survei lokal tidak mensyaratkan server PostgreSQL.

Daftar dependency Python langsung berada di `requirements.txt`; dependency untuk proses build berada di `requirements-build.txt`.

## 3. Membangun aplikasi

1. Unduh/checkout repository dan buka folder `enterprise`.
2. Pastikan `bin\\exiftool.exe` tersedia. Script akan mencoba menyalinnya dari `C:\\Tools\\ExifTool` bila ditemukan.
3. Jalankan `build_portable.bat`.
4. Jika build selesai, buka `dist\\APRJ_Next\\APRJ_Next.exe`.
5. Uji aplikasi terlebih dahulu di komputer pembangun, lalu salin seluruh folder `dist\\APRJ_Next` ke komputer lain dan lakukan smoke test kembali.

Build script melakukan pemeriksaan sintaks dan pemaketan PyInstaller. Itu **bukan** pengganti pengujian GUI, uji portable di komputer lain, atau validasi akurasi.

## 4. Alur penggunaan utama

### A. Survei Jalan

1. Buka aplikasi dan pilih tab **Survei Jalan**.
2. Pilih/muat folder atau foto sesuai kontrol pada antarmuka.
3. Tinjau foto dan lengkapi data survei secara manual.
4. Simpan catatan dan uji pembukaan kembali sebelum memproses banyak data.
5. Gunakan ekspor Excel untuk membuat salinan kerja atau bahan impor AI.

Jangan menganggap data tersimpan sebelum operasi simpan berhasil. Pertahankan salinan cadangan database dan file sumber.

### B. Rekayasa AI

1. Siapkan file Excel hasil survei dengan sheet dan kolom yang dikenali aplikasi; path foto sumber harus dapat diakses ketika impor.
2. Gunakan **Pilih Export Excel**, periksa pratinjau, lalu **Konfirmasi Impor**.
3. Periksa label dan pasangan foto. Jangan mengimpor label yang belum diverifikasi.
4. Pilih mode training yang tersedia, perangkat, epoch, dan batch size yang sesuai kemampuan komputer.
5. Mulai training dan tunggu status selesai; hindari menutup aplikasi saat proses berlangsung.
6. Periksa kesiapan checkpoint, lalu jalankan prediksi pada foto terpilih.
7. Verifikasi hasil prediksi dan terapkan secara manual. Prediksi bukan hasil survei resmi otomatis.

Model yang ada adalah CNN yang dilatih dari awal, bukan model pretrained. Kualitasnya bergantung pada mutu dan representativitas label. Estimasi lebar dari foto tidak boleh dianggap ukuran resmi tanpa kalibrasi dan verifikasi lapangan.

### C. PKRMS dan Modul Geospasial Jalan

1. Buka tab **PKRMS**.
2. Untuk pemetaan, pilih folder foto dan file ruas jalan berformat **KML** atau **GeoJSON** dengan geometri LineString/MultiLineString.
3. Atur interval STA dalam meter dan radius pencarian foto.
4. Jalankan pemrosesan; tunggu indeks GPS dan pencocokan selesai. Proses indeks dapat memerlukan waktu tergantung jumlah foto dan perangkat.
5. Tinjau status kandidat, jarak, nama ruas, dan STA. Status kandidat saat ini harus diperlakukan sebagai **perlu verifikasi**.
6. Buka foto kandidat ke Survei Jalan atau pemeriksaan AI untuk validasi visual.
7. Ekspor CSV atau gunakan ekspor PKRMS yang menyertakan sheet Pemetaan STA jika hasil tersedia.

Pemetaan saat ini menggunakan jarak ke titik STA hasil interpolasi dan radius pencarian. Sistem belum melakukan pencocokan arah/continuity secara komprehensif, belum mendukung KMZ langsung, dan belum menjamin foto benar-benar menggambarkan ruas yang dipilih. Periksa proyeksi/koordinat sumber dan hasil pada peta sebelum digunakan.

### D. Integrasi Online

1. Gunakan hanya jika server PostgreSQL dapat dijangkau.
2. Uji koneksi terlebih dahulu.
3. Jangan menulis password ke source code, dokumen publik, atau log.
4. Uji sinkronisasi dengan data contoh dan pastikan kegagalan jaringan tidak menghilangkan data lokal.

## 5. Data, lokasi, dan pembersihan

Lokasi utama saat aplikasi berjalan dari source:

- `data\\survey.sqlite3` — database survei.
- `data\\geospatial.sqlite3` — indeks dan hasil geospasial.
- `survey_ai\\dataset\\` — gambar/label dataset AI.
- `survey_ai\\models\\` — checkpoint model.
- `survey_ai\\training_runs\\` — ringkasan dan riwayat training.
- `logs\\` — log aplikasi.

Pada paket frozen, folder data ditentukan relatif terhadap lokasi executable. Sebelum memindahkan atau membersihkan folder aplikasi, tutup aplikasi dan buat cadangan database, dataset, checkpoint, serta hasil ekspor.

Fitur **CLEAN** ditujukan untuk cache yang dikenali; jangan menggunakannya sebagai pengganti pencadangan. Database, dataset, model, dan hasil pemetaan tidak boleh dihapus sebagai cache.

## 6. Troubleshooting

- **Build gagal karena ExifTool** — tempatkan file Windows `exiftool.exe` di `enterprise\\bin`; sertakan `exiftool_files` bila distribusi Anda memerlukannya.
- **Python tidak ditemukan** — instal Python 3.11 64-bit dan aktifkan Python Launcher serta Tcl/Tk.
- **Build gagal saat pip install** — periksa koneksi, ruang disk, dan versi Python; baca pesan error sebelum mengulang.
- **Aplikasi tidak dapat menyimpan** — pastikan folder aplikasi dapat ditulisi dan periksa `logs\\application_errors.log`.
- **AI tidak siap/prediksi gagal** — periksa checkpoint, label, jumlah kelas, dan kompatibilitas model.
- **Foto tidak punya koordinat** — pastikan metadata GPS tersedia dan dapat dibaca ExifTool.
- **Tidak ada kandidat foto** — cek radius, koordinat KML/GeoJSON, metadata GPS, dan apakah jalur foto yang dipilih benar.
- **Ekspor tidak cocok dengan template resmi** — cocokkan kolom terhadap template resmi yang berlaku; jangan menganggap format bantu otomatis memenuhi semua ketentuan.

## 7. Struktur modul inti

| File | Tanggung jawab |
|---|---|
| `aprj_main.py` | Entry point resmi |
| `aprj_ui.py` | Antarmuka dan alur aplikasi |
| `aprj_core.py` | Konstanta dan path aplikasi |
| `aprj_database.py` | Sinkronisasi PostgreSQL |
| `aprj_ai_model.py` | Definisi arsitektur model |
| `aprj_training_engine.py` | Training dan inferensi |
| `aprj_pkrms.py` | Utilitas audit/ekspor PKRMS |
| `aprj_geospasial.py` | Indeks GPS, geometri, STA, pencocokan, CSV |
| `requirements.txt` | Dependency runtime |
| `requirements-build.txt` | Dependency runtime + PyInstaller |
| `build_portable.bat` | Build portable Windows |
| `tests\\` | Tes regresi otomatis |

## 8. Status validasi

Tes unit dan pemeriksaan sintaks melalui GitHub Actions tidak membuktikan seluruh GUI, build Windows, training AI, PostgreSQL, skala jutaan foto, atau akurasi lapangan telah teruji. Sebelum penggunaan operasional, lakukan smoke test pada dataset kecil yang hasilnya dapat diverifikasi manual, lalu tingkatkan skala secara bertahap.
