# The voice call

« Appeler le tuteur » (top right) opens a voice line inside the current lesson: Kenobi's head listens, thinks and
speaks (its mouth follows its voice). You can interrupt it by speaking; if an answer takes time, it says so
(« Voyons voir. »). Every turn of the call joins the conversation, and code is shown on screen, not read aloud.

Kenobi needs **ears** (speech recognition) and **a voice** (speech synthesis). Settings › **Voix** offers two ways.

| | Whisper on your computer | A voice API key |
|---|---|---|
| Cost | free | paid by the provider (cents per call) |
| Privacy | your voice never leaves your computer | your voice is sent to the provider |
| Setup | one program to download and run, ~1.1 GB the first time | paste a key |
| Engines | Whisper (faster-whisper) to hear, Piper to speak (French voices Pierre and Jessica) | Mistral Voxtral, or ElevenLabs |

## Option 1 — Whisper on your computer (the voice bridge)

A web page cannot run Whisper by itself, so a small program does it on your computer: the **voice bridge**,
`kenobi-voice.py`. It installs Whisper and Piper once, then answers the page on `127.0.0.1` only.

**You need** Python 3.8 or later (`python3 --version`; on Windows install it from <https://www.python.org>, then use
`py`), about 1.5 GB of disk, and a few minutes for the first installation. No graphics card needed.

1. In Kenobi: Réglages › Voix › **« Whisper sur ton ordinateur »** › download **`kenobi-voice.py`**. The file
   carries the address of your Kenobi page, so the bridge knows which page to trust.
2. Open a terminal in your downloads folder and run it:

   ```bash
   cd ~/Downloads            # or Téléchargements, the folder where the file is
   python3 kenobi-voice.py   # Windows: py kenobi-voice.py
   ```

3. **The first time**, it tells you what it will install and where, and asks for confirmation (answer `o`):
   a separate Python environment with `faster-whisper` and `piper-tts`, the French Piper voice `fr_FR-upmc-medium`
   and the Whisper model `small`. Nothing else on your computer is changed.

   | System | Installed in |
   |---|---|
   | Linux | `~/.local/share/kenobi/voice` |
   | macOS | `~/Library/Application Support/kenobi/voice` |
   | Windows | `%LOCALAPPDATA%\kenobi\voice` |

4. Then the bridge starts and prints, last, a **pairing code** under « Code à coller dans Kenobi » (`8196-…`). The
   first time it also **opens Kenobi already paired**. Otherwise paste the code in Réglages › Voix, then
   « Enregistrer ».
5. « Écouter la voix » to check, then « Appeler le tuteur ».

Keep the bridge's window open while you call Kenobi; close it (Ctrl+C) when you are done. Next time, just run
`python3 kenobi-voice.py` again: it starts in a few seconds (the first sentence loads the models).

### Options

```text
python3 kenobi-voice.py [--whisper base|small|medium|large-v3-turbo] [--dossier DIR] [--port N]
                        [--kenobi ADDRESS] [--installer] [--oui] [--sans-navigateur] [--brancher]
                        [--nouveau-jeton]
```

- `--whisper medium` (or `large-v3-turbo`): better transcription of code words, slower; `base`: lighter.
- `--installer`: install, complete or repair the voice; `--oui`: without asking.
- `--nouveau-jeton`: new pairing code (paste it again in Kenobi).
- `--kenobi ADDRESS`: trust another Kenobi page, e.g. `https://example.org/Kenobi-tutor-IA/`.
- Port 8196 by default, or the next free one up to 8199.

### Uninstall

Delete the installation folder (table above) and the bridge's settings folder (`~/.config/kenobi-voice`,
`~/Library/Application Support/kenobi-voice` or `%APPDATA%\kenobi-voice`).

### Another local speech server

Any server that speaks the OpenAI audio API (`/v1/audio/transcriptions` and `/v1/audio/speech`), such as
[Speaches](https://github.com/speaches-ai/speaches) or [LocalAI](https://localai.io), works too: Réglages › Voix ›
« Un autre serveur vocal compatible OpenAI », its address and, if needed, its key. It must allow your Kenobi page's
origin (CORS).

## Option 2 — A voice API key

- **Mistral (Voxtral)**: create a key at <https://console.mistral.ai/api-keys>, paste it in Réglages › Voix ›
  « Voxtral · Mistral », choose a French voice (Léa, Camille, Manon, Marie). If your AI model is already Mistral,
  its key is used for the voice too. Transcription `voxtral-mini-transcribe`, voice `voxtral-mini-tts`.
- **ElevenLabs**: create a key at <https://elevenlabs.io/app/settings/api-keys> (it needs the text-to-speech,
  speech-to-text and voices permissions), paste it, « Charger les voix » (French voices are listed first), choose
  one. Transcription Scribe, voice model `eleven_flash_v2_5`.

## Tips

- Options in the voice tab: numbers said the Swiss way (septante, huitante, nonante); a short filler when the answer
  takes time.
- The microphone opens only during a call, and only on a secure page: the public site (HTTPS), `localhost`, or
  `127.0.0.1`. A self-hosted Kenobi opened over plain HTTP from another computer cannot use the microphone.
- Chrome asks once for permission to reach apps on your device (the voice bridge): accept it.
- « Je ne t'entends pas »: check which microphone your system uses. A code word misunderstood: say it again, write
  it, or use a bigger Whisper model.

## Self-hosted version

With `server.py`, the local voice runs inside the server: `python3 scripts/installer_voix.py` (or the button in
Réglages › Voix) installs the same Whisper and Piper, in the same folder. The voice bridge is not needed.
