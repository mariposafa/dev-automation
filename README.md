# Bootcamp: API transaksi (D1) dan konfigurasi deployment (D2)

Tugas D1 membangun fitur yang dipakai pengguna: pencatatan transaksi, CRUD,
impor Excel, dan ringkasan rasio pengeluaran. Tugas D2 membuat aplikasi bisa
berjalan dengan konfigurasi database berbeda di lokal, container, dan OpenShift.
intro.py tetap merupakan contoh belajar awal.

| Tugas | Kebutuhan pengguna | Pekerjaan engineer |
|---|---|---|
| D1 | Mencatat dan melihat pemasukan/pembelian | Endpoint, validasi, penyimpanan MongoDB |
| D1 | Memasukkan riwayat Excel | pandas membaca tabel, validasi semua baris, insert MongoDB |
| D1 | Melihat rasio pengeluaran bulanan | Hitung total nominal dan kategori dengan batas 80% |
| D2 | Bebas memilih Atlas atau MongoDB sendiri | Baca URI dan nama database dari environment variable |
| D2 | Aplikasi berjalan di OpenShift | ConfigMap, Secret, Deployment, Service, Route |
| D2 | Python lebih baru daripada 3.11 | Python 3.13 sesuai batas kompatibilitas Beanie |

Panduan langkah demi langkah dan penjelasan setiap file ada di
[Tugas D2: konfigurasi dan deployment](docs/tugas-d2.md).

## Menjalankan

Gunakan Python 3.13. Contoh PowerShell setelah Python 3.13 terpasang:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: isi MONGODB_URI dan MONGODB_DATABASE milikmu.
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --env-file .env
```

Koneksi MongoDB sekarang memakai environment variable, tanpa URI bawaan di kode.
`.env` dimuat oleh opsi `--env-file`; file tersebut tidak otomatis dimuat oleh main.py.
Kalau `.env` sudah ada, edit file itu dan jangan menimpanya dengan contoh lagi.
Buka http://127.0.0.1:8000/docs. Untuk OpenShift, build dan deploy ulang.
Perubahan lokal tidak otomatis mengubah website.

## Tugas D1: aturan input

1. amount harus angka JSON bulat, minimal 0. Angka negatif, desimal, boolean,
   dan angka dalam tanda kutip ditolak. Batas teknis bilangan MongoDB adalah
   9223372036854775807; tidak ada batas bisnis Rp1 miliar.
2. trx_type hanya pembelian atau pemasukan.
3. method hanya Cash, gopay, bca, shopee, mandiri. Penulisan harus persis.
4. Input tidak valid menghasilkan 422 beserta nama kolom dan alasan.
   ID tidak valid menghasilkan 422; transaksi tidak ditemukan menghasilkan 404.
5. date memakai YYYY-MM-DD tanpa jam. Tidak dikirim, null, atau "" saat menambah
   transaksi memakai tanggal hari ini dalam WIB. Tanggal valid yang dikirim
   pengguna dipakai apa adanya. Tidak ada larangan tanggal masa depan pada enam
   aturan ini. Tanggal tidak valid seperti 2026-02-30 ditolak.
6. PUT mengganti seluruh data transaksi; DELETE menghapus transaksi berdasarkan _id.

desc tetap berupa teks wajib saat menambah, seperti model awal. _id dibuat otomatis.
Kolom yang tidak dikenal ditolak agar salah ketik tidak diam-diam diabaikan.

## Tambah transaksi

POST /transaction/add:

```json
{
  "amount": 5000,
  "method": "Cash",
  "desc": "Bayar parkir",
  "trx_type": "pembelian",
  "date": "2026-09-21"
}
```

Untuk tanggal hari ini, hapus kolom date atau isi null / "".
Respons menampilkan date sebagai YYYY-MM-DD. MongoDB menyimpan tanggal sebagai
datetime pukul 00:00 karena BSON tidak memiliki tipe date-only; jam tersebut
merupakan representasi tanggal, bukan waktu kejadian.

Contoh input "2026-09-21" tersimpan sebagai BSON Date, yang dapat ditampilkan
di MongoDB sebagai ISODate("2026-09-21T00:00:00.000Z"). Jangan menulis ISODate(...)
di JSON Swagger; fungsi tersebut merupakan notasi shell MongoDB.

Swagger akan mendokumentasikan format date, tetapi tidak dijamin menampilkan
kalender. Kalender visual memerlukan tampilan input tersendiri.

## Update

PUT /transaction/{transaction_id}. Salin _id dari respons transaksi sebagai
transaction_id. Kirim seluruh data pengganti:

```json
{
  "amount": 7000,
  "method": "Cash",
  "desc": "Bayar parkir",
  "trx_type": "pembelian",
  "date": "2026-09-21"
}
```

amount, method, desc, dan trx_type wajib dikirim; input sebagian ditolak dengan 422.
Jika date tidak dikirim, null, atau "", tanggal menjadi hari ini (WIB).
Untuk mempertahankan tanggal lama, kirim tanggal tersebut secara eksplisit.
Kolom selain date tidak menerima null. Body kosong {} ditolak.
_id tidak bisa diedit. Validasi nominal, pilihan, dan format tanggal sama seperti tambah.

## Delete

DELETE /transaction/{transaction_id}. Hasil sukses memuat pesan penghapusan.
Penghapusan bersifat permanen; menghapus ID yang sudah tidak ada menghasilkan 404.

## Cari dan ringkas

GET /transaction menerima start_date dan end_date dengan format YYYY-MM-DD.
Seluruh tanggal akhir tercakup; tanggal awal setelah tanggal akhir ditolak.

GET /transaction/summary menerima year dan month (1-12). Di Swagger pilih
Try it out, isi tahun dan bulan, lalu Execute. Penilaian berdasarkan rasio
nominal pembelian terhadap pemasukan, bukan jumlah transaksi atau tanda net amount.

Respons sekarang berupa objek, bukan daftar. Rincian kelompok sebelumnya tetap
ada di groups. Perhitungannya:

- total_income: jumlah nominal pemasukan.
- total_expense: jumlah nominal pembelian.
- net_amount: pemasukan dikurangi pembelian (menggantikan remaining_amount).
- expense_ratio_percent: total pembelian / total pemasukan * 100.
  Ditampilkan dengan dua desimal; kategori ditentukan sebelum pembulatan.

| Kondisi | Status |
|---|---|
| Rasio pembelian < 80% | big_saver |
| Rasio pembelian >= 80% | reckless_spender |
| Pemasukan nol dan ada transaksi | belum_bisa_dinilai |
| Tidak ada transaksi | belum_ada_transaksi |
| Ada tipe lama yang tidak dikenal atau nominal negatif | data_perlu_diperiksa |

Net nol dengan pemasukan positif berarti rasio 100%, sehingga reckless_spender.
Pemasukan nol tidak dibagi; expense_ratio_percent bernilai null, termasuk saat
kedua total nol. Rasio juga null saat data tidak valid atau tidak ada transaksi.
Batas 80% adalah aturan aplikasi yang disepakati, bukan standar keuangan universal.
Hanya ada dua kategori perilaku; status lainnya menjelaskan mengapa penilaian belum
tersedia. Sisa bukan saldo rekening atau tabungan aktual. Hasil bergantung pada
kelengkapan transaksi. is_provisional bernilai true jika bulan belum berakhir.

Contoh: pemasukan Rp1.000.000 dan pembelian Rp700.000 menghasilkan sisa Rp300.000,
net_amount 300000, rasio 70%, dan status big_saver.

Data lama tidak dihapus atau dimigrasikan. Nilai lama seperti income/expense
tetap terlihat sebagai kelompok terpisah pada groups dan menyebabkan status
data_perlu_diperiksa, sehingga tidak diam-diam dinilai sebagai hemat. Gunakan PUT untuk
memperbaikinya bila diperlukan. Tanggal datetime lama ditampilkan memakai komponen
tanggal tersimpan, tanpa konversi zona waktu otomatis.

## Impor Excel dengan pandas

Pasang dependensi terbaru dengan `python -m pip install -r requirements.txt`,
lalu jalankan ulang aplikasi. Di Swagger `/docs`, pilih POST `/transaction/import`,
Try it out, Choose File, lalu Execute. Gunakan file `.xlsx` dengan header sheet
pertama: `date`, `amount`, `method`, `desc`, `trx_type` (urutan boleh berbeda).
Format file lama juga diterima: `datetime`, `amount`, `payment_method`, `description`.
Untuk format lama, nominal negatif menjadi pembelian dengan amount positif;
nominal positif menjadi pemasukan. Nol ditolak karena tipe tidak dapat ditentukan
dari tandanya. Nama kolom dipetakan ke model aplikasi; isi metode dan keterangan
dipertahankan, termasuk `cash` huruf kecil. Waktu pada datetime diambil tanggalnya
saja. Input manual tetap memakai pilihan metode yang ditetapkan sebelumnya.

Kode membaca Excel memakai `pd.read_excel()`, menyimpan tabel dalam DataFrame
`df`, lalu mengubahnya ke daftar baris dengan `df.to_dict(orient="records")`.
openpyxl tetap diperlukan sebagai engine pembaca .xlsx di belakang pandas.
Lihat [dokumentasi pandas](https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html).

Maksimal 5 MB dan 10.000 baris data. Baris kosong dilewati. Nilai transaksi tetap
divalidasi untuk nominal dan tipe; metode impor menerima teks asli.
Tanggal Excel asli, teks YYYY-MM-DD, atau YYYY-MM-DD HH:MM:SS diterima.
Tanggal impor wajib diisi; tanggal kosong ditolak agar riwayat tidak berubah.
Nominal Excel harus berupa angka, bukan teks berformat rupiah. Formula ditolak.

Jika ada baris salah, respons 422 menyebut nomor baris dan tidak ada penulisan
database yang dimulai. Jika berhasil, respons memuat imported_count.
Mengunggah ulang file yang sama menambahkan duplikat. Penulisan database bukan
transaksi atomik; kegagalan database bisa meninggalkan sebagian data tersimpan.

## Menjalankan tes

```powershell
python -m unittest discover -s tests -v
```

Tes menggunakan tiruan database untuk validasi, tambah, update, delete, dan
ringkasan. Uji koneksi serta penyimpanan MongoDB asli perlu dijalankan terpisah.
