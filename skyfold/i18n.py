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
    forms = _FORMS.get(_lang, {}).get(text)
    if forms and kw:
        s = forms(kw, s) if callable(forms) else _form(kw.get(forms[0]), forms[1], forms[2], s)
    return Text(s.format(**kw) if kw else s)  # marked as Skyfold's own wording (see safelog)


def _count(value) -> int | None:
    """The number a count was given as: 7, or "1,284" / "1.284" / "1 284" as the app writes them."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits and len(digits) < 16 else None


def _form(value, one: str | None, few: str | None, many: str) -> str:
    """The wording for this number. Bosnian has three: 1, 21, 31… / 2-4, 22-24… / 5-20, 25-30…
    (11-14 always take the last); English has two (1 / everything else)."""
    n = _count(value)
    if n is None:
        return many
    if _lang == "bs":
        if n % 10 == 1 and n % 100 != 11:
            return one or many
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return few or many
        return many
    return one if (n == 1 and one) else many


def _word(n, one: str, few: str, many: str) -> str:
    return _form(n, one, few, many)


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
    "Drive: updated: {name}": "Drive: ažurirano: {name}",
    "Drive: uploaded: {name}": "Drive: poslano: {name}",
    "{name} already exists; not replaced": "{name} već postoji; nije zamijenjen",
    "Couldn't read the Drive folder's sync marker: {error}": "Oznaka Drive foldera se ne može pročitati: {error}",
    "This Drive folder was used before and it isn't clear by which Apple ID. Choose a new, empty folder for iCloud Drive in Settings.":
        "Ovaj Drive folder je ranije korišten i nije jasno s kojim Apple ID-om. U Postavkama izaberi novi, prazan folder za iCloud Drive.",
    "{name} leads outside the photos folder; left alone": "{name} vodi izvan foldera za slike; ostavljeno kako jeste",
    "{name} leads outside the notes folder; left alone": "{name} vodi izvan foldera za bilješke; ostavljeno kako jeste",
    "The Notes folder already has a Notes.html that isn't from Skyfold. Choose an empty folder for Notes in Settings.":
        "Folder za bilješke već ima Notes.html koji nije od Skyfolda. U Postavkama izaberi prazan folder za bilješke.",
    "Notes.html in the Notes folder wasn't written by this Skyfold; it is left as it is. Move or delete it to get a new index.":
        "Notes.html u folderu za bilješke nije napisao ovaj Skyfold; ostavljen je kako jeste. Premjesti ga ili obriši da dobiješ novi indeks.",
    "Couldn't check the sync marker in {path}: {error}. Syncing has stopped so nothing gets changed by mistake; it is tried again next time.":
        "Oznaka sinhronizacije u {path} se ne može provjeriti: {error}. Sinhronizacija je zaustavljena da se ništa ne promijeni greškom; pokušava se ponovo sljedeći put.",
    "it appeared while it was being checked": "pojavila se dok se provjeravala",
    "it isn't an ordinary file": "nije običan fajl",
    "Could not open sign-in: {error}": "Nije moguće otvoriti prijavu: {error}",
    "Waiting for Apple… this may take a few minutes. You can close this window; it will return when Apple answers. Cancel stops sign-in.":
        "Čekanje na Apple… to može potrajati i nekoliko minuta. Možeš zatvoriti ovaj prozor; ponovo će se pojaviti kad Apple odgovori. Otkaži prekida prijavu.",
    "Apple didn't let the sign-in start right now (this happens after several attempts in a short time). Wait "
    "about 15 minutes, then try again.":
        "Apple trenutno nije dozvolio da prijava počne (to se dešava nakon više pokušaja u kratkom roku). "
        "Pričekaj oko 15 minuta, pa pokušaj ponovo.",
    "Waiting for Apple… this can take up to a minute. Keep this window open: the code is asked for here.":
        "Čeka se Apple… to može potrajati do jedne minute. Ne zatvaraj ovaj prozor: ovdje se upisuje kod.",
    "Skyfold found a database journal without its database and couldn't keep a copy of it:\n{details}"
    "\n\nSkyfold doesn't start, so it isn't overwritten. Restart Windows and try again, or move "
    "that file somewhere else yourself.":
        "Skyfold je našao dnevnik baze bez same baze i nije mogao sačuvati njegovu kopiju:\n{details}"
        "\n\nSkyfold se ne pokreće, da se taj fajl ne bi prepisao. Ponovo pokreni Windows i pokušaj opet, "
        "ili sam premjesti taj fajl negdje drugo.",
    "Photos: {n} missing files are kept until {folder} can be read. Fix its access rights, or move that folder "
    "out of the photos folder.":
        "Fotografije: {n} fajlova koji nedostaju zadržano je dok se {folder} ne može pročitati. Popravi prava pristupa "
        "ili premjesti taj folder iz foldera s fotografijama.",
    "The sync database of the earlier installation couldn't be taken over. The earlier file is kept "
    "unchanged:\n{details}\n\nStart with a new database? (No: Skyfold closes now and tries again at the next start.)":
        "Baza sinhronizacije ranije instalacije nije mogla biti preuzeta. Raniji fajl je zadržan nepromijenjen:"
        "\n{details}\n\nPočeti s novom bazom? (Ne: Skyfold se sad zatvara i pokušava ponovo pri sljedećem pokretanju.)",
    "Photos: {n} missing files are kept for now, part of the photos folder couldn't be read":
        "Fotografije: {n} fajlova koji nedostaju zasad se ne diraju, dio foldera s fotografijama se ne može pročitati",
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
    "Deleted in iCloud; {name} stays on this PC only for files that aren't synced (it isn't created in iCloud again)":
        "Obrisano u iCloudu; {name} ostaje na ovom PC-u samo zbog fajlova koji se ne sinhronizuju (ne pravi se ponovo u iCloudu)",
    "Deleted on this PC; {name} stays in iCloud only for files that aren't synced (it isn't created on this PC again)":
        "Obrisano na ovom PC-u; {name} ostaje u iCloudu samo zbog fajlova koji se ne sinhronizuju (ne pravi se ponovo na ovom PC-u)",
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
    "CONNECTION DIAGNOSTICS": "DIJAGNOSTIKA VEZE",
    "For a bug report about syncing: records for {n} minutes what kind of request Skyfold sent to Apple and how Apple answered, never names, file names, addresses or passwords. The file stays on this PC until you attach it to a report.": "Za prijavu greške sa sinhronizacijom: {n} minuta bilježi kakve je zahtjeve Skyfold slao Appleu i kako je Apple odgovorio, nikad imena, nazive fajlova, adrese ni lozinke. Fajl ostaje na ovom računaru dok ga ne priložiš prijavi.",
    "Show the file": "Prikaži fajl",
    "Delete": "Obriši",
    "Recording until {time}.": "Bilježi do {time}.",
    "Not recording.": "Ne bilježi.",
    "Stop recording": "Zaustavi bilježenje",
    "Record for {n} minutes": "Bilježi {n} minuta",
    "Skyfold blocked a connection to an address it doesn't know. Check for a Skyfold update, then try again.": "Skyfold je blokirao vezu prema adresi koju ne poznaje. Provjeri ima li novije verzije Skyfolda, pa pokušaj ponovo.",
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
    "Upload send buffer": "Buffer za slanje",
    "Verification code": "Verifikacijski kod",
    "iCloud Photos can't be reached right now": "iCloud Photos trenutno nije dostupan",
    "iCloud Photos is busy; uploads continue later": "iCloud Photos je zauzet; slanje se nastavlja kasnije",
    "Uploads to iCloud Photos wait: {error}": "Slanje u iCloud Photos čeka: {error}",
    "Downloads are up to date. Uploads to iCloud Photos are waiting.":
        "Preuzimanja su ažurna. Slanje u iCloud Photos čeka.",
    "Uploads to iCloud Photos aren't available for this account right now":
        "Slanje u iCloud Photos trenutno nije dostupno za ovaj nalog",
    "Automatic (recommended)": "Automatski (preporučeno)",
    "Fixed 8 MB": "Fiksno 8 MB",
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
    "Drive: {name} is no longer synced; {n} files that aren't in iCloud were kept on this PC":
        "Drive: {name} se više ne sinhronizuje; {n} fajlova kojih nema u iCloudu ostalo je na računaru",
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
    # 1.1 Settings: cards, sentence-case group titles
    "Windows": "Windows",
    "Account": "Nalog",
    "Syncing": "Sinhronizacija",
    "Checking and speed": "Provjera i brzina",
    "Deleting": "Brisanje",
    "Photos folder": "Folder sa slikama",
    "Photos window": "Prozor Fotografije",
    "Downloading": "Preuzimanje",
    "Drive folder": "Folder za Drive",
    "Good to know": "Dobro je znati",
    "Notes folder": "Folder za bilješke",
    "Bookmarks file": "Fajl s oznakama",
    "Import it in your browser": "Uvezi ga u preglednik",
    "Where your data is": "Gdje su tvoji podaci",
    "Help": "Pomoć",
    "Connection diagnostics": "Dijagnostika veze",
    "On": "Uključeno",
    "of {total} used": "od {total} zauzeto",
    "{free} free": "{free} slobodno",
    "{free} free on this drive": "{free} slobodno na ovom disku",
    "iCloud · {size} plan": "iCloud · paket {size}",
    "Skyfold waits quietly in the system tray.": "Skyfold tiho čeka u system trayu.",
    "Follows Windows, or always light, dark or OLED.": "Prati Windows, ili je uvijek svijetla, tamna ili OLED.",
    "Forgets this PC's sign-in to iCloud. Your files stay where they are.":
        "Ovaj PC zaboravlja prijavu na iCloud. Tvoji fajlovi ostaju gdje jesu.",
    "Larger deletions wait for your OK.": "Veća brisanja čekaju tvoju potvrdu.",
    "Otherwise they are sent with the next check.": "Inače se šalju sa sljedećom provjerom.",
    "Start with Windows": "Pokreni sa Windows-om",
    "Show notifications": "Prikazuj obavještenja",
    "Notifications": "Obavještenja",
    "Skyfold's own": "Skyfold-ova",
    "Windows' own": "Windows-ova",
    "Off": "Isključeno",
    "Skyfold's own notifications follow the app's theme and stay away while Do Not Disturb is on or a program "
    "runs full screen.":
        "Skyfold-ova obavještenja prate temu aplikacije i ne pojavljuju se dok je uključeno Ne ometaj ili dok neki "
        "program radi preko cijelog ekrana.",
    "Open": "Otvori",
    "Show in folder": "Prikaži u folderu",
    "Notifications, {n} new": "Obavještenja, novih: {n}",
    "Back": "Nazad",
    "Clear all": "Očisti sve",
    "Empty this list. Nothing else changes.": "Isprazni ovu listu. Ništa drugo se ne mijenja.",
    "No notifications yet. The ones Skyfold shows while it runs are kept here.":
        "Još nema obavještenja. Ona koja Skyfold prikaže dok radi čuvaju se ovdje.",
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
    "Drive: uploading ": "Drive: šaljem ",
    "Drive: uploading, {i} of {n} files done": "Drive: šaljem, gotovo {i} od {n} fajlova",
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
    "Settings: {path}": "Postavke: {path}",
    "Some notes are still to come. They follow with the next sync.":
        "Još nekoliko bilješki je na putu. Stići će sa sljedećom sinhronizacijom.",
    "Couldn't read this folder. Close and open it again to try again.":
        "Ovaj folder se ne može pročitati. Zatvori ga i ponovo otvori da pokušaš opet.",
    "iCloud didn't list every folder. Folders that aren't shown keep their current setting.":
        "iCloud nije izlistao sve foldere. Folderi koji nisu prikazani zadržavaju trenutnu postavku.",
    "iCloud didn't list every folder, so this choice can't be saved now: it would leave out folders that aren't "
    "shown. Try again later.":
        "iCloud nije izlistao sve foldere, pa se ovaj izbor sada ne može sačuvati: izostavio bi foldere koji nisu "
        "prikazani. Pokušaj ponovo kasnije.",
    "Couldn't refresh: {error}": "Osvježavanje nije uspjelo: {error}",
    "Shown as of the last refresh at {time}.": "Prikazano stanje sa zadnjeg osvježavanja u {time}.",
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
    "stays untouched and is no longer synced. Files in the new folder are synced both ways: deleting one there "
    "deletes it in iCloud too. Continue?":
        "Promijenio si folder.\n\nFajlovi se neće premještati: nova lokacija se puni iz iCloud-a, a stari folder "
        "ostaje netaknut i više se ne sinhronizuje. Fajlovi u novom folderu se sinhronizuju u oba smjera: "
        "kad obrišeš jedan tamo, briše se i u iCloud-u. Nastaviti?",
    "iCloud Drive": "iCloud Drive",
    "iCloud Drive synced": "iCloud Drive sinhronizovan",
    "iCloud Drive: finished with {n} errors. Will retry.": "iCloud Drive: završeno uz {n} grešaka. Pokušaću ponovo.",
    "Notes: finished with {n} errors. Will retry.": "Bilješke: završeno uz {n} grešaka. Pokušaću ponovo.",
    "Skyfold {version} is published, but its download isn't complete yet. Try again later.":
        "Skyfold {version} je objavljen, ali preuzimanje još nije spremno. Pokušaj ponovo kasnije.",
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
    "Notes: moved {name} to {folder}": "Bilješke: „{name}” premještena u {folder}",
    "Notes: updated {name}": "Bilješke: ažurirana „{name}”",
    "Notes: renamed {old} to {name}": "Bilješke: „{old}” preimenovana u „{name}”",
    "Notes: moved {n} notes to other folders": "Bilješke: {n} premještenih u druge foldere",
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
    "OLED": "OLED (crna)",
    "Switch to OLED theme": "Prebaci na OLED temu",
    "Light / dark / OLED": "Svijetlo / tamno / OLED",
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
    "Upload photos and videos I add to this folder to iCloud Photos": "Šalji u iCloud slike i videe koje dodam u ovaj folder",
    "Uploading {name}…": "Šaljem {name}…",
    "Uploading {name}": "Šaljem {name}",
    "{n} files": "{n} fajlova",
    "Upload unconfirmed: {name}. Check iCloud Photos and add it there manually if missing.": "Slanje nije potvrđeno: {name}. Provjeri iCloud slike i dodaj fajl ručno ako ga nema.",
    "Upload failed ({name}): {error}": "Slanje nije uspjelo ({name}): {error}",
    "Already in iCloud Photos: {name}": "Već je u iCloud slikama: {name}",
    "Uploaded to iCloud Photos: {name}": "Poslano u iCloud slike: {name}",
    "Photos upload": "Slanje slika u iCloud",
    "The photos folder now holds another Skyfold library (e.g. another disk with the same drive letter, or an older copy). Syncing has stopped so nothing gets deleted by mistake; choose the folder again in Settings.":
        "U folderu za slike je sada druga Skyfold biblioteka (npr. drugi disk s istim slovom ili starija kopija). Sinhronizacija je zaustavljena da se ništa ne obriše greškom; ponovo izaberi folder u Postavkama.",
    "The photos folder is inside another Skyfold photos folder ({path}). Choose a folder outside it in Settings.":
        "Folder za slike je unutar drugog Skyfold foldera za slike ({path}). U Postavkama izaberi folder izvan njega.",
    "This photos folder belongs to another Apple ID. Choose another folder in Settings.":
        "Ovaj folder za slike pripada drugom Apple ID-u. U Postavkama izaberi drugi folder.",
    "{path} is another photos folder inside this one; move it out of the photos folder":
        "{path} je drugi folder za slike unutar ovoga; premjesti ga iz foldera za slike",
    "GitHub is limiting update checks from your internet connection right now; try again in {n} min.":
        "GitHub trenutno ograničava provjere ažuriranja s tvoje internet veze; pokušaj ponovo za {n} min.",
    "was {old}": "ranije {old}",
    "Favorites": "Favoriti",
    "Bookmarks Menu": "Meni oznaka",
    'Choose a folder for the bookmarks file': 'Izaberi folder za fajl s oznakama',
    "Couldn't read {name}: {error}": 'Ne mogu pročitati {name}: {error}',
    'Safari Bookmarks': 'Safari oznake',
    'Safari bookmarks': 'Safari oznake',
    'Bookmarks': 'Oznake',
    'Your Safari bookmarks from iPhone, iPad or Mac as a file that Firefox, Chrome and Edge can import, kept up to date. Read-only: nothing changes in iCloud.': 'Tvoje Safari oznake s iPhonea, iPada ili Maca kao fajl koji Firefox, Chrome i Edge mogu uvesti, uvijek ažuran. Samo čita: u iCloudu se ništa ne mijenja.',
    'BOOKMARKS FILE': 'FAJL S OZNAKAMA',
    'Skyfold keeps "Safari Bookmarks.html" in this folder up to date: every 6 hours and when you click Sync now.': 'Skyfold u ovom folderu drži ažuran fajl "Safari Bookmarks.html": svakih 6 sati i kad klikneš Sinhronizuj sada.',
    'IMPORT IT IN YOUR BROWSER': 'UVEZI GA U PREGLEDNIK',
    'Firefox: Ctrl+Shift+O → Import and Backup → Import Bookmarks from HTML…': 'Firefox: Ctrl+Shift+O → Uvoz i rezervna kopija → Uvezi oznake iz HTML-a…',
    'Chrome or Edge: Bookmarks → Import bookmarks and settings → Bookmarks HTML file.': 'Chrome ili Edge: Oznake → Uvezi oznake i postavke → HTML fajl s oznakama.',
    'Your bookmarks appear in a "Safari Bookmarks" folder. Importing again adds a second copy, so delete the old folder first.': 'Oznake se pojave u folderu "Safari Bookmarks". Ponovni uvoz dodaje još jednu kopiju, pa prvo obriši stari folder.',
    "One-way: bookmarks you add in your browser don't go to Safari.": 'Jednosmjerno: oznake koje dodaš u pregledniku ne idu u Safari.',
    'Deleted bookmarks, tab groups and the Reading List are left out.': 'Obrisane oznake, grupe kartica i Lista za čitanje se izostavljaju.',
    "Safari bookmarks can't be read right now ({code}).": 'Safari oznake se trenutno ne mogu pročitati ({code}).',
    "Safari bookmarks can't be read: iCloud answered {code}.": 'Safari oznake se ne mogu pročitati: iCloud je odgovorio {code}.',
    "Safari bookmarks can't be read: iCloud didn't allow it ({code}).": 'Safari oznake se ne mogu pročitati: iCloud to nije dozvolio ({code}).',
    "Safari bookmarks can't be read: too many pages.": 'Safari oznake se ne mogu pročitati: previše stranica.',
    'Safari bookmarks: {n} saved for Firefox and Chrome': 'Safari oznake: {n} sačuvano za Firefox i Chrome',
    "{name} wasn't written by Skyfold; it is left as it is. Move it or choose another folder for Safari bookmarks.": '{name} nije napravio Skyfold; ostaje kakav jeste. Premjesti ga ili izaberi drugi folder za Safari oznake.',
    "The attachment folder {name} leads outside the Notes folder; nothing is written there.":
        "Folder s prilozima {name} vodi izvan foldera s bilješkama; tamo se ništa ne upisuje.",
    "the update's build origin can't be confirmed": "porijeklo ažuriranja se ne može potvrditi",
    "the update address isn't one of GitHub's": "adresa ažuriranja nije GitHubova",
    "there isn't enough free disk space for the update": "nema dovoljno slobodnog prostora na disku za ažuriranje",
    "the published checksum can't be read": "objavljeni kontrolni zbir se ne može pročitati",
    "cancelled": "otkazano",
    "the download doesn't match its published checksum": "preuzeti fajl se ne slaže s objavljenim kontrolnim zbirom",
    "the download contains an unsafe path": "preuzeti fajl sadrži nesigurnu putanju",
    "the download isn't a Skyfold build": "preuzeti fajl nije Skyfold",
    "Check for new versions of Skyfold": "Provjeravaj nove verzije Skyfolda",
    "Check for updates": "Provjeri ažuriranja",
    "Update – {app}": "Ažuriranje – {app}",
    "Skyfold {version} is available": "Dostupan je Skyfold {version}",
    "You have version {version}.": "Ti imaš verziju {version}.",
    "What's new": 'Šta je novo',
    # the remaining windows in the 1.1 design (Drive folders, notifications list) ---------------
    "iCloud Drive folders": "Folderi na iCloud Driveu",
    "Today": "Danas",
    "Earlier": "Ranije",
    "No description for this version.": "Nema opisa za ovu verziju.",
    "Skyfold downloads the new version, checks it, closes and starts again. Your settings and synced files stay as they are.": "Skyfold skine novu verziju, provjeri je, zatvori se i ponovo pokrene. Tvoje postavke i sinhronizovani fajlovi ostaju kakvi jesu.",
    "Download the new version from GitHub and replace your Skyfold folder with it (settings are kept).": "Skini novu verziju s GitHuba i njome zamijeni svoj Skyfold folder (postavke ostaju).",
    "Update now": "Ažuriraj sada",
    "Open download page": "Otvori stranicu za preuzimanje",
    "Downloading…": "Preuzimam…",
    "Installing… Skyfold closes and starts again.": "Instaliram… Skyfold se zatvara i ponovo pokreće.",
    "The update couldn't be done: {error}": 'Ažuriranje nije uspjelo: {error}',
    "Downloading… {pct}%": "Preuzimam… {pct}%",
    "Update": "Ažuriraj",
    "Skyfold {version} is available.": "Dostupan je Skyfold {version}.",
    "You have the latest version of Skyfold ({version}).": "Imaš najnoviju verziju Skyfolda ({version}).",
    "Update available": "Dostupno ažuriranje",
    "Skyfold {version} is available. Click to see what's new.": 'Dostupan je Skyfold {version}. Klikni da vidiš šta je novo.',
    "Couldn't check for updates: {error}": 'Provjera ažuriranja nije uspjela: {error}',
    "Notes on this PC": "Bilješke na ovom računaru",
    "Saved to this PC": "Sačuvano na ovom računaru",
    "Folders": "Folderi",
    "Your library": "Tvoja biblioteka",
    "Your exported notes, ready to read. Changes in Apple Notes appear after the next sync.":
        "Tvoje bilješke, spremne za čitanje. Izmjene iz Apple bilješki stižu nakon sljedeće sinhronizacije.",
    "All your notes from iCloud, kept on this PC.": "Sve tvoje bilješke iz iClouda, sačuvane na ovom računaru.",
    "Search notes and folders": "Pretraži bilješke i foldere",
    "Clear search": "Obriši pretragu",
    "Skip to notes": "Preskoči na bilješke",
    "Edit your notes on your iPhone, iPad or Mac": "Bilješke uređuješ na iPhone-u, iPad-u ili Mac-u",
    "Turn on JavaScript to search and browse this note index.":
        "Uključi JavaScript da bi pretraživao i pregledao bilješke.",
    "No notes found": "Nema bilješki",
    "Try another title or folder name.": "Probaj drugi naslov ili ime foldera.",
    "Show all notes": "Prikaži sve bilješke",
    "Switch to dark theme": "Prebaci na tamnu temu",
    "Switch to light theme": "Prebaci na svijetlu temu",
    "{n} note": "{n} bilješka",  # 1, and 21, 31, 101 … in Bosnian
    "{n} notes (2-4)": "{n} bilješke",
    "{n} folder": "{n} folder",  # 1, and 21, 31, 101 … in Bosnian
    "{n} folders (2-4)": "{n} foldera",
    "{n} folders": "{n} foldera",
    "The location opens in Google Maps in your browser. Lost device? Use iCloud.com for Lost Mode or erasing.":
        "Lokacija se otvara u Google Maps-u u pregledniku. Izgubljen uređaj? Na iCloud.com uključi "
        "Način izgubljenog uređaja ili brisanje.",
    "Open the original PDF": "Otvori originalni PDF",
    # Notes window
    "Smaller text (Ctrl \u2212)": "Manji tekst (Ctrl \u2212)",
    "Back to normal size (Ctrl 0)": "Normalna veličina (Ctrl 0)",
    "Larger text (Ctrl +)": "Veći tekst (Ctrl +)",
    "Larger text": "Veći tekst",
    "Smaller text": "Manji tekst",
    "Normal size (now {n}%)": "Normalna veličina (sada {n}%)",
    "Full screen": "Cijeli ekran",
    "Exit full screen": "Izađi iz cijelog ekrana",
    "Sort notes": "Poredaj bilješke",
    "More": "Više",
    "This note can't be read right now. Skyfold tries again in a moment.":
        "Ova bilješka se trenutno ne može pročitati. Skyfold pokušava ponovo za trenutak.",
    "Theme of this window": "Tema ovog prozora",
    "As Skyfold": "Kao Skyfold",
    "Newest first": "Najnovije prvo",
    "Oldest first": "Najstarije prvo",
    "Title A–Z": "Naslov A–Ž",
    "Audio recording": "Audio snimak",
    "Video": "Video",
    "Open the PDF": "Otvori PDF",
    "Open the attachment": "Otvori prilog",
    "Attachment unavailable on this PC": "Prilog nije dostupan na ovom PC-u",
    "Show {n} more pages": "Prikaži još {n} stranica",
    "Locked note": "Zaključana bilješka",
    "Search notes (Ctrl+K)": "Pretraži bilješke (Ctrl+K)",
    "Search notes": "Pretraži bilješke",
    "Folders": "Folderi",
    "Note": "Bilješka",
    "Open in browser": "Otvori u pregledniku",
    "All notes": "Sve bilješke",
    "All notes in every folder, {n} notes": "Sve bilješke iz svih foldera, bilješki: {n}",
    "Too many large pictures to show here. Open the note in your browser to see this one.":
        "Previše velikih slika za prikaz ovdje. Otvori bilješku u pregledniku da vidiš ovu.",
    "{name}, {n} notes": "{name}, bilješki: {n}",
    "Searching titles now · indexing note text…": "Pretražujem naslove · indeksiram tekst bilješki…",
    "{n} found": "Pronađeno: {n}",
    "Notes are turned off in Settings.": "Bilješke su isključene u postavkama.",
    "Your notes will appear here after the first sync.": "Bilješke će se pojaviti ovdje nakon prve sinhronizacije.",
    "No notes found.": "Nema pronađenih bilješki.",
    "This note is locked. Skyfold can't read locked notes; open it on your iPhone, iPad or Mac.":
        "Ova bilješka je zaključana. Skyfold ne može čitati zaključane bilješke; otvori je na iPhoneu, iPadu ili Macu.",
    "This note isn't on this PC yet. It will be after the next sync.":
        "Ova bilješka još nije na ovom PC-u. Biće nakon sljedeće sinhronizacije.",
    "This note is too big to show here. Open it in your browser.":
        "Ova bilješka je prevelika za prikaz ovdje. Otvori je u pregledniku.",
    "Click Notes in the panel to read and search your notes in Skyfold. Notes.html in that folder shows them "
    "in your web browser.":
        "Klikni Bilješke u panelu da ih čitaš i pretražuješ u Skyfoldu. Notes.html u tom folderu ih prikazuje "
        "u web pregledniku.",
    # Photos window (1.1)
    '1 day': '1 dan',
    '1 item moved to the Recycle Bin': '1 stavka premještena u Koš',
    '1 month': '1 mjesec',
    '1 selected': '1 odabrana',
    'Added in the last 30 days, newest first': 'Dodano u zadnjih 30 dana, najnovije prvo',
    'Added to Favourites': 'Dodano u Omiljene',
    'Added to iCloud': 'Dodano u iCloud',
    'Albums': 'Albumi',
    'All Photos': 'Sve slike',
    'Click photos to select them': 'Klikni slike da ih odabereš',
    'Collections': 'Zbirke',
    'Copied 1 file: paste it into any folder': 'Kopiran 1 fajl: zalijepi ga u bilo koji folder',
    'Copied {n} files: paste them into any folder': 'Kopirano fajlova: {n}. Zalijepi ih u bilo koji folder',
    'Copy': 'Kopiraj',
    "Couldn't change the favourite in iCloud: {error}": 'Omiljena se nije mogla promijeniti u iCloudu: {error}',
    'Delete {name}?': 'Obrisati {name}?',
    'Delete {n} items?': 'Obrisati stavke ({n})?',
    'Favourite': 'Omiljena',
    'Favourites': 'Omiljene',
    'Info': 'Info',
    'Kind': 'Vrsta',
    'Larger': 'Veće',
    'Library': 'Biblioteka',
    'Live Photo': 'Live Photo',
    'Live Photo (photo and 3-second video)': 'Live Photo (slika i video od 3 sekunde)',
    'Live Photos': 'Live Photos',
    'Media types': 'Vrste',
    'Message': 'Poruka',
    'Months': 'Mjeseci',
    'My albums': 'Moji albumi',
    'Next': 'Sljedeća',
    'No photos here yet. They appear as soon as Skyfold has synced them.': 'Ovdje još nema slika. Pojavit će se čim ih Skyfold sinhronizuje.',
    'Not signed in to iCloud': 'Prijava na iCloud nije aktivna',
    'Nothing found': 'Ništa nije pronađeno',
    'Nothing here': 'Ovdje nema ničega',
    'On this PC': 'Na ovom PC-u',
    'Open': 'Otvori',
    'Photo': 'Slika',
    'Play': 'Pusti',
    'Play Live Photo': 'Pusti Live Photo',
    'Play video': 'Pusti video',
    'Previous': 'Prethodna',
    'Recently added': 'Nedavno dodano',
    'Remove from Favourites': 'Ukloni iz Omiljenih',
    'Removed from Favourites': 'Uklonjeno iz Omiljenih',
    'Screenshot': 'Snimak ekrana',
    'Screenshots': 'Snimci ekrana',
    'Search': 'Pretraga',
    'Search photos': 'Pretraži slike',
    'Search results': 'Rezultati pretrage',
    'Select': 'Odaberi',
    'Select all': 'Odaberi sve',
    'Select some photos first': 'Prvo odaberi neke slike',
    'Show in Explorer': 'Prikaži u Exploreru',
    'Show in folder': 'Prikaži u folderu',
    'Show or hide the sidebar': 'Prikaži ili sakrij bočnu traku',
    'Size': 'Veličina',
    'Smaller': 'Manje',
    'Taken': 'Snimljeno',
    'They go to the Recycle Bin on this PC and to Recently Deleted in iCloud, where they stay for 30 days.': 'Idu u Koš na ovom PC-u i u Recently Deleted u iCloudu, gdje ostaju 30 dana.',
    'They go to the Recycle Bin on this PC. They stay in iCloud (Settings → Photos).': 'Idu u Koš na ovom PC-u. U iCloudu ostaju (Postavke → Photos).',
    'This photo is no longer in iCloud': 'Ove slike više nema u iCloudu',
    "Windows can't show HEIC photos yet. Get Microsoft's free “HEIF Image Extensions” from the Microsoft Store, then open the photo again.": 'Windows još ne može prikazati HEIC slike. Instaliraj Microsoftov besplatni „HEIF Image Extensions“ iz Microsoft Storea, pa ponovo otvori sliku.',
    'Years': 'Godine',
    'and to Recently Deleted in iCloud': 'i u Recently Deleted u iCloudu',
    '{a} of {b}': '{a} od {b}',
    '{n} days': '{n} dana',
    '{n} items': '{n} stavki',
    '{n} items moved to the Recycle Bin': 'Stavke premještene u Koš: {n}',
    '{n} months': '{n} mjeseci',
    '{n} results': 'Rezultata: {n}',
    '{n} selected': 'Odabrano: {n}',
    'January': 'Januar',
    'February': 'Februar',
    'March': 'Mart',
    'April': 'April',
    'May': 'Maj',
    'June': 'Juni',
    'July': 'Juli',
    'August': 'August',
    'September': 'Septembar',
    'October': 'Oktobar',
    'November': 'Novembar',
    'December': 'Decembar',
    'Monday': 'Ponedjeljak',
    'Tuesday': 'Utorak',
    'Wednesday': 'Srijeda',
    'Thursday': 'Četvrtak',
    'Friday': 'Petak',
    'Saturday': 'Subota',
    'Sunday': 'Nedjelja',
    'This photo couldn\'t be opened. Is it still in the photos folder?': 'Ova slika se ne može otvoriti. Da li je još u folderu sa slikama?',
    'Still deleting the previous photos': 'Još se brišu prethodne slike',
    'Deleting {n} items…': 'Brišem {n} stavki…',
    'iCloud gets the deletion at the next sync.': 'U iCloudu se briše pri sljedećoj sinhronizaciji.',
    '{n} items couldn\'t be deleted: {error}': '{n} stavki nije obrisano: {error}',
    '{name} was moved or replaced since it was synced; it was left alone': '{name} je premješten ili zamijenjen nakon sinhronizacije, pa je ostavljen na miru',
    'Hidden in iCloud, kept on this PC: {name}': 'Sakriveno u iCloudu, ostaje na ovom PC-u: {name}',
    "Deleted in iCloud, kept on this PC (it can't go to the Recycle Bin there): {name}": 'Obrisano u iCloudu, ostaje na ovom PC-u (tamo ne može u Koš): {name}',
    "PHOTOS WINDOW": "PROZOR FOTOGRAFIJE",
    "Open the Photos window when I click Photos": "Otvori prozor Fotografije kad kliknem na Fotografije",
    "Off: a click on Photos opens the photos folder in File Explorer instead.": "Isključeno: klik na Fotografije otvara folder sa slikama u File Exploreru.",
    "Last checked": "Zadnja provjera",
    "Deletion needs your OK": "Brisanje čeka tvoju potvrdu",
    "Up to date": "Sve je ažurno",
    "Everything matches iCloud.": "Sve je usklađeno sa iCloudom.",
    "Your photos are in step with iCloud.": "Tvoje slike su usklađene sa iCloudom.",
    "Started": "Počelo",
    "now": "sada",
    "This update can't be checked, so it wasn't installed.": "Ovo ažuriranje se ne može provjeriti, pa nije instalirano.",
    "Delete them on this PC too? They go to the Recycle Bin, where they can be restored.":
        "Obrisati ih i na ovom PC-u? Idu u Recycle Bin, odakle se mogu vratiti.",
    "Delete them in iCloud too? They go to Recently Deleted, where they stay for 30 days.":
        "Obrisati ih i u iCloudu? Idu u Nedavno obrisano, gdje ostaju 30 dana.",
    "More than your limit ({n}) at once, so Skyfold asks first.":
        "Više od tvoje granice ({n}) odjednom, pa Skyfold prvo pita.",
    "More than your limit at once, so Skyfold asks first.": "Više od tvoje granice odjednom, pa Skyfold prvo pita.",
    "Show the list": "Prikaži listu",
    "Step {n} of {total}": "Korak {n} od {total}",
    "Your password is only used to sign in, with Apple's two-factor code. It is never stored.":
        "Lozinka se koristi samo za prijavu, uz Appleov kod za potvrdu. Nikad se ne čuva.",
    "Browse…": "Pretraži…",
    "One folder": "Jedan folder",
    "Before you start": "Prije nego što počneš",
    "Choose a folder on a drive with enough space. Skyfold keeps it in step with iCloud.":
        "Izaberi folder na disku s dovoljno prostora. Skyfold ga drži usklađenim sa iCloudom.",
    "Skyfold syncs both ways, deletions included.": "Skyfold sinhronizuje u oba smjera, uključujući brisanja.",
    "Skyfold is free software without any warranty (GNU GPL v3). You use it at your own risk, and its authors are "
    "not liable for lost or changed files. Keep your own backup of anything important.":
        "Skyfold je besplatan program bez ikakve garancije (GNU GPL v3). Koristiš ga na vlastitu odgovornost, a "
        "njegovi autori ne odgovaraju za izgubljene ili izmijenjene fajlove. Čuvaj vlastitu kopiju svega važnog.",
    "Hide the list": "Sakrij listu",
    "Needs your OK": "Čeka tvoju potvrdu",
    "Needs attention": "Treba pažnju",
    "New version": "Nova verzija",
    "at {time}": "u {time}",
    "{names} and {last} are in step with iCloud.": "{names} i {last} su usklađeni sa iCloudom.",
}

_CATALOGS = {"bs": BS}


# Wordings that change with the number in them: text -> (the count's name, the form for 1, the
# form for 2-4 (Bosnian only), or a function of the values for texts with two counts). The
# catalog itself holds the form for 5 and more (Bosnian) or for every other number (English).
def _photos_videos_bs(kw, _s):
    return (_word(kw.get("photos"), "{photos} slika", "{photos} slike", "{photos} slika") + " · "
            + _word(kw.get("videos"), "{videos} video", "{videos} videa", "{videos} videa") + " · {size}")


def _photos_videos_en(kw, s):
    return (_word(kw.get("photos"), "{photos} photo", None, "{photos} photos") + " · "
            + _word(kw.get("videos"), "{videos} video", None, "{videos} videos") + " · {size}")


_FORMS_BS = {
    "{n} deleted in iCloud": ("n", "{n} obrisan u iCloud-u", "{n} obrisana u iCloud-u"),
    "{n} deleted on this PC": ("n", "{n} obrisan na PC-u", "{n} obrisana na PC-u"),
    "{n} items are waiting for your OK to be deleted.": (
        "n", "Čeka se tvoja potvrda za brisanje {n} stavke.", "Čeka se tvoja potvrda za brisanje {n} stavke."),
    "Finished with {n} errors. Will retry.": (
        "n", "Završeno uz {n} grešku. Pokušaću ponovo.", "Završeno uz {n} greške. Pokušaću ponovo."),
    "iCloud Drive: finished with {n} errors. Will retry.": (
        "n", "iCloud Drive: završeno uz {n} grešku. Pokušaću ponovo.",
        "iCloud Drive: završeno uz {n} greške. Pokušaću ponovo."),
    "Notes: finished with {n} errors. Will retry.": (
        "n", "Bilješke: završeno uz {n} grešku. Pokušaću ponovo.", "Bilješke: završeno uz {n} greške. Pokušaću ponovo."),
    "Followed {n} files moved inside the folder": (
        "n", "Praćeno premještanje {n} fajla unutar foldera", "Praćeno premještanje {n} fajla unutar foldera"),
    "Photos: {n} missing files are kept until {folder} can be read. Fix its access rights, or move that folder "
    "out of the photos folder.": (
        "n", "Fotografije: {n} fajl koji nedostaje zadržan je dok se {folder} ne može pročitati. Popravi prava "
             "pristupa ili premjesti taj folder iz foldera s fotografijama.",
        "Fotografije: {n} fajla koja nedostaju zadržana su dok se {folder} ne može pročitati. Popravi prava "
        "pristupa ili premjesti taj folder iz foldera s fotografijama."),
    "Photos: {n} missing files are kept for now, part of the photos folder couldn't be read": (
        "n", "Fotografije: {n} fajl koji nedostaje zasad se ne dira, dio foldera s fotografijama se ne može pročitati",
        "Fotografije: {n} fajla koja nedostaju zasad se ne diraju, dio foldera s fotografijama se ne može pročitati"),
    "The first download is done: {n} files. From now on only changes are synced.": (
        "n", "Prvo preuzimanje je gotovo: {n} fajl. Od sada se sinhronizuju samo izmjene.",
        "Prvo preuzimanje je gotovo: {n} fajla. Od sada se sinhronizuju samo izmjene."),
    "The first sync is done: {n} files downloaded. From now on only changes are synced.": (
        "n", "Prva sinhronizacija je gotova: preuzet {n} fajl. Od sada se sinhronizuju samo izmjene.",
        "Prva sinhronizacija je gotova: preuzeta {n} fajla. Od sada se sinhronizuju samo izmjene."),
    "Drive: {name} is no longer synced; {n} files that aren't in iCloud were kept on this PC": (
        "n", "Drive: {name} se više ne sinhronizuje; {n} fajl kojeg nema u iCloudu ostao je na računaru",
        "Drive: {name} se više ne sinhronizuje; {n} fajla kojih nema u iCloudu ostala su na računaru"),
    "{prefix}{i} of {n} files": ("n", "{prefix}{i} od {n} fajla", "{prefix}{i} od {n} fajla"),
    "{n} photos were deleted in iCloud": ("n", "{n} slika je obrisana u iCloud-u", "{n} slike su obrisane u iCloud-u"),
    "{n} photos were deleted on this PC": ("n", "{n} slika je obrisana na PC-u", "{n} slike su obrisane na PC-u"),
    "Drive: uploading, {i} of {n} files done": (
        "n", "Drive: šaljem, gotovo {i} od {n} fajla", "Drive: šaljem, gotovo {i} od {n} fajla"),
    "{files} files · {size}": ("files", "{files} fajl · {size}", "{files} fajla · {size}"),
    "{photos} photos · {videos} videos · {size}": _photos_videos_bs,
    "Notes: moved {n} notes to other folders": (
        "n", "Bilješke: {n} premještena u drugi folder", "Bilješke: {n} premještene u druge foldere"),
    "{n} files": ("n", "{n} fajl", "{n} fajla"),
    "Safari bookmarks: {n} saved for Firefox and Chrome": (
        "n", "Safari oznake: {n} sačuvana za Firefox i Chrome", "Safari oznake: {n} sačuvane za Firefox i Chrome"),
    "{n} days": ("n", "{n} dan", "{n} dana"),
    "{n} items": ("n", "{n} stavka", "{n} stavke"),
    "{n} months": ("n", "{n} mjesec", "{n} mjeseca"),
    "{n} notes": ("n", "{n} bilješka", "{n} bilješke"),
    "Deleting {n} items…": ("n", "Brišem {n} stavku…", "Brišem {n} stavke…"),
    "{n} items couldn't be deleted: {error}": (
        "n", "{n} stavka nije obrisana: {error}", "{n} stavke nisu obrisane: {error}"),
}

_FORMS_EN = {
    "{n} items are waiting for your OK to be deleted.": ("n", "{n} item is waiting for your OK to be deleted.", None),
    "Finished with {n} errors. Will retry.": ("n", "Finished with {n} error. Will retry.", None),
    "iCloud Drive: finished with {n} errors. Will retry.": ("n", "iCloud Drive: finished with {n} error. Will retry.", None),
    "Notes: finished with {n} errors. Will retry.": ("n", "Notes: finished with {n} error. Will retry.", None),
    "Followed {n} files moved inside the folder": ("n", "Followed {n} file moved inside the folder", None),
    "Photos: {n} missing files are kept until {folder} can be read. Fix its access rights, or move that folder "
    "out of the photos folder.": (
        "n", "Photos: {n} missing file is kept until {folder} can be read. Fix its access rights, or move that "
             "folder out of the photos folder.", None),
    "Photos: {n} missing files are kept for now, part of the photos folder couldn't be read": (
        "n", "Photos: {n} missing file is kept for now, part of the photos folder couldn't be read", None),
    "The first download is done: {n} files. From now on only changes are synced.": (
        "n", "The first download is done: {n} file. From now on only changes are synced.", None),
    "The first sync is done: {n} files downloaded. From now on only changes are synced.": (
        "n", "The first sync is done: {n} file downloaded. From now on only changes are synced.", None),
    "Drive: {name} is no longer synced; {n} files that aren't in iCloud were kept on this PC": (
        "n", "Drive: {name} is no longer synced; {n} file that isn't in iCloud was kept on this PC", None),
    "{prefix}{i} of {n} files": ("n", "{prefix}{i} of {n} file", None),
    "{n} photos were deleted in iCloud": ("n", "{n} photo was deleted in iCloud", None),
    "{n} photos were deleted on this PC": ("n", "{n} photo was deleted on this PC", None),
    "Drive: uploading, {i} of {n} files done": ("n", "Drive: uploading, {i} of {n} file done", None),
    "{files} files · {size}": ("files", "{files} file · {size}", None),
    "{photos} photos · {videos} videos · {size}": _photos_videos_en,
    "Notes: moved {n} notes to other folders": ("n", "Notes: moved {n} note to another folder", None),
    "{n} days": ("n", "{n} day", None),
    "{n} items": ("n", "{n} item", None),
    "{n} months": ("n", "{n} month", None),
    "{n} notes": ("n", "{n} note", None),
    "Deleting {n} items…": ("n", "Deleting {n} item…", None),
    "{n} items couldn't be deleted: {error}": ("n", "{n} item couldn't be deleted: {error}", None),
}

_FORMS = {"bs": _FORMS_BS, "en": _FORMS_EN}
