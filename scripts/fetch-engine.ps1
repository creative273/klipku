# Klipku - siapkan folder engine\ untuk dibundel ke installer.
# Dijalankan di mesin build (GitHub Actions windows-latest), BUKAN di laptop user.
# Hasil: engine\python (embeddable + paket), engine\backend, engine\bin, engine\models
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$PY_VER   = '3.11.9'
$FF_VER   = '7.1'
$root     = Split-Path -Parent $PSScriptRoot
$engine   = Join-Path $root 'engine'
$dl       = Join-Path $root '.download'

Remove-Item $engine -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $engine, $dl, "$engine\bin", "$engine\models" | Out-Null

function Get-File($url, $out) {
  if (-not (Test-Path $out)) {
    Write-Host "download $url"
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
  }
}

# 1. Python embeddable (interpreter resmi python.org, bertanda tangan PSF)
$pyZip = "$dl\python-$PY_VER-embed-amd64.zip"
Get-File "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip" $pyZip
Expand-Archive $pyZip "$engine\python" -Force
# aktifkan site-packages + folder backend di sys.path
$pth = Get-ChildItem "$engine\python\python*._pth" | Select-Object -First 1
@(
  "$($pth.BaseName).zip"
  '.'
  'Lib\site-packages'
  '..\backend'
  'import site'
) | Set-Content $pth.FullName -Encoding ascii
if (-not (Test-Path "$engine\python\$($pth.BaseName).zip")) { throw "zip stdlib tidak ditemukan untuk $($pth.Name)" }

# 2. pip + paket (wheel resmi PyPI, dipasang ke engine\python\Lib\site-packages)
Get-File 'https://bootstrap.pypa.io/get-pip.py' "$dl\get-pip.py"
& "$engine\python\python.exe" "$dl\get-pip.py" --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'get-pip gagal' }
& "$engine\python\python.exe" -m pip install --no-warn-script-location --only-binary=:all: -r "$root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'pip install gagal' }
# buang pip/setuptools/wheel + folder Scripts: membawa exe launcher (t64.exe, cli.exe, dll)
# yang sering dicurigai antivirus. Update yt-dlp di app nanti memakai unzip wheel, bukan pip.
& "$engine\python\python.exe" -m pip uninstall -y pip setuptools wheel
Remove-Item "$engine\python\Scripts" -Recurse -Force -ErrorAction SilentlyContinue

# 3. ffmpeg + ffprobe (gyan.dev essentials, ada libass)
$ffZip = "$dl\ffmpeg-$FF_VER-essentials_build.zip"
Get-File "https://github.com/GyanD/codexffmpeg/releases/download/$FF_VER/ffmpeg-$FF_VER-essentials_build.zip" $ffZip
Expand-Archive $ffZip "$dl\ffmpeg" -Force
$ffBin = Get-ChildItem "$dl\ffmpeg" -Recurse -Filter ffmpeg.exe | Select-Object -First 1
Copy-Item $ffBin.FullName, (Join-Path $ffBin.DirectoryName 'ffprobe.exe') "$engine\bin\"
$filters = & "$engine\bin\ffmpeg.exe" -hide_banner -filters 2>&1 | Out-String
if ($filters -notmatch ' ass ' -or $filters -notmatch ' vstack ') { throw 'ffmpeg tidak punya filter ass/vstack' }

# 4. deno (runtime JS yang dibutuhkan yt-dlp untuk YouTube)
Get-File 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' "$dl\deno.zip"
Expand-Archive "$dl\deno.zip" "$engine\bin" -Force

# 5. model deteksi wajah YuNet (OpenCV Zoo, Apache-2.0)
$yunet = "$engine\models\face_detection_yunet_2023mar.onnx"
Get-File 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx' $yunet
if ((Get-Item $yunet).Length -lt 100000) { throw 'model YuNet tidak valid (kemungkinan file pointer LFS)' }

# 6. kode backend
New-Item -ItemType Directory -Force "$engine\backend" | Out-Null
Copy-Item "$root\backend\*.py", "$root\backend\*.sql" "$engine\backend\"

# 7. uji cepat: import semua paket dengan python yang akan dibundel
& "$engine\python\python.exe" -c "import fastapi, uvicorn, yt_dlp, cv2, keyring, numpy; print('imports ok', cv2.__version__, yt_dlp.version.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'import test gagal' }

# 8. cari executable di engine selain yang memang diharapkan
$expected = @('python.exe','pythonw.exe','ffmpeg.exe','ffprobe.exe','deno.exe')
$extra = Get-ChildItem $engine -Recurse -Filter *.exe | Where-Object { $expected -notcontains $_.Name }
if ($extra) { throw ("exe tambahan di bundle: " + ($extra.FullName -join ', ')) }

$size = (Get-ChildItem $engine -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("engine siap: {0:N0} MB" -f $size)
