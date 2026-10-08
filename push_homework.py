import subprocess
import os

HW_FILE = "MCH2008_homework.xlsx"

if not os.path.exists(HW_FILE):
    print(f"Hata: '{HW_FILE}' dosyası klasörde bulunamadı!")
    exit(1)

print(f"🚀 '{HW_FILE}' GitHub'a gönderiliyor...")
try:
    subprocess.run(["git", "pull", "origin", "main", "--rebase"], check=False)
    subprocess.run(["git", "add", HW_FILE], check=True)
    res = subprocess.run(["git", "commit", "-m", "Update MCH2008 homework grades"], capture_output=True, text=True)
    
    if "nothing to commit" in res.stdout.lower() or "nothing to commit" in res.stderr.lower():
        print("ℹ️ Ödev dosyasında kaydedilmiş yeni bir değişiklik bulunamadı.")
    else:
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("✅ Başarılı: Ödevler GitHub'a aktarıldı! Web sitesi ~15 saniye içinde güncellenecektir.")
except Exception as e:
    print(f"⚠️ Hata: {e}")