import subprocess
import os

HW_FILE = "MCH2008_homework.xlsx"

if not os.path.exists(HW_FILE):
    print(f"Hata: '{HW_FILE}' dosyası klasörde bulunamadı!")
    exit(1)

print(f"🚀 Değişiklikler ve '{HW_FILE}' GitHub'a gönderiliyor...")
try:
    # Sadece tek dosya değil, app.py dahil tüm güncellemeleri sahneye al
    subprocess.run(["git", "add", "."], check=True)

    commit_res = subprocess.run(
        ["git", "commit", "-m", "Update portal app and homework records"],
        capture_output=True,
        text=True
    )

    if "nothing to commit" in commit_res.stdout.lower() or "nothing to commit" in commit_res.stderr.lower():
        print("ℹ️ Kaydedilmiş yeni bir değişiklik bulunamadı (dosyalar zaten güncel).")
    else:
        # Doğrudan güncel paketi gönder
        subprocess.run(["git", "push", "origin", "main", "--force"], check=True)
        print("✅ Başarılı: app.py ve ödevler GitHub'a yüklendi! Portal ~15 saniye içinde güncellenecektir.")

except Exception as e:
    print(f"⚠️ Hata: {e}")