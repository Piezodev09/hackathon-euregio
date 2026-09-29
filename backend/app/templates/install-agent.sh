#!/bin/sh
# Installiert den Agenten der Smart Bicycle Box auf einem Raspberry Pi (Raspberry Pi OS / Debian).
#
# Empfohlen (Skript vorher prüfen) – die genauen Befehle zeigt das Portal beim Einrichten an:
#   curl -fsSk --pinnedpubkey 'sha256//…' -o bike-ca.crt __BASE_URL__/install/server.crt   # nur bei eigenem Zertifikat
#   curl -fsSLO --cacert bike-ca.crt __BASE_URL__/install/agent.sh
#   sha256sum agent.sh            # mit der Prüfsumme im Portal vergleichen
#   sudo sh agent.sh --code XXXXX-XXXXX --ca-file bike-ca.crt
#
# Das Skript lädt das Agent-Paket (Version __VERSION__) von der Plattform, prüft dessen SHA-256,
# legt einen eingeschränkten Systembenutzer an, koppelt das Gerät per Einmal-Code mit seinen Stellplätzen
# und richtet einen gehärteten systemd-Dienst ein. Es ist wiederholbar (idempotent): mit einem neuen Code
# erneut ausgeführt, kommen weitere Stellplätze an diesem Pi dazu.
#
# Hardware wird automatisch erkannt: Arduino (auch mehrere, je Stellplatz einer), NFC-Leser am Arduino (PN532),
# USB-Leser im Tastaturmodus, PC/SC-Leser (z. B. ACR122U), Kamera. Nur tatsächlich benötigte Pakete werden
# installiert. Mit Desktop wird die Anzeige (Chromium im Kiosk-Modus) automatisch gestartet (--no-kiosk: nicht).
set -eu

BASE_URL="__BASE_URL__"
VERSION="__VERSION__"
SHA256="__SHA256__"
# Schlüssel-Pin des Plattform-Zertifikats (leer = öffentlich vertrauenswürdiges Zertifikat).
PIN="__PIN__"

CODE="${BIKE_ENROLL_CODE:-}"
SOURCE="serial"
SERIAL_PORT="auto"
CA_FILE=""
PREFIX="/opt/bike-agent"
ETC_DIR="/etc/bike-agent"
STATE_DIR="/var/lib/bike-agent"
AGENT_USER="bike-agent"
NO_SYSTEMD=0
ALLOW_HTTP=0
NAME=""
KIOSK=1
NO_COLOR_OPT=0

usage() {
  cat <<USAGE
Aufruf: sudo sh agent.sh --code XXXXX-XXXXX [Optionen]
  --code CODE          Kopplungscode aus dem Portal (alternativ Umgebungsvariable BIKE_ENROLL_CODE)
  --source serial|simulator   Datenquelle (Standard: serial = Arduino per USB)
  --serial-port PFAD   z. B. /dev/ttyACM0 (Standard: automatisch erkennen)
  --ca-file DATEI      Zertifikat der Plattform (selbst signiert); ohne Angabe wird es per Pin geholt
  --pin sha256//…      Schlüssel-Pin des Plattform-Zertifikats (Standard: im Skript hinterlegt)
  --name NAME          Gerätename (Standard: Hostname)
  --prefix DIR         Installationsverzeichnis (Standard: $PREFIX)
  --etc-dir DIR        Konfigurationsverzeichnis (Standard: $ETC_DIR)
  --state-dir DIR      Zustandsverzeichnis (Standard: $STATE_DIR)
  --no-systemd         keinen Dienst einrichten (Tests, Container)
  --no-kiosk           Anzeige am Pi-Bildschirm nicht automatisch starten
  --allow-http         unverschlüsseltes HTTP erlauben (NUR lokale Entwicklung)
  --no-color           Ausgabe ohne Farben (auch über NO_COLOR=1)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --code) CODE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --ca-file) CA_FILE="$2"; shift 2 ;;
    --pin) PIN="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --etc-dir) ETC_DIR="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --no-kiosk) KIOSK=0; shift ;;
    --allow-http) ALLOW_HTTP=1; shift ;;
    --no-color) NO_COLOR_OPT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------- Ausgabe (Farben/Symbole wie im Portal)
# Zustände immer mit Symbol + Wort + Farbe; ohne Terminal, mit NO_COLOR oder --no-color schlicht und ohne Farben.
UI_COLOR=1
[ -t 1 ] || UI_COLOR=0
[ -n "${NO_COLOR:-}" ] && UI_COLOR=0
[ "${TERM:-dumb}" = "dumb" ] && UI_COLOR=0
[ "$NO_COLOR_OPT" -eq 1 ] && UI_COLOR=0
UI_UTF=0
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in *UTF-8*|*utf-8*|*UTF8*|*utf8*) UI_UTF=1 ;; esac
UI_LEVEL=8
case "${COLORTERM:-}" in truecolor|24bit) UI_LEVEL=24 ;; *) case "${TERM:-}" in *256color*) UI_LEVEL=256 ;; esac ;; esac
export UI_COLOR UI_UTF UI_LEVEL

ESC=$(printf '\033')
if [ "$UI_COLOR" -eq 1 ]; then
  B="${ESC}[1m"; D="${ESC}[2m"; R="${ESC}[0m"
  case "$UI_LEVEL" in
    24) P="${ESC}[38;2;42;165;174m"; G="${ESC}[38;2;46;170;95m"; E="${ESC}[38;2;235;87;75m"; Y="${ESC}[38;2;245;183;0m" ;;
    256) P="${ESC}[38;5;37m"; G="${ESC}[38;5;35m"; E="${ESC}[38;5;167m"; Y="${ESC}[38;5;214m" ;;
    *) P="${ESC}[36m"; G="${ESC}[32m"; E="${ESC}[31m"; Y="${ESC}[33m" ;;
  esac
else
  B=""; D=""; R=""; P=""; G=""; E=""; Y=""
fi
if [ "$UI_UTF" -eq 1 ]; then OK="✓"; NO="✗"; SKIP="–"; RUN="…"; else OK="[OK]"; NO="[X]"; SKIP="[-]"; RUN="..."; fi

STEP=0
STEPS=9
STEP_TEXT=""
STEP_T0=0
IN_STEP=0
LOG=""
UI_PY=""

now_ms() { t=$(date +%s%N 2>/dev/null || date +%s); case "$t" in *N) echo $(( ${t%N} * 1000 )) ;; *) echo $(( t / 1000000 )) ;; esac; }
elapsed() { awk -v a="$STEP_T0" -v b="$(now_ms)" 'BEGIN { s = (b - a) / 1000; if (s < 0.1) s = 0.1; printf "%.1f s", s }' | tr . ,; }

step_begin() {
  STEP=$((STEP + 1)); STEP_TEXT="$1"; STEP_T0=$(now_ms); IN_STEP=1
  if [ "$UI_COLOR" -eq 1 ]; then printf '  %s%s%s %s%s[%d/%d]%s %s' "$P" "$RUN" "$R" "$D" "" "$STEP" "$STEPS" "$R" "$STEP_TEXT"
  else printf '  [%d/%d] %s ... ' "$STEP" "$STEPS" "$STEP_TEXT"; fi
}
step_end() {  # $1 Symbol+Farbe, $2 Wort, $3 Zusatz
  IN_STEP=0
  if [ "$UI_COLOR" -eq 1 ]; then
    printf '\r%s[K  %s %s[%d/%d]%s %s  %s%s%s\n' "$ESC" "$1" "$D" "$STEP" "$STEPS" "$R" "$STEP_TEXT" "$D" "$3" "$R"
  else
    printf '%s%s\n' "$2" "${3:+ ($3)}"
  fi
}
step_ok() { step_end "${G}${OK}${R}" "OK" "${1:+$1 · }$(elapsed)"; }
step_skip() {
  if [ "$UI_COLOR" -eq 1 ]; then step_end "${D}${SKIP}${R}" "" "übersprungen${1:+: $1}"
  else step_end "" "übersprungen" "$1"; fi
}
skip_step() { step_begin "$1"; step_skip "$2"; }

# Befehl ausführen, Ausgabe nur ins Protokoll
run() { "$@" >>"$LOG" 2>&1; }

# Kästen und Abschlusskarte (Breite in Zeichen, Umbruch, Farben) übernimmt ein kleines Python-Programm
ui_box() { if [ -n "$UI_PY" ] && [ -f "$UI_PY" ]; then python3 "$UI_PY" "$@"; else shift; for l in "$@"; do printf '  %s\n' "${l#*:}"; done; fi; }

die() {  # $1 Meldung, $2 optional Hinweis
  if [ "$IN_STEP" -eq 1 ]; then step_end "${E}${NO}${R}" "FEHLER" "fehlgeschlagen"; fi
  printf '\n' >&2
  set -- "$1" "${2:-}"
  if [ -n "$LOG" ] && [ -s "$LOG" ]; then
    TAIL=$(tail -n 12 "$LOG" | cut -c1-200 | sed 's/^/log:/')
    (
      set -f; IFS='
'
      # shellcheck disable=SC2086
      ui_box err "Installation abgebrochen" "err:$1" ${2:+"hint:$2"} "" "dim:Protokoll: $LOG" "dim:Letzte Zeilen:" $TAIL >&2
    )
  else
    ui_box err "Installation abgebrochen" "err:$1" ${2:+"hint:$2"} >&2
  fi
  exit 1
}

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1
[ "$NO_SYSTEMD" -eq 1 ] || [ "$IS_ROOT" -eq 1 ] || die "Bitte mit sudo ausführen." "sudo sh agent.sh --code …"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM
if [ "$IS_ROOT" -eq 1 ] && [ "$NO_SYSTEMD" -eq 0 ]; then LOG=/var/log/bike-agent-install.log; else LOG="${TMPDIR:-/tmp}/bike-agent-install.log"; fi
: > "$LOG" 2>/dev/null || LOG="$TMP/install.log"
chmod 600 "$LOG" 2>/dev/null || true
printf 'Smart Bicycle Box – Installation %s, Agent %s, Plattform %s\n' "$(date '+%F %T')" "$VERSION" "$BASE_URL" >> "$LOG"

if command -v python3 >/dev/null 2>&1; then
  UI_PY="$TMP/ui.py"
  cat > "$UI_PY" <<'UIPY'
import json, os, shutil, sys, textwrap, unicodedata

COLOR = os.environ.get("UI_COLOR") == "1"
UTF = os.environ.get("UI_UTF") == "1"
LEVEL = os.environ.get("UI_LEVEL", "8")
PAL = {"24": {"p": "38;2;42;165;174", "g": "38;2;46;170;95", "e": "38;2;235;87;75", "y": "38;2;245;183;0"},
       "256": {"p": "38;5;37", "g": "38;5;35", "e": "38;5;167", "y": "38;5;214"},
       "8": {"p": "36", "g": "32", "e": "31", "y": "33"}}[LEVEL]


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if COLOR and s else s


def width(s):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


SYM = {"ok": ("✓" if UTF else "[OK]", "g"), "err": ("✗" if UTF else "[X]", "e"), "warn": ("!" if UTF else "[!]", "y"),
       "skip": ("–" if UTF else "[-]", None)}
H, V, TL, TR, BL, BR = ("─", "│", "╭", "╮", "╰", "╯") if UTF else ("-", "|", "+", "+", "+", "+")


def render(line, inner):
    """'kind:text' -> Liste (sichtbarer Text, farbiger Text) passend umbrochen."""
    kind, sep, text = line.partition(":")
    if kind not in ("ok", "err", "warn", "skip", "hint", "h", "dim", "log", "brand"):
        kind, text = "", line
    if kind in SYM:
        sym, col = SYM[kind]
        prefix, plain_prefix = (c(PAL[col], sym) if col else c("2", sym)) + " ", sym + " "
    elif kind == "hint":
        prefix, plain_prefix = c(PAL["y"], "→") + " " if UTF else "-> ", ("→ " if UTF else "-> ")
    else:
        prefix = plain_prefix = ""
    avail = max(10, inner - width(plain_prefix))
    num = text[:3] if len(text) > 3 and text[0].isdigit() and text[1:3] == ". " else ""
    parts = textwrap.wrap(text, avail, subsequent_indent=" " * len(num)) or [""]
    out = []
    for i, part in enumerate(parts):
        vis = (plain_prefix if i == 0 else " " * width(plain_prefix)) + part
        styled = part
        if kind == "h":
            styled = c("1", part)
        elif kind in ("dim", "log", "skip"):
            styled = c("2", part)
        elif kind == "err":
            styled = c("1;" + PAL["e"], part)
        elif kind == "brand":
            styled = c("1", part)
        out.append((vis, (prefix if i == 0 else " " * width(plain_prefix)) + styled))
    return out


def box(style, title, lines):
    cols = shutil.get_terminal_size((80, 24)).columns
    inner = min(68, max(40, cols - 6))
    border = {"brand": PAL["p"], "ok": PAL["g"], "err": PAL["e"]}.get(style, PAL["p"])
    rows = []
    if style == "brand":
        mark = c(PAL["y"], "▣" if UTF else "#")
        rows.append(("# " + title if not UTF else "▣ " + title, mark + " " + c("1", title)))
    else:
        sym = SYM["ok" if style == "ok" else "err"][0]
        rows.append((sym + " " + title, c("1;" + border, sym + " " + title)))
    for line in lines:
        rows.extend(render(line, inner))
    print(c(border, TL + H * (inner + 2) + TR))
    for vis, styled in rows:
        pad = inner - width(vis)
        print(c(border, V) + " " + styled + " " * max(0, pad) + " " + c(border, V))
    print(c(border, BL + H * (inner + 2) + BR))


def summary():
    e = os.environ
    lines = ["h:Stellplätze"]
    stalls = [s for s in e.get("SUM_STALLS", "").split("\n") if s]
    lines += [f"ok:{s}" for s in stalls] or ["warn:Keine Stellplätze gemeldet – Ausgabe im Protokoll prüfen"]
    lines += ["", "h:Hardware"]
    if e.get("SUM_SOURCE") == "simulator":
        lines.append("skip:Simulator – keine Hardware nötig, Daten werden als SIMULATION gekennzeichnet")
    else:
        try:
            hw = json.loads(e.get("SUM_HW") or "{}")
        except ValueError:
            hw = {}
        ports = hw.get("ports", [])
        for p in ports:
            name = p.get("path", "").replace("/dev/serial/by-id/", "")
            lines.append(f"ok:Arduino ({p.get('kind', '?')}) {name}")
        if not ports:
            lines.append("warn:Kein Arduino gefunden – per USB anschließen (bis dahin STATUS UNBEKANNT)")
        for r in hw.get("readers", []):
            lines.append(f"ok:NFC-Leser {r.get('name', '')} ({r.get('kind', '').upper()})")
        if not hw.get("readers"):
            lines.append("skip:Kein externer NFC-Leser (PN532 am Arduino genügt)")
        cam = hw.get("camera", "none")
        lines.append(f"ok:Kamera: {cam}" if cam not in ("none", "", None) else "skip:Keine Kamera (optional)")
    lines.append(f"ok:Anzeige-Autostart: {e['SUM_KIOSK']}" if e.get("SUM_KIOSK", "nein") != "nein"
                 else "skip:Anzeige-Autostart: nicht eingerichtet (kein Desktop oder --no-kiosk)")
    lines += ["", "h:Dienst"]
    svc = e.get("SUM_SERVICE", "")
    lines.append({"ok": "ok:bike-agent läuft", "fail": "err:bike-agent läuft nicht – siehe unten",
                  "": "skip:ohne systemd installiert (--no-systemd)"}.get(svc, "skip:" + svc))
    lines += ["", "h:Lokale Anzeige", "http://127.0.0.1:8088/local", "", "h:So geht's weiter",
              "1. Portal → Gateways: der Pi erscheint nach etwa einer Minute als online",
              "2. Portal → Lesegeräte → Karte anlernen",
              "3. Weitere Stellplätze: neuen Code erzeugen und dieses Skript erneut ausführen", ""]
    if e.get("SUM_ROOT") == "1":
        lines.append("dim:Hilfe: bike-agent status · bike-agent doctor · bike-agent hardware")
    lines.append(f"dim:Protokoll: {e.get('SUM_LOG', '')}")
    box("ok" if svc != "fail" else "err", "Fertig – Gateway eingerichtet" if svc != "fail" else "Eingerichtet, aber der Dienst läuft nicht", lines)


if __name__ == "__main__":
    if sys.argv[1] == "summary":
        summary()
    else:
        box(sys.argv[1], sys.argv[2], sys.argv[3:])
UIPY
fi

# ---------------------------------------------------------------------- Kopf
case "$SOURCE" in serial|simulator) ;; *) die "--source muss serial oder simulator sein" ;; esac
printf '\n'
ui_box brand "Smart Bicycle Box" "dim:Gateway-Installation · Agent $VERSION" "dim:Plattform: $BASE_URL"
printf '\n'

# ---------------------------------------------------------------------- 1. Voraussetzungen
step_begin "Voraussetzungen prüfen"
case "$BASE_URL" in
  https://*) ;;
  http://127.0.0.1*|http://localhost*) ;;
  http://*) [ "$ALLOW_HTTP" -eq 1 ] || die "Plattform-URL ist nicht HTTPS." "Nur für Entwicklung: --allow-http" ;;
  *) die "Ungültige Plattform-URL" ;;
esac
command -v python3 >/dev/null 2>&1 || die "python3 fehlt." "sudo apt install python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 oder neuer erforderlich." "Raspberry Pi OS Bookworm oder neuer verwenden."
if [ -z "$CODE" ]; then
  if [ -t 0 ]; then
    IN_STEP=0; printf '\n'
    printf '  %sKopplungscode aus dem Portal:%s ' "$B" "$R"
    read -r CODE
    STEP=$((STEP - 1)); step_begin "Voraussetzungen prüfen"
  else
    die "Kein Kopplungscode angegeben." "sudo sh agent.sh --code XXXXX-XXXXX"
  fi
fi
step_ok "Python $(python3 -c 'import platform; print(platform.python_version())')"

# Angeschlossene USB-Geräte (Hersteller:Produkt) – für die Paketauswahl, ohne lsusb
usb_ids() { for d in /sys/bus/usb/devices/*; do [ -r "$d/idVendor" ] && printf '%s:%s\n' "$(cat "$d/idVendor")" "$(cat "$d/idProduct")"; done 2>/dev/null || true; }
HAS_PCSC=0
# ACS (ACR122U u. ä.), SCM/Identiv, HID Omnikey, Feitian: PC/SC-Leser
if usb_ids | grep -Eq '^(072f|04e6|076b|096e):'; then HAS_PCSC=1; fi
HAS_USB_CAM=0
if ls /dev/video* >/dev/null 2>&1 && ! command -v rpicam-still >/dev/null 2>&1 && ! command -v libcamera-still >/dev/null 2>&1; then HAS_USB_CAM=1; fi

# ---------------------------------------------------------------------- 2. Pakete
if [ "$IS_ROOT" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
  step_begin "Pakete installieren (nur was diese Hardware braucht)"
  PKGS="ca-certificates"
  [ "$SOURCE" = "serial" ] && PKGS="$PKGS python3-serial"
  [ "$HAS_PCSC" -eq 1 ] && PKGS="$PKGS pcscd python3-pyscard"
  [ "$HAS_USB_CAM" -eq 1 ] && PKGS="$PKGS fswebcam"
  command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || PKGS="$PKGS curl"
  # shellcheck disable=SC2086
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q $PKGS \
    || { run apt-get update -q && run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q $PKGS; } \
    || die "Pakete konnten nicht installiert werden." "Internetverbindung des Pi prüfen, dann erneut ausführen."
  if [ "$HAS_PCSC" -eq 1 ]; then
    # Der Kernel-NFC-Treiber würde den Leser sonst vor pcscd belegen.
    printf 'blacklist pn533_usb\nblacklist pn533\nblacklist nfc\n' > /etc/modprobe.d/bike-agent-pcsc.conf
    modprobe -r pn533_usb pn533 nfc 2>/dev/null || true
    run systemctl enable --now pcscd.socket || true
  fi
  step_ok "$(echo "$PKGS" | wc -w | tr -d ' ') Paket(e)"
else
  skip_step "Pakete installieren" "kein apt oder ohne root"
fi

# ---------------------------------------------------------------------- 3. Systembenutzer
if [ "$IS_ROOT" -eq 1 ]; then
  step_begin "Systembenutzer $AGENT_USER"
  if ! id "$AGENT_USER" >/dev/null 2>&1; then
    run useradd --system --no-create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$AGENT_USER" \
      || die "Systembenutzer konnte nicht angelegt werden."
    NEWUSER="angelegt"
  else
    NEWUSER="vorhanden"
  fi
  # dialout: Arduino; input: USB-Leser im Tastaturmodus; video: Kamera
  for g in dialout input video; do usermod -a -G "$g" "$AGENT_USER" 2>/dev/null || true; done
  step_ok "$NEWUSER, Gruppen dialout/input/video"
else
  skip_step "Systembenutzer" "ohne root"
fi

# ---------------------------------------------------------------------- 4. Zertifikat
if [ -z "$CA_FILE" ] && [ -n "$PIN" ] && [ "${BASE_URL#https://}" != "$BASE_URL" ]; then
  step_begin "Plattform-Zertifikat per Pin prüfen"
  command -v curl >/dev/null 2>&1 || die "Für das angeheftete Zertifikat wird curl benötigt." "sudo apt install curl"
  run curl -fsSk --pinnedpubkey "$PIN" -o "$TMP/bike-ca.crt" "$BASE_URL/install/server.crt" \
    || die "Zertifikat passt nicht zum Pin – falscher Server?" "Befehle im Portal neu anzeigen lassen (Gateway einrichten) und genau so ausführen."
  CA_FILE="$TMP/bike-ca.crt"
  step_ok "Schlüssel stimmt"
elif [ -n "$CA_FILE" ]; then
  step_begin "Plattform-Zertifikat"
  [ -r "$CA_FILE" ] || die "Zertifikatsdatei $CA_FILE nicht lesbar."
  step_ok "$CA_FILE"
else
  skip_step "Plattform-Zertifikat" "öffentlich vertrauenswürdig"
fi

# ---------------------------------------------------------------------- 5. Herunterladen und prüfen
step_begin "Agent $VERSION herunterladen und prüfen"
URL="$BASE_URL/install/agent.tar.gz"
if command -v curl >/dev/null 2>&1; then
  if [ -n "$CA_FILE" ]; then run curl -fsSL --proto '=https,http' --cacert "$CA_FILE" -o "$TMP/agent.tar.gz" "$URL"
  else run curl -fsSL -o "$TMP/agent.tar.gz" "$URL"; fi
else
  if [ -n "$CA_FILE" ]; then run wget -q --ca-certificate="$CA_FILE" -O "$TMP/agent.tar.gz" "$URL"
  else run wget -q -O "$TMP/agent.tar.gz" "$URL"; fi
fi || die "Download fehlgeschlagen." "Netzwerk und Adresse der Plattform prüfen: $BASE_URL"
ACTUAL=$(sha256sum "$TMP/agent.tar.gz" | cut -d' ' -f1)
[ "$ACTUAL" = "$SHA256" ] || die "Prüfsumme stimmt nicht – Paket verändert oder unvollständig." "Skript neu herunterladen; erwartet ${SHA256%"${SHA256#????????????}"}…, erhalten ${ACTUAL%"${ACTUAL#????????????}"}…"
step_ok "SHA-256 ${SHA256%"${SHA256#????????????}"}…"

# ---------------------------------------------------------------------- 6. Installieren
step_begin "Installieren nach $PREFIX"
REL="$PREFIX/releases/$VERSION"
mkdir -p "$REL" "$ETC_DIR" "$STATE_DIR"
run tar -xzf "$TMP/agent.tar.gz" -C "$REL" --no-same-owner || die "Entpacken fehlgeschlagen."
ln -sfn "$REL" "$PREFIX/current.new" && mv -Tf "$PREFIX/current.new" "$PREFIX/current" 2>/dev/null \
  || { rm -f "$PREFIX/current"; ln -s "$REL" "$PREFIX/current"; rm -f "$PREFIX/current.new"; }
[ -n "$CA_FILE" ] && cp "$CA_FILE" "$ETC_DIR/ca.crt"
if [ "$IS_ROOT" -eq 1 ]; then
  # Der Agent darf nur Releases (Selbst-Update) und seinen Zustand schreiben.
  chown -R "$AGENT_USER:$AGENT_USER" "$PREFIX/releases" "$STATE_DIR"
  chown -h "$AGENT_USER:$AGENT_USER" "$PREFIX" "$PREFIX/current"
  chown root:"$AGENT_USER" "$ETC_DIR"
  chmod 750 "$ETC_DIR" "$STATE_DIR"
  cat > /usr/local/bin/bike-agent <<WRAP
#!/bin/sh
exec sudo -u $AGENT_USER python3 $PREFIX/current/agent.py --state-dir $STATE_DIR "\$@"
WRAP
  chmod 755 /usr/local/bin/bike-agent
fi
step_ok "Version $VERSION"

# ---------------------------------------------------------------------- 7. Koppeln
step_begin "Mit den Stellplätzen koppeln"
ENROLL_ARGS="--url $BASE_URL --source $SOURCE --serial-port $SERIAL_PORT"
[ -n "$CA_FILE" ] && ENROLL_ARGS="$ENROLL_ARGS --ca-file $ETC_DIR/ca.crt"
[ "$ALLOW_HTTP" -eq 1 ] && ENROLL_ARGS="$ENROLL_ARGS --allow-http"
[ -n "$NAME" ] && ENROLL_ARGS="$ENROLL_ARGS --name $NAME"
RUN_AS=""
[ "$IS_ROOT" -eq 1 ] && RUN_AS="runuser -u $AGENT_USER --"
# Code über die Umgebung statt als Argument übergeben (nicht in der Prozessliste sichtbar).
export BIKE_ENROLL_CODE="$CODE"
# shellcheck disable=SC2086
if ! $RUN_AS python3 "$PREFIX/current/agent.py" --state-dir "$STATE_DIR" enroll $ENROLL_ARGS > "$TMP/enroll.out" 2>&1; then
  cat "$TMP/enroll.out" >> "$LOG"
  unset BIKE_ENROLL_CODE
  if grep -q "invalid_or_expired_code" "$TMP/enroll.out"; then
    die "Kopplungscode ungültig, bereits benutzt oder abgelaufen (30 Minuten)." "Im Portal einen neuen Code erzeugen und das Skript erneut ausführen."
  fi
  die "Kopplung fehlgeschlagen." "Plattform erreichbar? Uhrzeit des Pi korrekt? Details im Protokoll."
fi
unset BIKE_ENROLL_CODE
cat "$TMP/enroll.out" >> "$LOG"
# „Gekoppelt: Stellplatz 'Name' (…)“ (ab 1.4) bzw. „Gekoppelt mit Station 'Name' …“ (ältere Agents)
STALLS=$(sed -n "s/^Gekoppelt[^']*'\(.*\)' (.*$/\1/p" "$TMP/enroll.out")
N_STALLS=$(printf '%s\n' "$STALLS" | grep -c . || true)
if [ "$N_STALLS" -eq 1 ]; then step_ok "1 Stellplatz"; else step_ok "$N_STALLS Stellplätze"; fi

# ---------------------------------------------------------------------- 8. Dienst
SERVICE=""
if [ "$NO_SYSTEMD" -eq 0 ]; then
  step_begin "Dienst einrichten und starten"
  cat > /etc/systemd/system/bike-agent.service <<UNIT
[Unit]
Description=Smart Bicycle Box - Agent (Gateway)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$AGENT_USER
Group=$AGENT_USER
SupplementaryGroups=dialout input video
ExecStart=/usr/bin/python3 $PREFIX/current/agent.py --state-dir $STATE_DIR run
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=$STATE_DIR $PREFIX
CapabilityBoundingSet=
DeviceAllow=char-ttyACM rw
DeviceAllow=char-ttyUSB rw
DeviceAllow=char-input r
DeviceAllow=char-video4linux rw
DeviceAllow=char-media rw
DeviceAllow=char-dma_heap rw

[Install]
WantedBy=multi-user.target
UNIT
  run systemctl daemon-reload
  run systemctl enable bike-agent.service
  run systemctl restart bike-agent.service  # neue Stellplätze sofort übernehmen
  # Läuft er auch nach ein paar Sekunden noch? (Restart=always würde einen Absturz verdecken)
  SERVICE="fail"
  i=0
  while [ $i -lt 10 ]; do
    sleep 1; i=$((i + 1))
    if [ $i -ge 3 ] && systemctl is-active --quiet bike-agent.service; then SERVICE="ok"; break; fi
  done
  if [ "$SERVICE" = "ok" ]; then step_ok "läuft"
  else
    step_end "${E}${NO}${R}" "FEHLER" "läuft nicht"
    journalctl -u bike-agent -n 20 --no-pager >> "$LOG" 2>&1 || true
  fi
else
  skip_step "Dienst einrichten" "--no-systemd"
fi

# ---------------------------------------------------------------------- 9. Anzeige am Pi-Bildschirm
# Chromium im Kiosk-Modus beim Anmelden des Desktop-Benutzers starten
KIOSK_DONE="nein"
BROWSER=$(command -v chromium-browser || command -v chromium || true)
USER_HOME=""
[ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
if [ "$KIOSK" -eq 1 ] && [ "$IS_ROOT" -eq 1 ] && [ -n "$BROWSER" ] && [ -n "$USER_HOME" ] && [ -d "$USER_HOME" ]; then
  step_begin "Anzeige-Autostart (Chromium, Vollbild)"
  AUTOSTART="$USER_HOME/.config/autostart"
  mkdir -p "$AUTOSTART"
  cat > "$AUTOSTART/bike-display.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Smart Bicycle Box – Anzeige
Exec=$BROWSER --kiosk --noerrdialogs --disable-infobars --no-first-run --incognito http://127.0.0.1:8088/local
X-GNOME-Autostart-enabled=true
DESKTOP
  # Wayfire (ältere Raspberry Pi OS Bookworm) liest XDG-Autostart nicht immer: zusätzlich eintragen
  if [ -f "$USER_HOME/.config/wayfire.ini" ] && ! grep -q "bike-display" "$USER_HOME/.config/wayfire.ini"; then
    printf '\n[autostart]\nbike-display = %s --kiosk --noerrdialogs --disable-infobars --no-first-run --incognito http://127.0.0.1:8088/local\n' "$BROWSER" >> "$USER_HOME/.config/wayfire.ini"
  fi
  chown -R "$SUDO_USER" "$AUTOSTART" 2>/dev/null || true
  KIOSK_DONE="ja, nach dem nächsten Anmelden am Desktop"
  step_ok "für $SUDO_USER"
elif [ "$KIOSK" -eq 0 ]; then
  skip_step "Anzeige-Autostart" "--no-kiosk"
else
  skip_step "Anzeige-Autostart" "kein Desktop/Chromium gefunden"
fi

# ---------------------------------------------------------------------- Zusammenfassung
HW=""
[ "$SOURCE" = "serial" ] && HW=$($RUN_AS python3 "$PREFIX/current/hardware.py" --json 2>>"$LOG" || true)
printf '\n'
SUM_STALLS="$STALLS" SUM_HW="$HW" SUM_SOURCE="$SOURCE" SUM_KIOSK="$KIOSK_DONE" SUM_SERVICE="$SERVICE" SUM_ROOT="$IS_ROOT" SUM_LOG="$LOG" \
  ui_box summary || {
  printf 'Fertig. Stellplätze:\n%s\nLokale Anzeige: http://127.0.0.1:8088/local\nProtokoll: %s\n' "$STALLS" "$LOG"; }
if [ "$SERVICE" = "fail" ]; then
  printf '\n  %sLetzte Meldungen des Dienstes:%s\n' "$B" "$R"
  journalctl -u bike-agent -n 8 --no-pager 2>/dev/null | sed 's/^/    /' || true
  exit 1
fi
printf '\n'
exit 0
