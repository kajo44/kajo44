@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title APRJ Next - Instalasi Lingkungan Python

echo ============================================================
echo  APRJ Next - Instalasi Dependensi
echo ============================================================
echo.
echo Target: Windows 10/11 64-bit, Python 3.11 64-bit.
echo PyTorch CPU akan dipasang; unduhan dapat berukuran besar.
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python Launcher "py" tidak ditemukan.
  echo Instal Python 3.11 64-bit dari python.org.
  echo Saat instalasi, aktifkan Python Launcher dan Tcl/Tk.
  pause
  exit /b 1
)

py -3.11 -c "import struct,sys; print('Python:',sys.version); print('Arsitektur:',struct.calcsize('P')*8,'bit')" 
if errorlevel 1 goto :fail

py -3.11 -c "import struct,sys; sys.exit(0 if struct.calcsize('P')*8 == 64 else 1)"
if errorlevel 1 (
  echo ERROR: APRJ Next memerlukan Python 64-bit.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Membuat lingkungan virtual .venv...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :fail
)

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

echo.
echo [1/4] Memperbarui pip...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo.
echo [2/4] Memasang semua dependensi dari requirements.txt...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [3/4] Memverifikasi PyTorch dan dependensi...
"%PYTHON_EXE%" -c "import torch; import PIL; import openpyxl; import psycopg2; import PyInstaller; print('PyTorch:',torch.__version__); print('CUDA tersedia:',torch.cuda.is_available()); print('Pillow:',PIL.__version__); print('openpyxl:',openpyxl.__version__); print('psycopg2:',psycopg2.__version__); print('PyInstaller:',PyInstaller.__version__)"
if errorlevel 1 goto :fail

echo.
echo [4/4] Memeriksa sintaks dan menguji arsitektur AI...
"%PYTHON_EXE%" -m py_compile aprj_main.py aprj_ui.py aprj_core.py aprj_database.py aprj_ai_model.py aprj_pkrms.py aprj_training_engine.py
if errorlevel 1 goto :fail

"%PYTHON_EXE%" -c "import torch; from aprj_ai_model import SurveyMultiTaskNet; m=SurveyMultiTaskNet({'uji':['kelas_a','kelas_b']}); y=m(torch.zeros(1,3,224,224)); assert 'classes' in y and 'widths' in y; print('Tes forward model: BERHASIL')"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo INSTALASI BERHASIL
echo.
echo Untuk menjalankan aplikasi:
echo   .venv\Scripts\python.exe aprj_main.py
echo.
echo Untuk membangun aplikasi portable:
echo   build_portable.bat
echo.
echo Catatan: ini memverifikasi instalasi dan forward-pass model,
echo bukan bukti akurasi model atau training pada dataset nyata.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo INSTALASI / PEMERIKSAAN GAGAL.
echo Baca pesan error di atas. Jika perlu, jalankan ulang setelah
echo memperbaiki koneksi internet atau versi Python.
pause
exit /b 1
