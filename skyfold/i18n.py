"""Tiny translation layer. Source strings are English; other languages map English -> text.

Usage: t("Downloading {i} of {n}", i=3, n=10). The language is chosen once at start-up
(config reads it from settings.json), so module-level labels come out in the right language.
"""

from __future__ import annotations

LANGUAGES = {"en": "English", "bs": "Bosanski"}
_lang = "en"


from .safelog import Text  # noqa: E402


def set_language(code: str) -> None:
    global _lang
    _lang = code if code in LANGUAGES else "en"


def language() -> str:
    return _lang


def t(text: str, **kw) -> str:
    s = _CATALOGS.get(_lang, {}).get(text, text)
    return Text(s.format(**kw) if kw else s)  # marked as Skyfold's own wording (see safelog)


BS: dict[str, str] = {
    # tray & flyout ---------------------------------------------------------------
    "Everything is in sync": "Sve je sinhronizovano",
    "Everything is in sync.": "Sve je sinhronizovano.",
    "Syncing…": "Sinhronizujem…",
    "Paused": "Pauzirano",
    "Offline": "Bez veze",
    "Sync problem": "Problem sa sinhronizacijom",
    "Sign-in required": "Potrebna prijava",
    "Choose a folder for your photos": "Izaberi folder za galeriju",
    "Show": "Prikaži",
    "Sync now": "Sinhronizuj sada",
    "Full library check": "Potpuna provjera biblioteke",
    "Pause": "Pauziraj",
    "Resume syncing": "Nastavi sinhronizaciju",
    "Pause syncing": "Pauziraj sinhronizaciju",
    "Open photos folder": "Otvori folder sa slikama",
    "Settings…": "Postavke…",
    "Settings": "Postavke",
    "Open log": "Otvori log",
    "Quit": "Izlaz",
    "Confirmation needed": "Potrebna potvrda",
    "The system tray is not available.": "System tray nije dostupan.",
    "Could not set up start with Windows: {error}": "Ne mogu podesiti automatsko pokretanje: {error}",
    "Not signed in": "Nije prijavljen",
    "Review": "Pregledaj",
    "Sign in": "Prijavi se",
    "Photos": "Fotografije",
    "Videos": "Videa",
    "On disk": "Na disku",
    "RECENT ACTIVITY": "NEDAVNA AKTIVNOST",
    "Recent activity": "Nedavna aktivnost",
    "Drive": "Drive",
    "Other": "Ostalo",
    "More": "Više",
    "Open iCloud.com": "Otvori iCloud.com",
    "Pause all syncing": "Pauziraj sinhronizaciju",
    "{pct}% used · {free} available": "Iskorišteno {pct}% · slobodno {free}",
    "No activity yet.": "Još nema aktivnosti.",
    "Stop": "Zaustavi",
    "Choose folder": "Izaberi folder",
    "Signing in to iCloud is required to sync.": "Za sinhronizaciju je potrebna prijava na iCloud.",
    "Last check: {when}": "Zadnja provjera: {when}",
    "{n} deleted in iCloud": "{n} obrisanih u iCloud-u",
    "{n} deleted on this PC": "{n} obrisanih na PC-u",
    "A large deletion is waiting for your OK: {what}.": "Veliko brisanje čeka potvrdu: {what}.",
    " and ": " i ",
    # time ------------------------------------------------------------------------
    "never": "nikad",
    "just now": "upravo sada",
    "{n} min ago": "prije {n} min",
    "today at {time}": "danas u {time}",
    # worker ----------------------------------------------------------------------
    "Choose where your photos should be kept before the first sync.":
        "Prije prve sinhronizacije izaberi gdje će se čuvati slike.",
    "Sign in with your Apple ID to start syncing.": "Prijavi se svojim Apple ID-jem da počne sinhronizacija.",
    "Connecting to iCloud…": "Povezujem se sa iCloud-om…",
    "Can't reach iCloud. Will retry shortly.": "Nema veze sa iCloud-om. Pokušaću ponovo uskoro.",
    "Your session has expired. Please sign in again.": "Sesija je istekla. Prijavi se ponovo.",
    "Your iCloud session has expired. Click to sign in again.": "iCloud sesija je istekla. Klikni da se prijaviš ponovo.",
    "Syncing is paused.": "Sinhronizacija je pauzirana.",
    "You are signed out. Sign in to continue.": "Odjavljen si. Prijavi se da nastaviš.",
    "Checking for changes…": "Provjeravam promjene…",
    "Sync stopped.": "Sinhronizacija prekinuta.",
    "Error: {error}": "Greška: {error}",
    "Sync error: {error}": "Greška pri sinhronizaciji: {error}",
    "{n} items are waiting for your OK to be deleted.": "Čeka se tvoja potvrda za brisanje {n} stavki.",
    "Downloaded: {n}": "Preuzeto: {n}",
    "Deleted on PC: {n}": "Obrisano na PC-u: {n}",
    "Deleted in iCloud: {n}": "Obrisano u iCloud-u: {n}",
    "Photos synced": "Slike sinhronizovane",
    "Finished with {n} errors. Will retry.": "Završeno uz {n} grešaka. Pokušaću ponovo.",
    "Photos you deleted only on this PC will be downloaded again.": "Ručno obrisane slike će biti ponovo preuzete.",
    # engine ----------------------------------------------------------------------
    "Photos folder is not available: {path}": "Folder za slike nije dostupan: {path}",
    "The photos folder doesn't look like before (its sync marker is missing). "
    "Syncing has stopped so nothing gets deleted by mistake.":
        "Folder za slike ne izgleda kao ranije (nedostaje oznaka sinhronizacije). "
        "Sinhronizacija je zaustavljena da se ništa ne bi obrisalo greškom.",
    "Followed {n} files moved inside the folder": "Praćeno premještanje {n} fajlova unutar foldera",
    "Checking iCloud for changes…": "Provjeravam promjene u iCloud-u…",
    "Reading your iCloud library…": "Čitam iCloud biblioteku…",
    "Reading your iCloud library… {n}": "Čitam iCloud biblioteku… {n}",
    "Deleted on this PC only: {name}": "Obrisano samo na PC-u: {name}",
    "Deleting in iCloud…": "Brišem u iCloud-u…",
    "Couldn't delete in iCloud ({name}): {error}": "Brisanje u iCloud-u nije uspjelo ({name}): {error}",
    "Deleted in iCloud: {name}": "Obrisano u iCloud-u: {name}",
    "Deleted in iCloud, kept on this PC: {name}": "Obrisano u iCloud-u, zadržano na PC-u: {name}",
    "Moving deleted photos to the Recycle Bin…": "Premještam obrisane u Recycle Bin…",
    "Couldn't delete {name}: {error}": "Ne mogu obrisati {name}: {error}",
    "Deleted on this PC (Recycle Bin): {name}": "Obrisano na PC-u (u Recycle Bin): {name}",
    "Downloading {i} of {n}": "Preuzimam {i} od {n}",
    "file size doesn't match": "veličina fajla se ne poklapa",
    "Download failed ({name}): {error}": "Preuzimanje nije uspjelo ({name}): {error}",
    "Mac package left in iCloud (it only works on a Mac): {name}":
        "Mac paket ostaje na iCloudu (radi samo na Macu): {name}",
    "Left in iCloud, {reason}: {name}": "Ostaje na iCloudu, {reason}: {name}",
    "its name has a character Windows doesn't allow": "naziv ima znak koji Windows ne dozvoljava",
    "its name ends with a space or dot": "naziv završava razmakom ili tačkom",
    "its name is reserved by Windows": "Windows rezerviše taj naziv",
    "its path is too long for Windows": "putanja je predugačka za Windows",
    "Downloaded {n} new files": "Preuzeto novih fajlova: {n}",
    "Downloaded 1 new file": "Preuzet 1 novi fajl",
    # remote ----------------------------------------------------------------------
    "iCloud can't be reached over the web. On your iPhone check: Settings → [your name] → "
    "iCloud → 'Access iCloud Data on the Web' must be on.":
        "iCloud nije dostupan preko weba. Na iPhone-u provjeri: Postavke → [ime] → "
        "iCloud → 'Pristup iCloud podacima na webu' mora biti uključeno.",
    "Access to iCloud was denied. If 'Advanced Data Protection' is on, also turn on "
    "'Access iCloud Data on the Web'.":
        "Pristup iCloudu je odbijen. Ako je uključena 'Napredna zaštita podataka' "
        "(Advanced Data Protection), uključi i 'Pristup iCloud podacima na webu'.",
    # auth ------------------------------------------------------------------------
    "This Apple account is locked. Unlock it at iforgot.apple.com.":
        "Apple nalog je zaključan. Otključaj ga na iforgot.apple.com.",
    "Wrong Apple ID or password.": "Pogrešan Apple ID ili lozinka.",
    "Can't reach Apple's servers. Check your internet connection.":
        "Nema veze sa Apple serverima. Provjeri internet.",
    "Sign-in failed: {error}": "Prijava nije uspjela: {error}",
    "This account uses the old two-step verification, which isn't supported.":
        "Ovaj nalog koristi staru dvostepenu verifikaciju koja nije podržana.",
    "This account requires a security key (FIDO), which isn't supported yet.":
        "Nalog traži sigurnosni ključ (FIDO). To trenutno nije podržano.",
    "Enter the 6-digit code shown on your iPhone, iPad or Mac.":
        "Unesi 6-cifreni kod prikazan na iPhone-u, iPad-u ili Mac-u.",
    "We sent a code by SMS to your phone number. Enter it below.":
        "Poslali smo kod SMS-om na tvoj broj telefona. Unesi ga ispod.",
    "Code check failed: {error}": "Provjera koda nije uspjela: {error}",
    "That code isn't right. Try again.": "Kod nije ispravan. Pokušaj ponovo.",
    # config ----------------------------------------------------------------------
    "Choose a folder.": "Izaberi folder.",
    "Enter the full folder path.": "Unesi punu putanju foldera.",
    "Clear": "Očisti",
    "{name} can't go to the Recycle Bin; not deleted": "{name} ne može u Kantu za smeće; nije obrisano",
    "{name} couldn't be moved to the Recycle Bin": "{name} se nije mogao premjestiti u Kantu za smeće",
    "{name} is on a network drive without a Recycle Bin; not deleted": "{name} je na mrežnom disku bez Kante za smeće; nije obrisano",
    "{name} is on a drive without a Recycle Bin; not deleted": "{name} je na disku bez Kante za smeće; nije obrisano",
    "an attachment couldn't be downloaded; tried again next time": "prilog se nije mogao preuzeti; ponovo sljedeći put",
    "{name} leads outside the photos folder; left alone": "{name} vodi izvan foldera sa slikama; ostavljeno kako jeste",
    "{name} isn't a page Skyfold wrote; the note gets another name next time": "{name} nije stranica koju je napravio Skyfold; bilješka sljedeći put dobija drugo ime",
    "previous version": "prethodna verzija",
    "Not deleted in iCloud, it changed there since you confirmed: {name}": "Nije obrisano u iCloudu, promijenilo se tamo nakon vaše potvrde: {name}",
    "{name} changed while it was downloading; tried again next time": "{name} se promijenio tokom preuzimanja; ponovo sljedeći put",
    "Drive: downloaded: {name}": "Drive: preuzeto: {name}",
    "Drive: uploaded: {name}": "Drive: poslano: {name}",
    "{name} already exists; not replaced": "{name} već postoji; nije zamijenjen",
    "Couldn't read the Drive folder's sync marker: {error}": "Oznaka Drive foldera se ne može pročitati: {error}",
    "This Drive folder was used before and it isn't clear by which Apple ID. Choose a new, empty folder for iCloud Drive in Settings.":
        "Ovaj Drive folder je ranije korišten i nije jasno s kojim Apple ID-om. U Postavkama izaberi novi, prazan folder za iCloud Drive.",
    "{name} leads outside the photos folder; left alone": "{name} vodi izvan foldera za slike; ostavljeno kako jeste",
    "{name} leads outside the notes folder; left alone": "{name} vodi izvan foldera za bilješke; ostavljeno kako jeste",
    "The Notes folder already has a Notes.html that isn't from Skyfold. Choose an empty folder for Notes in Settings.":
        "Folder za bilješke već ima Notes.html koji nije od Skyfolda. U Postavkama izaberi prazan folder za bilješke.",
    "This Drive folder belongs to another Apple ID. Choose a new, empty folder for iCloud Drive in Settings.":
        "Ovaj Drive folder pripada drugom Apple ID-u. U Postavkama izaberi novi, prazan folder za iCloud Drive.",
    "{name} leads outside the Drive folder; left alone": "{name} vodi izvan Drive foldera; ostavljeno kako jeste",
    "another item has the same name in different letter case": "druga stavka ima isto ime s drugačijim velikim/malim slovima",
    "Open folder": "Otvori folder",
    "Sync": "Sinhronizuj",
    "Set up": "Podesi",
    "iCloud.com": "iCloud.com",
    "Pause": "Pauza",
    "Waiting for your OK": "Čeka tvoju potvrdu",
    "Opens Google Maps in your browser with the device's last location.":
        "Otvara Google Maps u pregledniku s posljednjom lokacijom uređaja.",
    "After a restart, Apple asks for your password once more before it shows your devices. Photos, Drive and Notes keep syncing without it, and the password still isn't stored.":
        "Nakon ponovnog pokretanja Apple još jednom traži lozinku prije nego što pokaže tvoje uređaje. Slike, Drive i bilješke se i bez toga sinhronizuju, a lozinka se i dalje ne čuva.",
    "You signed in with another Apple ID. Turned off: {services}. Their folders hold the other account's files; choose a new, empty folder in Settings to turn them on again.":
        "Prijava drugim Apple ID-om. Isključeno: {services}. Njihovi folderi sadrže fajlove drugog naloga; u Postavkama izaberi novi, prazan folder da ih ponovo uključiš.",
    "Yes": "Da",
    "No": "Ne",
    "OK": "U redu",
    "Apple has updated its terms. Accept them on your iPhone (Settings) or at icloud.com, then sign in here again.":
        "Apple je ažurirao uslove korištenja. Prihvati ih na iPhoneu (Postavke) ili na icloud.com, pa se ovdje ponovo prijavi.",
    "Apple asks to allow access to your iCloud data (Advanced Data Protection). Approve the request on your iPhone, or turn on Settings → [your name] → iCloud → 'Access iCloud Data on the Web'.":
        "Apple traži dozvolu za pristup tvojim iCloud podacima (napredna zaštita podataka). Odobri zahtjev na iPhoneu ili uključi Postavke → [tvoje ime] → iCloud → 'Pristup iCloud podacima na webu'.",
    "Skyfold tries again by itself.": "Skyfold sam pokušava ponovo.",
    "Done": "Gotovo",
    "Couldn't read the Drive folder on this PC. Syncing has stopped so nothing gets deleted by mistake.":
        "Folder za Drive na ovom PC-u se ne može pročitati. Sinhronizacija je zaustavljena da se ništa ne obriše greškom.",
    "Folder {name} is missing on this PC, it is downloaded again (it isn't deleted in iCloud).":
        "Folder {name} nedostaje na ovom PC-u, preuzima se ponovo (u iCloudu se ne briše).",
    "Couldn't create folder ({name}): {error}": "Folder se ne može napraviti ({name}): {error}",
    "Deleted in iCloud, but changed on this PC: kept {name}": "Obrisano u iCloudu, ali izmijenjeno na ovom PC-u: zadržano {name}",
    "Drive: can't read {name} on this PC right now; it is left alone and checked again next time":
        "Drive: {name} se trenutno ne može pročitati na ovom PC-u; ostavljeno je kako jeste i provjerava se ponovo sljedeći put",
    "Deleted on this PC, but changed in iCloud: kept {name}": "Obrisano na ovom PC-u, ali izmijenjeno u iCloudu: zadržano {name}",
    "Waiting for sign-in": "Čeka prijavu",
    "Not set up yet": "Još nije podešeno",
    "Resume": "Nastavi",
    "charging": "puni se",
    "HELP": "POMOĆ",
    "Source code and bug reports (GitHub)": "Izvorni kod i prijava grešaka (GitHub)",
    "License: GNU GPL v3": "Licenca: GNU GPL v3",
    "Show the log file": "Prikaži log datoteku",
    "Drive: downloaded 1 file": "Drive: preuzet 1 fajl",
    "Drive: uploaded 1 file": "Drive: poslan 1 fajl",
    "Sign in again": "Prijavi se ponovo",
    "Drive: renamed/moved in iCloud: {old} → {new}": "Drive: preimenovano/premješteno na iCloudu: {old} → {new}",
    "Drive: renamed/moved on this PC: {old} → {new}": "Drive: preimenovano/premješteno na ovom PC-u: {old} → {new}",
    "Renamed or moved files and folders are renamed or moved on the other side too.":
        "Preimenovani ili premješteni fajlovi i folderi se i na drugoj strani preimenuju ili premjeste.",
    "No limit": "Bez ograničenja",
    "{n} MB/s": "{n} MB/s",
    "Download speed limit": "Ograničenje brzine preuzimanja",
    "Keeps some of your connection free while Skyfold downloads, for example during video calls.":
        "Ostavlja dio interneta slobodnim dok Skyfold preuzima, npr. tokom video poziva.",
    "Click for details": "Klikni za detalje",
    "Sync problem": "Problem sa sinhronizacijom",
    "Last seen: {when}": "Zadnji put: {when}",
    "Skyfold tries again by itself. Most problems like this are a short break in the connection to Apple and go away on the next sync.":
        "Skyfold sam pokušava ponovo. Većina ovakvih problema je kratak prekid veze s Appleom i nestane pri sljedećoj sinhronizaciji.",
    "Try again now": "Pokušaj ponovo sada",
    "Copy text": "Kopiraj tekst",
    "Your photos are on this PC": "Tvoje slike su na ovom PC-u",
    "The first download is done: {n} files. From now on only changes are synced.":
        "Prvo preuzimanje je gotovo: {n} fajlova. Od sada se sinhronizuju samo izmjene.",
    "iCloud Drive is ready": "iCloud Drive je spreman",
    "The first sync is done: {n} files downloaded. From now on only changes are synced.":
        "Prva sinhronizacija je gotova: preuzeto {n} fajlova. Od sada se sinhronizuju samo izmjene.",
    "FOLDERS": "FOLDERI",
    "Choose folders…": "Izaberi foldere…",
    "Folders you leave out stay in iCloud untouched and aren't downloaded.":
        "Folderi koje ne izabereš ostaju netaknuti na iCloudu i ne preuzimaju se.",
    "All of iCloud Drive": "Cijeli iCloud Drive",
    "No folders (only files outside folders)": "Nijedan folder (samo fajlovi van foldera)",
    "{n} chosen: {names}": "Izabrano {n}: {names}",
    "What should Skyfold sync from your iCloud Drive?": "Šta da Skyfold sinhronizuje s tvog iCloud Drivea?",
    "You can sync everything, or only the folders you need. This can be changed later in Settings.":
        "Možeš sinhronizovati sve ili samo foldere koji ti trebaju. To se kasnije može promijeniti u Postavkama.",
    "Everything": "Sve",
    "{n} folders on this PC will no longer be synced.": "Broj foldera na ovom PC-u koji se više neće sinhronizovati: {n}.",
    "They stay in iCloud either way. Keep the copies on this PC, or move them to the Recycle Bin?":
        "Na iCloudu ostaju u svakom slučaju. Zadržati kopije na ovom PC-u ili ih premjestiti u Recycle Bin?",
    "Move to Recycle Bin": "Premjesti u Recycle Bin",
    "Choose iCloud Drive folders – {app}": "Izbor iCloud Drive foldera – {app}",
    "What should be synced?": "Šta da se sinhronizuje?",
    "Checked folders are downloaded and kept in sync. Unchecked folders stay in iCloud, untouched. "
    "Files directly in iCloud Drive (outside any folder) are always synced.":
        "Označeni folderi se preuzimaju i sinhronizuju. Neoznačeni ostaju na iCloudu, netaknuti. "
        "Fajlovi direktno u iCloud Driveu (van foldera) se uvijek sinhronizuju.",
    "Reading your iCloud Drive folders…": "Čitam foldere s tvog iCloud Drivea…",
    "Open a folder to choose only some of the folders inside it.":
        "Otvori folder da izabereš samo neke foldere unutar njega.",
    "There are no folders in your iCloud Drive.": "Na tvom iCloud Driveu nema foldera.",
    "Couldn't read iCloud Drive: {error}": "Ne mogu pročitati iCloud Drive: {error}",
    "Loading…": "Učitavam…",
    "Drive: moved to the Recycle Bin (no longer synced): {name}": "Drive: premješteno u Recycle Bin (više se ne sinhronizuje): {name}",
    "less than a minute": "manje od minute",
    "about {m} min": "oko {m} min",
    "about {h} h {m} min": "oko {h} h {m} min",
    "about {h} h": "oko {h} h",
    "{prefix}{i} of {n} files": "{prefix}{i} od {n} fajlova",
    "{done} of {total}": "{done} od {total}",
    "{speed}/s": "{speed}/s",
    "{time} left": "još {time}",
    "Photos: ": "Slike: ",
    "Drive: ": "Drive: ",
    "Clear the activity list. Your files are not touched.": "Briše listu aktivnosti. Tvoji fajlovi se ne diraju.",
    "BEFORE YOU START": "PRIJE NEGO ŠTO POČNEŠ",
    "Skyfold syncs in both directions, deletions included. It is free software provided "
    "without any warranty (GNU GPL v3): you use it at your own risk, and its authors are not "
    "liable for lost or changed files. Keep your own backup of anything important.":
        "Skyfold sinhronizuje u oba smjera, uključujući brisanje. To je besplatan program bez ikakve "
        "garancije (GNU GPL v3): koristiš ga na vlastitu odgovornost, a autori ne odgovaraju za "
        "izgubljene ili izmijenjene fajlove. Čuvaj vlastitu rezervnu kopiju svega što ti je važno.",
    "I understand and accept this": "Razumijem i prihvatam",
    "This folder can't contain Skyfold itself ({app}). Choose another folder, or move Skyfold "
    "somewhere else first.":
        "Ovaj folder ne smije sadržavati sam Skyfold ({app}). Izaberi drugi folder ili prvo premjesti "
        "Skyfold negdje drugo.",
    "Skyfold uses this folder internally. Choose another folder.":
        "Ovaj folder koristi Skyfold interno. Izaberi drugi folder.",
    "Drive {drive} is not available.": "Disk {drive} nije dostupan.",
    "This folder is in {service}: photos would also be uploaded to that cloud, using space and "
    "bandwidth. A normal folder such as Pictures is recommended.":
        "Folder je u {service}-u: slike će se dodatno slati i u taj oblak, što troši prostor i "
        "internet. Preporuka je običan folder, npr. Slike.",
    "All in one folder": "Sve u jednom folderu",
    "By year": "Po godinama",
    "By year and month": "Po godinama i mjesecima",
    "Year  (2025)": "Godina  (2025)",
    "Year / Month  (2025/11)": "Godina / Mjesec  (2025/11)",
    # login dialog ----------------------------------------------------------------
    "Sign in – {app}": "Prijava – {app}",
    "Sign in to iCloud": "Prijava na iCloud",
    "Use the Apple ID you use on your iPhone and iPad.": "Prijavi se Apple ID-jem koji koristiš na iPhone-u i iPad-u.",
    "Apple ID (email)": "Apple ID (e-mail)",
    "Password": "Lozinka",
    "Your password is never stored. Only Apple's session, which lasts about two months, is kept.":
        "Lozinka se ne čuva. Čuva se samo Apple-ova sesija, koja traje otprilike dva mjeseca.",
    "Cancel": "Otkaži",
    "Signing in…": "Prijavljujem…",
    "Confirm": "Potvrdi",
    "Checking…": "Provjeravam…",
    "Enter your Apple ID and password.": "Unesi Apple ID i lozinku.",
    # settings dialog ---------------------------------------------------------------
    "Settings – {app}": "Postavke – {app}",
    "ACCOUNT": "NALOG",
    "Sign out": "Odjavi se",
    "PHOTOS FOLDER": "FOLDER SA SLIKAMA",
    "Change…": "Promijeni…",
    "Layout": "Raspored",
    "The layout applies to new photos; existing ones stay where they are.":
        "Raspored važi za nove slike; postojeće ostaju gdje jesu.",
    "SYNC": "SINHRONIZACIJA",
    "Check iCloud every": "Provjeri iCloud svakih",
    "Apple doesn't notify apps about changes, so iCloud is checked at intervals. Each check only "
    "reads what changed; the whole library is compared once a day.":
        "Apple ne javlja promjene aplikacijama, pa se iCloud provjerava u intervalima. Svaka provjera "
        "čita samo promjene; cijela biblioteka se uporedi jednom dnevno.",
    "Download videos": "Preuzimaj videa",
    "Download the video part of Live Photos (.MOV)": "Preuzimaj video dio Live fotografija (.MOV)",
    "DELETING": "BRISANJE",
    "Deleted on the phone → delete on this PC too": "Obrisano na telefonu → obriši i na PC-u",
    "The file goes to the Recycle Bin, where it can be restored.": "Fajl ide u Recycle Bin, odakle se može vratiti.",
    "Deleted on this PC → delete in iCloud too": "Obrisano na PC-u → obriši i u iCloud-u",
    "The photo goes to 'Recently Deleted' on the phone and can be restored for 30 days.":
        "Slika ide u 'Nedavno obrisano' na telefonu i može se vratiti 30 dana.",
    "Send deletions on this PC right away (watch the folder)": "Brisanje na PC-u šalji odmah (prati folder)",
    "Ask before deleting more than": "Traži potvrdu ako se briše više od",
    "Bring back photos I deleted only on this PC": "Vrati slike koje sam obrisao samo na PC-u",
    "Downloads again the photos deleted on this PC while deleting in iCloud was off.":
        "Ponovo preuzima slike koje su obrisane na PC-u dok je brisanje u iCloud-u bilo isključeno.",
    "SYSTEM": "SISTEM",
    "Start with Windows": "Pokreni sa Windows-om",
    "Show notifications": "Prikazuj obavještenja",
    "Language": "Jezik",
    "Save": "Sačuvaj",
    "{app} {version} · settings are kept in the 'data' folder next to the app.":
        "{app} {version} · postavke su u folderu 'data' pored aplikacije.",
    " The app is in {service}, so the sign-in and database are kept only on this PC ({path}).":
        " Program je u {service}-u, pa se prijava i baza čuvaju samo na ovom računaru ({path}).",
    "Choose a folder for photos": "Izaberi folder za slike",
    "Sign out of iCloud? Photos on this PC stay.": "Odjaviti se sa iCloud-a? Slike na PC-u ostaju.",
    "Photos deleted only on this PC will be downloaded again.": "Slike obrisane samo na PC-u biće ponovo preuzete.",
    "Photos folder": "Folder za slike",
    "Use this folder anyway?": "Ipak koristiti ovaj folder?",
    "New folder": "Novi folder",
    "You changed the photos folder.\n\nPhotos won't be moved: the new location is filled from iCloud, "
    "and the old folder stays untouched and is no longer synced. Continue?":
        "Promijenio si folder za slike.\n\nSlike se neće premještati: nova lokacija se puni iz iCloud-a, "
        "a stari folder ostaje netaknut i više se ne sinhronizuje. Nastaviti?",
    "Restart to change the language?": "Ponovo pokrenuti program da se promijeni jezik?",
    # pending dialog ----------------------------------------------------------------
    "Confirm deletion – {app}": "Potvrda brisanja – {app}",
    "A large deletion is waiting for your OK": "Veliko brisanje čeka potvrdu",
    "For safety, when more items than allowed in Settings are deleted at once, Skyfold asks first. "
    "Nothing is deleted permanently.":
        "Radi sigurnosti, kad se odjednom briše više stavki nego što je dozvoljeno u postavkama, "
        "Skyfold prvo pita. Ništa se ne briše trajno.",
    "{n} photos were deleted in iCloud": "{n} slika je obrisano u iCloud-u",
    "{n} files or folders were deleted in iCloud Drive": "Obrisano na iCloud Driveu (fajlova ili foldera): {n}",
    "{n} files or folders were deleted on this PC": "Obrisano na ovom PC-u (fajlova ili foldera): {n}",
    "{n} photos were deleted on this PC": "{n} slika je obrisano na PC-u",
    "Delete on this PC too": "Obriši i na PC-u",
    "Keep on this PC": "Zadrži na PC-u",
    "Delete in iCloud too": "Obriši i u iCloud-u",
    "Bring them back to this PC": "Vrati ih na PC",
    "Decide later": "Odluči kasnije",
    "… and {n} more": "… i još {n}",
    # first run -----------------------------------------------------------------------
    "Welcome – {app}": "Dobrodošao – {app}",
    "Where should your photos go?": "Gdje da čuvam slike?",
    "Choose the folder your iPhone and iPad gallery will sync to. It's separate from the program; "
    "pick a drive with enough space.":
        "Izaberi folder u koji će se sinhronizovati galerija sa iPhone-a i iPad-a. Folder je odvojen "
        "od programa; najbolje je na disku sa dovoljno prostora.",
    "Choose…": "Izaberi…",
    "HOW SHOULD PHOTOS BE ORGANISED?": "KAKO DA SLOŽIM SLIKE?",
    "An empty folder works best. Folder and layout can be changed later in Settings.":
        "Najbolje je izabrati prazan folder. Folder i raspored se kasnije mogu promijeniti u Postavkama.",
    "Later": "Kasnije",
    "Continue": "Nastavi",
    "Choose a folder for the gallery": "Izaberi folder za galeriju",
    "Can't create the folder: {error}": "Ne mogu napraviti folder: {error}",
    # marker file -----------------------------------------------------------------------
    "This folder is synced by Skyfold. Don't delete this file.\n":
        "Ovaj folder sinhronizuje Skyfold. Ne briši ovaj fajl.\n",
    # iCloud Drive & settings v2 ----------------------------------------------------------
    "A renamed or moved file is sent as a new file and the old one goes to Recently Deleted.":
        "Preimenovan ili premješten fajl šalje se kao novi, a stari ide u Nedavno obrisano.",
    "About": "O programu",
    "Changed on both sides, kept both: {name}": "Promijenjeno na obje strane, sačuvane obje verzije: {name}",
    "Checking iCloud Drive…": "Provjeravam iCloud Drive…",
    "Choose a folder for iCloud Drive": "Izaberi folder za iCloud Drive",
    "Couldn't create folder in iCloud ({name}): {error}": "Ne mogu napraviti folder u iCloud-u ({name}): {error}",
    "Couldn't resolve conflict ({name}): {error}": "Ne mogu riješiti konflikt ({name}): {error}",
    "DOWNLOAD": "PREUZIMANJE",
    "DRIVE FOLDER": "FOLDER ZA DRIVE",
    "Deleted in iCloud → delete on this PC too": "Obrisano u iCloud-u → obriši i na PC-u",
    "Drive folder is not available: {path}": "Drive folder nije dostupan: {path}",
    "Drive: deleted in iCloud: {name}": "Drive: obrisano u iCloud-u: {name}",
    "Drive: deleted on this PC (Recycle Bin): {name}": "Drive: obrisano na PC-u (u Recycle Bin): {name}",
    "Drive: downloaded {n} files": "Drive: preuzeto fajlova: {n}",
    "Drive: downloading {i} of {n}": "Drive: preuzimam {i} od {n}",
    "Drive: uploaded {n} files": "Drive: poslano fajlova: {n}",
    "Drive: uploading {i} of {n}": "Drive: šaljem {i} od {n}",
    "Enabled": "Uključeno",
    "File/folder clash, skipped: {name}": "Fajl i folder istog imena, preskočeno: {name}",
    "Files are downloaded to this PC; there are no online-only placeholders.":
        "Fajlovi se preuzimaju na ovaj PC; nema fajlova koji su samo na mreži.",
    "GOOD TO KNOW": "DOBRO JE ZNATI",
    "General": "Općenito",
    "Keeps a folder on this PC and your iCloud Drive the same, in both directions.":
        "Drži folder na ovom PC-u i tvoj iCloud Drive istim, u oba smjera.",
    "Reading iCloud Drive…": "Čitam iCloud Drive…",
    "Reading iCloud Drive… {n}": "Čitam iCloud Drive… {n}",
    "Settings: the 'data' folder next to the app.": "Postavke: folder 'data' pored aplikacije.",
    "Sign-in and sync database: {path}": "Prijava i baza sinhronizacije: {path}",
    "Skyfold is not made by Apple. It uses the same web services as iCloud.com.":
        "Skyfold nije Apple-ov program. Koristi iste web servise kao iCloud.com.",
    "The Drive folder and the photos folder must be separate.": "Drive folder i folder sa slikama moraju biti odvojeni.",
    "The Drive folder doesn't look like before (its sync marker is missing). "
    "Syncing has stopped so nothing gets deleted by mistake.":
        "Drive folder ne izgleda kao ranije (nedostaje oznaka sinhronizacije). "
        "Sinhronizacija je zaustavljena da se ništa ne bi obrisalo greškom.",
    "The app is in {service}, so the sign-in and database are kept only on this PC.":
        "Program je u {service}-u, pa se prijava i baza čuvaju samo na ovom računaru.",
    "The file goes to 'Recently Deleted' in iCloud Drive and can be restored for 30 days.":
        "Fajl ide u 'Nedavno obrisano' u iCloud Drive-u i može se vratiti 30 dana.",
    "Upload failed ({name}): {error}": "Slanje nije uspjelo ({name}): {error}",
    "Uploaded: {n}": "Poslano: {n}",
    "Version {version}": "Verzija {version}",
    "WHERE YOUR DATA IS": "GDJE SU TVOJI PODACI",
    "When a file changed on both sides, both versions are kept; the PC one gets \"(PC conflict …)\" in its name.":
        "Kad se fajl promijeni na obje strane, čuvaju se obje verzije; ona s PC-a dobije \"(PC conflict …)\" u imenu.",
    "You changed the folder.\n\nFiles won't be moved: the new location is filled from iCloud, and the old folder "
    "stays untouched and is no longer synced. Continue?":
        "Promijenio si folder.\n\nFajlovi se neće premještati: nova lokacija se puni iz iCloud-a, a stari folder "
        "ostaje netaknut i više se ne sinhronizuje. Nastaviti?",
    "iCloud Drive": "iCloud Drive",
    "iCloud Drive synced": "iCloud Drive sinhronizovan",
    "iCloud Drive: finished with {n} errors. Will retry.": "iCloud Drive: završeno uz {n} grešaka. Pokušaću ponovo.",
    "{n} min": "{n} min",
    "Off · click to turn on": "Isključeno · klikni da uključiš",
    "checked {when}": "provjereno {when}",
    "first read pending": "čeka prvo čitanje",
    "first sync in progress": "prva sinhronizacija u toku",
    "{files} files · {size}": "{files} fajlova · {size}",
    "{photos} photos · {videos} videos · {size}": "{photos} slika · {videos} videa · {size}",
    # Notes -----------------------------------------------------------------------------
    "Notes": "Bilješke",
    "Apple Notes": "Apple bilješke",
    "{n} notes": "{n} bilješki",
    "NOTES FOLDER": "FOLDER ZA BILJEŠKE",
    "Choose a folder for your notes": "Izaberi folder za bilješke",
    "Keeps a copy of all your notes on this PC as web pages, updated automatically. "
    "Editing stays on your iPhone, iPad or Mac.":
        "Čuva kopiju svih bilješki na ovom PC-u kao web stranice, automatski ažurirane. "
        "Uređivanje ostaje na iPhone-u, iPad-u ili Mac-u.",
    "Open Notes.html in that folder to browse and search all notes in your web browser.":
        "Otvori Notes.html u tom folderu za pregled i pretragu svih bilješki u web pregledniku.",
    "Each note is an HTML file with its pictures and attachments, in the same folders as in Notes.":
        "Svaka bilješka je HTML fajl sa slikama i prilozima, u istim folderima kao u Notes-u.",
    "Notes deleted on your devices are moved to the Recycle Bin here.":
        "Bilješke obrisane na uređajima ovdje idu u Recycle Bin.",
    "Locked notes can't be read; they are only listed.": "Zaključane bilješke se ne mogu pročitati; samo su navedene.",
    "The notes folder must be separate from the other folders.": "Folder za bilješke mora biti odvojen od ostalih foldera.",
    "Notes folder is not available: {path}": "Folder za bilješke nije dostupan: {path}",
    "The Notes folder doesn't look like before (its sync marker is missing). "
    "Syncing has stopped so nothing gets deleted by mistake.":
        "Folder za bilješke ne izgleda kao ranije (nedostaje oznaka sinhronizacije). "
        "Sinhronizacija je zaustavljena da se ništa ne bi obrisalo greškom.",
    "Reading Apple Notes…": "Čitam Apple bilješke…",
    "Downloading note {i} of {n}": "Preuzimam bilješku {i} od {n}",
    "Couldn't export note ({name}): {error}": "Ne mogu izvesti bilješku ({name}): {error}",
    "Notes: downloaded {n}": "Bilješke: preuzeto {n}",
    "Notes: downloaded {name}": "Bilješke: preuzeto „{name}”",
    "Notes: removed {name}": "Bilješke: uklonjeno {name}",
    "Note has no content": "Bilješka nema sadržaj",
    "Untitled": "Bez naslova",
    "Search notes…": "Pretraži bilješke…",
    "locked – open it on your iPhone": "zaključano – otvori na iPhone-u",
    "Updated {when}": "Ažurirano {when}",
    # albums, storage, Find My ---------------------------------------------------------
    "Album folders couldn't be updated: {error}": "Folderi albuma nisu ažurirani: {error}",
    "Album folders use no extra disk space. They mirror iCloud; deleting there doesn't delete photos.":
        "Folderi albuma ne zauzimaju dodatni prostor. Prate iCloud; brisanje u njima ne briše slike.",
    "Show albums as folders (Albums\…)": "Prikaži albume kao foldere (Albums\…)",
    "Updating album folders…": "Ažuriram foldere albuma…",
    "iCloud storage": "iCloud prostor",
    "{used} of {total}": "{used} od {total}",
    "Find My devices…": "Pronađi uređaje (Find My)…",
    "Find My": "Find My",
    "Find My – {app}": "Find My – {app}",
    "Refresh": "Osvježi",
    "Close": "Zatvori",
    "Map": "Karta",
    "Play sound": "Pusti zvuk",
    "Sending…": "Šaljem…",
    "Sound sent ✓": "Zvuk poslan ✓",
    "online": "na mreži",
    "offline": "van mreže",
    "located {when}": "lociran {when}",
    "Locating your devices…": "Tražim tvoje uređaje…",
    "No devices found.": "Nema pronađenih uređaja.",
    "Sign in first.": "Prvo se prijavi.",
    "Theme": "Tema",
    "System": "Sistemska",
    "Light": "Svijetla",
    "Dark": "Tamna",
    "Not enough disk space: {need} to download, {free} free on {drive}. "
    "Free up space or choose a folder on another disk in Settings.":
        "Nema dovoljno mjesta na disku: treba preuzeti {need}, a slobodno je {free} na {drive}. "
        "Oslobodi prostor ili u Postavkama izaberi folder na drugom disku.",
    "All of your iCloud Drive will be downloaded to {folder}.": "Cijeli tvoj iCloud Drive biće preuzet u {folder}.",
    "{free} free on {disk}. Large Drives can take a while to download.":
        "Slobodno {free} na {disk}. Preuzimanje velikog Drive-a može potrajati.",
    "Not enough space for iCloud Drive on {disk}.": "Nema dovoljno mjesta za iCloud Drive na {disk}.",
    "Your iCloud Drive holds up to about {size}, but only {free} is free there. "
    "Choose a folder on a disk with more space.":
        "Tvoj iCloud Drive ima do oko {size}, a tamo je slobodno samo {free}. "
        "Izaberi folder na disku s više prostora.",
    "About {size} will be downloaded from iCloud Drive.": "Iz iCloud Drive-a biće preuzeto oko {size}.",
    "Folder: {folder}\n{free} free on {disk}, {left} left afterwards.\n\n"
    "The first download of a large Drive can take a few hours.":
        "Folder: {folder}\nSlobodno {free} na {disk}, poslije ostaje {left}.\n\n"
        "Prvo preuzimanje velikog Drive-a može trajati nekoliko sati.",
    "Turn on": "Uključi",
    "All notes": "Sve bilješke",
    "Light / dark": "Svijetlo / tamno",
    "Collapse all": "Skupi sve",
    "Expand all": "Raširi sve",
    "The location opens in Google Maps in your browser. Lost device? Use iCloud.com for Lost Mode or erasing.":
        "Lokacija se otvara u Google Maps-u u pregledniku. Izgubljen uređaj? Na iCloud.com uključi "
        "Način izgubljenog uređaja ili brisanje.",
    "Open the original PDF": "Otvori originalni PDF",
}

_CATALOGS = {"bs": BS}
