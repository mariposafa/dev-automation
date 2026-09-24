# Tugas D2: database fleksibel dan Python yang kompatibel

## Apa bedanya dengan tugas kemarin?

D1 berfokus pada fitur pengguna: tambah, cari, update, hapus transaksi,
impor Excel, dan ringkasan bulanan. Rincian aturan dan contoh request ada di
[README](../README.md). D2 berfokus pada cara engineer mengonfigurasi dan
menjalankan fitur itu di beberapa lingkungan.

Alur fitur D1: pengguna mengirim request → FastAPI memvalidasi → MongoDB menyimpan
atau membaca transaksi → respons dikirim ke pengguna. Rasio tetap nominal
pembelian / pemasukan × 100. Di bawah 80% adalah big_saver, mulai 80% adalah
reckless_spender. Kategori big_spender khusus net nol belum diterapkan.

## Tiga hint mentor

1. **Environment variable**: nilai konfigurasi yang diberikan kepada proses aplikasi.
   `config.py` membaca `MONGODB_URI` dan `MONGODB_DATABASE` dengan `os.environ`.
   Keduanya wajib diisi; pesan startup menyebut nama variabel yang kurang.
2. **Konfigurasi OpenShift**: ConfigMap menyimpan nama database yang tidak rahasia.
   Deployment memasukkannya sebagai environment variable melalui `configMapKeyRef`.
3. **Secret OpenShift**: menyimpan URI yang bisa berisi username/password.
   Deployment mengambilnya melalui `secretKeyRef`. File contoh tidak berisi
   kredensial asli; isi Secret asli hanya di lingkunganmu.

```text
ConfigMap: MONGODB_DATABASE ─┐
                           ├─ Deployment → environment variable → config.py
Secret: MONGODB_URI ────────┘                                   ↓
                                                  main.py → MongoDB pilihan
```

Nama variabel sama di semua lingkungan. Mengganti Atlas dengan MongoDB sendiri
cukup mengganti URI dan, bila perlu, nama database lalu menjalankan ulang aplikasi.
Ini mengganti tujuan koneksi; tidak memindahkan data dari database lama.

## File dan kegunaannya

| File | Keterangan |
|---|---|
| `main.py` | D1: endpoint; D2: lifespan membuka koneksi sesuai konfigurasi dan menutupnya saat shutdown |
| `config.py` | Membaca dan memeriksa konfigurasi database saat startup |
| `.env.example` | Contoh konfigurasi lokal; salin menjadi `.env` |
| `requirements.txt` | Library yang dipasang ke environment Python atau image |
| `.python-version` | Penanda versi 3.13 untuk alat yang mendukungnya; tidak mengganti Python secara otomatis |
| `Containerfile` | Resep image Python 3.13, pemasangan dependensi, dan perintah menjalankan FastAPI |
| `manifest/configmap.yaml` | Nama database `bootcamp` |
| `manifest/secret.yaml.example` | Template URI; salin menjadi `manifest/secret.yaml` dan isi sendiri |
| `manifest/deployment.yaml` | Menjalankan satu Pod, memakai image D2 dan konfigurasi database |
| `manifest/service.yaml` | Menghubungkan port 8000 ke Pod berlabel `bootcamp: its-mlpt` |
| `manifest/route.yaml` | Alamat HTTPS OpenShift menuju Service `my-container` |

Nama `my-container` dan label dari materi mentor dipertahankan agar Deployment
dan Service tetap cocok. Alamat registry internal diperbaiki menjadi
`image-registry.openshift-image-registry.svc:5000`. Tag D2 memakai
`1.1.0` agar terpisah dari image lama.

`.env` dan `manifest/secret.yaml` dikecualikan dari Git dan konteks build image.
Secret merupakan mekanisme pengelolaan kredensial, bukan alasan untuk memasukkan
password ke repository. Base64 pada Secret bukan enkripsi. Ganti kredensial lama
yang sebelumnya pernah tertulis di kode/chat, lalu gunakan URI baru pada konfigurasi.

## Kenapa Python 3.13?

Pemeriksaan pada 22 September 2026: seri stabil terbaru Python adalah 3.14,
tetapi metadata Beanie 2.2.0 mensyaratkan `Python >=3.10,<3.14`.
Karena tugas meminta versi terbaru **yang kompatibel**, proyek memilih seri 3.13.
Tag `python:3.13-slim` mengambil patch yang tersedia pada seri tersebut saat image
dasar ditarik. Gunakan `--pull=always` saat build untuk memperbarui image dasar.

- [Status versi Python](https://devguide.python.org/versions/)
- [Persyaratan Beanie](https://pypi.org/project/beanie/2.2.0/)
- [Dukungan Python 3.13 pada pandas 2.2.3](https://pandas.pydata.org/docs/whatsnew/v2.2.3.html)
- [ConfigMap OpenShift](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/building_applications/config-maps)

`requirements.txt` membatasi versi mayor dependensi utama; ini belum merupakan
lockfile seluruh versi paket. Instalasi dan tes tetap perlu dijalankan setelah build.

## 1. Development lokal

Di terminal Bash Developer Sandbox/Linux, Python 3.13 harus sudah terpasang.
Venv 3.11 lama tidak berubah hanya karena Containerfile diedit.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` sebelum menjalankan server. Contoh database lokal tanpa autentikasi:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=bootcamp
```

Contoh Atlas (ganti placeholder, jangan salin sebagai kredensial nyata):

```dotenv
MONGODB_URI=mongodb+srv://USERNAME:PASSWORD@CLUSTER_HOST/?retryWrites=true&w=majority
MONGODB_DATABASE=bootcamp
```

Gunakan URI dari penyedia database. Karakter khusus pada username/password harus
di-URL-encode sesuai format URI MongoDB. Server database harus sudah tersedia,
kredensial benar, dan jaringan mengizinkan koneksi dari tempat aplikasi berjalan.

```bash
python -m pip check
python -m unittest discover -s tests -v
python -m uvicorn main:app --reload --env-file .env
```

Buka `http://127.0.0.1:8000/docs`. Untuk Windows PowerShell, lihat perintah README.
Tes memakai mock database, jadi tidak memerlukan URI nyata atau menulis data asli.
Jika `python3.13` belum tersedia di Sandbox, gunakan image pada langkah 2 untuk
menjalankan dan menguji aplikasi dengan 3.13.

## 2. Container lokal

Jalankan dari folder `bootcamp-mlpt` yang berisi Containerfile:

```bash
podman build --pull=always -f Containerfile -t image-name:1.1.0 .
podman run --rm image-name:1.1.0 python --version
podman run --rm image-name:1.1.0 python -m pip check
podman run --rm image-name:1.1.0 python -m unittest discover -s tests -v
podman run --rm --env-file .env -p 8000:8000 image-name:1.1.0
```

Podman memasukkan isi `.env` sebagai environment variable; file itu tidak disalin
ke image. Memasang pandas di venv tidak memasangnya ke image yang sudah dibangun.

`localhost` dalam container menunjuk container itu sendiri. Bila MongoDB berjalan
di host Podman, gunakan alamat host yang dapat dijangkau, misalnya
`host.containers.internal` pada lingkungan Podman yang mendukungnya. Untuk MongoDB
dalam jaringan container bersama, gunakan nama container/alias MongoDB.
Untuk Atlas, gunakan URI Atlas yang sama selama jaringan mengizinkan.

## 3. Push image D2

Setelah tes container berhasil dan `oc` sudah login ke cluster yang benar:

```bash
oc whoami -t | podman login --username "$(oc whoami)" --password-stdin default-route-openshift-image-registry.apps.rm1.0a51.p1.openshiftapps.com
podman tag image-name:1.1.0 default-route-openshift-image-registry.apps.rm1.0a51.p1.openshiftapps.com/nabilahsafa-dev/image-name:1.1.0
podman push default-route-openshift-image-registry.apps.rm1.0a51.p1.openshiftapps.com/nabilahsafa-dev/image-name:1.1.0
```

Token dialirkan langsung ke Podman; tidak perlu ditampilkan atau dikirim ke chat.
Push menyimpan image di registry. Deployment adalah langkah terpisah untuk
menjalankan image tersebut sebagai aplikasi.

## 4. Konfigurasi dan deployment OpenShift

Salin template Secret, lalu isi `stringData.MONGODB_URI` dengan URI sebenarnya:

```bash
cp manifest/secret.yaml.example manifest/secret.yaml
```

URI harus dapat dijangkau dari Pod. `localhost` di Pod tidak menunjuk komputer
pengguna. Bila memakai MongoDB sendiri dalam cluster, gunakan alamat Service
MongoDB dan autentikasi yang sesuai. YAML proyek ini tidak memasang server MongoDB.

Terapkan satu per satu dari terminal VS Code/Sandbox:

```bash
oc apply -f manifest/configmap.yaml
oc apply -f manifest/secret.yaml
oc apply -f manifest/deployment.yaml
oc apply -f manifest/service.yaml
oc apply -f manifest/route.yaml
oc rollout status deployment/my-container -n nabilahsafa-dev --timeout=120s
oc get pods -n nabilahsafa-dev -l bootcamp=its-mlpt
oc logs deployment/my-container -n nabilahsafa-dev --tail=50
oc get route my-container -n nabilahsafa-dev
```

ConfigMap/Secret perlu tersedia sebelum container bisa startup. Pada console,
resource yang sama bisa dilihat di project `nabilahsafa-dev`. Jangan menempel YAML
Deployment ke tab YAML milik Project. Jangan apply `secret.yaml.example` langsung.

Buka `https://HOST_DARI_ROUTE/docs`. Path `/` belum memiliki endpoint di main.py,
jadi respons 404 di `/` saja tidak berarti deployment gagal. Tunggu log
`Application startup complete` dan coba GET melalui Swagger untuk memeriksa
koneksi MongoDB. Impor Excel hanya bila data belum diimpor agar tidak duplikat.

Alur jaringan: browser → Route HTTPS → Service port 8000 → Pod FastAPI → MongoDB.

Jika nilai ConfigMap/Secret diganti atau image dibangun ulang dengan tag yang sama,
restart Deployment supaya Pod baru mengambil nilai/image baru:

```bash
oc rollout restart deployment/my-container -n nabilahsafa-dev
oc rollout status deployment/my-container -n nabilahsafa-dev --timeout=120s
```

Mengubah konfigurasi tidak perlu build ulang image. Mengubah kode, library, atau
versi Python memerlukan build dan push ulang image.

## Membaca error

| Gejala | Pemeriksaan |
|---|---|
| Environment variable wajib diisi | Isi dua variabel; untuk lokal gunakan `--env-file .env` |
| CreateContainerConfigError | Pastikan nama/key Secret dan ConfigMap sesuai Deployment |
| ImagePullBackOff | Pastikan tag D2 berhasil dipush dan alamat registry benar |
| CrashLoopBackOff | Baca Logs Pod; cek URI, kredensial, DNS, dan akses jaringan database |
| Route tidak melayani aplikasi | Cek startup selesai, label Service cocok dengan Pod, dan port 8000 |

Tes otomatis memverifikasi validasi transaksi, impor Excel, rasio, pemilihan
database melalui environment variable, serta penutupan koneksi saat shutdown atau
startup gagal. Tes mock tidak membuktikan koneksi Atlas maupun rollout OpenShift;
keduanya perlu diperiksa di lingkungan deployment sebenarnya.

## Hasil verifikasi perubahan ini

Pada 22 September 2026 di Windows, Python 3.13.15 berhasil memasang dependensi.
`pip check` lulus dan seluruh 27 tes D1/D2 lulus. Versi utama yang terpasang:
FastAPI 0.141.1, Beanie 2.2.0, PyMongo 4.18.1, Pydantic 2.13.5,
pandas 3.0.6, dan openpyxl 3.1.5.

YAML berhasil diparse dan diperiksa kecocokan label Deployment/Service,
referensi nama/key ConfigMap/Secret, namespace, serta port Route/Service/container.
Ini pemeriksaan lokal, bukan validasi API server OpenShift.

Build container Linux, koneksi MongoDB asli, dan rollout OpenShift belum dijalankan
dari workspace ini. Jalankan langkah 2 sampai 4 di Developer Sandbox untuk
memverifikasi lingkungan tersebut. Ada peringatan deprecation dari TestClient
tentang httpx; peringatan tersebut tidak menggagalkan tes.
