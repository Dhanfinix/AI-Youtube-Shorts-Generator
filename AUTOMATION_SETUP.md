# ⚙️ YtClipper Automation: Complete Setup Walkthrough

This guide walks you through the 3 simple steps to connect **Google Sheets**, **Telegram**, and **GitHub Actions** to bring your hands-free content generation engine to life!

---

## 📊 Langkah 1: Setup Google Sheets & API Credentials

Kita menggunakan Google Sheets sebagai database pekerjaan Anda. Setiap baris baru mewakili satu video YouTube yang ingin Anda kliping.

### 1. Buat Spreadsheet Baru
Buat Google Sheet baru bernama **`YtClipper_Jobs`** (atau nama lain sesuai preferensi Anda). Berikan nama kolom persis seperti berikut di baris pertama (**Row 1**):

| Row 1 Headers (Baris 1) | Deskripsi |
| :--- | :--- |
| **Timestamp** | Waktu pengisian (opsional, untuk pencatatan Anda) |
| **YouTube URL** | Link video panjang YouTube yang ingin diproses |
| **Clips Count** | Jumlah klips vertical shorts yang ingin disimpan (Default: `3`) |
| **Language** | Kode bahasa, e.g. `id` / `en` (Default: `auto`) |
| **Status** | Ketik **`Pending`** untuk baris baru yang ingin diproses |
| **Processed At** | Diisi otomatis oleh skrip saat pemrosesan dimulai |
| **Clips Output** | Diisi otomatis berisi daftar judul dan skor viral klip yang sukses |

> [!IMPORTANT]
> Sistem pencarian baris baru sangat sensitif terhadap huruf besar/kecil. Pastikan nama kolom **`YouTube URL`**, **`Clips Count`**, **`Language`**, **`Status`**, **`Processed At`**, dan **`Clips Output`** tertulis dengan benar di Baris 1.

---

### 2. Dapatkan Kredensial Google Service Account
Skrip Python membutuhkan akun robot (Service Account) resmi untuk mengakses lembar kerja Anda dengan aman.
1. Masuk ke [Google Cloud Console](https://console.cloud.google.com/).
2. Buat proyek baru (atau gunakan proyek yang ada).
3. Cari dan aktifkan **Google Drive API** dan **Google Sheets API** untuk proyek tersebut.
4. Buka menu **IAM & Admin > Service Accounts > Create Service Account**.
5. Setelah akun dibuat, masuk ke detail akun tersebut, buka tab **Keys**, pilih **Add Key > Create New Key**, lalu pilih format **JSON**. Berkas JSON kredensial Anda akan otomatis terunduh ke komputer Anda.
6. **Hubungkan dengan Google Sheets Anda:** Buka berkas JSON tersebut, cari kolom `"client_email"` (misal `ytclipper-bot@proyek.iam.gserviceaccount.com`), lalu **Share/Bagikan** hak akses Google Sheets `YtClipper_Jobs` Anda ke email tersebut dengan hak akses sebagai **Editor**.

---

## 📱 Langkah 2: Setup Telegram Bot & Chat ID

Video pendek `.mp4` hasil render beserta judul dan esensinya akan dikirimkan otomatis langsung ke akun Telegram Anda.

1. **Buat Telegram Bot Baru:**
   * Chat dengan [@BotFather](https://t.me/BotFather) di Telegram.
   * Kirim perintah `/newbot` dan ikuti petunjuknya.
   * BotFather akan memberikan Anda **`TELEGRAM_BOT_TOKEN`** (contoh: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).

2. **Dapatkan Telegram Chat ID Anda:**
   * Buat sebuah **Grup** atau **Channel** Telegram baru, lalu masukkan bot yang baru saja Anda buat ke dalamnya sebagai Admin (agar bot bisa mengirim video).
   * Dapatkan ID Grup/Channel Anda dengan cara mengirim chat ke dalam grup tersebut, lalu buka browser Anda dan akses link berikut:
     `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates`
   * Cari bagian `"chat"` dan salin nilai `"id"`-nya (contoh Grup/Channel ID biasanya diawali dengan tanda minus, seperti `-1002012345678` atau `-401234567`).

---

## 🔐 Langkah 3: Setup GitHub Repository Secrets

Agar GitHub Actions dapat berjalan otomatis tanpa membocorkan kunci rahasia Anda ke publik, masukkan kunci tersebut sebagai Repository Secrets.

1. Masuk ke halaman repository GitHub Anda.
2. Buka menu **Settings > Secrets and variables > Actions**.
3. Klik tombol **New repository secret** untuk membuat secrets berikut satu per satu:

| Nama Secret | Nilai Rahasia (Value) |
| :--- | :--- |
| **`GEMINI_API_KEY`** | Kunci API Gemini Anda untuk menganalisis highlight viral. |
| **`GOOGLE_SERVICE_ACCOUNT_JSON`** | Buka berkas JSON Service Account yang Anda unduh di Langkah 1, salin seluruh teks kontennya (termasuk kurung kurawal `{ }`), lalu tempelkan di sini. |
| **`TELEGRAM_BOT_TOKEN`** | Token bot Telegram Anda dari Langkah 2. |
| **`TELEGRAM_CHAT_ID`** | ID Chat/Grup/Channel Telegram target Anda dari Langkah 2. |
| **`GOOGLE_SHEET_NAME`** | *(Opsional)* Nama Google Sheet Anda (Default: `YtClipper_Jobs`). |
| **`YOUTUBE_COOKIES`** | *(Sangat Direkomendasikan)* Tempelkan seluruh teks mentah dari berkas `cookies.txt` Anda langsung di sini (tanpa konversi apa pun). |
| **`YOUTUBE_COOKIES_BASE64`** | *(Alternatif)* Berkas cookies YouTube yang telah dikonversi ke format Base64. |

---

## ⚡ Langkah 4: Cara Menjalankan & Menguji

### Menjalankan Secara Lokal (Local Test)
Jika Anda ingin mengujinya langsung dari terminal komputer Mac Anda sebelum di-push:
1. Pastikan Anda sudah membuat berkas `.env` lokal Anda yang berisi variabel di atas, atau taruh berkas JSON Service Account Anda di folder root dengan nama `google_credentials.json`.
2. Jalankan perintah:
   ```bash
   ./venv/bin/python automation.py
   ```

### Menjalankan Secara Otomatis (GitHub Actions)
1. Setelah Anda melakukan push file pipeline kita ke GitHub, workflow otomatis akan langsung aktif.
2. Untuk memicu pemrosesan secara langsung tanpa menunggu cron-job 30 menit:
   * Masuk ke tab **Actions** di GitHub repository Anda.
   * Pilih workflow **`YtClipper Automated Pipeline`** di kolom kiri.
   * Klik tombol dropdown **Run workflow** di sebelah kanan, lalu klik **Run workflow**.

Sistem akan otomatis mengunduh video, memotong highlight, mengirimkan video mentah ke Telegram Anda, dan menandai baris Google Sheet Anda sebagai `Done` secara otomatis! 🎉
