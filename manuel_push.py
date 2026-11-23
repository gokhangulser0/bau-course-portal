import os
import json
import subprocess
from datetime import datetime

# Betiğin bulunduğu klasöre geç
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

DB_FILE = "grades_database.xlsx"
META_FILE = "portal_meta.json"


def check_excel_locked(filepath):
    """Excel dosyasının açık/kilitli olup olmadığını kontrol eder."""
    lock_file = os.path.join(os.path.dirname(filepath), f"~${os.path.basename(filepath)}")
    if os.path.exists(lock_file):
        return True
    try:
        with open(filepath, "r+b"):
            return False
    except PermissionError:
        return True


def run_git(args, check=True):
    """Git komutlarını çalıştırır ve çıktıyı döndürür."""
    return subprocess.run(
        ["git"] + args,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )


def main():
    print("📤 Manuel Veritabanı GitHub Push İşlemi Başlatılıyor...\n")

    # 1. Dosya varlık kontrolü
    if not os.path.exists(DB_FILE):
        print(f"❌ Hata: '{DB_FILE}' dosyası bulunamadı!")
        return

    # 2. Excel açık mı kontrolü
    if check_excel_locked(DB_FILE):
        print(f"⚠️ UYARI: '{DB_FILE}' şu anda Excel'de açık görünüyor!")
        print("👉 Lütfen dosyayı kaydedip Excel penceresini tamamen kapattıktan sonra betiği tekrar çalıştırın.")
        return

    # 3. İsteğe bağlı açıklama alma
    user_note = input("📝 Commit açıklaması (Boş bırakıp Enter'a basarsanız otomatik tarih yazılır): ").strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Manual DB update: {user_note} ({now_str})" if user_note else f"Manual update: grades_database ({now_str})"

    # 4. portal_meta.json güncelleme
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump({"db_updated": datetime.now().astimezone().isoformat(timespec="seconds")}, f)

    try:
        # 5. Yarım kalmış rebase kilidi varsa temizle ve main dalına geç
        for lock_dir in [".git/rebase-merge", ".git/rebase-apply"]:
            if os.path.exists(lock_dir):
                run_git(["rebase", "--abort"], check=False)
        run_git(["checkout", "-B", "main"], check=True)

        # 6. Sadece veritabanı ve zaman damgası dosyasını sahneye al
        run_git(["add", DB_FILE, META_FILE], check=True)

        # Değişiklik olup olmadığını kontrol et
        diff_check = run_git(["diff", "--cached", "--quiet"], check=False)
        if diff_check.returncode == 0:
            print("ℹ️ 'grades_database.xlsx' dosyasında yeni bir değişiklik tespit edilmedi.")
        else:
            run_git(["commit", "-m", commit_msg], check=True)
            print(f"✅ Değişiklikler commit edildi: '{commit_msg}'")

        # 7. Uzak depodaki kod güncellemelerini çek, çakışma olursa yerel Excel'i (-X ours) koru
        print("🔄 GitHub ile senkronize ediliyor...")
        run_git(["fetch", "origin", "main"], check=True)
        run_git(["merge", "origin/main", "-X", "ours", "--no-edit"], check=True)

        # 8. GitHub'a gönder
        run_git(["push", "origin", "main"], check=True)
        print("\n🚀 BAŞARILI: Güncel 'grades_database.xlsx' GitHub'a yüklendi ve portal güncellendi!")

    except subprocess.CalledProcessError as e:
        print("\n❌ Git işlemi sırasında bir hata oluştu:")
        if e.stderr:
            print(e.stderr.strip())
        elif e.stdout:
            print(e.stdout.strip())


if __name__ == "__main__":
    main()