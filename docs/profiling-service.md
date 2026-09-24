# Menjalankan dua service

`main.py` adalah API transaksi. `profiling.py` adalah aplikasi FastAPI terpisah.
POST `/transaction/add` menyimpan transaksi, lalu memanggil POST
`/profiling/check` melalui HTTP untuk pengeluaran. Check hanya membaca database;
tidak menyalin atau menambah transaksi. Keduanya harus memakai MONGODB_URI dan
MONGODB_DATABASE yang sama. Gunakan database pengujian untuk data contoh.

Profiling menyediakan GET `/profiling/summary?year=2026&month=9` dan POST
`/profiling/check` dengan JSON `{"year":2026,"month":9}`. Endpoint summary lama
di main tetap tersedia sebagai proxy. POST tambahan yang belum dijelaskan mentor
belum dibuat. Moving average tetap 3 bulan penuh sebelumnya, konfigurabel lewat
PROFILING_WINDOW_MONTHS pada service profiling.

## Lokal (dua terminal, konfigurasi database dalam .env)

```bash
python -m uvicorn profiling:app --host 0.0.0.0 --port 8011 --env-file .env
```

```bash
export PROFILING_SERVICE_URL=http://127.0.0.1:8011
python -m uvicorn main:app --host 0.0.0.0 --port 8010 --env-file .env
```

## Podman DevSpace

Gunakan nama baru supaya container pengujian sebelumnya tidak ditimpa.
File `.env` tidak dimasukkan ke image. Kedua container menggunakan database
yang ditentukan file tersebut; jangan impor ulang data yang sudah ada.
Konfigurasi pengujian yang dipakai: `MONGODB_DATABASE=bootcamp_profiling_clean_01`.
URI MongoDB tetap di `.env`/Secret; jangan upload kredensial ke GitHub.

```bash
podman_dev() { podman --root /tmp/podman-dev-storage --runroot /tmp/podman-dev-run --storage-driver vfs "$@"; }
podman_dev network create bootcamp-services
podman_dev build -t localhost/bootcamp-mlpt:1.3.0 -f Containerfile .
podman_dev build -t localhost/bootcamp-profiling:1.3.1 -f Containerfile.profiling .
podman_dev run -d --name profiling-v13 --network bootcamp-services --network-alias profiling -p 8021:8000 --env-file .env -e PROFILING_WINDOW_MONTHS=3 localhost/bootcamp-profiling:1.3.1
podman_dev run -d --name transactions-v13 --network bootcamp-services -p 8020:8000 --env-file .env -e PROFILING_SERVICE_URL=http://profiling:8000 localhost/bootcamp-mlpt:1.3.0
curl -sS http://localhost:8021/health/ready
curl -sS http://localhost:8020/health/ready
curl -sS 'http://localhost:8020/profiling/summary?year=2026&month=9'
```

Swagger transaksi: port 8020 `/docs`. Swagger profiling: port 8021 `/docs`.
Kegagalan profiling mengembalikan `profiling.status=unavailable` pada transaksi
yang sudah berhasil tersimpan. Jangan ulangi POST transaksi untuk mencoba ulang
profiling; gunakan GET summary. Readiness transaksi hanya memeriksa database,
bukan ketersediaan profiling.

## OpenShift

Push image transaksi sebagai `image-name:1.3.0`, dan image profiling sebagai
`profiling:1.3.1` ke registry namespace sendiri. Manifest memakai
`nabilahsafa-dev`; sesuaikan bila namespace berbeda. Setelah kedua image tersedia,
Secret transaction-secret dan ConfigMap transaction-config harus tersedia.

```bash
oc apply -f manifest/configmap.yaml
oc apply -f manifest/profiling.yaml
oc apply -f manifest/deployment.yaml
oc rollout status deployment/profiling
oc rollout status deployment/my-container
```

Profiling menggunakan Service internal `http://profiling:8000`, tanpa Route
publik tambahan. MongoDB VM memerlukan policy pada manifest/mongodb-vm.yaml
yang sudah mengizinkan kedua service. Deploy MongoDB dan sesuaikan Secret
sebelum mengalihkan koneksi database; pemisahan service ini tidak memigrasi data.
