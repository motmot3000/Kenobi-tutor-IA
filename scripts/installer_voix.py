#!/usr/bin/env python3
"""Installe la voix locale de Kenobi : Piper (parler) et faster-whisper (entendre), sur cet ordinateur.

    python3 scripts/installer_voix.py            # installe, ou complète une installation
    python3 scripts/installer_voix.py --verifier # redit et réécoute une phrase d'essai

Bibliothèque standard seulement. Tout va dans un dossier du compte (`~/.local/share/kenobi/voice` sous Linux),
hors du dépôt : un environnement Python à part, la voix française et le modèle de transcription. Environ 1,1 Go.
Rien d'autre n'est modifié sur l'ordinateur ; supprimer ce dossier désinstalle la voix.

Versions figées, vérifiées ensemble le 2026-11-12 (Python 3.13) : faster-whisper 1.2.1 demande PyAV 15.1.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PACKAGES = ("piper-tts==1.8.0", "faster-whisper==1.2.1", "av==15.1.0", "huggingface-hub==1.33.0")
VOICE = "fr_FR-upmc-medium"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/upmc/medium/"
VOICE_FILES = ((VOICE + ".onnx", 76_733_615), (VOICE + ".onnx.json", None))
WHISPER_MODELS = ("base", "small", "medium", "large-v3-turbo")
TEST_SENTENCE = "Bonjour, je suis Kenobi, ton tuteur."
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_dir():
    override = os.environ.get("KENOBI_VOICE_DIR")
    if override:
        return Path(os.path.expanduser(override))
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "kenobi" / "voice"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi" / "voice"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "kenobi" / "voice"


class Failure(Exception):
    pass


class Reporter:
    def __init__(self, as_json):
        self.as_json = as_json

    def step(self, step, label, percent=None):
        if self.as_json:
            print(json.dumps({"step": step, "label": label, "percent": percent}, ensure_ascii=False), flush=True)
        else:
            print(f"[{percent if percent is not None else '··':>3} %] {label}", flush=True)


def venv_python(root):
    return root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(arguments, failure, timeout=3600):
    try:
        result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure(failure) from None
    if result.returncode != 0:
        tail = " ".join((result.stdout or "").strip().splitlines()[-2:])[-260:]
        raise Failure(f"{failure} {tail}".strip())
    return result.stdout


def create_venv(root, report):
    python = venv_python(root)
    if python.is_file():
        return python
    report.step("venv", "Création de l'environnement Python de la voix", 4)
    uv = shutil.which("uv")
    try:
        run([sys.executable, "-m", "venv", str(root / "venv")], "Création de l'environnement impossible.")
    except Failure:
        # Certaines distributions livrent Python sans le module venv complet : uv sait s'en passer.
        if not uv:
            raise Failure("Création de l'environnement impossible : installe le paquet python3-venv "
                          "(Debian, Ubuntu : sudo apt install python3-venv), ou uv.") from None
        shutil.rmtree(root / "venv", ignore_errors=True)
        run([uv, "venv", str(root / "venv")], "Création de l'environnement impossible avec uv.")
    return python


def install_packages(python, report):
    report.step("pip", "Installation de Piper et de Whisper (≈ 500 Mo)", 10)
    has_pip = subprocess.run([str(python), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL).returncode == 0
    uv = shutil.which("uv")
    if has_pip:
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES],
            "Installation des paquets impossible.")
    elif uv:
        run([uv, "pip", "install", "--python", str(python), *PACKAGES], "Installation des paquets impossible.")
    else:
        raise Failure("Ni pip ni uv dans l'environnement : installe python3-venv, puis relance.")


def download(url, target, expected, report, label, start, end):
    if target.is_file() and (expected is None or target.stat().st_size == expected):
        return
    temporary = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Kenobi/1.0"}), timeout=60) as response:
            total = int(response.headers.get("Content-Length") or expected or 0)
            done, last = 0, 0.0
            with open(temporary, "wb") as output:
                while True:
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    output.write(block)
                    done += len(block)
                    if total and time.monotonic() - last > 0.7:
                        last = time.monotonic()
                        report.step("voice", f"{label} ({done // 1_000_000} / {total // 1_000_000} Mo)",
                                    round(start + (end - start) * done / total))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise Failure(f"Téléchargement impossible : {url}") from None
    if expected is not None and temporary.stat().st_size != expected:
        temporary.unlink(missing_ok=True)
        raise Failure("Téléchargement de la voix incomplet : relance l'installation.")
    os.replace(temporary, target)


def download_whisper(python, root, model, report):
    report.step("whisper", f"Téléchargement du modèle de transcription « {model} »", 62)
    code = ("import sys\nfrom faster_whisper import WhisperModel\n"
            "WhisperModel(sys.argv[1], device='cpu', compute_type='int8', download_root=sys.argv[2])\n")
    run([str(python), "-c", code, model, str(root / "models" / "whisper")],
        "Téléchargement du modèle de transcription impossible.")


def smoke_test(python, root, model):
    """Dit une phrase avec Piper, la fait transcrire par Whisper : la preuve que la voix marche de bout en bout."""
    wav = root / "essai.wav"
    process = subprocess.Popen(
        [str(python), str(PROJECT_ROOT / "voice_worker.py"), "--voice", str(root / "models" / "piper" / (VOICE + ".onnx")),
         "--whisper", model, "--whisper-root", str(root / "models" / "whisper")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        ready = json.loads(process.stdout.readline() or "{}")
        if not ready.get("ready"):
            raise Failure("La voix locale ne démarre pas.")

        def ask(payload):
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline() or "{}")

        spoken = ask({"op": "tts", "text": TEST_SENTENCE, "path": str(wav), "speaker": "pierre"})
        heard = ask({"op": "stt", "path": str(wav), "language": "fr"})
        if not spoken.get("ok") or not heard.get("ok") or not heard.get("text"):
            raise Failure("L'essai de la voix a échoué.")
        return {"speakers": sorted(ready.get("speakers") or {}), "tts_seconds": spoken.get("took"),
                "stt_seconds": heard.get("took"), "heard": heard.get("text")}
    except (OSError, ValueError):
        raise Failure("L'essai de la voix a échoué.") from None
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=10)
        except Exception:
            process.kill()
        wav.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Installe la voix locale de Kenobi (Piper + faster-whisper).")
    parser.add_argument("--dir", type=Path, default=None, help="dossier d'installation")
    parser.add_argument("--whisper", choices=WHISPER_MODELS, default="small",
                        help="modèle de transcription : base (léger), small (défaut), medium ou large-v3-turbo (plus justes, plus lents)")
    parser.add_argument("--verifier", action="store_true", help="essayer la voix déjà installée, sans rien télécharger")
    parser.add_argument("--json", action="store_true", help="avancement en lignes JSON (pour l'interface)")
    args = parser.parse_args(argv)
    root = (args.dir or default_dir()).expanduser().resolve()
    report = Reporter(args.json)
    try:
        if args.verifier:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            result = smoke_test(Path(manifest["python"]), root, manifest["whisper"]["model"])
            report.step("done", f"Voix locale en état : « {result['heard']} »", 100)
            return 0
        if sys.version_info < (3, 9):
            raise Failure("Python 3.9 ou plus récent est nécessaire.")
        (root / "models" / "piper").mkdir(parents=True, exist_ok=True)
        (root / "models" / "whisper").mkdir(parents=True, exist_ok=True)
        python = create_venv(root, report)
        install_packages(python, report)
        report.step("voice", "Téléchargement de la voix française (77 Mo)", 48)
        for name, size in VOICE_FILES:
            download(VOICE_URL + name, root / "models" / "piper" / name, size, report, "Voix française", 48, 60)
        download_whisper(python, root, args.whisper, report)
        report.step("test", "Essai : dire une phrase et la réécouter", 94)
        result = smoke_test(python, root, args.whisper)
        manifest = {
            "version": 1,
            "installedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "python": str(python),
            "packages": list(PACKAGES),
            "piper": {"voice": VOICE, "file": f"models/piper/{VOICE}.onnx", "speakers": result["speakers"]},
            "whisper": {"model": args.whisper, "root": "models/whisper"},
            "test": {"tts_seconds": result["tts_seconds"], "stt_seconds": result["stt_seconds"]},
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.step("done", f"Voix locale prête dans {root}", 100)
        return 0
    except Failure as failure:
        report.step("error", str(failure), None)
        return 1
    except (OSError, ValueError, KeyError):
        report.step("error", "Installation de la voix interrompue par une erreur inattendue.", None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
